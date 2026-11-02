"""Tests for scripts/oryx_layout.py and scripts/oryx_fetch.py.

Fixtures are synthetic: a 52-key split board shaped like a Voyager
(four rows of 6+6, then 2+2 thumbs), written the way Oryx writes keymap.c.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gen_locale_legends as gen  # noqa: E402
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
    assert [l.name for l in four_layers.layers] == ["Base", "Layer 1", "Layer 2", "Layer 3"]


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
        ("pc", "LT(1, KC_SPACE)", "Space/Layer 1"),
        ("pc", "TG(2)", "TG Layer 2"),
        ("pc", "TO(2)", "TO Layer 2"),
        ("pc", "OSL(2)", "OSL Layer 2"),
        ("pc", "DF(2)", "DF Layer 2"),
        ("pc", "MO(2)", "Layer 2"),
        ("pc", "TT(2)", "TT Layer 2"),
        ("mac", "OSM(MOD_LSFT)", "OSM ⇧"),
        ("pc", "KC_TRANSPARENT", "▽"),
        ("pc", "KC_NO", "·"),
        ("mac", "DUAL_FUNC_0", "$/⌘"),
        ("pc", "DUAL_FUNC_1", "Ctrl/Layer 3"),
        ("pc", "MT(MOD_LSFT | MOD_LCTL | MOD_LALT, KC_F)", "F/Meh"),
        ("pc", "ST_MACRO_3", "Macro 3"),
        ("pc", "TD(DANCE_2)", "Dance 2"),
        ("pc", "LM(2, MOD_LSFT)", "LM Layer 2"),
        # Locale keycodes: the character each key produces, from keymap_german.h etc.
        ("pc", "DE_Z", "Z"),
        ("pc", "DE_UDIA", "Ü"),
        ("pc", "DE_EXLM", "!"),  # S(DE_1)
        ("pc", "DE_AT", "@"),  # ALGR(DE_Q)
        ("pc", "DE_BSLS", "\\"),  # the header spells it "(backslash)"
        ("pc", "DE_CIRC", "^"),  # "^ (dead)"
        ("pc", "FR_A", "A"),
        ("pc", "FR_EACU", "é"),  # keymap_french.h, not keymap_canadian_french.h's É
        ("pc", "FR_EXLM", "!"),
        ("pc", "MT(MOD_LCTL, DE_Z)", "Z/Ctrl"),
        ("pc", "LT(1, FR_A)", "A/Layer 1"),
        ("pc", "LCTL(DE_Z)", "Ctrl+Z"),
        # A prefix no locale uses, and keycodes that only look like a locale.
        ("pc", "XX_Z", "Xx Z"),
        ("pc", "CM_TOGG", "Cm Togg"),  # combo toggle, not Colemak (CM_*)
        ("pc", "EE_CLR", "Ee Clr"),  # EEPROM clear, not Estonian (EE_*)
    ],
)
def test_legends(four_layers, style, raw, text):
    assert ol.Labeller(four_layers, style).legend(raw).text() == text


@pytest.mark.parametrize(
    "raw,desc",
    [
        ("MO(2)", "Hold for Layer 2."),
        ("TG(2)", "Toggles Layer 2 on/off."),
        ("TO(2)", "Switches to Layer 2."),
        ("OSL(2)", "Next key only on Layer 2."),
        ("DF(2)", "Sets Layer 2 as the default layer."),
        ("TT(2)", "Hold for Layer 2; tap it several times to toggle it on/off."),
    ],
)
def test_layer_keys_are_not_tap_hold(four_layers, raw, desc):
    lg = ol.Labeller(four_layers).legend(raw)
    assert lg.kind == "layer" and lg.hold == "" and lg.desc == desc
    assert "/" not in lg.text()


def test_lm_is_a_layer_key(four_layers):
    lg = ol.Labeller(four_layers).legend("LM(2, MOD_LCTL | MOD_LSFT)")
    assert (lg.tap, lg.hold, lg.kind, lg.func) == ("Layer 2", "", "layer", "LM")
    assert lg.note == "+Ctrl+Shift"
    assert lg.desc == "Hold for Layer 2 with Ctrl+Shift."


@pytest.mark.parametrize(
    "fn,hold",
    [
        ("LCAG_T", "Ctrl+Alt+Super"),
        ("RCAG_T", "Ctrl+Alt+Super"),
        ("SAGR_T", "Alt+Shift"),
        ("CTL_T", "Ctrl"),
        ("SFT_T", "Shift"),
        ("ALT_T", "Alt"),
        ("OPT_T", "Alt"),
        ("GUI_T", "Super"),
        ("CMD_T", "Super"),
        ("WIN_T", "Super"),
    ],
)
def test_mod_tap_aliases_keep_their_hold(four_layers, fn, hold):
    lg = ol.Labeller(four_layers).legend(f"{fn}(KC_A)")
    assert (lg.tap, lg.hold, lg.kind) == ("A", hold, "mod")


def test_swap_hands_tap_keeps_its_hold(four_layers):
    lg = ol.Labeller(four_layers).legend("SH_T(KC_A)")
    assert (lg.tap, lg.hold) == ("A", "Swap Hands")


def test_lcag_modifies_a_key(four_layers):
    assert ol.Labeller(four_layers).legend("LCAG(KC_A)").text() == "Ctrl+Alt+Super+A"


def test_tap_hold_layer_keys_keep_hold(four_layers):
    lg = ol.Labeller(four_layers).legend("LT(1, KC_SPACE)")
    assert (lg.tap, lg.hold, lg.desc) == ("Space", "Layer 1", "")


def test_unnamed_layer_fallback_is_not_position_like():
    km = ol.parse_keymap(make_keymap([base_keys(), filler(), filler()]))
    ol.assign_layer_names(km, None)
    assert [l.name for l in km.layers] == ["Layer 0", "Layer 1", "Layer 2"]
    assert ol.Labeller(km).layer("1") == "Layer 1"


def test_no_layer_name_next_to_position(tmp_path, capsys):
    """Unnamed layers must never read as `L1 L4.5` (layer name beside a position)."""
    import re
    km = ol.parse_keymap(make_keymap([base_keys(), filler(), filler()]))
    km.layers[1].keys[3] = "TOGGLE_LAYER_COLOR"
    km.layers[1].keys[4] = "RGB_MODE_FORWARD"
    ol.assign_layer_names(km, None)
    lab = ol.Labeller(km)
    geo = ol.Geometry.build(make_info(), km)
    light = ol.lighting_data(km, lab, geo, lighting_info(), [])
    md = ol.render_markdown(km, lab, geo, {}, None, light)
    assert "RGB Mode key: L0.4 on Layer 1" in md
    assert not re.search(r"\bL(?:ayer )?\d+\s+[LR]\d+\.\d+", md)


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
    assert "no way to leave" not in text  # Layer 2 has TO(0)
    assert all(lvl == "warn" for lvl, _ in issues)
    assert "Base: 1 transparent key(s)" in text


def test_lint_flags_trap_and_dangling():
    base = base_keys()
    base[0] = "MO(9)"
    trap = filler()
    trap[48] = "KC_NO"  # covers the TG(2) below, so nothing leaves layer 2
    km = ol.parse_keymap(make_keymap([base, filler(), trap, filler()]))
    ol.assign_layer_names(km, None)
    issues = ol.lint(km, ol.Labeller(km))
    assert ("error", "Layer 0: `MO(9)` points at layer 9, which does not exist") in issues
    assert any(m.startswith("Layer 2: no way to leave this layer after TG(Layer 2)") for _, m in issues)


def test_lm_reaches_its_layer():
    base = base_keys()
    base[0] = "LM(1, MOD_LCTL)"
    base[40] = "KC_SPACE"  # drop the fixture's LT(1, ...), so only LM reaches layer 1
    base[48] = "KC_ESCAPE"  # and its TG(2)
    km = ol.parse_keymap(make_keymap([base, filler(), filler()]))
    ol.assign_layer_names(km, None)
    lab = ol.Labeller(km)
    geo = ol.Geometry.build(make_info(), km)
    assert lab.targets("LM(1, MOD_LCTL)") == [("LM", 1)]
    text = "\n".join(m for _, m in ol.lint(km, lab))
    assert "Layer 1: no key reaches" not in text
    assert "Layer 1: entered with" not in text  # held, so there is nothing to leave
    assert "Layer 2: no key reaches" in text
    access = {a["name"]: a["how"] for a in ol.access_map(km, lab, geo)}
    assert access["Layer 1"] == ["hold the layer key on Layer 0 (L0.0)"]
    # The key that holds a layer is marked on that layer's drawing, as for MO.
    yaml_text, _ = ol.drawer_yaml(km, lab, geo, {})
    assert "held" in yaml_text.split('"Layer 1":')[1].splitlines()[1]


def test_multi_mod_keycode_does_not_split_its_table_row():
    base = base_keys()
    base[0] = "LM(1, MOD_LCTL | MOD_LSFT)"
    km = ol.parse_keymap(make_keymap([base, filler()]))
    ol.assign_layer_names(km, None)
    md = ol.render_markdown(km, ol.Labeller(km), ol.Geometry.build(make_info(), km), {}, None)
    assert "| `LM(1, MOD_LCTL \\| MOD_LSFT)` | Hold for Layer 1 with Ctrl+Shift. |" in md


def test_lm_pointing_at_a_missing_layer_is_an_error():
    base = base_keys()
    base[0] = "LM(9, MOD_LCTL)"
    km = ol.parse_keymap(make_keymap([base, filler()]))
    ol.assign_layer_names(km, None)
    issues = ol.lint(km, ol.Labeller(km))
    assert ("error", "Layer 0: `LM(9, MOD_LCTL)` points at layer 9, which does not exist") in issues


def test_access_map(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    access = {a["name"]: a["how"] for a in ol.access_map(four_layers, ol.Labeller(four_layers), geo)}
    assert access["Base"] == ["base layer", "switch key on Layer 2 (L0.0)"]
    assert access["Layer 1"] == ["hold Space on Base (L3.4)"]
    assert access["Layer 3"] == ["hold Ctrl on Base (L3.5)"]


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
    assert "- **Layer 0** (L0.1): `W` → `P`" in out
    assert "- **Layer 2**: new layer" in out


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


def test_viewer_key_json_carries_desc(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    html = ol.render_viewer(four_layers, ol.Labeller(four_layers), geo, {"title": "x"}, {}, ["<svg/>"] * 4)
    assert '"desc": "Toggles Layer 2 on/off."' in html
    assert '"desc": "Next key only with Shift."' in html


def test_osm_is_not_tap_hold(four_layers):
    lg = ol.Labeller(four_layers).legend("OSM(MOD_LSFT)")
    assert lg.hold == "" and lg.desc == "Next key only with Shift."


def test_long_layer_key_legend_keeps_layer_number(four_layers):
    four_layers.layers[1].name = "Layer 10"
    four_layers.layers[2].name = "Navigation"
    lab = ol.Labeller(four_layers)
    assert lab.legend("OSL(1)").text(11) == "OSL Laye…10"
    assert lab.legend("TG(2)").text(11) == "TG Navigat…"
    assert len(lab.legend("TG(2)").text(11)) <= 11
    assert lab.legend("TG(2)").text() == "TG Navigation"


def test_long_mo_legend_keeps_layer_number(four_layers):
    four_layers.layers[2].name = "Navigation Layer 2"
    lg = ol.Labeller(four_layers).legend("MO(2)")
    assert lg.text(11) == "Navigatio…2"
    assert len(lg.text(11)) == 11 and lg.text() == "Navigation Layer 2"


def test_tap_hold_legend_keeps_layer_number(four_layers):
    lab = ol.Labeller(four_layers)
    assert lab.legend("LT(1, KC_SPACE)").text() == "Space/Layer 1"
    assert lab.legend("LT(1, KC_SPACE)").text(11) == "Space/Lay…1"
    assert lab.legend("MT(MOD_LGUI, KC_A)").text(11) == "A/Super"  # fits: unchanged
    assert lab.legend("LT(1, KC_SPACE)").text(11).endswith("1")


def test_specials_table_has_descriptions(four_layers):
    four_layers.layers[0].keys[1] = "MO(1)"
    geo = ol.Geometry.build(make_info(), four_layers)
    md = ol.render_markdown(four_layers, ol.Labeller(four_layers), geo, {}, None)
    assert "| Pos | Legend | Keycode | Does |" in md
    assert "| TG Layer 2 | `TG(2)` | Toggles Layer 2 on/off. |" in md
    assert "| Layer 1 | `MO(1)` | Hold for Layer 1. |" in md


def test_trimmed_names_stay_distinct(four_layers):
    four_layers.layers[1].name = "Navigation"
    four_layers.layers[2].name = "Application"
    lab = ol.Labeller(four_layers)
    a, b = (lab.legend(f"LT({i}, KC_SPACE)").text(11) for i in (1, 2))
    assert a != b and len(a) <= 11 and len(b) <= 11


def test_specials_table_escapes_pipes(four_layers):
    four_layers.layers[1].name = "Nav | Sym"
    four_layers.layers[0].keys[1] = "MO(1)"
    geo = ol.Geometry.build(make_info(), four_layers)
    md = ol.render_markdown(four_layers, ol.Labeller(four_layers), geo, {}, None)
    row = next(l for l in md.splitlines() if "`MO(1)`" in l)
    assert "Nav \\| Sym" in row
    assert len(row.replace("\\|", "").split("|")) - 2 == 4  # four cells


def test_viewer_mod_branch_prefers_desc():
    html = (Path(ol.__file__).parent / "viewer.html").read_text(encoding="utf-8")
    assert "mod: k.desc ? k.desc : k.hold" in html


def test_default_layer_heading_has_no_redundant_index(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    four_layers.layers[1].name = "Layer 1"
    md = ol.render_markdown(four_layers, ol.Labeller(four_layers), geo, {}, None)
    assert "## Layer 1\n" in md and "## Base (layer 0)" in md and "(layer 1)" not in md


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


def test_extract_source_keeps_i18n_h(tmp_path):
    """keymap.c includes it, so a recent export doesn't build without it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x_source/keymap.c", '#include "i18n.h"\n')
        zf.writestr("x_source/i18n.h", I18N_H)
    out = tmp_path / "layout"
    assert oryx_fetch.extract_source(buf.getvalue(), out) == ["i18n.h", "keymap.c"]
    assert (out / "i18n.h").read_text() == I18N_H


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
    assert light["oryxColours"] == ["Layer 1"] and light["baseColoured"] is False


