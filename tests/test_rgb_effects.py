"""Golden test: scripts/rgb_effects.js must match QMK's C effects byte for byte.

Compiles the real quantum/rgb_matrix/animations headers from ZSA's QMK fork
with gcc, renders every deterministic effect at a spread of timers, speeds,
colours and key-hit histories, and compares against the JavaScript port run
under node on the same inputs.

Needs a QMK tree (.cache/qmk_firmware-firmware*, made by `make build`), a
keyboard info file (build/info-*.json, made by `make render`), gcc and node.
Skipped when any is missing, e.g. in the lightweight CI checks job.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Effects whose output depends only on inputs (no random numbers, no hidden state).
STATELESS = [
    "solid_color", "alphas_mods", "gradient_up_down", "gradient_left_right", "breathing",
    "band_sat", "band_val", "band_pinwheel_sat", "band_pinwheel_val", "band_spiral_sat",
    "band_spiral_val", "cycle_all", "cycle_left_right", "cycle_up_down", "rainbow_moving_chevron",
    "cycle_out_in", "cycle_out_in_dual", "cycle_pinwheel", "cycle_spiral", "dual_beacon",
    "rainbow_beacon", "rainbow_pinwheels", "flower_blooming", "hue_breathing", "hue_pendulum",
    "hue_wave", "riverflow",
]
REACTIVE = [
    "solid_reactive_simple", "solid_reactive", "solid_reactive_wide", "solid_reactive_multiwide",
    "solid_reactive_cross", "solid_reactive_multicross", "solid_reactive_nexus",
    "solid_reactive_multinexus", "splash", "multisplash", "solid_splash", "solid_multisplash",
]
TIMERS = [0, 16, 777, 5000, 32767, 40000, 65535, 70001, 123456]
CONFIGS = [(0, 255, 255, 127), (200, 180, 175, 60), (85, 255, 90, 255), (140, 0, 255, 1)]


def _find(pattern: str) -> str | None:
    hits = sorted(glob.glob(str(ROOT / pattern)))
    return hits[-1] if hits else None


def _cache_dir() -> str:
    """CACHE_DIR exactly as scripts/lib.sh resolves it (it moves when the repo path has spaces)."""
    out = subprocess.run(["bash", "-c", 'source "$1/scripts/lib.sh" && printf %s "$CACHE_DIR"', "_", str(ROOT)],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 and out.stdout else str(ROOT / ".cache")


QMK = os.environ.get("ORYX_QMK_DIR") or _find(os.path.join(_cache_dir(), "qmk_firmware-firmware*"))
INFO = os.environ.get("ORYX_INFO_JSON") or _find("build/info-*.json")
pytestmark = pytest.mark.skipif(
    not (QMK and INFO and shutil.which("gcc") and shutil.which("node")),
    reason="needs a QMK tree, build/info-*.json, gcc and node (run `make build` first)",
)

HARNESS = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "lib8tion/lib8tion.h"
#include "color.h"
#include "layout.h"

#define HAS_FLAGS(bits, flags) ((bits & flags) == flags)
#define LED_FLAG_MODIFIER 0x01
#define LED_FLAG_KEYLIGHT 0x04
#define NO_LED 255
#define HAS_ANY_FLAGS(bits, flags) ((bits & flags) != 0x00)
typedef uint8_t led_flags_t;
typedef struct { uint8_t x, y; } led_point_t;
typedef struct { led_point_t point[RGB_MATRIX_LED_COUNT]; uint8_t flags[RGB_MATRIX_LED_COUNT]; } led_config_t;
typedef struct { uint8_t iter; led_flags_t flags; bool init; } effect_params_t;
typedef struct { hsv_t hsv; uint8_t speed; } rgb_config_t;
#define LED_HITS_TO_REMEMBER 8
typedef struct { uint8_t count; uint8_t x[8]; uint8_t y[8]; uint8_t index[8]; uint16_t tick[8]; } last_hit_t;

led_config_t g_led_config;
led_point_t k_rgb_matrix_center = {CENTER_X, CENTER_Y};
rgb_config_t rgb_matrix_config;
uint32_t g_rgb_timer;
last_hit_t g_last_hit_tracker;
static uint8_t out[RGB_MATRIX_LED_COUNT][3];

static void rgb_matrix_set_color(int i, uint8_t r, uint8_t g, uint8_t b) { out[i][0] = r; out[i][1] = g; out[i][2] = b; }
static rgb_t rgb_matrix_hsv_to_rgb(hsv_t hsv) { return hsv_to_rgb(hsv); }
static bool rgb_matrix_check_finished_leds(uint8_t m) { (void)m; return false; }
#define RGB_MATRIX_USE_LIMITS(min, max) uint8_t min = 0, max = RGB_MATRIX_LED_COUNT; (void)min
#define RGB_MATRIX_TEST_LED_FLAGS() if (!HAS_ANY_FLAGS(g_led_config.flags[i], params->flags)) continue
#define RGB_MATRIX_EFFECT(name)
#define RGB_MATRIX_CUSTOM_EFFECT_IMPLS
#define RGB_MATRIX_KEYREACTIVE_ENABLED
@ENABLES@
#include "rgb_matrix/animations/runners/rgb_matrix_runners.inc"
@INCLUDES@

typedef bool (*effect_f)(effect_params_t*);
static const struct { const char* key; effect_f fn; } EFFECTS[] = { @TABLE@ };

int main(void) {
    @POINTS@
    int timers[] = { @TIMERS@ };
    int configs[][4] = { @CONFIGS@ };
    effect_params_t p = { 0, 0xFF, false };
    printf("[");
    int first = 1;
    for (unsigned e = 0; e < sizeof(EFFECTS) / sizeof(EFFECTS[0]); e++)
    for (unsigned c = 0; c < sizeof(configs) / sizeof(configs[0]); c++)
    for (unsigned t = 0; t < sizeof(timers) / sizeof(timers[0]); t++)
    for (int h = 0; h < HITSETS; h++) {
        rgb_matrix_config.hsv = (hsv_t){configs[c][0], configs[c][1], configs[c][2]};
        rgb_matrix_config.speed = configs[c][3];
        g_rgb_timer = timers[t];
        memset(&g_last_hit_tracker, 0, sizeof g_last_hit_tracker);
        @HITS@
        memset(out, 0, sizeof out);
        EFFECTS[e].fn(&p);
        printf("%s{\"e\":\"%s\",\"c\":%u,\"t\":%u,\"h\":%d,\"rgb\":[", first ? "" : ",", EFFECTS[e].key, c, t, h);
        first = 0;
        for (int i = 0; i < RGB_MATRIX_LED_COUNT; i++) printf("%s%u,%u,%u", i ? "," : "", out[i][0], out[i][1], out[i][2]);
        printf("]}\n");
    }
    printf("]\n");
    return 0;
}
"""

