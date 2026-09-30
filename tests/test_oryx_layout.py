"""Tests for scripts/oryx_layout.py and scripts/oryx_fetch.py.

Fixtures are synthetic: a 52-key split board shaped like a Voyager
(four rows of 6+6, then 2+2 thumbs), written the way Oryx writes keymap.c.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import oryx_fetch  # noqa: E402
import oryx_layout as ol  # noqa: E402

ROWS = [12, 12, 12, 12, 4]


def layer_src(index: int, keys: list[str]) -> str:
    assert len(keys) == sum(ROWS)
    lines, i = [], 0
    for n in ROWS:
        lines.append("    " + ", ".join(keys[i : i + n]) + ",")
        i += n
    lines[-1] = lines[-1].rstrip(",")
    return f"  [{index}] = LAYOUT_voyager(\n" + "\n".join(lines) + "\n  ),"


def base_keys() -> list[str]:
    keys = ["KC_Q", "KC_W", "KC_E", "KC_R", "KC_T", "KC_Y"] * 2
    keys += ["MT(MOD_LGUI, KC_A)", "KC_S", "DUAL_FUNC_0", "KC_F", "KC_G", "KC_H"] + ["KC_J"] * 6
    keys += ["KC_Z", "KC_X", "KC_C", "KC_V", "KC_B", "KC_N"] + ["KC_M"] * 6
    keys += ["KC_TAB", "KC_NO", "KC_NO", "KC_ESCAPE", "LT(1, KC_SPACE)", "DUAL_FUNC_1"] + ["KC_TRANSPARENT"] + ["KC_NO"] * 5
    keys += ["TG(2)", "KC_ENTER", "LCTL(LSFT(KC_Z))", "OSM(MOD_LSFT)"]
    return keys


def filler(k: str = "KC_TRANSPARENT") -> list[str]:
    return [k] * sum(ROWS)


KEYMAP_C = """
#include QMK_KEYBOARD_H
#include "version.h"
// a comment with a fake LAYOUT( call inside
/* block comment: keymaps[][MATRIX_ROWS][MATRIX_COLS] = { */
#define DUAL_FUNC_0 LT(14, KC_8)
#define DUAL_FUNC_1 LT(11, KC_W)

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
LAYERS
};