def test_lighting_keys_and_markdown(four_layers):
    four_layers.layers[1].keys[3] = "TOGGLE_LAYER_COLOR"
    four_layers.layers[1].keys[4] = "RGB_MODE_FORWARD"
    four_layers.ledmap[0] = [(0, 255, 255)]
    lab = ol.Labeller(four_layers)
    geo = ol.Geometry.build(make_info(), four_layers)
    light = ol.lighting_data(four_layers, lab, geo, lighting_info(), [])
    assert light["toggleKeys"] == ["L0.3 on Layer 1"] and light["modeKeys"] == ["L0.4 on Layer 1"]
    assert light["baseColoured"] is True
    keys = {k["kc"] for k in light["keys"]}
    assert {"KC_A", "KC_SPACE", "KC_DLR"} <= keys  # tap keycodes of MT/LT/dual-function keys
    md = "\n".join(ol.lighting_markdown(light))
    assert "Toggle Layer Colors (L0.3 on Layer 1)" in md and "RGB Mode key: L0.4 on Layer 1" in md


def test_effect_catalogue_matches_engine():
    keys = [k for k, _ in ol.effect_catalogue()]
    assert keys[0] == "solid_color" and keys[-1] == "riverflow" and len(keys) == len(set(keys)) == 50


def test_no_lighting_without_rgb_matrix(four_layers):
    geo = ol.Geometry.build(make_info(), four_layers)
    info = make_info()
    del info["rgb_matrix"]
    assert ol.lighting_data(four_layers, ol.Labeller(four_layers), geo, info, []) is None


