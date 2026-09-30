#!/usr/bin/env bash
# Shared helpers for oryx-overlay scripts. Source this file; don't run it.
# Written for bash 3.2 (the macOS default): no associative arrays, no ${x,,}.
# shellcheck disable=SC2034  # variables here are used by the scripts that source this file

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${CACHE_DIR:-$ROOT/.cache}"
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
LAYOUT_DIR="$ROOT/layout"
CUSTOM_DIR="$ROOT/custom"
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

# QMK's module include paths break on whitespace, so the cache path (where the
# QMK tree lives) must not contain any.
SPACE_HINT="Set CACHE_DIR to a path without spaces (e.g. CACHE_DIR=\$HOME/.cache/oryx-overlay), or use 'make docker-build'."
cache_path_has_space() { [[ "$CACHE_DIR" =~ [[:space:]] ]]; }

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
