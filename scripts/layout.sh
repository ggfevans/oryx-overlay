#!/usr/bin/env bash
# Run oryx_layout.py with this repo's settings (legend style, keyboard info).
# Usage: scripts/layout.sh diff [REF]    key-level changes since REF (default HEAD)
#        scripts/layout.sh lint          unreachable layers, dangling layer keys, traps
#        scripts/layout.sh parse         parsed keymap as JSON

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"
load_config
cd "$ROOT" || exit 1
cmd="${1:-}"
[[ -n "$cmd" ]] || { sed -n '2,5p' "$0"; exit 2; }
shift

INFO=()
[[ -s "$(info_file)" ]] && INFO=(--info "$(info_file)")

case "$cmd" in
  diff)
    ref="${1:-HEAD}"
    exec "$PY" scripts/oryx_layout.py --style "$LEGEND_STYLE" diff "$ref:layout/keymap.c" layout/keymap.c \
      --meta "$META_FILE" ${INFO[@]+"${INFO[@]}"} --raw
    ;;
  lint|parse)
    exec "$PY" scripts/oryx_layout.py --style "$LEGEND_STYLE" "$cmd" --meta "$META_FILE" ${INFO[@]+"${INFO[@]}"} "$@"
    ;;
  *) die "unknown command: $cmd" ;;
esac