def test_transparent_key_over_toggle_is_an_exit():
    # Layer 2 is entered with TG(2) on the base layer; its transparent key at the
    # same position falls through to that TG(2), so it is not a trap.
    km = ol.parse_keymap(make_keymap([base_keys(), filler(), filler(), filler()]))
    ol.assign_layer_names(km, None)
    assert not [m for _, m in ol.lint(km, ol.Labeller(km)) if "no way to leave" in m]


# Trap check: the lint walks QMK's layer state, so these spell out what firmware does.
TRAP = "no way to leave this layer"
TG2, LT1, DF1 = 48, 40, 41  # where base_keys() puts TG(2), LT(1, ...) and DUAL_FUNC_1


def lint_layers(*layers: list[str]) -> list[tuple[str, str]]:
    km = ol.parse_keymap(make_keymap(list(layers)))
    ol.assign_layer_names(km, None)
    return ol.lint(km, ol.Labeller(km))


def traps(*layers: list[str]) -> list[str]:
    return [m for _, m in lint_layers(*layers) if TRAP in m or "get back to" in m]


def closed(**keys: str) -> list[str]:
    """A layer that covers TG(2), LT(1) and DUAL_FUNC_1, so only `keys` (k48="TO(0)") act."""
    layer = filler()
    layer[TG2] = layer[LT1] = layer[DF1] = "KC_NO"
    for pos, kc in keys.items():
        layer[int(pos[1:])] = kc
    return layer


def test_transparency_skips_inactive_layers():
    # Issue 17, false positive: layer 1 has KC_A here but isn't on, so layer 2's
    # transparent key lands on the base layer's TG(2).
    middle = filler()
    middle[TG2] = "KC_A"
    assert traps(base_keys(), middle, filler(), filler()) == []


def test_toggling_a_lower_layer_does_not_leave():
    # Issue 17, false negative: TG(1) only adds a layer underneath layer 2.
    assert len(traps(base_keys(), filler(), closed(k0="TG(1)"), filler())) == 1


def test_trap_message_names_the_key_that_gets_you_there():
    assert traps(base_keys(), filler(), closed(), filler()) == [
        "Layer 2: no way to leave this layer after TG(Layer 2) on Layer 0 (fine if a macro, combo, tap dance or custom code does)."
        " In Oryx, add TO(Layer 0) or TG(Layer 2) on Layer 2, or make the key that sits over TG(Layer 2) transparent"
    ]


def test_df_does_not_clear_a_toggled_layer():
    found = traps(base_keys(), filler(), closed(k0="DF(1)"), filler())
    assert any(m.startswith("Layer 2: no way to leave") for m in found)


@pytest.mark.parametrize("exit_key", ["TO(0)", "TO(3)", "TG(2)", "MO(2)", "LT(2, KC_A)", "LM(2, MOD_LCTL)", "OSL(2)", "TT(2)"])
def test_exits_from_a_toggled_layer(exit_key):
    # Releasing MO, LT, LM or OSL clears the layer's bit, even one TG set.
    layer3 = filler()
    layer3[0] = "TO(0)"
    assert traps(base_keys(), filler(), closed(k5=exit_key), layer3) == []


def test_to_replaces_the_active_layers():
    # TO(3) drops layers 1 and 2, so layer 3's transparent key no longer reaches the
    # TG(3) on layer 2, and the base layer has KC_Q there.
    base = base_keys()
    base[TG2] = "TO(2)"
    layer3 = closed()
    layer3[5] = "KC_TRANSPARENT"
    assert traps(base, filler(), closed(k5="TG(3)", k6="TO(3)"), layer3) == [
        "Layer 3: no way to leave this layer after TO(Layer 3) on Layer 2 (fine if a macro, combo, tap dance or custom code does)."
        " In Oryx, add TO(Layer 0) or TG(Layer 3) on Layer 3"
    ]


def test_to_cycle_that_never_returns_to_base():
    # Each layer can be left, but only for the other one.
    base = base_keys()
    base[TG2] = "TO(1)"
    assert traps(base, closed(k0="TO(2)"), closed(k0="TO(1)"), filler()) == [
        "Layer 1, Layer 2: no way to get back to Layer 0 from these layers (fine if a macro, combo, tap dance or custom code does)."
        " In Oryx, add TO(Layer 0) on them"
    ]


def test_momentary_layers_are_never_traps():
    base = base_keys()
    base[TG2] = "MO(2)"
    assert traps(base, closed(), closed(), closed()) == []


def test_osl_is_not_a_trap():
    base = base_keys()
    base[TG2] = "OSL(2)"
    assert traps(base, filler(), closed(), filler()) == []


