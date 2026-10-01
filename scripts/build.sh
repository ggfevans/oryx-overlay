#!/usr/bin/env bash
# Compile firmware from layout/ (Oryx) + custom/ (yours) into build/.
#
# The overlay is applied to a staging copy inside the QMK tree, never to your
# files, so custom/ can't conflict with Oryx:
#   custom/config.h         appended to config.h   via #include
#   custom/rules.mk         appended to rules.mk   via -include
#   custom/keymap_extra.c   appended to keymap.c   via #include (combos, overrides, helpers)
#   custom/keymap.json      "modules" merged into keymap.json (firmware v25+)
#   custom/modules/<owner>/<name>/  staged in a per-build QMK userspace (firmware v25+)
#   modules/<owner>/<name>/         the template's bundled modules, staged the same
#                                   way first; a custom/modules/ module of the same
#                                   name replaces the template's
#
# Every build first resets the QMK tree's keyboards/ and modules/ to ZSA's branch,
# so nothing from an earlier build (another KEYBOARD, a deleted module) leaks in.
#
# Usage: scripts/build.sh [--no-render] [--stage-only]
#   --stage-only   reset and stage, print the staged paths, don't compile

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

RENDER=1
STAGE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-render) RENDER=0 ;;
    --stage-only) STAGE_ONLY=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

load_config
[[ -f "$LAYOUT_DIR/keymap.c" ]] || die "layout/keymap.c missing. Run 'make sync' first."
MAJOR="$(fw_major)"
if (( MAJOR < 24 )); then
  die "layout was compiled against firmware v$MAJOR; oryx-overlay needs v24+. Open it in Oryx and compile once to upgrade."
fi
problem="$(cache_path_problem)"
[[ -z "$problem" ]] || die "$problem"
if [[ $STAGE_ONLY == 0 ]]; then
  require_toolchain
fi
ensure_qmk
if [[ $STAGE_ONLY == 0 ]]; then
  ensure_qmk_pydeps
fi

