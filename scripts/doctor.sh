#!/usr/bin/env bash
# Report which tools oryx-overlay can find, and what each one is for.

# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

status=0
check() {
  local cmd="$1" why="$2" need="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '  %s✓%s %-20s %s\n' "$C_G" "$C_0" "$cmd" "$why"
  elif [[ "$need" == required ]]; then
    printf '  %s✗%s %-20s %s (required)\n' "$C_R" "$C_0" "$cmd" "$why"
    status=1
  else
    printf '  %s-%s %-20s %s (optional)\n' "$C_Y" "$C_0" "$cmd" "$why"
  fi
}

printf '%sCore%s\n' "$C_B" "$C_0"
check git "sync with the oryx branch" required
check python3 "fetching from Oryx, drawing, diffs" required
printf '%sLocal builds%s (or use make docker-build)\n' "$C_B" "$C_0"
check qmk "QMK CLI (make setup)" optional
check arm-none-eabi-gcc "ARM compiler for STM32 boards" optional
check dfu-suffix "part of dfu-util; QMK needs it to package .bin files" optional
printf '%sDrawings%s\n' "$C_B" "$C_0"
check keymap "keymap-drawer (make setup)" optional
printf '%sOther%s\n' "$C_B" "$C_0"
check docker "container builds" optional
check claude "Claude Code" optional

if ! ( load_config ) >/dev/null 2>&1; then
  printf '\n%sConfig%s  oryx.conf is missing or incomplete:\n' "$C_B" "$C_0"
  ( load_config ) 2>&1 | sed 's/^/  /' || true
  status=1
elif load_config; then
  printf '\n%sConfig%s  layout=%s  keyboard=zsa/%s  keymap=%s\n' "$C_B" "$C_0" "$ORYX_LAYOUT_ID" "$KEYBOARD" "$KEYMAP_NAME"
  if [[ -f "$META_FILE" ]]; then
    printf '%sLayout%s  "%s", revision %s, firmware v%s\n' "$C_B" "$C_0" "$(meta title)" "$(meta revision)" "$(meta qmk_version)"
  else
    printf '%sLayout%s  not fetched yet (make sync)\n' "$C_B" "$C_0"
  fi
fi

problem="$(cache_path_problem)"
if [[ -n "$problem" ]]; then
  printf '%sCache%s   %s✗%s %s\n' "$C_B" "$C_0" "$C_R" "$C_0" "$problem"
  status=1
else
  printf '%sCache%s   %s\n' "$C_B" "$C_0" "$CACHE_DIR"
fi
exit $status