def test_every_way_in_counts_a_momentary_entry_hides_no_trap():
    # MO(2) is safe but TG(2) latches the layer, and layer 2 covers both keys.
    base = base_keys()
    base[0] = "MO(2)"
    layer2 = closed()
    layer2[0] = "KC_NO"
    assert len(traps(base, filler(), layer2, filler())) == 1


def test_momentary_key_for_self_under_transparency_is_the_way_out():
    base = base_keys()
    base[0] = "MO(2)"
    assert traps(base, filler(), closed(), filler()) == []


def test_one_safe_path_does_not_clear_a_trapped_one():
    # Layer 1 toggled on, then TG(2): layer 2's transparent key finds layer 1's TG(2).
    # Held on LT(1) instead, layer 1 goes away on release and the same key finds KC_Q.
    base = base_keys()
    base[TG2] = "KC_A"
    base[1] = "TG(1)"
    middle = filler()
    middle[0] = "TG(2)"
    assert len(traps(base, middle, closed(k1="KC_NO"), filler())) == 1


def test_a_trapped_path_is_not_reported_when_every_path_gets_out():
    # As above, with TG(1) left visible: from the held path, layer 2 can turn layer 1
    # on and leave through its TG(2).
    base = base_keys()
    base[TG2] = "KC_A"
    base[1] = "TG(1)"
    middle = filler()
    middle[0] = "TG(2)"
    assert traps(base, middle, closed(), filler()) == []


def test_exit_through_a_held_layer():
    # Layer 2 has no exit of its own, but LT(1) falls through it and layer 1 has TG(2).
    middle = filler()
    middle[0] = "TG(2)"
    layer2 = closed()
    layer2[LT1] = "KC_TRANSPARENT"
    assert traps(base_keys(), middle, layer2, filler()) == []


def test_held_key_cannot_also_be_pressed():
    # The only TG(2) on layer 1 sits under the finger holding LT(1).
    middle = filler()
    middle[LT1] = "TG(2)"
    layer2 = closed()
    layer2[LT1] = "KC_TRANSPARENT"
    assert len(traps(base_keys(), middle, layer2, filler())) == 1


def test_latched_tt_needs_an_exit():
    base = base_keys()
    base[TG2] = "TT(2)"
    assert len(traps(base, filler(), closed(), filler())) == 1  # was skipped: TT isn't TG
    assert traps(base, filler(), filler(), filler()) == []  # transparent over its own TT


def test_df_needs_a_way_back_and_to_does_not_clear_the_default():
    base = base_keys()
    base[TG2] = "DF(2)"
    assert len(traps(base, filler(), closed(k0="TO(0)"), filler())) == 1  # TO leaves the default on
    assert traps(base, filler(), closed(k0="DF(0)"), filler()) == []


def test_tri_layer_is_on_only_with_both_layers():
    km = ol.parse_keymap(
        make_keymap([base_keys(), filler(), filler(), closed()])
        + "\nlayer_state_t layer_state_set_user(layer_state_t state) { return update_tri_layer_state(state, 1, 2, 3); }\n"
    )
    ol.assign_layer_names(km, None)
    assert not [m for _, m in ol.lint(km, ol.Labeller(km)) if TRAP in m]


def test_too_many_states_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(ol, "MAX_STATES", 1)
    assert [m for _, m in lint_layers(base_keys(), filler(), filler(), filler()) if "trap check skipped" in m]


def test_combo_reaches_a_layer():
    base = base_keys()
    base[TG2] = "KC_A"
    km = ol.parse_keymap(make_keymap([base, filler(), filler(), filler()]) + "\ncombo_t key_combos[] = { COMBO(combo0, OSL(2)), };\n")
    ol.assign_layer_names(km, None)
    assert km.combos == ["OSL(2)"]
    assert not [m for _, m in ol.lint(km, ol.Labeller(km)) if m.startswith("Layer 2: no key reaches")]


# LT(dummy, KC) helpers: the export names a key, then process_record_user swallows it.
HELPER = """
    case TH_LTGT:
      if (record->tap.count > 0) {
        if (record->event.pressed) {
          register_code16(KC_LT);
        } else {
          unregister_code16(KC_LT);
        }
      }
      BODY
"""


def helper_keymap(layer: str, body: str = "return false;") -> ol.Keymap:
    base = base_keys()
    base[0] = "TH_LTGT"
    src = make_keymap([base, filler(), filler(), filler()])
    src = src.replace("#define DUAL_FUNC_0", f"#define TH_LTGT LT({layer}, KC_F14)\n#define DUAL_FUNC_0")
    src = src.replace("    case ST_MACRO_0:", HELPER.replace("BODY", body) + "    case ST_MACRO_0:")
    km = ol.parse_keymap(src)
    ol.assign_layer_names(km, None)
    return km


def test_dummy_layer_helper_is_not_dangling():
    km = helper_keymap("6")
    assert not [i for i in ol.lint(km, ol.Labeller(km)) if i[0] == "error"]


def test_dummy_layer_helper_at_end_of_switch_is_not_dangling():
    base = base_keys()
    base[0] = "LT(6, KC_F14)"
    src = make_keymap([base, filler(), filler(), filler()]).replace(
        "      return false;\n  }\n  return true;", "      return false;\n    case LT(6,KC_F14):\n      tap_code(KC_A);\n      return false;\n  }\n  return true;"
    )
    km = ol.parse_keymap(src)
    ol.assign_layer_names(km, None)
    assert not [i for i in ol.lint(km, ol.Labeller(km)) if i[0] == "error"]


def test_dummy_layer_helper_does_not_reach_a_real_layer():
    km = helper_keymap("3")
    assert ol.Labeller(km).targets("TH_LTGT") == []


@pytest.mark.parametrize(
    "body",
    [
        "return true;",  # the hold reaches the real LT
        "if (record->tap.count) { return false; } return true;",
        "break;",
        "if (x) { return false; }",  # falls through when x is false
    ],
)
def test_helper_that_lets_the_hold_through_is_still_checked(body):
    km = helper_keymap("6", body)
    assert ("error", "Layer 0: `TH_LTGT` points at layer 6, which does not exist") in ol.lint(km, ol.Labeller(km))


def test_case_outside_process_record_user_is_not_a_helper():
    extra = "uint16_t get_tapping_term(uint16_t keycode, keyrecord_t *record) { switch (keycode) { case TH_LTGT: return false; } return 200; }\n"
    base = base_keys()
    base[0] = "TH_LTGT"
    src = make_keymap([base, filler(), filler(), filler()]).replace("#define DUAL_FUNC_0", "#define TH_LTGT LT(6, KC_F14)\n#define DUAL_FUNC_0")
    km = ol.parse_keymap(src + extra)
    ol.assign_layer_names(km, None)
    assert any(lvl == "error" for lvl, _ in ol.lint(km, ol.Labeller(km)))