QMK_DIR="$(qmk_dir)"
acquire_build_lock "$CACHE_DIR/.build-lock-$(qmk_branch)"
KB="$(kb_path)"
[[ -d "$QMK_DIR/keyboards/$KB" ]] || die "keyboard '$KB' not found in $(qmk_branch). Check KEYBOARD in oryx.conf."
# A board family (moonlander, ergodox_ez) builds one of its boards under another
# name, so the firmware would never be found. Ask for the exact board instead.
# Boards below $KB: folders with keyboard.json or info.json and no board below them.
BOARDS="$(cd "$QMK_DIR/keyboards/$KB" && find . -mindepth 2 -maxdepth 3 -path '*/keymaps' -prune -o \( -name keyboard.json -o -name info.json \) -print \
  | sed -e 's|^\./||' -e 's|/[^/]*$||' | sort -u \
  | awk -v kb="${KB#zsa/}" '{ d[NR] = $0 } END { for (i = 1; i <= NR; i++) { leaf = 1; for (j = 1; j <= NR; j++) if (index(d[j], d[i] "/") == 1) leaf = 0; if (leaf) printf "%s%s/%s", (n++ ? " " : ""), kb, d[i] } }')"
[[ -z "$BOARDS" ]] || die "KEYBOARD=${KB#zsa/} is a board family, not a board. Set KEYBOARD in oryx.conf to one of: $BOARDS"

log "Resetting ${QMK_DIR#"$ROOT"/} to ZSA $(qmk_branch)"
reset_qmk_tree "$QMK_DIR"
STEM="$(printf '%s' "$KB" | tr '/' '_')_${KEYMAP_NAME}"
rm -f "$QMK_DIR/$STEM.bin" "$QMK_DIR/$STEM.hex"

KM_REL="keyboards/$KB/keymaps/$KEYMAP_NAME"
KM="$QMK_DIR/$KM_REL"

# Community modules come from a userspace folder outside the tree (firmware v25+).
USERSPACE=""
if [[ -f "$QMK_DIR/lib/python/qmk/community_modules.py" ]]; then
  USERSPACE="$(userspace_dir)"
  rm -rf "$USERSPACE"
  mkdir -p "$USERSPACE/modules"
  printf '{\n    "userspace_version": "1.0",\n    "build_targets": []\n}\n' >"$USERSPACE/qmk.json"
fi

log "Staging layout/ + custom/ into ${KM#"$ROOT"/}"
rm -rf "$KM"  # in case KEYMAP_NAME matches one of ZSA's keymaps (default, oryx)
mkdir -p "$KM"
find "$LAYOUT_DIR" -maxdepth 1 -type f ! -name '.*' -exec cp {} "$KM/" \;

overlay_note() { printf '\n/* ---- oryx-overlay: %s ---- */\n' "$1"; }
if [[ -d "$CUSTOM_DIR" ]]; then
  mkdir -p "$KM/custom"
  find "$CUSTOM_DIR" -mindepth 1 -maxdepth 1 ! -name modules ! -name 'README*' ! -name '.*' -exec cp -R {} "$KM/custom/" \;

  if [[ -s "$CUSTOM_DIR/config.h" ]]; then
    { overlay_note "custom/config.h"; printf '#include "custom/config.h"\n'; } >>"$KM/config.h"
  fi
  if [[ -s "$CUSTOM_DIR/rules.mk" ]]; then
    { printf '\n# ---- oryx-overlay: custom/rules.mk ----\n'; printf -- '-include %s/custom/rules.mk\n' "$KM_REL"; } >>"$KM/rules.mk"
  fi
  if [[ -s "$CUSTOM_DIR/keymap_extra.c" ]]; then
    { overlay_note "custom/keymap_extra.c"; printf '#include "custom/keymap_extra.c"\n'; } >>"$KM/keymap.c"
  fi
fi

# Community modules: the template's modules/ first, then custom/modules/, so a
# module of yours with the same <owner>/<name> replaces the template's.
stage_modules() {
  local src="$1" label="$2" mod rel owner
  [[ -d "$src" ]] || return 0
  for mod in "$src"/*/*/; do
    [[ -f "$mod/qmk_module.json" ]] || continue
    rel="${mod#"$src"/}"
    rel="${rel%/}"
    owner="${rel%%/*}"
    # A userspace module shadows the tree's module of the same name, and zsa/oryx
    # is what Keymapp talks to. Never let a staged module replace one ZSA ships.
    [[ ! -e "$QMK_DIR/modules/$owner" ]] \
      || die "$label/$owner/ uses an owner name ZSA's firmware already ships (modules/$owner/). Rename the folder, e.g. to $label/my_$owner/, and update custom/keymap.json."
    if [[ -e "$USERSPACE/modules/$rel" ]]; then
      log "Module: $rel (your $label/$rel replaces the template's modules/$rel)"
      rm -rf "$USERSPACE/modules/$rel"
    else
      log "Module: $rel"
    fi
    mkdir -p "$USERSPACE/modules/$owner"
    cp -R "${mod%/}" "$USERSPACE/modules/$rel"
  done
}
if [[ -n "$USERSPACE" ]]; then
  stage_modules "$MODULES_DIR" modules
  stage_modules "$CUSTOM_DIR/modules" custom/modules
fi

# Merge custom/keymap.json's modules into Oryx's keymap.json, then check that every
# listed module exists, so a missing one fails here with a hint instead of mid-compile.
"$PY" - "$CUSTOM_DIR/keymap.json" "$KM/keymap.json" "$MAJOR" "$USERSPACE" "$QMK_DIR" <<'PY'
import json, sys
from pathlib import Path
custom_file, target, major = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
userspace, qmk_dir = sys.argv[4], Path(sys.argv[5])
custom = json.loads(custom_file.read_text()) if custom_file.is_file() and custom_file.stat().st_size else {}
extra = [m for m in custom.get("modules", []) if m]
data = json.loads(target.read_text()) if target.exists() else {}
if extra:
    if major < 25 or not userspace:
        sys.exit(f"error: community modules need firmware v25+, layout uses v{major}")
    mods = data.setdefault("modules", [])
    mods.extend(m for m in extra if m not in mods)
    target.write_text(json.dumps(data, indent=4) + "\n")
roots = [Path(userspace) / "modules", qmk_dir / "modules"]
missing = [m for m in data.get("modules", []) if not any((r / m / "qmk_module.json").is_file() for r in roots)]
if userspace and missing:
    sys.exit("error: community module not found: " + ", ".join(missing)
             + ". Add it as custom/modules/<owner>/<name>/ (or restore the template's modules/<owner>/<name>/), or remove it from custom/keymap.json.")
PY

if [[ $STAGE_ONLY == 1 ]]; then
  printf 'keymap=%s\nuserspace=%s\n' "$KM" "$USERSPACE"
  exit 0
fi

log "Compiling $KB:$KEYMAP_NAME against ZSA $(qmk_branch)"
mkdir -p "$BUILD_DIR"
LOG="$BUILD_DIR/build.log"
# Our userspace, and only ours: this also overrides any `qmk config user.overlay_dir`.
if [[ -n "$USERSPACE" ]]; then
  export QMK_USERSPACE="$USERSPACE"
fi
if ! QMK_HOME="$QMK_DIR" make -C "$QMK_DIR" -j"$(cpu_count)" "$KB:$KEYMAP_NAME" >"$LOG" 2>&1; then
  grep -E 'error|Error|undefined reference' "$LOG" | grep -v '^\s*$' | head -n 25 >&2 || true
  die "compile failed; full log: ${LOG#"$ROOT"/}"
fi

FW=""
for ext in bin hex; do
  if [[ -f "$QMK_DIR/$STEM.$ext" ]]; then FW="$QMK_DIR/$STEM.$ext"; fi
done
[[ -n "$FW" ]] || die "build succeeded but no $STEM.bin/.hex found; see ${LOG#"$ROOT"/}"

OUT="$BUILD_DIR/$(basename "$FW")"
cp "$FW" "$OUT"
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$BUILD_DIR" && sha256sum "$(basename "$OUT")" >"$(basename "$OUT").sha256")
else
  (cd "$BUILD_DIR" && shasum -a 256 "$(basename "$OUT")" >"$(basename "$OUT").sha256")
fi
SIZE="$(wc -c <"$OUT" | tr -d ' ')"
ok "Firmware: ${OUT#"$ROOT"/} ($SIZE bytes, firmware v$(meta qmk_version), Oryx revision $(meta revision))"

if [[ $RENDER == 1 ]]; then
  "$ROOT/scripts/render.sh" || warn "rendering docs/ failed; firmware is fine"
fi

cat >&2 <<EOF

Flash it with Keymapp: open Keymapp > Flash > select ${OUT#"$ROOT"/}
(Moonlander: make sure KEYBOARD in oryx.conf matches your revision, A or B.)
EOF