const uint8_t PROGMEM ledmap[][RGB_MATRIX_LED_COUNT][3] = {
    [1] = { {0,255,255}, {85,255,255}, {0,0,0} },
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
  switch (keycode) {
    case ST_MACRO_0:
    if (record->event.pressed) {
      SEND_STRING("https://example.com // not a comment");
    }
    break;
    case DUAL_FUNC_0:
      if (record->tap.count > 0) {
        if (record->event.pressed) {
          register_code16(KC_DLR);
        } else {
          unregister_code16(KC_DLR);
        }
      } else {
        if (record->event.pressed) {
          register_code16(KC_LEFT_GUI);
        } else {
          unregister_code16(KC_LEFT_GUI);
        }
      }
      return false;
    case DUAL_FUNC_1:
      if (record->tap.count > 0) {
        if (record->event.pressed) {
          register_code16(KC_RIGHT_CTRL);
        } else {
          unregister_code16(KC_RIGHT_CTRL);
        }
      } else {
        if (record->event.pressed) {
          layer_on(3);
        } else {
          layer_off(3);
        }
      }
      return false;
  }
  return true;
}
"""


def make_keymap(layers: list[list[str]]) -> str:
    return KEYMAP_C.replace("LAYERS", "\n".join(layer_src(i, k) for i, k in enumerate(layers)))


def make_info() -> dict:
    """Grid positions: left half x 0..5, right half x 8..13; thumbs inboard."""
    layout, leds = [], []
    for r in range(4):
        for c in range(6):
            layout.append({"matrix": [r, c], "x": c, "y": r})
        for c in range(6):
            layout.append({"matrix": [r + 6, c], "x": 8 + c, "y": r})
    for m, x in (([4, 0], 4), ([4, 1], 5), ([10, 0], 8), ([10, 1], 9)):
        layout.append({"matrix": m, "x": x, "y": 4.5})
    leds = [{"matrix": k["matrix"]} for k in layout]
    return {"layouts": {"LAYOUT": {"layout": layout}}, "rgb_matrix": {"layout": leds}}


@pytest.fixture
def four_layers() -> ol.Keymap:
    nav = filler()
    nav[0] = "TO(0)"
    km = ol.parse_keymap(make_keymap([base_keys(), filler(), nav, filler()]))
    ol.assign_layer_names(km, {"layers": [{"position": 0, "title": "Base"}, {"position": 1, "title": "Layer"}]})
    return km


def test_strip_comments_keeps_strings():
    src = 'a = "x // y"; // gone\nb = 1; /* gone */ c = \'/\';'
    out = ol.strip_comments(src)
    assert '"x // y"' in out and "gone" not in out and "'/'" in out


def test_split_args_respects_nesting():
    assert ol.split_args("MT(MOD_LSFT | MOD_LCTL, KC_A), KC_B,  LCTL(LSFT(KC_Z)) ,") == [
        "MT(MOD_LSFT | MOD_LCTL, KC_A)",
        "KC_B",
        "LCTL(LSFT(KC_Z))",
    ]


def test_parse_layers_rows_and_ledmap(four_layers):
    km = four_layers
    assert [l.index for l in km.layers] == [0, 1, 2, 3]
    assert km.layout_macro == "LAYOUT_voyager"
    assert all(len(l.keys) == 52 for l in km.layers)
    assert km.row_sizes == ROWS
    assert km.ledmap[1][:2] == [(0, 255, 255), (85, 255, 255)]


def test_layer_names_fall_back(four_layers):
    assert [l.name for l in four_layers.layers] == ["Base", "L1", "L2", "L3"]


def test_dual_function_keys(four_layers):
    assert four_layers.dual_funcs["DUAL_FUNC_0"] == ("KC_DLR", "KC_LEFT_GUI")
    assert four_layers.dual_funcs["DUAL_FUNC_1"] == ("KC_RIGHT_CTRL", "MO(3)")


@pytest.mark.parametrize(
    "style,raw,text",
    [
        ("pc", "MT(MOD_LGUI, KC_A)", "A/Super"),
        ("mac", "MT(MOD_LGUI, KC_A)", "A/⌘"),
        ("mac", "LCTL(LSFT(KC_Z))", "⌃⇧Z"),
        ("pc", "LCTL(LSFT(KC_Z))", "Ctrl+Shift+Z"),
        ("pc", "LT(1, KC_SPACE)", "Space/L1"),
        ("pc", "TG(2)", "L2/toggle"),
        ("mac", "OSM(MOD_LSFT)", "⇧/one-shot"),
        ("pc", "KC_TRANSPARENT", "▽"),
        ("pc", "KC_NO", "·"),
        ("mac", "DUAL_FUNC_0", "$/⌘"),
        ("pc", "DUAL_FUNC_1", "Ctrl/L3"),
        ("pc", "MT(MOD_LSFT | MOD_LCTL | MOD_LALT, KC_F)", "F/Meh"),
        ("pc", "ST_MACRO_3", "Macro 3"),
        ("pc", "TD(DANCE_2)", "Dance 2"),
    ],
)
def test_legends(four_layers, style, raw, text):
    assert ol.Labeller(four_layers, style).legend(raw).text() == text


def test_geometry_rows_and_positions(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    assert len(geo.grid) == 5
    assert geo.pos[0] == "L0.0" and geo.pos[6] == "R0.0"
    assert geo.pos[48] == "L4.0" and geo.pos[51] == "R4.1"
    # Thumbs still sit inboard in the printed grid.
    assert geo.grid[4].index(48) == 4


def test_positions_do_not_depend_on_info(four_layers):
    assert ol.Geometry.build(make_info(), four_layers).pos == ol.Geometry.build({}, four_layers).pos


def test_geometry_without_info_uses_rows(four_layers):
    geo = ol.Geometry.build({}, four_layers)
    assert [len([k for k in row if k is not None]) for row in geo.grid] == ROWS
    assert geo.keys == []


def test_led_colours_map_to_keys(four_layers):
    colours = ol.key_colours(four_layers, ol.Geometry.build(make_info(), four_layers))
    assert colours == {1: {0: "ff0000", 1: "00ff00"}}


def test_lint(four_layers):
    issues = ol.lint(four_layers, ol.Labeller(four_layers))
    text = "\n".join(m for _, m in issues)
    assert "L2: entered with TG" not in text  # has TO(0) to leave
    assert all(lvl == "warn" for lvl, _ in issues)
    assert "Base: 1 transparent key(s)" in text


def test_lint_flags_trap_and_dangling():
    base = base_keys()
    base[0] = "MO(9)"
    km = ol.parse_keymap(make_keymap([base, filler(), filler(), filler()]))
    ol.assign_layer_names(km, None)
    issues = ol.lint(km, ol.Labeller(km))
    assert ("error", "L0: `MO(9)` points at layer 9, which does not exist") in issues
    assert any("L2: entered with TG but has no TO/TG key to leave" in m for _, m in issues)


def test_access_map(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    access = {a["name"]: a["how"] for a in ol.access_map(four_layers, ol.Labeller(four_layers), geo)}
    assert access["Base"] == ["base layer", "switch key on L2 (L0.0)"]
    assert access["L1"] == ["hold Space on Base (L3.4)"]
    assert access["L3"] == ["hold Ctrl on Base (L3.5)"]


def test_markdown_render(four_layers, tmp_path):
    geo = ol.Geometry.build(make_info(), four_layers)
    md = ol.render_markdown(four_layers, ol.Labeller(four_layers, "mac"), geo, {"title": "T", "qmk_version": "25.0"}, None)
    assert md.startswith("# T\n")
    assert "## Base (layer 0)" in md and "| L1.2 | $/⌘ | `DUAL_FUNC_0` |" in md


def test_diff_cli(tmp_path, capsys):
    old = tmp_path / "old.c"
    new = tmp_path / "new.c"
    old.write_text(make_keymap([base_keys(), filler()]))
    changed = base_keys()
    changed[1] = "KC_P"
    new.write_text(make_keymap([changed, filler(), filler()]))
    ol.main(["diff", str(old), str(new), "--meta", "/nonexistent", "--info", "/nonexistent"])
    out = capsys.readouterr().out
    assert "- **L0** L0.1: `W` → `P`" in out
    assert "- **L2**: new layer" in out


def test_render_cli_text_only(tmp_path, capsys):
    km = tmp_path / "keymap.c"
    km.write_text(make_keymap([base_keys(), filler()]))
    info = tmp_path / "info.json"
    info.write_text(json.dumps(make_info()))
    assert ol.main(["render", "--keymap", str(km), "--meta", "/x", "--info", str(info), "--out", str(tmp_path / "docs"), "--no-svg"]) == 0
    assert (tmp_path / "docs" / "keymap.md").read_text().count("```text") == 2


def test_viewer_escapes_script_close(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    four_layers.layers[0].name = "</script><b>"
    html = ol.render_viewer(four_layers, ol.Labeller(four_layers), geo, {"title": "x"}, {}, ["<svg/>"] * 4)
    assert html.count("</script>") == 3  # only the page's own closing tags (data, engine, app)


def test_extract_source(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.md", "ignored")
        zf.writestr("zsa_x_source/keymap.c", "int a;\r\n")
        zf.writestr("zsa_x_source/rules.mk", "X = yes\n")
        zf.writestr("zsa_x.bin", b"\x00")
    out = tmp_path / "layout"
    out.mkdir()
    (out / "stale.h").write_text("old")
    (out / ".gitkeep").write_text("")
    assert oryx_fetch.extract_source(buf.getvalue(), out) == ["keymap.c", "rules.mk"]
    assert (out / "keymap.c").read_text() == "int a;\n"
    assert not (out / "stale.h").exists() and (out / ".gitkeep").exists()


def test_extract_source_rejects_escapes(tmp_path):
    buf = io.BytesIO()
    outside = tmp_path / "outside.txt"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x_source/keymap.c", "ok")
        zf.writestr("x_source/../../evil.c", "no")
        zf.writestr("x_source/" + str(outside), "no")
        zf.writestr("x_source/..\\..\\win.c", "no")
    out = tmp_path / "layout"
    assert oryx_fetch.extract_source(buf.getvalue(), out) == ["keymap.c"]
    assert not outside.exists()
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["keymap.c"]


def test_named_layers_and_tri_layer():
    src = """