def with_process_record(layers: list[list[str]], function: str, defines: str = "") -> ol.Keymap:
    """A keymap whose process_record_user is exactly `function`."""
    src = make_keymap(layers)
    src = src[: src.index("bool process_record_user")] + function
    km = ol.parse_keymap(src.replace("#define DUAL_FUNC_0", defines + "\n#define DUAL_FUNC_0"))
    ol.assign_layer_names(km, None)
    return km


def lt9_keymap(function: str) -> ol.Keymap:
    base = base_keys()
    base[0] = "LT(9, KC_F14)"
    return with_process_record([base, filler(), filler(), filler()], function)


LT9_ERROR = ("error", "Layer 0: `LT(9, KC_F14)` points at layer 9, which does not exist")


@pytest.mark.parametrize(
    "line",
    [
        'SEND_STRING("{");',
        "tap_code16('{');",
        "#ifdef A\n  if (a) {\n#else\n  if (b) {\n#endif\n  }",
    ],
)
def test_unbalanced_process_record_user_does_not_crash(line):
    km = lt9_keymap("bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n  " + line + "\n  return true;\n}\n")
    assert km.intercepted == set()
    assert LT9_ERROR in ol.lint(km, ol.Labeller(km))  # unreadable, so nothing is swallowed


def test_unbalanced_combo_is_skipped_and_the_rest_still_read():
    src = make_keymap([base_keys(), filler(), filler(), filler()])
    km = ol.parse_keymap('const char *s = "COMBO(";\n' + src + "\ncombo_t c[] = { COMBO(combo0, OSL(2)) };\nconst char *t = \"COMBO(\";\n")
    assert km.combos == ["OSL(2)"]


def test_nested_switch_does_not_swallow_the_hold():
    km = lt9_keymap(
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n"
        "  if (record->tap.count > 0) {\n    switch (keycode) {\n      case LT(9, KC_F14):\n"
        "        tap_code(KC_A);\n        return false;\n    }\n  }\n  return true;\n}\n"
    )
    assert LT9_ERROR in ol.lint(km, ol.Labeller(km))


def test_nested_switch_keeps_a_real_layer_reach():
    base = base_keys()
    base[0] = "LT(3, KC_F14)"
    km = with_process_record(
        [base, filler(), filler(), filler()],
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n"
        "  if (record->tap.count > 0) {\n    switch (keycode) {\n      case LT(3, KC_F14):\n"
        "        tap_code(KC_A);\n        return false;\n    }\n  }\n  return true;\n}\n",
    )
    assert ol.Labeller(km).targets("LT(3, KC_F14)") == [("LT", 3)]


@pytest.mark.parametrize(
    "body",
    [
        "if (x) return other(keycode); return false;",
        "if (x) return y; return false;",
    ],
)
def test_case_with_another_return_is_not_swallowed(body):
    km = helper_keymap("6", body)
    assert ("error", "Layer 0: `TH_LTGT` points at layer 6, which does not exist") in ol.lint(km, ol.Labeller(km))


def test_early_return_before_the_switch_is_not_swallowed():
    km = lt9_keymap(
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n  if (x) return true;\n"
        "  switch (keycode) {\n    case LT(9, KC_F14):\n      return false;\n  }\n  return true;\n}\n"
    )
    assert LT9_ERROR in ol.lint(km, ol.Labeller(km))


def test_top_level_switch_with_only_return_false_is_swallowed():
    km = lt9_keymap(
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n"
        "  switch (keycode) {\n    case LT(9, KC_F14):\n      if (x) { return false; }\n      tap_code(KC_A);\n      return false;\n  }\n"
        "  return true;\n}\n"
    )
    assert not [i for i in ol.lint(km, ol.Labeller(km)) if i[0] == "error"]


def test_key_transparent_on_every_active_layer_acts_as_layer_0():
    # layer_switch_get_layer() ends in `return 0`: layer 0's key, even with layer 0 off.
    base = base_keys()
    base[5], base[6] = "DF(1)", "DF(0)"
    assert traps(base, filler(), filler(), filler()) == []


def test_transparent_all_the_way_down_does_nothing():
    base = base_keys()
    base[TG2] = "DF(2)"
    base[5] = "KC_TRANSPARENT"
    layer2 = closed(k5="KC_TRANSPARENT")
    assert len(traps(base, filler(), layer2, filler())) == 1


def test_tt_release_inverts_instead_of_clearing():
    # OP_TAP_TOGGLE: hold TT(1), press TO(2), release, and layer 1 comes back on top of 2.
    base = base_keys()
    base[TG2] = "TT(1)"
    middle = filler()
    middle[0] = "TO(2)"
    km = ol.parse_keymap(make_keymap([base, middle, closed(), filler()]))  # closed: no second TT tap
    ol.assign_layer_names(km, None)
    graph, _ = ol.layer_states(km, ol.Labeller(km))
    assert (0b110, 1, frozenset()) in graph


def test_trap_advice_uses_layer_names():
    km = ol.parse_keymap(make_keymap([base_keys(), filler(), closed(), filler()]))
    ol.assign_layer_names(km, None)
    km.layers[0].name, km.layers[2].name = "Base", "Gaming"
    assert [m for _, m in ol.lint(km, ol.Labeller(km)) if TRAP in m] == [
        "Gaming: no way to leave this layer after TG(Gaming) on Base (fine if a macro, combo, tap dance or custom code does)."
        " In Oryx, add TO(Base) or TG(Gaming) on Gaming, or make the key that sits over TG(Gaming) transparent"
    ]


def test_trap_on_the_default_layer_advises_df():
    base = base_keys()
    base[TG2] = "DF(2)"
    found = traps(base, filler(), closed(k0="TO(0)"), filler())
    assert found[0].endswith("In Oryx, add DF(Layer 0) on Layer 2")


def test_df_loop_that_never_returns_advises_df():
    base = base_keys()
    base[TG2] = "DF(1)"
    found = traps(base, closed(k0="DF(2)"), closed(k0="DF(1)"), filler())
    assert found == [
        "Layer 1, Layer 2: no way to get back to Layer 0 from these layers (fine if a macro, combo, tap dance or custom code does)."
        " In Oryx, add DF(Layer 0) on them"
    ]


def combo_keymap(base: list[str], layer2: list[str], *results: str) -> ol.Keymap:
    combos = ", ".join(f"COMBO(combo{i}, {r})" for i, r in enumerate(results))
    km = ol.parse_keymap(make_keymap([base, filler(), layer2, filler()]) + "\ncombo_t key_combos[] = { " + combos + " };\n")
    ol.assign_layer_names(km, None)
    return km


def test_combo_counts_as_an_exit():
    km = combo_keymap(base_keys(), closed(), "TO(0)")
    assert not [m for _, m in ol.lint(km, ol.Labeller(km)) if TRAP in m]


def test_trap_entered_from_a_combo_says_so():
    base = base_keys()
    base[TG2] = "KC_A"
    km = combo_keymap(base, closed(), "TO(2)")
    assert any(m.startswith("Layer 2: no way to leave this layer after TO(Layer 2) on a combo") for _, m in ol.lint(km, ol.Labeller(km)))


