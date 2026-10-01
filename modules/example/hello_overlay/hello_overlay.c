// A minimal QMK community module. Enable it by adding "example/hello_overlay"
// to "modules" in custom/keymap.json. QMK calls process_record_hello_overlay()
// before Oryx's process_record_user(), so no edit to keymap.c is needed.
// Docs: https://docs.qmk.fm/features/community_modules

#include QMK_KEYBOARD_H

ASSERT_COMMUNITY_MODULES_MIN_API_VERSION(1, 0, 0);

bool process_record_hello_overlay(uint16_t keycode, keyrecord_t *record) {
    // Example: make Shift+Backspace send Delete.
    if (keycode == KC_BSPC && record->event.pressed && (get_mods() & MOD_MASK_SHIFT)) {
        const uint8_t mods = get_mods();
        del_mods(MOD_MASK_SHIFT);
        tap_code(KC_DEL);
        set_mods(mods);
        return false;  // handled; stop further processing
    }
    return true;  // let QMK and Oryx handle everything else
}