# Key-hit histories for reactive effects: (led index, ms since the hit).
HIT_SETS = [
    [],
    [(0, 0)],
    [(5, 40), (30, 10)],
    [(1, 900), (2, 600), (3, 300), (10, 120), (20, 60), (40, 30), (50, 5), (60, 0)],
    [(7, 65000)],
]


def _c_name(key: str) -> str:
    return key.upper()


def _golden(tmp: Path, info: dict, effects: list[str], hit_sets: list) -> tuple[list[dict], list[list]]:
    leds = info["rgb_matrix"]["layout"]
    n = len(leds)
    cx, cy = info["rgb_matrix"].get("center_point", [112, 32])
    hits = [[h for h in hs if h[0] < n] for hs in hit_sets]
    (tmp / "layout.h").write_text(f"#define RGB_MATRIX_LED_COUNT {n}\n#define CENTER_X {cx}\n#define CENTER_Y {cy}\n#define HITSETS {len(hits)}\n")
    anim = Path(QMK) / "quantum/rgb_matrix/animations"
    includes = sorted({f.name for f in anim.glob("*.h")} - {"typing_heatmap_anim.h", "digital_rain_anim.h", "pixel_flow_anim.h", "pixel_fractal_anim.h", "pixel_rain_anim.h"})
    points = "\n    ".join(f"g_led_config.point[{i}] = (led_point_t){{{l['x']}, {l['y']}}}; g_led_config.flags[{i}] = {l.get('flags', 0)};" for i, l in enumerate(leds))
    hit_code = []
    for k, hs in enumerate(hits):
        body = " ".join(
            f"g_last_hit_tracker.index[{j}] = {i}; g_last_hit_tracker.x[{j}] = {leds[i]['x']}; g_last_hit_tracker.y[{j}] = {leds[i]['y']}; g_last_hit_tracker.tick[{j}] = {tk};"
            for j, (i, tk) in enumerate(hs)
        )
        hit_code.append(f"if (h == {k}) {{ g_last_hit_tracker.count = {len(hs)}; {body} }}")
    src = (
        HARNESS.replace("@ENABLES@", "\n".join(f"#define ENABLE_RGB_MATRIX_{_c_name(e)}" for e in effects))
        .replace("@INCLUDES@", "\n".join(f'#include "rgb_matrix/animations/{f}"' for f in includes))
        .replace("@TABLE@", ", ".join(f'{{"{e}", {_c_name(e)}}}' for e in effects))
        .replace("@POINTS@", points)
        .replace("@TIMERS@", ", ".join(map(str, TIMERS)))
        .replace("@CONFIGS@", ", ".join("{%d,%d,%d,%d}" % c for c in CONFIGS))
        .replace("@HITS@", "\n        ".join(hit_code))
    )
    (tmp / "harness.c").write_text(src)
    q = Path(QMK)
    exe = tmp / "harness"
    res = subprocess.run(
        ["gcc", "-O1", "-w", "-std=gnu11", f"-I{tmp}", f"-I{q / 'quantum'}", f"-I{q / 'lib'}", f"-I{q / 'lib/lib8tion'}",
         f"-I{q / 'platforms'}", "-DPROGMEM=", "-Dpgm_read_byte(x)=(*(x))",
         str(tmp / "harness.c"), str(q / "quantum/color.c"), "-o", str(exe)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, "harness failed to compile:\n" + res.stderr[-3000:]
    return json.loads(subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout), hits


NODE = r"""
const R = require(process.argv[2]);
const info = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const cases = JSON.parse(require('fs').readFileSync(process.argv[4], 'utf8'));
const leds = info.rgb_matrix.layout;
const out = [];
for (const k of cases) {
  const eng = R.createEngine({ leds, center: info.rgb_matrix.center_point, matrix: info.matrix_size ? [info.matrix_size.rows, info.matrix_size.cols] : null });
  eng.setEffect(k.e);
  eng.setConfig({ h: k.cfg[0], s: k.cfg[1], v: k.cfg[2], speed: k.cfg[3] });
  const hits = { count: k.hits.length, x: k.hits.map(h => leds[h[0]].x), y: k.hits.map(h => leds[h[0]].y), index: k.hits.map(h => h[0]), tick: k.hits.map(h => h[1]) };
  out.push(eng.renderAt(k.timer, hits));
}
process.stdout.write(JSON.stringify(out));
"""


def test_effects_match_qmk(tmp_path):
    info = json.loads(Path(INFO).read_text())
    effects = STATELESS + REACTIVE
    golden, hits = _golden(tmp_path, info, effects, HIT_SETS)
    cases = [
        {"e": g["e"], "cfg": CONFIGS[g["c"]], "timer": TIMERS[g["t"]], "hits": hits[g["h"]]}
        for g in golden
    ]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    (tmp_path / "run.js").write_text(NODE)
    js = json.loads(subprocess.run(
        ["node", str(tmp_path / "run.js"), str(ROOT / "scripts/rgb_effects.js"), INFO, str(tmp_path / "cases.json")],
        check=True, capture_output=True, text=True,
    ).stdout)
    mismatches = []
    # Equal lengths first, so a short node run can't let zip() skip comparisons.
    assert len(js) == len(golden) == len(cases), f"node rendered {len(js)} frames, QMK rendered {len(golden)}"
    for g, c, frame in zip(golden, cases, js):
        assert len(frame) == len(g["rgb"]), f"{g['e']}: node gave {len(frame)} values, QMK {len(g['rgb'])}"
        if g["rgb"] != frame:
            bad = next(i for i, (a, b) in enumerate(zip(g["rgb"], frame)) if a != b)
            mismatches.append(f"{g['e']} cfg={c['cfg']} timer={c['timer']} hits={len(c['hits'])}: first diff at LED {bad // 3} ({g['rgb'][bad//3*3:bad//3*3+3]} vs {frame[bad//3*3:bad//3*3+3]})")
    assert not mismatches, f"{len(mismatches)} of {len(golden)} frames differ:\n" + "\n".join(mismatches[:15])
    assert len(golden) == len(effects) * len(CONFIGS) * len(TIMERS) * len(HIT_SETS)


def test_every_board_effect_is_ported():
    info = json.loads(Path(INFO).read_text())
    src = (ROOT / "scripts/rgb_effects.js").read_text()
    missing = [k for k in info["rgb_matrix"].get("animations", {}) if f'["{k}"' not in src and f'"{k}",' not in src]
    assert not missing, f"effects the board supports but rgb_effects.js lacks: {missing}"
