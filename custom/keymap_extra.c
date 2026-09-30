// custom/keymap_extra.c: C appended to the end of Oryx's keymap.c at build time.
//
// Everything in keymap.c is in scope here (layers, custom keycodes, Oryx's
// DUAL_FUNC_n defines), and QMK's introspection sees what you define, so
// this is the place for combos, key overrides and helper functions.
//
// Callbacks Oryx already defines (process_record_user, keyboard_post_init_user,
// rgb_matrix_indicators_user, ...) can't be defined again here. To hook into
// those, prefer a community module in custom/modules/ (firmware v25+), which
// gets its own process_record_<module>() without touching keymap.c.
//
// Example combo (needs COMBO_ENABLE = yes in custom/rules.mk):
//
// const uint16_t PROGMEM jk_combo[] = {KC_J, KC_K, COMBO_END};
// combo_t key_combos[] = {
//     COMBO(jk_combo, KC_ESC),
// };