def test_combo_to_a_missing_layer_is_an_error():
    km = combo_keymap(base_keys(), filler(), "MO(9)")
    assert ("error", "Combo: `MO(9)` points at layer 9, which does not exist") in ol.lint(km, ol.Labeller(km))


def test_access_map_lists_combos():
    base = base_keys()
    base[TG2] = "KC_A"
    km = combo_keymap(base, filler(), "OSL(2)")
    lab = ol.Labeller(km)
    access = {a["name"]: a["how"] for a in ol.access_map(km, lab, ol.Geometry.build({}, km))}
    assert access["Layer 2"] == ["combo (OSL)"]


def test_transparent_advice_when_the_entry_layer_stays_on():
    # TG(1) latches layer 1, so a transparent key on layer 2 would reach its TG(2).
    base = base_keys()
    base[TG2], base[LT1] = "TG(1)", "KC_A"
    middle = filler()
    middle[0] = "TG(2)"
    found = [m for m in traps(base, middle, closed(k0="KC_NO"), filler()) if m.startswith("Layer 2:")]
    assert len(found) == 1
    assert found[0].endswith("or make the key that sits over TG(Layer 2) transparent")


def test_no_transparent_advice_when_the_entry_layer_is_gone():
    # MO(1) is released, so layer 1 is off and a transparent key on layer 2 reaches base.
    base = base_keys()
    base[TG2], base[LT1], base[0] = "KC_A", "KC_A", "MO(1)"
    middle = filler()
    middle[5] = "TG(2)"
    found = traps(base, middle, closed(k0="KC_NO"), filler())
    assert len(found) == 1
    assert found[0].endswith("In Oryx, add TO(Layer 0) or TG(Layer 2) on Layer 2")


@pytest.mark.parametrize(
    "head",
    [
        "if (record->event.pressed) switch (keycode) {",
        "if (x) { y(); } else switch (keycode) {",
        "if (x) y(); else switch (keycode) {",
        "while (x) switch (keycode) {",
    ],
)
def test_conditional_switch_does_not_swallow(head):
    km = lt9_keymap(
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n  " + head + "\n"
        "    case LT(9, KC_F14):\n      return false;\n  }\n  return true;\n}\n"
    )
    assert LT9_ERROR in ol.lint(km, ol.Labeller(km))


@pytest.mark.parametrize("head", ["foo();", "if (x) { foo(); }"])
def test_switch_after_a_finished_statement_can_swallow(head):
    km = lt9_keymap(
        "bool process_record_user(uint16_t keycode, keyrecord_t *record) {\n  " + head + "\n  switch (keycode) {\n"
        "    case LT(9, KC_F14):\n      return false;\n  }\n  return true;\n}\n"
    )
    assert not [i for i in ol.lint(km, ol.Labeller(km)) if i[0] == "error"]


@pytest.mark.parametrize("name", ["<!--<script", "</script>", "a&b", "x\u2028y<>"])
def test_embed_json_is_inert_in_script_block(name):
    data = {"layers": [{"name": name, "keys": ["</script>"]}]}
    blob = ol.embed_json(data)
    assert not any(c in blob for c in "<>&\u2028\u2029")
    assert json.loads(blob) == data


@pytest.mark.skipif(shutil.which("keymap") is None, reason="keymap-drawer not installed")
def test_render_draws_layer_named_with_leading_dash(tmp_path):
    # Issue #18: "-s -Nav" made argparse read the name as a flag, so no drawing was written.
    km = tmp_path / "keymap.c"
    km.write_text(make_keymap([base_keys(), filler()]))
    info = tmp_path / "info.json"
    info.write_text(json.dumps(make_info()))
    meta = tmp_path / "oryx.json"
    meta.write_text(json.dumps({"layers": [{"position": 0, "title": "Base"}, {"position": 1, "title": "-Nav"}]}))
    out = tmp_path / "docs"
    args = ["render", "--keymap", str(km), "--meta", str(meta), "--info", str(info), "--out", str(out)]
    assert ol.main(args) == 0
    for name in ("keymap.svg", "keymap-base.svg", "index.html"):
        assert (out / name).exists()
    assert "-Nav" in (out / "index.html").read_text(encoding="utf-8")


def test_locale_table_missing_falls_back(four_layers, monkeypatch):
    """Without the table a locale key keeps the old title-cased legend: never a crash."""
    monkeypatch.setattr(ol, "LOCALES", {})
    assert ol.Labeller(four_layers).legend("DE_Z").text() == "De Z"


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"legends": 1}', '{"legends": {}, "layouts": 1}'])
def test_load_locales_survives_a_bad_table(tmp_path, content):
    path = tmp_path / "locale_legends.json"
    if content is not None:
        path.write_text(content)
    assert ol.load_locales(path) == ({}, {})


@pytest.mark.parametrize(
    "content",
    [
        '{"legends": [], "layouts": {}}',
        '{"legends": {"DE": []}, "layouts": {}}',
        '{"legends": {"DE": {"Z": 1}}, "layouts": {}}',
        '{"legends": {}, "layouts": []}',
        '{"legends": {}, "layouts": {"DE": {}}}',
        '{"legends": {}, "layouts": {"DE": []}}',
        '{"legends": {}, "layouts": {"DE": [1]}}',
        '{"legends": {}, "layouts": {"DE": [{"Y": 1}]}}',
    ],
)
def test_wrong_shape_table_keeps_the_old_legend(tmp_path, monkeypatch, content):
    path = tmp_path / "locale_legends.json"
    path.write_text(content)
    tables = ol.load_locales(path)
    assert tables == ({}, {})
    monkeypatch.setattr(ol, "LOCALES", tables[0])
    monkeypatch.setattr(ol, "LAYOUTS", tables[1])
    assert labeller_with_i18n("#define DE_Z KC_Y\n").legend("DE_Z").text() == "De Z"


# The shape of the i18n.h Oryx ships beside keymap.c: its own names, one per line.
I18N_H = """\
#define DE_UE KC_LBRC
#define DE_AE KC_QUOT
#define DE_Z KC_Y
#define DE_LESS KC_NUBS
#define DE_MORE S(KC_NUBS)
#define DE_QST S(KC_MINS)
#define DE_AT ALGR(KC_Q)
#define DE_SHOUT S(KC_1)
#define KC_MAC_COPY LGUI(KC_C)
"""


def labeller_with_i18n(text: str) -> ol.Labeller:
    km = ol.parse_keymap(make_keymap([base_keys()]))
    km.i18n = ol.parse_i18n(text)
    return ol.Labeller(km)


