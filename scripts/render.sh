#!/usr/bin/env bash
# Regenerate docs/keymap.md, docs/keymap.yaml and the SVG drawings from layout/.
# Usage: scripts/render.sh [--no-svg]
# Without the qmk CLI (no keyboard geometry) or keymap-drawer, writes the text view only.

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
load_config
[[ -f "$LAYOUT_DIR/keymap.c" ]] || die "layout/keymap.c missing. Run 'make sync' first."

INFO=()
if command -v qmk >/dev/null 2>&1; then
  ensure_info
  INFO=(--info "$(info_file)")
else
  warn "qmk CLI not found, so no keyboard geometry: writing the text view only (run 'make setup')"
  set -- --no-svg "$@"
fi
if ! command -v keymap >/dev/null 2>&1; then
  warn "keymap-drawer not installed; writing the text view only (run 'make setup' for drawings)"
  set -- --no-svg "$@"
fi
exec "$PY" "$ROOT/scripts/oryx_layout.py" --style "$LEGEND_STYLE" render \
  --keymap "$LAYOUT_DIR/keymap.c" --meta "$META_FILE" ${INFO[@]+"${INFO[@]}"} --custom-config "$CUSTOM_DIR/config.h" --out "$ROOT/docs" "$@"
