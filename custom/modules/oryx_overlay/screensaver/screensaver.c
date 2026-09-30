// Idle screensaver for Oryx layouts.
//
// Oryx layer colours repaint every LED on each frame, so RGB Matrix effects
// never show on a fully coloured layout. After SCREENSAVER_TIMEOUT ms without
// input this module switches to SCREENSAVER_MODE and turns layer colours off,
// both in RAM only. The next key event, or any other input, puts both back.
// Nothing is written to EEPROM, so a power cut mid-screensaver changes nothing.
//
// Enable: add "oryx_overlay/screensaver" to "modules" in custom/keymap.json.
// Configure in custom/config.h:
//   #define SCREENSAVER_TIMEOUT 300000                    // ms idle, default 5 min
//   #define SCREENSAVER_MODE RGB_MATRIX_CYCLE_LEFT_RIGHT  // must be enabled in the firmware
//
// SPDX-License-Identifier: GPL-2.0-or-later

#include QMK_KEYBOARD_H
#ifdef COMMUNITY_MODULE_ORYX_ENABLE
#    include "oryx.h"
#endif

ASSERT_COMMUNITY_MODULES_MIN_API_VERSION(1, 0, 0);

#ifndef RGB_MATRIX_ENABLE
#    error "oryx_overlay/screensaver needs RGB Matrix (Moonlander, Voyager or Planck EZ)."
#endif

// The layer-colour switch lives in ZSA's keyboard_config on these boards.
#if !defined(KEYBOARD_zsa_moonlander) && !defined(KEYBOARD_zsa_voyager) && !defined(KEYBOARD_zsa_planck_ez)
#    error "oryx_overlay/screensaver supports the Moonlander, Voyager and Planck EZ."
#endif

#ifndef SCREENSAVER_TIMEOUT
#    define SCREENSAVER_TIMEOUT 300000
#endif

// If this fails to compile with "undeclared", that effect is not in your
// firmware: tick it in Oryx (Advanced Settings > RGB) or add
// #define ENABLE_RGB_MATRIX_<NAME> to custom/config.h.
#ifndef SCREENSAVER_MODE
#    define SCREENSAVER_MODE RGB_MATRIX_CYCLE_LEFT_RIGHT
#endif

#if SCREENSAVER_TIMEOUT <= 0
#    error "SCREENSAVER_TIMEOUT must be a positive number of milliseconds."
#endif

// RGB_MATRIX_TIMEOUT switches the LEDs off after the same idle clock; if it
// fires first, the screensaver would never be seen.
#if defined(RGB_MATRIX_TIMEOUT) && RGB_MATRIX_TIMEOUT > 0 && RGB_MATRIX_TIMEOUT <= SCREENSAVER_TIMEOUT
#    error "RGB_MATRIX_TIMEOUT is not longer than SCREENSAVER_TIMEOUT, so the LEDs turn off first. Raise the RGB timeout in Oryx or lower SCREENSAVER_TIMEOUT."
#endif

static bool    active;
static uint8_t saved_mode;
static bool    saved_layer_led_off;

static bool leds_controlled_elsewhere(void) {
#ifdef COMMUNITY_MODULE_ORYX_ENABLE
    // Keymapp and Oryx live training drive the LEDs themselves.
    return rawhid_state.rgb_control;
#else
    return false;
#endif
}

static void screensaver_start(void) {
    saved_mode          = rgb_matrix_get_mode();
    saved_layer_led_off = keyboard_config.disable_layer_led;
    rgb_matrix_mode_noeeprom(SCREENSAVER_MODE);
    keyboard_config.disable_layer_led = true;
    active                            = true;
}

static void screensaver_stop(void) {
    active                            = false;
    keyboard_config.disable_layer_led = saved_layer_led_off;
    rgb_matrix_mode_noeeprom(saved_mode);
}

void housekeeping_task_screensaver(void) {
    const bool idle = last_input_activity_elapsed() > (uint32_t)SCREENSAVER_TIMEOUT;
    if (active) {
        // Catches input that never reaches process_record (encoders, pointing
        // devices), and the case where something else took over the LEDs.
        if (!idle || leds_controlled_elsewhere()) {
            screensaver_stop();
        }
    } else if (idle && rgb_matrix_is_enabled() && !leds_controlled_elsewhere()) {
        screensaver_start();
    }
}

bool process_record_screensaver(uint16_t keycode, keyrecord_t *record) {
    // Runs before the keyboard's own handlers, so a Toggle Layer Colors or RGB
    // Mode press acts on (and saves) the real settings, not the screensaver's.
    if (active) {
        screensaver_stop();
    }
    return true;
}