@pytest.mark.parametrize(
    "raw,text",
    [
        ("DE_UE", "Ü"),
        ("DE_AE", "Ä"),  # in the table DE_AE is the Æ of keymap_german_mac_iso.h
        ("DE_Z", "Z"),
        ("DE_LESS", "<"),
        ("DE_MORE", ">"),
        ("DE_QST", "?"),
        ("DE_AT", "@"),
        ("DE_SHOUT", "!"),  # a name no header or alias knows: only the value says what it types
        ("MT(MOD_LCTL, DE_UE)", "Ü/Ctrl"),
        ("KC_MAC_COPY", "Mac Copy"),  # not a locale key: i18n.h also holds Oryx macros
        ("DE_SS", "ß"),  # not in this i18n.h
    ],
)
def test_i18n_h_values_give_the_legend(raw, text):
    assert labeller_with_i18n(I18N_H).legend(raw).text() == text


def test_i18n_h_picks_the_header_that_matches_its_values():
    """FR_ is four layouts: the values say this one is Canadian, so FR_EACU is its É."""
    lab = labeller_with_i18n("#define FR_EACU KC_SLSH\n#define FR_Q KC_Q\n#define FR_W KC_W\n#define FR_A KC_A\n#define FR_Z KC_Z\n")
    assert lab.legend("FR_EACU").text() == "É"
    assert lab.legend("FR_A").text() == "A"
    french = labeller_with_i18n("#define FR_EACU KC_2\n#define FR_Q KC_A\n#define FR_A KC_Q\n")
    assert french.legend("FR_EACU").text() == "é"


def test_i18n_h_value_with_long_modifier_names():
    lab = labeller_with_i18n("#define DE_X1 LSFT(KC_1)\n#define DE_X2 RALT(KC_Q)\n#define DE_X3 RSFT(KC_1)\n")
    assert [lab.legend(k).text() for k in ("DE_X1", "DE_X2", "DE_X3")] == ["!", "@", "!"]


@pytest.mark.parametrize(
    "junk",
    ["", "not a header\n\x00\n", "#define\n#define DE_UE\n#define DE_UE (((\n#define DE_UE KC_NOPE(\n", "#define ZZ_Q KC_A\n"],
)
def test_junk_i18n_h_falls_back(junk):
    lab = labeller_with_i18n(junk)
    assert lab.legend("DE_UE").text() == "Ü"  # the alias still knows it
    assert lab.legend("ZZ_Q").text() == "Zz Q"


def test_read_i18n_finds_it_beside_keymap(tmp_path):
    (tmp_path / "keymap.c").write_text("")
    assert ol.read_i18n(str(tmp_path / "keymap.c")) == {}
    (tmp_path / "i18n.h").write_text(I18N_H)
    assert ol.read_i18n(str(tmp_path / "keymap.c"))["DE_AT"] == "ALGR(KC_Q)"
    (tmp_path / "i18n.h").write_bytes(b"\xff\xfe#define")
    assert ol.read_i18n(str(tmp_path / "keymap.c")) == {}


