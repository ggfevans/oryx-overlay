"""Behaviour test for custom/modules/oryx_overlay/screensaver.

Compiles screensaver.c with gcc against small stand-ins for the QMK calls it
makes, then drives the idle clock and key events from a C harness. Needs gcc
only; skipped without it, or if you deleted the module.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom/modules/oryx_overlay/screensaver/screensaver.c"

pytestmark = pytest.mark.skipif(
    not (shutil.which("gcc") and MODULE.exists()), reason="needs gcc and the screensaver module"
)

STUBS = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#define ASSERT_COMMUNITY_MODULES_MIN_API_VERSION(a, b, c)
#define RGB_MATRIX_CYCLE_LEFT_RIGHT 14
typedef struct { int pressed; } keyevent_t;
typedef struct { keyevent_t event; } keyrecord_t;
typedef struct { bool disable_layer_led; } keyboard_config_t;
typedef struct { bool rgb_control; } rawhid_state_t;
extern keyboard_config_t keyboard_config;
extern rawhid_state_t rawhid_state;
uint32_t last_input_activity_elapsed(void);
uint8_t rgb_matrix_get_mode(void);
bool rgb_matrix_is_enabled(void);
void rgb_matrix_mode_noeeprom(uint8_t mode);
"""

HARNESS = r"""
#include "stubs.h"
keyboard_config_t keyboard_config;
rawhid_state_t rawhid_state;
static uint32_t idle_ms;
static uint8_t mode = 3;
static bool enabled = true;
uint32_t last_input_activity_elapsed(void) { return idle_ms; }
uint8_t rgb_matrix_get_mode(void) { return mode; }
bool rgb_matrix_is_enabled(void) { return enabled; }
void rgb_matrix_mode_noeeprom(uint8_t m) { mode = m; }
void housekeeping_task_screensaver(void);
bool process_record_screensaver(uint16_t keycode, keyrecord_t *record);
#define STATE(tag) printf("%s mode=%d layer_off=%d\n", tag, mode, keyboard_config.disable_layer_led)
int main(void) {
    keyrecord_t press = {{1}};
    idle_ms = 1000;  housekeeping_task_screensaver(); STATE("busy");
    idle_ms = 1001;  housekeeping_task_screensaver(); STATE("idle");
    process_record_screensaver(0, &press);             STATE("key");
    idle_ms = 1001;  housekeeping_task_screensaver(); STATE("idle2");
    idle_ms = 0;     housekeeping_task_screensaver(); STATE("encoder");
    keyboard_config.disable_layer_led = true;
    idle_ms = 5000;  housekeeping_task_screensaver();
    idle_ms = 0;     housekeeping_task_screensaver(); STATE("was_off");
    keyboard_config.disable_layer_led = false;
    enabled = false; idle_ms = 5000; housekeeping_task_screensaver(); STATE("rgb_off");
    enabled = true;  rawhid_state.rgb_control = true; housekeeping_task_screensaver(); STATE("keymapp");
    rawhid_state.rgb_control = false; housekeeping_task_screensaver(); STATE("idle3");
    rawhid_state.rgb_control = true;  housekeeping_task_screensaver(); STATE("taken");
    return 0;
}
"""

DEFINES = [
    "-DRGB_MATRIX_ENABLE", "-DKEYBOARD_zsa_moonlander", "-DCOMMUNITY_MODULE_ORYX_ENABLE",
    "-DSCREENSAVER_TIMEOUT=1000",
]


def _compile(tmp: Path, *extra: str) -> subprocess.CompletedProcess:
    (tmp / "stubs.h").write_text(STUBS)
    (tmp / "oryx.h").write_text("")
    (tmp / "harness.c").write_text(HARNESS)
    return subprocess.run(
        ["gcc", "-std=c11", "-Wall", "-Werror", f"-I{tmp}", '-DQMK_KEYBOARD_H="stubs.h"',
         *DEFINES, *extra, str(MODULE), str(tmp / "harness.c"), "-o", str(tmp / "t")],
        capture_output=True, text=True,
    )


def test_starts_when_idle_and_restores_on_input(tmp_path):
    built = _compile(tmp_path)
    assert built.returncode == 0, built.stderr
    out = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == [
        "busy mode=3 layer_off=0",      # not idle yet: untouched
        "idle mode=14 layer_off=1",     # screensaver effect, layer colours off
        "key mode=3 layer_off=0",       # key event restores before Oryx sees it
        "idle2 mode=14 layer_off=1",
        "encoder mode=3 layer_off=0",   # non-key input restores via housekeeping
        "was_off mode=3 layer_off=1",   # user's own layer-colours-off setting is kept
        "rgb_off mode=3 layer_off=0",   # RGB switched off: never starts
        "keymapp mode=3 layer_off=0",   # Keymapp owns the LEDs: never starts
        "idle3 mode=14 layer_off=1",
        "taken mode=3 layer_off=0",     # Keymapp takes over mid-screensaver: restore
    ]


def test_rgb_timeout_shorter_than_screensaver_fails_to_build(tmp_path):
    built = _compile(tmp_path, "-DRGB_MATRIX_TIMEOUT=1000")
    assert built.returncode != 0
    assert "RGB_MATRIX_TIMEOUT" in built.stderr


def test_unsupported_board_fails_to_build(tmp_path):
    (tmp_path / "stubs.h").write_text(STUBS)
    built = subprocess.run(
        ["gcc", "-std=c11", "-fsyntax-only", f"-I{tmp_path}", '-DQMK_KEYBOARD_H="stubs.h"',
         "-DRGB_MATRIX_ENABLE", str(MODULE)],
        capture_output=True, text=True,
    )
    assert built.returncode != 0
    assert "supports the Moonlander" in built.stderr
