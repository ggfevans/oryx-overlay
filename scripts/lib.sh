#!/usr/bin/env bash
# Shared helpers for oryx-overlay scripts. Source this file; don't run it.
# Written for bash 3.2 (the macOS default): no associative arrays, no ${x,,}.
# shellcheck disable=SC2034  # variables here are used by the scripts that source this file

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# QMK's makefiles can't build from a path with whitespace in it. When the repo
# lives in one (~/My Projects/...), keep the QMK tree in the user cache instead,
# one folder per repo. An explicit CACHE_DIR always wins.
if [[ -z "${CACHE_DIR:-}" ]]; then
  case "$ROOT" in
    *[[:space:]]*) CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/oryx-overlay/$(printf '%s' "$ROOT" | cksum | awk '{print $1}')" ;;
    *) CACHE_DIR="$ROOT/.cache" ;;
  esac
fi
# QMK runs from inside its own tree (make -C), so a relative or quoted-tilde
# CACHE_DIR would resolve somewhere else there. Make it absolute once, here.
# shellcheck disable=SC2088  # matching a literal, unexpanded tilde on purpose
case "$CACHE_DIR" in
  "~"|"~/"*) CACHE_DIR="$HOME${CACHE_DIR#\~}" ;;
esac
[[ "$CACHE_DIR" == /* ]] || CACHE_DIR="$PWD/$CACHE_DIR"
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
LAYOUT_DIR="$ROOT/layout"
CUSTOM_DIR="$ROOT/custom"
MODULES_DIR="$ROOT/modules"  # template-owned community modules
META_FILE="$LAYOUT_DIR/.oryx.json"
QMK_REPO="${QMK_REPO:-https://github.com/zsa/qmk_firmware.git}"

# Prefer the project virtualenv made by `make setup`, when present. Not inside the
# container: the mounted .venv points at the host's Python, which doesn't exist there.
if [[ -d "$ROOT/.venv/bin" ]] && [[ ! -f /.dockerenv ]]; then
  PATH="$ROOT/.venv/bin:$PATH"
fi
PY="${PYTHON:-python3}"

if [[ -t 2 ]]; then
  C_B=$'\033[1m' C_R=$'\033[31m' C_Y=$'\033[33m' C_G=$'\033[32m' C_0=$'\033[0m'
else
  C_B='' C_R='' C_Y='' C_G='' C_0=''
fi
log()  { printf '%s==>%s %s\n' "$C_B" "$C_0" "$*" >&2; }
ok()   { printf '%s✓%s %s\n' "$C_G" "$C_0" "$*" >&2; }
warn() { printf '%swarning:%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }

CONFIG_KEYS="ORYX_LAYOUT_ID KEYBOARD KEYMAP_NAME LEGEND_STYLE ORYX_GEOMETRY QMK_BRANCH"

# Load oryx.conf. Variables already set in the environment win over the file,
# so CI inputs and one-off runs (KEYBOARD=voyager make build) just work.
load_config() {
  [[ -f "$ROOT/oryx.conf" ]] || die "missing $ROOT/oryx.conf"
  local k saved=""
  for k in $CONFIG_KEYS; do
    if [[ -n "${!k:-}" ]]; then saved="$saved $k"; eval "__env_$k=\${!k}"; fi
  done
  # shellcheck source=/dev/null
  source "$ROOT/oryx.conf"
  for k in $saved; do eval "$k=\$__env_$k"; done

  : "${ORYX_LAYOUT_ID:?set ORYX_LAYOUT_ID in oryx.conf}"
  : "${KEYBOARD:?set KEYBOARD in oryx.conf}"
  KEYMAP_NAME="${KEYMAP_NAME:-oryx-overlay}"
  LEGEND_STYLE="${LEGEND_STYLE:-pc}"
  # Oryx names geometries after the board family: moonlander, voyager, ergodox-ez, planck-ez.
  ORYX_GEOMETRY="${ORYX_GEOMETRY:-$(printf '%s' "${KEYBOARD%%/*}" | tr '_' '-')}"
  export ORYX_LAYOUT_ID KEYBOARD KEYMAP_NAME LEGEND_STYLE ORYX_GEOMETRY
}

# Read a field from layout/.oryx.json.
meta() {
  [[ -f "$META_FILE" ]] || die "no layout fetched yet ($META_FILE missing). Run: make sync"
  "$PY" -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2], ""); print(v if v is not None else "")' "$META_FILE" "$1"
}

# Major firmware version Oryx compiled against, e.g. 25.
fw_major() {
  local v
  v="$(meta qmk_version)"
  v="${v%%.*}"
  # Digits only: the value feeds paths, branch names and bash arithmetic.
  [[ "$v" =~ ^[0-9]{1,3}$ ]] || die "layout/.oryx.json has an invalid qmk_version"
  printf '%s' "$v"
}

qmk_branch() { printf '%s' "${QMK_BRANCH:-firmware$(fw_major)}"; }
qmk_dir()    { printf '%s' "$CACHE_DIR/qmk_firmware-$(qmk_branch)"; }
# Per-build QMK userspace holding modules/ and custom/modules/ (firmware v25+), outside the QMK tree.
userspace_dir() { printf '%s' "$CACHE_DIR/userspace-$(qmk_branch)"; }

# Empty when the cache path is usable, else a one-line explanation.
cache_path_problem() {
  case "$CACHE_DIR" in
    *[[:space:]]*) printf 'CACHE_DIR (%s) contains whitespace, which QMK cannot build from. Set CACHE_DIR to a path without spaces, e.g. CACHE_DIR=~/.cache/oryx-overlay make build, or use make docker-build.' "$CACHE_DIR" ;;
  esac
}

# A repository URL without a trailing slash or .git, for comparing remotes.
same_repo_url() { local u="${1%/}"; printf '%s' "${u%.git}"; }

# One build at a time per QMK tree: a second build would reset or restage the
# tree under the first. The lock is a symlink whose target names its owner
# ("pid@host"): creating it is atomic and carries the owner in the same step, on
# macOS and Linux alike. A lock is never taken over automatically, since two
# builds could both decide it was stale; a dead owner is reported instead.
acquire_build_lock() {
  local lock="$1" token owner pid host
  token="$$@$(uname -n)"
  # A directory here (e.g. an older lock format) would make ln -s put the link
  # inside it and "succeed". Refuse it rather than guess whether it is in use.
  if [[ -d "$lock" ]] && [[ ! -L "$lock" ]]; then
    die "unexpected directory at $lock (an older build lock?). If no build is running, remove it and build again: rm -rf '$lock'"
  fi
  if ! ln -s "$token" "$lock" 2>/dev/null; then
    owner="$(readlink "$lock" 2>/dev/null)" || owner=""
    pid="${owner%%@*}" host="${owner#*@}"
    if [[ "$host" == "$(uname -n)" ]] && [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      die "a build lock was left by process $pid, which is no longer running (it was interrupted). Remove it and build again: rm -f '$lock'"
    fi
    die "another build (process ${pid:-?} on ${host:-?}) is using this QMK tree. Wait for it to finish, or if it is not running: rm -f '$lock'"
  fi
  # Confirm the link is the lock itself, not a link created inside a directory
  # that appeared after the check above (ln has no portable "no target dir").
  if [[ ! -L "$lock" ]] || [[ "$(readlink "$lock")" != "$token" ]]; then
    rm -f "$lock/$token" 2>/dev/null || true
    die "could not take the build lock at $lock. If no build is running, remove it and build again: rm -rf '$lock'"
  fi
  BUILD_LOCK="$lock" BUILD_LOCK_TOKEN="$token"
  trap '[[ "$(readlink "$BUILD_LOCK" 2>/dev/null)" == "$BUILD_LOCK_TOKEN" ]] && rm -f "$BUILD_LOCK"' EXIT
}

# Put the cached QMK tree's keyboards/ and modules/ back to exactly ZSA's branch:
# undo edits to tracked files and delete everything else there (staged keymaps
# from earlier builds at any keyboard level, modules copied in by older versions
# of this script). Compiled objects in .build/ are kept for incremental builds.
reset_qmk_tree() {
  local dir="$1" top p paths
  paths=()
  top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || top=""
  # Never let git fall through to an enclosing repository (such as this one).
  if [[ -z "$top" ]] || [[ "$(cd "$top" && pwd -P)" != "$(cd "$dir" && pwd -P)" ]]; then
    die "$dir is not a git checkout of ZSA's QMK fork. Run 'make update-qmk' to download it again."
  fi
  # Only ever build (and reset) the fork and branch this layout needs.
  local url branch
  url="$(git -C "$dir" remote get-url origin 2>/dev/null)" || url=""
  branch="$(git -C "$dir" symbolic-ref --short -q HEAD 2>/dev/null)" || branch=""
  if [[ "$(same_repo_url "$url")" != "$(same_repo_url "$QMK_REPO")" ]] || [[ "$branch" != "$(qmk_branch)" ]]; then
    die "$dir is ${url:-an unknown repository} on ${branch:-a detached HEAD}, not $QMK_REPO on $(qmk_branch). Run 'make update-qmk' to download it again."
  fi
  for p in keyboards modules; do
    [[ -d "$dir/$p" ]] && paths+=("$p")
  done
  (( ${#paths[@]} )) || return 0
  git -C "$dir" checkout --quiet -- "${paths[@]}" || die "could not reset $dir"
  git -C "$dir" clean -ffdxq -- "${paths[@]}" || die "could not clean $dir"
}

# Keyboard path inside the QMK tree. Before firmware v25 the Moonlander had no
# revision folders (revision B didn't exist yet), so moonlander/reva -> moonlander.
kb_path() {
  local kb="$KEYBOARD"
  if [[ ! -d "$(qmk_dir)/keyboards/zsa/$kb" ]] && [[ "$kb" == moonlander/rev* ]] && [[ -d "$(qmk_dir)/keyboards/zsa/moonlander" ]]; then
    [[ "$kb" == moonlander/reva ]] || die "$kb needs firmware v25+, but this build uses $(qmk_branch). Compile the layout once in Oryx to upgrade."
    kb=moonlander
  fi
  printf 'zsa/%s' "$kb"
}

# Physical-layout JSON for the configured keyboard (one per board, so switching
# KEYBOARD for one run never draws with another board's geometry).
info_file() { printf '%s/info-%s.json' "$BUILD_DIR" "$(printf '%s' "$KEYBOARD" | tr '/' '_')"; }

cpu_count() { getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2; }

# Shallow-clone ZSA's QMK fork at the branch matching the layout's firmware.
ensure_qmk() {
  local dir branch
  dir="$(qmk_dir)"
  branch="$(qmk_branch)"
  if [[ -f "$dir/Makefile" ]]; then
    return 0
  fi
  log "Cloning $QMK_REPO ($branch) into ${dir#"$ROOT"/} (one-off, ~1 minute)"
  mkdir -p "$CACHE_DIR"
  rm -rf "$dir"
  git clone --quiet --depth 1 --branch "$branch" --recurse-submodules --shallow-submodules \
    --jobs "$(cpu_count)" "$QMK_REPO" "$dir" \
    || die "could not clone $QMK_REPO branch $branch (does that firmware branch exist?)"
  ok "QMK $branch ready"
}

# The interpreter the qmk CLI runs under (from its shebang).
qmk_python() {
  local line interp
  line="$(head -n 1 "$(command -v qmk)")"
  line="${line#\#!}"
  # shellcheck disable=SC2086  # split "env python3" into words
  set -- $line
  if [[ "$(basename "$1")" == env ]]; then shift; interp="$(command -v "$1")"; else interp="$1"; fi
  printf '%s' "$interp"
}

# Each ZSA firmware branch pins its own Python requirements (firmware24 needs
# appdirs, for example). Install whatever the qmk CLI's interpreter lacks into
# .cache/pydeps-<branch> and put it on PYTHONPATH, so builds work the same in a
# venv, Homebrew, CI or a non-root container.
ensure_qmk_pydeps() {
  local dir target py missing
  dir="$(qmk_dir)"
  [[ -f "$dir/requirements.txt" ]] || return 0
  py="$(qmk_python)"
  # Keyed by interpreter and platform: a Mac's packages must not leak into the Linux container.
  target="$CACHE_DIR/pydeps-$(qmk_branch)-$("$py" -c 'import sys, sysconfig; print(sys.implementation.cache_tag + "-" + sysconfig.get_platform())')"
  export PYTHONPATH="$target${PYTHONPATH:+:$PYTHONPATH}"
  missing="$("$py" - "$dir/requirements.txt" <<'PY'
import re, sys
from importlib import metadata
for line in open(sys.argv[1]):
    req = line.split("#", 1)[0].strip()
    if not req:
        continue
    name = re.split(r"[<>=!~\[; ]", req, maxsplit=1)[0]
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        print(req)
PY
)"
  [[ -n "$missing" ]] || return 0
  log "Installing Python packages $(qmk_branch) needs: $(printf '%s' "$missing" | tr '\n' ' ')"
  # shellcheck disable=SC2086  # one requirement per word
  "$py" -m pip install --quiet --disable-pip-version-check --target "$target" $missing \
    || die "could not install $(qmk_branch)'s Python requirements into ${target#"$ROOT"/}"
}

require_cmd() {
  local cmd="$1" hint="$2"
  command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' not found. $hint"
}

require_toolchain() {
  require_cmd qmk "Run 'make setup' for a local virtualenv, or use 'make docker-build'."
  require_cmd arm-none-eabi-gcc "Install the ARM toolchain (see README > Building locally), or use 'make docker-build'."
}

# Keyboard metadata (physical layout, LED positions) used for drawing.
ensure_info() {
  local out
  out="$(info_file)"
  if [[ -s "$out" ]]; then
    return 0
  fi
  require_cmd qmk "Run 'make setup' (or 'make docker-render')."
  ensure_qmk
  ensure_qmk_pydeps
  mkdir -p "$BUILD_DIR"
  QMK_HOME="$(qmk_dir)" qmk info -kb "$(kb_path)" -f json >"$out.tmp" 2>/dev/null \
    || die "qmk info failed for $(kb_path); check KEYBOARD in oryx.conf"
  mv "$out.tmp" "$out"
}
