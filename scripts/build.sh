#!/usr/bin/env bash
# Compile firmware from layout/ (Oryx) + custom/ (yours) into build/.
#
# The overlay is applied to a staging copy inside the QMK tree, never to your
# files, so custom/ can't conflict with Oryx:
#   custom/config.h         appended to config.h   via #include
#   custom/rules.mk         appended to rules.mk   via -include
#   custom/keymap_extra.c   appended to keymap.c   via #include (combos, overrides, helpers)
#   custom/keymap.json      "modules" merged into keymap.json (firmware v25+)
#   custom/modules/<owner>/<name>/  copied into the QMK tree as community modules
#
# Usage: scripts/build.sh [--no-render]

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

RENDER=1
for arg in "$@"; do
  case "$arg" in
    --no-render) RENDER=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

load_config
[[ -f "$LAYOUT_DIR/keymap.c" ]] || die "layout/keymap.c missing. Run 'make sync' first."
MAJOR="$(fw_major)"
if (( MAJOR < 24 )); then
  die "layout was compiled against firmware v$MAJOR; oryx-overlay needs v24+. Open it in Oryx and compile once to upgrade."
fi
require_toolchain
ensure_qmk
ensure_qmk_pydeps

QMK_DIR="$(qmk_dir)"
KB="$(kb_path)"
[[ -d "$QMK_DIR/keyboards/$KB" ]] || die "keyboard '$KB' not found in $(qmk_branch). Check KEYBOARD in oryx.conf."
KM_REL="keyboards/$KB/keymaps/$KEYMAP_NAME"
KM="$QMK_DIR/$KM_REL"

log "Staging layout/ + custom/ into ${KM#"$ROOT"/}"
rm -rf "$KM"
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
  if [[ -s "$CUSTOM_DIR/keymap.json" ]]; then
    "$PY" - "$CUSTOM_DIR/keymap.json" "$KM/keymap.json" "$MAJOR" <<'PY'
import json, sys
from pathlib import Path
custom = json.loads(Path(sys.argv[1]).read_text())
extra = [m for m in custom.get("modules", []) if m]
if extra:
    if int(sys.argv[3]) < 25:
        sys.exit(f"error: community modules need firmware v25+, layout uses v{sys.argv[3]}")
    target = Path(sys.argv[2])
    data = json.loads(target.read_text()) if target.exists() else {}
    mods = data.setdefault("modules", [])
    mods.extend(m for m in extra if m not in mods)
    target.write_text(json.dumps(data, indent=4) + "\n")
PY
  fi
  if [[ -d "$CUSTOM_DIR/modules" ]]; then
    for mod in "$CUSTOM_DIR"/modules/*/*/; do
      [[ -f "$mod/qmk_module.json" ]] || continue
      rel="${mod#"$CUSTOM_DIR"/modules/}"
      rel="${rel%/}"
      rm -rf "$QMK_DIR/modules/$rel"
      mkdir -p "$QMK_DIR/modules/$(dirname "$rel")"
      cp -R "$mod" "$QMK_DIR/modules/$rel"
      log "Module: $rel"
    done
  fi
fi

log "Compiling $KB:$KEYMAP_NAME against ZSA $(qmk_branch)"
mkdir -p "$BUILD_DIR"
LOG="$BUILD_DIR/build.log"
if ! QMK_HOME="$QMK_DIR" make -C "$QMK_DIR" -j"$(cpu_count)" "$KB:$KEYMAP_NAME" >"$LOG" 2>&1; then
  grep -E 'error|Error|undefined reference' "$LOG" | grep -v '^\s*$' | head -n 25 >&2 || true
  die "compile failed; full log: ${LOG#"$ROOT"/}"
fi

STEM="$(printf '%s' "$KB" | tr '/' '_')_${KEYMAP_NAME}"
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