enum planck_layers { _BASE, _LOWER, _RAISE, _ADJUST };
#define LOWER MO(_LOWER)
#define RAISE MO(_RAISE)
const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
  [_BASE] = LAYOUT_planck_grid(KC_A, LOWER, RAISE, KC_B),
  [_LOWER] = LAYOUT_planck_grid(KC_1, KC_TRNS, KC_TRNS, KC_2),
  [_RAISE] = LAYOUT_planck_grid(KC_3, KC_TRNS, KC_TRNS, KC_4),
  [_ADJUST] = LAYOUT_planck_grid(QK_BOOT, KC_TRNS, KC_TRNS, KC_NO),
};
layer_state_t layer_state_set_user(layer_state_t state) {
    return update_tri_layer_state(state, _LOWER, _RAISE, _ADJUST);
}
"""
    km = ol.parse_keymap(src)
    ol.assign_layer_names(km, {"layers": [{"position": i, "title": t} for i, t in enumerate(["Base", "Lower", "Raise", "Adjust"])]})
    lab = ol.Labeller(km)
    assert lab.legend("LOWER").text() == "Lower"
    assert lab.targets("RAISE") == [("MO", 2)]
    assert not [m for _, m in ol.lint(km, lab) if "no key reaches" in m]
    access = {a["name"]: a["how"] for a in ol.access_map(km, lab, ol.Geometry.build({}, km))}
    assert access["Adjust"] == ["hold Lower and Raise together (tri-layer)"]


def test_drawer_yaml_keeps_unicode(four_layers):
    four_layers.layers[1].name = "Sym 🔣"
    yaml_text, _ = ol.drawer_yaml(four_layers, ol.Labeller(four_layers), ol.Geometry.build(make_info(), four_layers), {})
    assert '"Sym 🔣":' in yaml_text


def lighting_info() -> dict:
    info = make_info()
    info["rgb_matrix"]["animations"] = {"breathing": True, "cycle_all": True, "splash": True, "typing_heatmap": False}
    info["rgb_matrix"]["center_point"] = [112, 32]
    for led in info["rgb_matrix"]["layout"]:
        led.update(x=10, y=10, flags=4)
    info["matrix_size"] = {"rows": 12, "cols": 6}
    return info


def test_lighting_status_follows_oryx_and_custom_config(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    oryx_cfg = "#undef ENABLE_RGB_MATRIX_SPLASH\n#define RGB_MATRIX_STARTUP_SPD 60\n#undef RGB_MATRIX_TIMEOUT\n#define RGB_MATRIX_TIMEOUT 900000\n"
    custom_cfg = "// custom\n#define ENABLE_RGB_MATRIX_TYPING_HEATMAP\n#undef ENABLE_RGB_MATRIX_BREATHING\n"
    light = ol.lighting_data(four_layers, ol.Labeller(four_layers), geo, lighting_info(), [oryx_cfg, custom_cfg])
    status = {e["key"]: e["status"] for e in light["effects"]}
    assert status["solid_color"] == "on" and status["cycle_all"] == "on"
    assert status["splash"] == "oryx"          # board has it, Oryx's config.h removed it
    assert status["breathing"] == "oryx"       # removed in custom/config.h
    assert status["typing_heatmap"] == "on"    # added in custom/config.h
    assert "raindrops" not in status           # the board can't run it
    cycle = {e["key"]: e["cycle"] for e in light["effects"] if e["status"] == "on"}
    assert cycle["solid_color"] == 0 and cycle["cycle_all"] < cycle["typing_heatmap"]
    assert light["startup"]["speed"] == 60 and light["timeoutMs"] == 900000
    assert light["oryxColours"] == ["L1"] and light["baseColoured"] is False


def test_lighting_keys_and_markdown(four_layers):
    four_layers.layers[1].keys[3] = "TOGGLE_LAYER_COLOR"
    four_layers.layers[1].keys[4] = "RGB_MODE_FORWARD"
    four_layers.ledmap[0] = [(0, 255, 255)]
    lab = ol.Labeller(four_layers)
    geo = ol.Geometry.build(make_info(), four_layers)
    light = ol.lighting_data(four_layers, lab, geo, lighting_info(), [])
    assert light["toggleKeys"] == ["L1 L0.3"] and light["modeKeys"] == ["L1 L0.4"]
    assert light["baseColoured"] is True
    keys = {k["kc"] for k in light["keys"]}
    assert {"KC_A", "KC_SPACE", "KC_DLR"} <= keys  # tap keycodes of MT/LT/dual-function keys
    md = "\n".join(ol.lighting_markdown(light))
    assert "Toggle Layer Colors (L1 L0.3)" in md and "RGB Mode key: L1 L0.4" in md


def test_effect_catalogue_matches_engine():
    keys = [k for k, _ in ol.effect_catalogue()]
    assert keys[0] == "solid_color" and keys[-1] == "riverflow" and len(keys) == len(set(keys)) == 50


def test_no_lighting_without_rgb_matrix(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    info = make_info()
    del info["rgb_matrix"]
    assert ol.lighting_data(four_layers, ol.Labeller(four_layers), geo, info, []) is None