def temp_git(repo: Path, *args: str) -> None:
    """git in a test's throwaway repo, cut off from the host's config (signing, hooks, identity)."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    config = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", *config, "-C", str(repo), *args], check=True, env=env)


def test_read_i18n_at_a_git_ref(tmp_path, monkeypatch):
    temp_git(tmp_path, "init", "-q")
    (tmp_path / "layout").mkdir()
    (tmp_path / "layout/keymap.c").write_text("")
    temp_git(tmp_path, "add", "-A")
    temp_git(tmp_path, "commit", "-qm", "no i18n.h")
    (tmp_path / "layout/i18n.h").write_text(I18N_H)
    temp_git(tmp_path, "add", "-A")
    temp_git(tmp_path, "commit", "-qm", "i18n.h")
    monkeypatch.chdir(tmp_path)
    assert ol.read_i18n("HEAD:layout/keymap.c")["DE_UE"] == "KC_LBRC"
    assert ol.read_i18n("HEAD~1:layout/keymap.c") == {}


def oryx_only_keymap() -> str:
    """A keymap with DE_SHOUT, which only an i18n.h can explain."""
    keys = base_keys()
    keys[0] = "DE_SHOUT"
    return make_keymap([keys, filler()])


def test_render_cli_uses_i18n_h_beside_keymap(tmp_path):
    km = tmp_path / "keymap.c"
    km.write_text(oryx_only_keymap())
    (tmp_path / "i18n.h").write_text("#define DE_SHOUT S(KC_1)\n")
    info = tmp_path / "info.json"
    info.write_text(json.dumps(make_info()))
    assert ol.main(["render", "--keymap", str(km), "--meta", "/x", "--info", str(info), "--out", str(tmp_path / "docs"), "--no-svg"]) == 0
    md = (tmp_path / "docs" / "keymap.md").read_text(encoding="utf-8")
    assert "De Shout" not in md
    assert "[     !     ]" in md.split("```text")[1]


def test_render_cli_without_i18n_h_humanises(tmp_path):
    km = tmp_path / "keymap.c"
    km.write_text(oryx_only_keymap())
    assert ol.main(["render", "--keymap", str(km), "--meta", "/x", "--out", str(tmp_path / "docs"), "--no-svg"]) == 0
    assert "De Shout" in (tmp_path / "docs" / "keymap.md").read_text(encoding="utf-8")


@pytest.fixture
def i18n_history(tmp_path, monkeypatch):
    """A repo where DE_SHOUT changes meaning: no i18n.h, then `!`, then `"`, same keymap.c."""
    def git(*args: str) -> None:
        temp_git(tmp_path, *args)

    git("init", "-q")
    layout = tmp_path / "layout"
    layout.mkdir()
    (layout / "keymap.c").write_text(oryx_only_keymap())
    git("add", "-A")
    git("commit", "-qm", "no i18n.h")
    for value in ("S(KC_1)", "S(KC_2)"):
        (layout / "i18n.h").write_text(f"#define DE_SHOUT {value}\n")
        git("add", "-A")
        git("commit", "-qm", value)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_diff_reads_each_revisions_own_i18n_h(i18n_history, capsys):
    ol.main(["diff", "HEAD~1:layout/keymap.c", "HEAD:layout/keymap.c", "--meta", "/x"])
    assert capsys.readouterr().out.strip() == '- **Layer 0** (L0.0): `!` → `"`'


def test_diff_old_ref_against_working_tree(i18n_history, capsys):
    ol.main(["diff", "HEAD~1:layout/keymap.c", "layout/keymap.c", "--meta", "/x"])
    assert capsys.readouterr().out.strip() == '- **Layer 0** (L0.0): `!` → `"`'


def test_diff_revision_without_i18n_h_humanises(i18n_history, capsys):
    ol.main(["diff", "HEAD~2:layout/keymap.c", "HEAD~1:layout/keymap.c", "--meta", "/x"])
    assert capsys.readouterr().out.strip() == "- **Layer 0** (L0.0): `De Shout` → `!`"


@pytest.mark.parametrize(
    "raw,text",
    [
        # Names from QMK's older headers, which old exports (no i18n.h) still use.
        ("DE_UE", "Ü"), ("DE_AE", "Ä"), ("DE_OE", "Ö"), ("DE_LESS", "<"), ("DE_MORE", ">"), ("DE_QST", "?"),
        ("DE_DQOT", '"'), ("DE_PARA", "§"), ("DE_RING", "°"), ("DE_SQ2", "²"), ("DE_SQ3", "³"),
        ("FR_AMP", "&"), ("FR_APOS", "'"), ("FR_LESS", "<"), ("FR_GRTR", ">"), ("FR_OVRR", "°"),
        ("FR_UMLT", "¨"), ("FR_MU", "µ"), ("FR_BULT", "¤"),
    ],
)
def test_older_names_without_i18n_h(four_layers, raw, text):
    assert four_layers.i18n == {}
    assert ol.Labeller(four_layers).legend(raw).text() == text


def test_no_table_legend_is_a_bare_combining_mark():
    bare = [f"{p}_{k}" for p, keys in ol.LOCALES.items() for k, v in keys.items() if all(unicodedata.combining(c) for c in v)]
    assert bare == []
    assert ol.LOCALES["FR"]["DGRV"] == "`"  # the spacing form in keymap_canadian_french.h


QMK_TREE = Path(__file__).resolve().parents[1] / ".cache" / gen.FIRMWARE
needs_tree = pytest.mark.skipif(not QMK_TREE.is_dir(), reason="pinned QMK tree not cached")


@pytest.mark.parametrize(
    "comment,legend",
    [
        ("Z", "Z"),
        ("^ (dead)", "^"),
        ("(backslash)", "\\"),
        ("(backslash, not physically present)", "\\"),
        ("| (not physically present)", "|"),
        ("µ (dead Greek key)", "µ"),
        ("Muhenkan (無変換)", None),  # too long to draw: the generator uses the name
        ("(non-breaking space)", None),
        ("\xad (soft hyphen)", None),  # invisible
        ("\u030f (dead)", None),  # a combining mark has nothing to sit on, and no width in a grid
        ("", None),
    ],
)
def test_legend_from_header_comment(comment, legend):
    assert gen.legend(comment) == legend


def test_generator_prefers_the_plain_header_and_a_real_legend(tmp_path):
    (tmp_path / "keymap_french.h").write_text("#define FR_EACU KC_2 // é\n#define FR_NBSP KC_3\n", encoding="utf-8")
    (tmp_path / "keymap_canadian_french.h").write_text("#define FR_EACU KC_SLSH // É\n", encoding="utf-8")
    (tmp_path / "keymap_nordic.h").write_text("#define NO_QUOT KC_RBRC // a sentence about this umlaut key\n")
    (tmp_path / "keymap_norwegian.h").write_text("#define NO_QUOT KC_BSLS // '\n#define QMK_NO_VERSION 1 // x\n")
    (tmp_path / "keymap_us.h").write_text("#define KC_TILD S(KC_GRAVE) // ~\n")
    assert gen.table(tmp_path) == {
        "FR": {"EACU": "é", "NBSP": "NBSP"},
        "NO": {"QUOT": "'"},
    }


def test_generator_prefers_a_spacing_legend(tmp_path):
    (tmp_path / "keymap_french_afnor.h").write_text("#define FR_DGRV KC_Q // \u030f (dead)\n", encoding="utf-8")
    (tmp_path / "keymap_canadian_french.h").write_text("#define FR_DGRV KC_W // ` (dead)\n#define FR_DCMM KC_E // \u0326 (dead)\n", encoding="utf-8")
    assert gen.table(tmp_path) == {"FR": {"DGRV": "`", "DCMM": "DCMM"}}


def test_generator_maps_expanded_values_per_header(tmp_path):
    (tmp_path / "keymap_german.h").write_text(
        "#define DE_Z    KC_Y    // Z\n#define DE_1 KC_1 // 1\n#define DE_EXLM S(DE_1)    // !\n#define DE_AT   ALGR(KC_Q) // @\n"
    )
    (tmp_path / "keymap_german_mac_iso.h").write_text("#define DE_Z KC_Y // Z\n#define DE_L KC_L // L\n#define DE_AT A(DE_L) // @\n")
    (tmp_path / "keymap_german_copy.h").write_text("#define DE_Z KC_Y // Z\n#define DE_1 KC_1 // 1\n#define DE_EXLM S(KC_1) // !\n#define DE_AT ALGR(KC_Q) // @\n")
    assert gen.layouts(tmp_path) == {
        "DE": [
            {"Y": "Z", "1": "1", "S(1)": "!", "ALGR(Q)": "@"},  # the identical copy is dropped
            {"Y": "Z", "L": "L", "A(L)": "@"},
        ]
    }


def test_generator_records_the_tree_it_read(tmp_path, monkeypatch):
    headers = tmp_path / "qmk_firmware-firmware99/quantum/keymap_extras"
    headers.mkdir(parents=True)
    (headers / "keymap_german.h").write_text("#define DE_Z KC_Y // Z\n")
    monkeypatch.setattr(gen, "OUT", tmp_path / "out.json")
    monkeypatch.setattr(sys, "argv", ["gen", str(headers.parents[1])])
    assert gen.main() == 0
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert "qmk_firmware-firmware99" in data["_source"] and "firmware25" not in data["_source"]
    assert data["layouts"]["DE"] == [{"Y": "Z"}]


@needs_tree
def test_aliases_point_at_keycodes_the_headers_define():
    fresh = gen.table(QMK_TREE / "quantum" / "keymap_extras")
    for prefix, names in gen.ALIASES.items():
        for target in names.values():
            assert target in fresh[prefix], f"{prefix}_{target}"


@needs_tree
def test_mod_aliases_cover_every_mod_tap_in_the_pinned_tree():
    """Each `X_T(kc)` macro in QMK must give the modifiers that macro really holds."""
    src = (QMK_TREE / "quantum" / "quantum_keycodes.h").read_text(encoding="utf-8")
    macros = dict(re.findall(r"^#define (\w+)_T\(kc\) (.+?)(?:\s*//.*)?$", src, re.M))
    assert "LCAG" in macros and "SAGR" in macros
    del macros["SH"]  # swap-hands tap, not a mod: test_swap_hands_tap_keeps_its_hold

    def mods(name: str) -> set[str]:
        body = macros[name]
        if m := re.fullmatch(r"(\w+)_T\(kc\)", body):
            return mods(m.group(1))
        return {ol.MOD_KEYS[tok] for tok in re.findall(r"MOD_(\w+)", body)}

    assert {name: ol.MOD_ALIASES.get(name) for name in macros} == {name: mods(name) for name in macros}


@needs_tree
def test_locale_table_matches_the_pinned_tree():
    """Regenerating must not change what the table says; a key QMK adds later is fine."""
    fresh = gen.table(QMK_TREE / "quantum" / "keymap_extras")
    for prefix, legends in ol.LOCALES.items():
        for name, legend in legends.items():
            if name in fresh.get(prefix, {}):
                assert fresh[prefix][name] == legend, f"{prefix}_{name}: run scripts/gen_locale_legends.py"
    assert gen.layouts(QMK_TREE / "quantum" / "keymap_extras").keys() >= ol.LAYOUTS.keys()
