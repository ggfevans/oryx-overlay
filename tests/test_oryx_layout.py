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
    assert "- Layer added: **Layer 2** (position 2)" in out


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


# --- diff: pairing layers across revisions (#12) ---------------------------------

NAV = filler()
NAV[:4] = ["KC_LEFT", "KC_DOWN", "KC_UP", "KC_RIGHT"]
SYM = filler()
SYM[:4] = ["KC_EXLM", "KC_AT", "KC_HASH", "KC_DLR"]
FUN = filler()
FUN[:4] = ["KC_F1", "KC_F2", "KC_F3", "KC_F4"]
NEW = filler()
NEW[:4] = ["KC_1", "KC_2", "KC_3", "KC_4"]


def titles(*names: str) -> dict:
    return {"layers": [{"position": i, "title": name} for i, name in enumerate(names)]}


def edited(keys: list[str], at: int = 0, to: str = "KC_HOME") -> list[str]:
    keys = list(keys)
    keys[at] = to
    return keys


def base_to(nav: int | None = 1, sym: int = 2) -> list[str]:
    """base_keys() with its LT to Nav and TG to Sym pointing where those layers are now."""
    keys = base_keys()
    keys[40] = f"LT({nav}, KC_SPACE)" if nav is not None else "KC_SPACE"
    keys[48] = f"TG({sym})"
    return keys


def dual_hold_to(src: str, layer: int) -> str:
    """Point DUAL_FUNC_1's hold, `layer_on(3)` in KEYMAP_C, at another layer."""
    return src.replace("layer_on(3)", f"layer_on({layer})").replace("layer_off(3)", f"layer_off({layer})")


def export(layers: list[list[str]], hold: int = 3) -> str:
    """A keymap.c with no lights (KEYMAP_C's stay on layer 1 wherever layers go) and DUAL_FUNC_1 holding `hold`."""
    return dual_hold_to(make_keymap(layers).replace("[1] = { {0,255,255}, {85,255,255}, {0,0,0} },\n", ""), hold)


@pytest.fixture
def layout_diff(tmp_path, monkeypatch, capsys):
    """Commit an old export, leave a new one in the worktree and diff them as sync.sh does."""
    temp_git(tmp_path, "init", "-q")
    layout = tmp_path / "layout"
    layout.mkdir()
    monkeypatch.chdir(tmp_path)

    def write(src: str, meta: dict | str | list | None) -> None:
        (layout / "keymap.c").write_text(src)
        (layout / ".oryx.json").unlink(missing_ok=True)
        if meta is not None:
            (layout / ".oryx.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))

    def run(old, old_meta, new, new_meta, *flags: str) -> str:
        write(old if isinstance(old, str) else export(old), old_meta)
        temp_git(tmp_path, "add", "-A")
        temp_git(tmp_path, "commit", "-qm", "old", "--allow-empty")
        write(new if isinstance(new, str) else export(new), new_meta)
        assert ol.main(["diff", "HEAD:layout/keymap.c", "layout/keymap.c", "--meta", "layout/.oryx.json", *flags]) == 0
        return capsys.readouterr().out.strip()

    return run


FOUR = [base_keys(), NAV, SYM, FUN]
FOUR_NAMES = titles("Base", "Nav", "Sym", "Fun")


def test_diff_unchanged(layout_diff):
    assert layout_diff(FOUR, FOUR_NAMES, FOUR, FOUR_NAMES) == "No keymap changes."


def test_diff_insert_reports_only_the_insert(layout_diff):
    new = export([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN], 4)
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert out == "- Layer added: **Mouse** (position 1)"


def test_diff_removal(layout_diff):
    new = export([base_to(nav=None, sym=1), SYM, FUN], 2)
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Sym", "Fun"))
    assert out.splitlines() == [
        "- Layer removed: **Nav** (position 1)",
        "- **Base** (L3.4): `Space/Nav (removed)` → `Space`",
    ]


def test_diff_reorder_reports_one_move(layout_diff):
    new = export([base_to(nav=2, sym=1), SYM, NAV, FUN])
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Sym", "Nav", "Fun"))
    assert out == "- Layer moved: **Nav** (position 1 → 2)"


def test_diff_move_past_several_layers_is_one_move(layout_diff):
    new = export([base_to(nav=3, sym=1), SYM, FUN, NAV], 2)
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Sym", "Fun", "Nav"))
    assert out == "- Layer moved: **Nav** (position 1 → 3)"


def test_diff_rename_with_unchanged_content(layout_diff):
    out = layout_diff(FOUR, FOUR_NAMES, FOUR, titles("Base", "Arrows", "Sym", "Fun"))
    assert out == "- Layer renamed: **Nav** → **Arrows**"


def test_diff_rename_plus_an_edit(layout_diff):
    new = [base_keys(), edited(NAV), SYM, FUN]
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Arrows", "Sym", "Fun"))
    assert out.splitlines() == ["- Layer renamed: **Nav** → **Arrows**", "- **Arrows** (L0.0): `←` → `Home`"]


def test_diff_rename_and_move_keeps_the_layer(layout_diff):
    new = export([base_to(nav=2, sym=1), SYM, NAV, FUN])
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Sym", "Arrows", "Fun"))
    assert out.splitlines() == ["- Layer renamed: **Nav** → **Arrows**", "- Layer moved: **Arrows** (position 1 → 2)"]


def test_diff_two_renames_swapped_follow_the_content(layout_diff):
    """The keys stayed put and only the names traded places: not a move, and no key lines."""
    out = layout_diff(FOUR, FOUR_NAMES, FOUR, titles("Base", "Sym", "Nav", "Fun"))
    assert out.splitlines() == ["- Layer renamed: **Nav** → **Sym**", "- Layer renamed: **Sym** → **Nav**"]


def test_diff_same_name_new_content_stays_the_same_layer(layout_diff):
    rewritten = [base_keys(), NEW, SYM, FUN]
    out = layout_diff(FOUR, FOUR_NAMES, rewritten, FOUR_NAMES)
    assert "Layer added" not in out and "Layer removed" not in out
    assert "- **Nav** (L0.0): `←` → `1`" in out


def test_diff_rewritten_layer_under_a_new_name_is_a_replacement(layout_diff):
    out = layout_diff(FOUR, FOUR_NAMES, [base_keys(), NEW, SYM, FUN], titles("Base", "Mouse", "Sym", "Fun"))
    assert out.splitlines() == [
        "- Layer added: **Mouse** (position 1)",
        "- Layer removed: **Nav** (position 1)",
        "- **Base** (L3.4): `Space/Nav (removed)` → `Space/Mouse`",
    ]


def test_diff_a_tiny_layer_edit_under_a_new_name_is_not_a_match(layout_diff):
    """One key of two matching isn't a majority, however small the layer."""
    one, two = filler(), filler()
    one[:2] = ["KC_A", "KC_B"]
    two[:2] = ["KC_A", "KC_C"]
    out = layout_diff([base_keys(), one], titles("Base", "One"), [base_keys(), two], titles("Base", "Two"))
    assert out.splitlines()[:2] == ["- Layer added: **Two** (position 1)", "- Layer removed: **One** (position 1)"]


def test_diff_unnamed_insert(layout_diff):
    plain = titles("Layer", "Layer", "Layer", "Layer")
    new = export([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN], 4)
    out = layout_diff(FOUR, plain, new, titles("Layer", "Layer", "Layer", "Layer", "Layer"))
    assert out == "- Layer added: **Layer 1** (position 1)"


def test_diff_unnamed_insert_plus_an_edit_labels_the_new_position(layout_diff):
    plain = titles("Layer", "Layer", "Layer", "Layer")
    new = export([base_to(nav=2, sym=3), NEW, NAV, edited(SYM), FUN], 4)
    out = layout_diff(FOUR, plain, new, titles("Layer", "Layer", "Layer", "Layer", "Layer"))
    assert out.splitlines() == ["- Layer added: **Layer 1** (position 1)", "- **Layer 3** (L0.0): `!` → `Home`"]


def test_diff_unnamed_layer_rewritten_in_place_pairs_by_index(layout_diff):
    plain = titles("Layer", "Layer", "Layer", "Layer")
    out = layout_diff(FOUR, plain, [base_keys(), NEW, SYM, FUN], plain)
    assert "Layer added" not in out and "- **Layer 1** (L0.0): `←` → `1`" in out


def test_diff_naming_an_unnamed_layer_is_a_rename(layout_diff):
    plain = titles("Base", "Layer", "Sym", "Fun")
    assert layout_diff(FOUR, plain, FOUR, FOUR_NAMES) == "- Layer renamed: **Layer 1** → **Nav**"


def test_diff_identical_blank_layers_keep_their_order(layout_diff):
    plain = titles("Layer", "Layer", "Layer")
    old = [base_keys(), filler(), filler(), FUN]
    new = export([base_to(nav=2, sym=3), NEW, filler(), filler(), FUN], 4)
    out = layout_diff(old, plain, new, titles("Layer", "Layer", "Layer", "Layer", "Layer"))
    assert out == "- Layer added: **Layer 1** (position 1)"


def test_diff_duplicate_names_insert(layout_diff):
    old = [base_keys(), NAV, SYM, FUN]
    names = titles("Base", "Nav", "Nav", "Fun")
    new = export([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN], 4)
    out = layout_diff(old, names, new, titles("Base", "Mouse", "Nav", "Nav", "Fun"))
    assert out == "- Layer added: **Mouse** (position 1)"


def test_diff_duplicate_names_edit_lands_on_the_right_one(layout_diff):
    names = titles("Base", "Nav", "Nav", "Fun")
    out = layout_diff(FOUR, names, [base_keys(), NAV, edited(SYM), FUN], names)
    assert out == "- **Nav 2** (L0.0): `!` → `Home`"


def test_diff_reference_retargeted_to_another_layer(layout_diff):
    new = [base_to(sym=3), NAV, SYM, FUN]
    assert layout_diff(FOUR, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L4.0): `TG Sym` → `TG Fun`"


def test_diff_same_number_new_layer_is_a_change(layout_diff):
    """TG(2) stayed TG(2), but the layers traded places under it, so it does something else."""
    plain = titles("Layer", "Layer", "Layer", "Layer")
    new = [base_to(nav=2), SYM, NAV, FUN]  # the LT follows Nav; the TG doesn't follow Sym
    out = layout_diff(FOUR, plain, new, plain)
    assert out.splitlines() == ["- Layer moved: **Layer 2** (position 1 → 2)", "- **Layer 0** (L4.0): `TG Layer 1` → `TG Layer 2`"]


def test_diff_reference_into_a_new_layer_is_a_change(layout_diff):
    new = export([base_to(nav=2, sym=1), NEW, NAV, SYM, FUN], 4)
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert out.splitlines() == ["- Layer added: **Mouse** (position 1)", "- **Base** (L4.0): `TG Sym` → `TG Mouse`"]


def test_diff_dummy_layer_of_an_intercepted_key_is_not_a_reference(layout_diff):
    """LT(2, KC_F14) handled in process_record_user: the 2 is a dummy, not the Sym layer."""
    def with_dummy(src: str) -> str:
        src = src.replace("#define DUAL_FUNC_0", "#define TH_X LT(2, KC_F14)\n#define DUAL_FUNC_0")
        return src.replace("case ST_MACRO_0:", "case TH_X:\n      tap_code(KC_A);\n      return false;\n    case ST_MACRO_0:")

    old_keys = edited(base_keys(), 1, "TH_X")
    new_keys = edited(base_to(nav=2, sym=3), 1, "TH_X")
    new = with_dummy(export([new_keys, NEW, NAV, SYM, FUN], 4))
    out = layout_diff(with_dummy(export([old_keys, NAV, SYM, FUN])), FOUR_NAMES, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert out == "- Layer added: **Mouse** (position 1)"


def renumber_duals(src: str) -> str:
    """What Oryx does when a dual-function key is added earlier in the file."""
    src = src.replace("DUAL_FUNC_1", "DUAL_FUNC_7").replace("DUAL_FUNC_0", "DUAL_FUNC_1").replace("DUAL_FUNC_7", "DUAL_FUNC_0")
    return src.replace("LT(14, KC_8)", "LT(9, KC_F2)").replace("LT(11, KC_W)", "LT(4, KC_E)")


def test_diff_dual_func_renumber_is_no_change(layout_diff):
    old = make_keymap(FOUR)
    assert layout_diff(old, FOUR_NAMES, renumber_duals(old), FOUR_NAMES) == "No keymap changes."


def test_diff_dual_func_real_behaviour_change_is_reported(layout_diff):
    old = make_keymap(FOUR)
    new = renumber_duals(old).replace("code16(KC_DLR)", "code16(KC_EQUAL)")
    out = layout_diff(old, FOUR_NAMES, new, FOUR_NAMES, "--raw")
    assert out == "- **Base** (L1.2): `$/Super` → `=/Super`  (`DUAL_FUNC_0` → `DUAL_FUNC_1`)"


def test_diff_dual_func_hold_follows_its_layer(layout_diff):
    new = export([base_keys(), NAV, SYM, FUN], 2)
    assert layout_diff(FOUR, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L3.5): `Ctrl/Fun` → `Ctrl/Sym`"


def test_diff_same_legend_real_change_shows_the_raw_keycodes(layout_diff):
    new = [edited(base_keys(), 0, "KC_LSFT"), NAV, SYM, FUN]
    old = [edited(base_keys(), 0, "KC_RSFT"), NAV, SYM, FUN]
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L0.0): `Shift` → `Shift`  (`KC_RSFT` → `KC_LSFT`)"


def test_diff_leds_follow_the_layer(layout_diff):
    def lit(src: str, position: int) -> str:
        return src.replace("[1] = { {0,255,255}", f"[{position}] = {{ {{0,255,255}}")

    old = lit(make_keymap(FOUR), 1)
    new = dual_hold_to(lit(make_keymap([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN]), 2), 4)
    out = layout_diff(old, FOUR_NAMES, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert out == "- Layer added: **Mouse** (position 1)"


def test_diff_real_led_change_names_the_layer(layout_diff):
    old = make_keymap(FOUR)
    new = old.replace("{85,255,255}", "{86,255,255}")
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- LED colours changed on layer(s) Nav"


def test_diff_old_side_without_metadata_pairs_by_content_then_index(layout_diff):
    new = export([base_to(nav=2, sym=3), NEW, NAV, edited(SYM), FUN], 4)
    out = layout_diff(FOUR, None, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert out.splitlines() == ["- Layer added: **Mouse** (position 1)", "- **Sym** (L0.0): `!` → `Home`"]


@pytest.mark.parametrize("meta", [None, {"title": "no layers key"}, {"layers": []}])
def test_diff_old_side_without_names_falls_back_to_index(layout_diff, meta):
    out = layout_diff(FOUR, meta, [base_keys(), NEW, SYM, FUN], FOUR_NAMES)
    assert "renamed" not in out and "Layer added" not in out and "Layer removed" not in out
    assert "- **Nav** (L0.0): `←` → `1`" in out


@pytest.mark.parametrize("meta", ["{not json", ["not", "a", "dict"]])
def test_diff_corrupt_old_metadata_is_ignored(layout_diff, meta):
    out = layout_diff(FOUR, meta, [base_keys(), edited(NAV), SYM, FUN], FOUR_NAMES)
    assert out == "- **Nav** (L0.0): `←` → `Home`"


def test_diff_both_sides_refs_read_their_own_metadata(tmp_path, monkeypatch, capsys):
    temp_git(tmp_path, "init", "-q")
    layout = tmp_path / "layout"
    layout.mkdir()
    for keymap, meta in ((FOUR, FOUR_NAMES), (FOUR, titles("Base", "Arrows", "Sym", "Fun"))):
        (layout / "keymap.c").write_text(make_keymap(keymap))
        (layout / ".oryx.json").write_text(json.dumps(meta))
        temp_git(tmp_path, "add", "-A")
        temp_git(tmp_path, "commit", "-qm", "export", "--allow-empty")
    monkeypatch.chdir(tmp_path)
    ol.main(["diff", "HEAD~1:layout/keymap.c", "HEAD:layout/keymap.c", "--meta", "/x"])
    assert capsys.readouterr().out.strip() == "- Layer renamed: **Nav** → **Arrows**"


def test_diff_plain_old_file_uses_metadata_beside_it_not_the_new_one(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a/keymap.c").write_text(export(FOUR))
    (tmp_path / "a/.oryx.json").write_text(json.dumps(FOUR_NAMES))
    new = export([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN], 4)
    (tmp_path / "b/keymap.c").write_text(new)
    meta = tmp_path / "b/.oryx.json"
    meta.write_text(json.dumps(titles("Base", "Mouse", "Nav", "Sym", "Fun")))
    ol.main(["diff", str(tmp_path / "a/keymap.c"), str(tmp_path / "b/keymap.c"), "--meta", str(meta)])
    assert capsys.readouterr().out.strip() == "- Layer added: **Mouse** (position 1)"
    (tmp_path / "a/.oryx.json").unlink()  # --meta describes the new file, so it mustn't name the old one
    ol.main(["diff", str(tmp_path / "a/keymap.c"), str(tmp_path / "b/keymap.c"), "--meta", str(meta)])
    assert capsys.readouterr().out.strip() == "- Layer added: **Mouse** (position 1)"


def test_diff_limit_keeps_layer_lines_and_says_what_it_dropped(layout_diff):
    rewritten = [f"KC_F{i % 12 + 1}" for i in range(sum(ROWS))]
    new = export([rewritten, NEW, NAV, SYM, FUN], 4)
    meta = titles("Base", "Mouse", "Nav", "Sym", "Fun")
    full = layout_diff(FOUR, FOUR_NAMES, new, meta).splitlines()
    short = layout_diff(FOUR, FOUR_NAMES, new, meta, "--limit", "5").splitlines()
    keys = len(full) - 1
    assert keys > 5
    assert short == full[:6] + [f"- …and {keys - 5} more key changes"]


# --- sync.sh's call, end to end ----------------------------------------------------


@pytest.fixture
def sync_repo(tmp_path):
    """A repo with sync.sh's real scripts, and an oryx_fetch.py that serves a prepared export."""
    repo = tmp_path / "repo"
    shutil.copytree(Path(ol.__file__).parent, repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "scripts/oryx_fetch.py").write_text(
        'import os, shutil, sys\n'
        'out = sys.argv[sys.argv.index("--out") + 1]\n'
        'for name in os.listdir(os.environ["FAKE_EXPORT"]):\n'
        '    shutil.copy(os.path.join(os.environ["FAKE_EXPORT"], name), out)\n'
        'print("revision=r2\\nqmk_version=25.0\\nchange_note=Added a layer")\n'
    )
    (repo / "oryx.conf").write_text('ORYX_LAYOUT_ID=default\nKEYBOARD=voyager\n')
    (repo / "layout").mkdir()
    (repo / "layout/keymap.c").write_text(make_keymap(FOUR))
    (repo / "layout/.oryx.json").write_text(json.dumps(FOUR_NAMES))
    temp_git(repo, "init", "-q")
    temp_git(repo, "checkout", "-qb", "main")
    temp_git(repo, "add", "-A")
    temp_git(repo, "commit", "-qm", "first export")
    return repo


def sync(repo: Path, tmp_path: Path, keymap: str, meta: dict) -> subprocess.CompletedProcess:
    export = tmp_path / "export"
    export.mkdir()
    (export / "keymap.c").write_text(keymap)
    (export / ".oryx.json").write_text(json.dumps(meta))
    env = {
        **os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PYTHON": sys.executable,
        "CACHE_DIR": str(tmp_path / "cache"), "FAKE_EXPORT": str(export),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(["bash", str(repo / "scripts/sync.sh")], capture_output=True, text=True, env=env, cwd=repo)


def test_sync_commit_message_reports_the_insert(sync_repo, tmp_path):
    new = export([base_to(nav=2, sym=3), NEW, NAV, SYM, FUN], 4)
    res = sync(sync_repo, tmp_path, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert res.returncode == 0, res.stderr
    body = subprocess.run(["git", "-C", str(sync_repo), "log", "-1", "oryx", "--format=%B"], capture_output=True, text=True, check=True).stdout
    assert "- Layer added: **Mouse** (position 1)" in body
    assert "→" not in body


def test_sync_commit_message_is_bounded_but_keeps_layer_lines(sync_repo, tmp_path):
    rewritten = [f"KC_F{i % 12 + 1}" for i in range(sum(ROWS))]
    new = export([rewritten, NEW, [k.replace("KC_F", "KC_") for k in rewritten], SYM, FUN], 4)
    res = sync(sync_repo, tmp_path, new, titles("Base", "Mouse", "Nav", "Sym", "Fun"))
    assert res.returncode == 0, res.stderr
    body = subprocess.run(["git", "-C", str(sync_repo), "log", "-1", "oryx", "--format=%B"], capture_output=True, text=True, check=True).stdout
    assert "- Layer added: **Mouse** (position 1)" in body
    assert sum(line.startswith("- **") for line in body.splitlines()) == 60
    assert re.search(r"^- …and \d+ more key changes$", body, re.M)


# --- diff: review follow-ups (#12) -------------------------------------------------


def test_diff_i18n_h_on_one_side_only_is_no_change(tmp_path, capsys):
    """Legends fall back to the locale tables, so DE_SS is ß with or without an i18n.h."""
    src = make_keymap([edited(base_keys(), 0, "DE_SS"), filler()])
    for side in ("a", "b"):
        (tmp_path / side).mkdir()
        (tmp_path / side / "keymap.c").write_text(src)
    (tmp_path / "a/i18n.h").write_text("#define DE_SS KC_MINS\n")
    assert ol.main(["diff", str(tmp_path / "a/keymap.c"), str(tmp_path / "b/keymap.c"), "--meta", "/x"]) == 0
    assert capsys.readouterr().out.strip() == "No keymap changes."


PLAIN = titles(*["Layer"] * 9)


def test_diff_blank_insert_before_a_later_blank_layer(layout_diff):
    """Oryx creates layers blank. The old blank is at the end, and must stay there."""
    old = [base_keys(), NAV, SYM, FUN, filler()]
    new = export([base_to(sym=3), NAV, filler(), SYM, FUN, filler()], 4)
    assert layout_diff(old, PLAIN, new, PLAIN) == "- Layer added: **Layer 2** (position 2)"


def test_diff_blank_insert_beside_blanks_moves_nothing(layout_diff):
    old = [base_keys(), NAV, filler(), filler(), FUN]
    new = export([base_to(sym=3), NEW, NAV, filler(), filler(), FUN], 4)
    out = layout_diff(old, PLAIN, edit_ref(new), PLAIN)
    assert out == "- Layer added: **Layer 1** (position 1)"


def edit_ref(src: str) -> str:
    """The LT in base_to() defaults to layer 1: send it after Nav, which moved to 2."""
    return src.replace("LT(1, KC_SPACE)", "LT(2, KC_SPACE)")


def test_diff_copying_a_layer_over_another_stays_in_place(layout_diff):
    """Layer 1 became a copy of layer 2: layer 1 changed, layer 2 didn't, and nothing moved."""
    out = layout_diff(FOUR, PLAIN, [base_keys(), SYM, SYM, FUN], PLAIN)
    assert out.splitlines() == [f"- **Layer 1** (L0.{i}): `{a}` → `{b}`" for i, (a, b) in enumerate(zip("←↓↑→", "!@#$"))]


def dances(**bodies) -> str:
    """Oryx's tap dance code: name=(tap, hold, double tap, double hold), "" for none.
    A double tap of "" is the plain two presses Oryx writes when none is set."""
    out, table = [], []
    for name, (tap, hold, double, double_hold) in bodies.items():
        n = name.removeprefix("DANCE_")
        cases = [f"case SINGLE_TAP: {tap}; break;" if "(" in tap and not tap.startswith("KC") else f"case SINGLE_TAP: register_code16({tap}); break;"]
        if hold:
            cases.append(f"case SINGLE_HOLD: {hold if hold.startswith('layer_') else f'register_code16({hold})'}; break;")
        cases.append(f"case DOUBLE_TAP: register_code16({double}); break;" if double else f"case DOUBLE_TAP: register_code16({tap}); register_code16({tap}); break;")
        if double_hold:
            cases.append(f"case DOUBLE_HOLD: register_code16({double_hold}); break;")
        cases.append(f"case DOUBLE_SINGLE_TAP: tap_code16({tap}); register_code16({tap});")
        body = "\n        ".join(cases)
        out.append(
            f"void dance_{n}_finished(tap_dance_state_t *state, void *user_data) {{\n"
            f"    dance_state[{n}].step = dance_step(state);\n"
            f"    switch (dance_state[{n}].step) {{\n        {body}\n    }}\n}}\n"
        )
        table.append(f"        [DANCE_{n}] = ACTION_TAP_DANCE_FN_ADVANCED(on_dance_{n}, dance_{n}_finished, dance_{n}_reset),")
    return "\n".join(out) + "\ntap_dance_action_t tap_dance_actions[] = {\n" + "\n".join(table) + "\n};\n"


def danced(key: str, code: str, at: int = 14) -> str:
    """FOUR with `key` at L1.2, where base_keys() has DUAL_FUNC_0, plus dance code."""
    return export([edited(base_keys(), at, key), NAV, SYM, FUN]) + code


def test_parse_dances_real_shape():
    km = ol.parse_keymap(danced("TD(DANCE_0)", dances(DANCE_0=("KC_1", "KC_EXLM", "", ""), DANCE_1=("KC_Z", "", "", "KC_LEFT_CTRL"), DANCE_2=("KC_U", "", "LGUI(KC_SPACE)", ""))))
    assert km.dances == {
        "DANCE_0": ("KC_1", "KC_EXLM", "", ""),
        "DANCE_1": ("KC_Z", "", "", "KC_LEFT_CTRL"),
        "DANCE_2": ("KC_U", "", "LGUI(KC_SPACE)", ""),
    }


def test_diff_tap_dance_to_dual_function_with_same_behaviour(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_DLR", "KC_LEFT_GUI", "", "")))
    assert layout_diff(old, FOUR_NAMES, FOUR, FOUR_NAMES) == "- 1 tap dance became a dual-function key (same tap and hold; timing differs)"


def test_diff_tap_dance_to_dual_function_with_new_behaviour(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_DLR", "KC_EXLM", "", "")))
    assert layout_diff(old, FOUR_NAMES, FOUR, FOUR_NAMES) == "- **Base** (L1.2): `$/!` → `$/Super`"


def test_diff_tap_dance_renumber_is_no_change(layout_diff):
    body = ("KC_U", "", "LGUI(KC_SPACE)", "")
    old = danced("TD(DANCE_0)", dances(DANCE_0=body))
    new = danced("TD(DANCE_4)", dances(DANCE_4=body))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "No keymap changes."


def test_diff_tap_dance_body_edit_under_the_same_number(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_EQUAL", "KC_ESCAPE", "", "")))
    new = danced("TD(DANCE_0)", dances(DANCE_0=("KC_MPLY", "KC_MNXT", "", "")))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L1.2): `=/Esc` → `Play/Next`"


def test_diff_double_hold_stays_a_dance(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_Z", "", "", "KC_LEFT_CTRL")))
    new = danced("TD(DANCE_0)", dances(DANCE_0=("KC_Z", "KC_LEFT_CTRL", "", "")))
    out = layout_diff(old, FOUR_NAMES, new, FOUR_NAMES)
    assert out == "- **Base** (L1.2): `Z, double-hold Ctrl` → `Z/Ctrl`"


def test_diff_double_tap_is_part_of_the_dance(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_U", "", "KC_V", "")))
    new = danced("TD(DANCE_1)", dances(DANCE_1=("KC_U", "", "KC_W", "")))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L1.2): `U, double-tap V` → `U, double-tap W`"


def test_diff_tap_dance_hold_follows_its_layer(layout_diff):
    """Oryx's hold can be layer_move, here to Sym, which an insert renumbers."""
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_ESCAPE", "layer_move(2)", "", "")))
    new = export([edited(base_to(nav=2, sym=3), 14, "TD(DANCE_0)"), NEW, NAV, SYM, FUN], 4) + dances(DANCE_0=("KC_ESCAPE", "layer_move(3)", "", ""))
    assert layout_diff(old, FOUR_NAMES, new, titles("Base", "Mouse", "Nav", "Sym", "Fun")) == "- Layer added: **Mouse** (position 1)"


@pytest.mark.parametrize(
    "broken",
    [
        "void dance_0_finished(tap_dance_state_t *state, void *user_data) {\n  rgblight_toggle();\n}\n",
        "void dance_0_finished(tap_dance_state_t *state, void *user_data) {\n  switch (x) {\n    case SINGLE_TAP: my_code(); break;\n  }\n}\n",
        "void dance_0_finished(tap_dance_state_t *state, void *user_data) {\n  switch (x) {\n    case TRIPLE_TAP: register_code16(KC_A); break;\n  }\n}\n",
        "void dance_0_finished(tap_dance_state_t *state, void *user_data) {\n  switch (x) {\n",
    ],
)
def test_diff_unreadable_dance_falls_back_to_its_number(layout_diff, broken):
    table = "tap_dance_action_t tap_dance_actions[] = {\n  [DANCE_0] = ACTION_TAP_DANCE_FN_ADVANCED(on_dance_0, dance_0_finished, dance_0_reset),\n};\n"
    src = danced("TD(DANCE_0)", broken + table)
    assert ol.parse_keymap(src).dances == {}
    assert layout_diff(src, FOUR_NAMES, src, FOUR_NAMES) == "No keymap changes."
    other = src.replace("TD(DANCE_0)", "TD(DANCE_1)")
    assert layout_diff(src, FOUR_NAMES, other, FOUR_NAMES) == "- **Base** (L1.2): `Dance 0` → `Dance 1`"


@pytest.mark.parametrize(
    "a,b",
    [
        ("MT(MOD_LSFT | MOD_LCTL, KC_A)", "MT(MOD_LSFT|MOD_LCTL, KC_A)"),
        ("MT(MOD_LSFT | MOD_LCTL, KC_A)", "MT(MOD_LCTL | MOD_LSFT, KC_A)"),
        ("OSM(MOD_LSFT|MOD_LALT)", "OSM( MOD_LALT | MOD_LSFT )"),
        ("LM(1,MOD_LSFT|MOD_LCTL)", "LM(1, MOD_LCTL|MOD_LSFT)"),
    ],
)
def test_diff_spelling_of_a_mod_set_is_no_change(layout_diff, a, b):
    old, new = [edited(base_keys(), 0, a), NAV, SYM, FUN], [edited(base_keys(), 0, b), NAV, SYM, FUN]
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "No keymap changes."


def test_diff_different_mods_are_a_change(layout_diff):
    old = [edited(base_keys(), 0, "MT(MOD_LSFT | MOD_LCTL, KC_A)"), NAV, SYM, FUN]
    new = [edited(base_keys(), 0, "MT(MOD_LSFT | MOD_LALT, KC_A)"), NAV, SYM, FUN]
    assert "L0.0" in layout_diff(old, FOUR_NAMES, new, FOUR_NAMES)


@pytest.mark.parametrize("limit", ["-1", "x"])
def test_diff_rejects_a_bad_limit(tmp_path, limit, capsys):
    km = tmp_path / "keymap.c"
    km.write_text(make_keymap(FOUR))
    with pytest.raises(SystemExit) as exc:
        ol.main(["diff", str(km), str(km), "--limit", limit])
    assert exc.value.code == 2 and "--limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    "meta",
    [
        {"layers": "x"},
        {"layers": 3},
        {"layers": ["x", None]},
        {"layers": [{"position": None, "title": "Nav"}]},
        {"layers": [{"position": "x", "title": "Nav"}]},
        {"layers": [{"position": 1, "title": 7}]},
        {"layers": [{"title": "Nav"}]},
    ],
)
def test_diff_malformed_old_layer_entries_are_ignored(layout_diff, meta):
    out = layout_diff(FOUR, meta, [base_keys(), edited(NAV), SYM, FUN], FOUR_NAMES)
    assert out == "- **Nav** (L0.0): `←` → `Home`"


def test_sync_survives_a_failing_summary_and_says_so(sync_repo, tmp_path):
    (sync_repo / "scripts/oryx_layout.py").write_text("raise RuntimeError('boom')\n")
    temp_git(sync_repo, "commit", "-qam", "break the summary")
    res = sync(sync_repo, tmp_path, make_keymap([base_keys(), NEW, SYM, FUN]), FOUR_NAMES)
    assert res.returncode == 0, res.stderr
    assert "warning:" in res.stderr and "summary" in res.stderr
    body = subprocess.run(["git", "-C", str(sync_repo), "log", "-1", "oryx", "--format=%B"], capture_output=True, text=True, check=True).stdout
    assert "summary unavailable" in body


# --- diff: second review (#12) -----------------------------------------------------


def moving_duals(layers: list[list[str]]) -> str:
    """An export whose DUAL_FUNC_1 holds with layer_move, the layer action Oryx's dances use."""
    return export(layers).replace("layer_on(3)", "layer_move(3)").replace("layer_off(3)", "layer_move(3)")


def two_duals_as_dances() -> str:
    """FOUR with both of base_keys()'s dual-function keys written as the dances they equal."""
    keys = edited(edited(base_keys(), 14, "TD(DANCE_0)"), 41, "TD(DANCE_1)")
    return export([keys, NAV, SYM, FUN]) + dances(DANCE_0=("KC_DLR", "KC_LEFT_GUI", "", ""), DANCE_1=("KC_RIGHT_CTRL", "layer_move(3)", "", ""))


def test_diff_tap_dances_becoming_dual_functions_are_counted_once(layout_diff):
    out = layout_diff(two_duals_as_dances(), FOUR_NAMES, moving_duals(FOUR), FOUR_NAMES)
    assert out == "- 2 tap dances became dual-function keys (same tap and hold; timing differs)"


def test_diff_dual_functions_becoming_tap_dances_are_counted_once(layout_diff):
    out = layout_diff(moving_duals(FOUR), FOUR_NAMES, two_duals_as_dances(), FOUR_NAMES)
    assert out == "- 2 dual-function keys became tap dances (same tap and hold; timing differs)"


def test_diff_timing_line_survives_the_limit(layout_diff):
    rewritten = [k.replace("KC_LEFT", "KC_HOME").replace("KC_DOWN", "KC_END") for k in NAV]
    old = two_duals_as_dances()
    out = layout_diff(old, FOUR_NAMES, moving_duals([base_keys(), rewritten, SYM, FUN]), FOUR_NAMES, "--limit", "1").splitlines()
    assert out == [
        "- 2 tap dances became dual-function keys (same tap and hold; timing differs)",
        "- **Nav** (L0.0): `←` → `Home`",
        "- …and 1 more key changes",
    ]


def test_diff_a_new_dance_with_new_behaviour_is_not_counted(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_DLR", "KC_EXLM", "", "")))
    assert "timing" not in layout_diff(old, FOUR_NAMES, FOUR, FOUR_NAMES)


def test_diff_renumbers_have_no_timing_line(layout_diff):
    body = ("KC_DLR", "KC_LEFT_GUI", "", "")
    old = danced("TD(DANCE_0)", dances(DANCE_0=body))
    new = danced("TD(DANCE_4)", dances(DANCE_4=body))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "No keymap changes."
    src = make_keymap(FOUR)
    assert layout_diff(src, FOUR_NAMES, renumber_duals(src), FOUR_NAMES) == "No keymap changes."


def test_diff_transparent_blank_before_a_blocked_blank(layout_diff):
    """Both are blank, but the new layer isn't the KC_NO layer that was already there."""
    old = [base_keys(), NAV, SYM, FUN, filler("KC_NO")]
    new = [base_keys(), NAV, SYM, FUN, filler(), filler("KC_NO")]
    assert layout_diff(old, PLAIN, new, PLAIN) == "- Layer added: **Layer 4** (position 4)"


def test_diff_swapping_outer_layers_moves_those_two(layout_diff):
    """Sym sits between them and never left position 2, so it isn't "moved 2 → 2"."""
    new = export([base_to(nav=3, sym=2), FUN, SYM, NAV], 1)
    out = layout_diff(FOUR, FOUR_NAMES, new, titles("Base", "Fun", "Sym", "Nav"))
    assert out.splitlines() == ["- Layer moved: **Fun** (position 3 → 1)", "- Layer moved: **Nav** (position 1 → 3)"]


@pytest.mark.parametrize("key", ["TD(DANCE_0)", "LSFT(TD(DANCE_0))"])
def test_diff_a_dance_that_presses_itself(layout_diff, key):
    src = danced("TD(DANCE_0)", dances(DANCE_0=(key, "KC_B", "", "")))
    assert layout_diff(src, FOUR_NAMES, src, FOUR_NAMES) == "No keymap changes."


def test_diff_dances_that_press_each_other(layout_diff):
    src = danced("TD(DANCE_0)", dances(DANCE_0=("TD(DANCE_1)", "KC_B", "", ""), DANCE_1=("TD(DANCE_0)", "KC_B", "", "")))
    assert layout_diff(src, FOUR_NAMES, src, FOUR_NAMES) == "No keymap changes."


def test_diff_a_dual_function_that_presses_itself(layout_diff):
    src = make_keymap(FOUR).replace("register_code16(KC_DLR)", "register_code16(DUAL_FUNC_0)")
    assert layout_diff(src, FOUR_NAMES, src, FOUR_NAMES) == "No keymap changes."


PLAIN_DANCE = dances(DANCE_0=("KC_A", "KC_B", "", ""))
NOT_PLAIN = {
    "code before the switch": lambda c: c.replace("    switch (", "    my_code();\n    switch (", 1),
    "code after the step": lambda c: c.replace("dance_step(state);", "dance_step(state); my_code();", 1),
    "no step": lambda c: c.replace("    dance_state[0].step = dance_step(state);\n", "", 1),
    "another step function": lambda c: c.replace("= dance_step(state)", "= my_step(state)", 1),
    "another dance's step": lambda c: c.replace("dance_state[0].step = dance_step", "dance_state[1].step = dance_step", 1),
    "a switch on something else": lambda c: c.replace("switch (dance_state[0].step)", "switch (other)", 1),
    "a missing break": lambda c: c.replace("case SINGLE_TAP: register_code16(KC_A); break;", "case SINGLE_TAP: register_code16(KC_A);", 1),
    "no double tap": lambda c: re.sub(r" *case DOUBLE_TAP:.*\n", "", c, count=1),
    "no double single tap": lambda c: re.sub(r" *case DOUBLE_SINGLE_TAP:.*\n", "", c, count=1),
    "a tapped hold": lambda c: c.replace("case SINGLE_HOLD: register_code16(KC_B)", "case SINGLE_HOLD: tap_code16(KC_B)", 1),
    "a tapped tap": lambda c: c.replace("case SINGLE_TAP: register_code16(KC_A)", "case SINGLE_TAP: tap_code16(KC_A)", 1),
    "a call hidden in an argument": lambda c: c.replace("case SINGLE_HOLD: register_code16(KC_B);", "case SINGLE_HOLD: register_code16(KC_B), my_code();", 1),
    "cases out of order": lambda c: c.replace("case SINGLE_TAP:", "case X_TAP:", 1).replace("case SINGLE_HOLD:", "case SINGLE_TAP:", 1).replace("case X_TAP:", "case SINGLE_HOLD:", 1),
}


@pytest.mark.parametrize("name", NOT_PLAIN)
def test_dance_code_that_is_not_plain_is_not_read(name):
    assert ol.parse_keymap(danced("TD(DANCE_0)", PLAIN_DANCE)).dances
    assert ol.parse_keymap(danced("TD(DANCE_0)", NOT_PLAIN[name](PLAIN_DANCE))).dances == {}


@pytest.mark.parametrize("name", NOT_PLAIN)
def test_diff_unread_dance_is_never_equal_to_a_plain_one(layout_diff, name):
    old = danced("TD(DANCE_0)", PLAIN_DANCE)
    new = danced("TD(DANCE_0)", NOT_PLAIN[name](PLAIN_DANCE))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L1.2): `A/B` → `Dance 0`"


@pytest.mark.parametrize("name", NOT_PLAIN)
def test_diff_edit_in_an_unread_dance_is_reported(layout_diff, name):
    old = danced("TD(DANCE_0)", NOT_PLAIN[name](PLAIN_DANCE))
    new = danced("TD(DANCE_0)", NOT_PLAIN[name](PLAIN_DANCE).replace("KC_B", "KC_C"))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "- **Base** (L1.2): `Dance 0` → `Dance 0`  (tap dance body changed)"


def test_diff_unread_dance_renumber_is_no_change(layout_diff):
    broken = NOT_PLAIN["code before the switch"]
    old = danced("TD(DANCE_0)", broken(PLAIN_DANCE))
    new = danced("TD(DANCE_3)", broken(PLAIN_DANCE).replace("dance_state[0]", "dance_state[3]").replace("dance_0", "dance_3").replace("DANCE_0", "DANCE_3"))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == "No keymap changes."


def test_diff_raw_flag_does_not_repeat_equal_identifiers(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_EQUAL", "KC_ESCAPE", "", "")))
    new = danced("TD(DANCE_0)", dances(DANCE_0=("KC_EQUAL", "KC_HOME", "", "")))
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES, "--raw") == "- **Base** (L1.2): `=/Esc` → `=/Home`"


def test_diff_same_legend_same_code_gets_no_empty_suffix(layout_diff):
    """The key is untouched, but the layer it reaches only exists now: no raw `TG(9)` → `TG(9)`."""
    old = [edited(base_keys(), 0, "TG(4)"), NAV, SYM, FUN]
    new = [edited(base_keys(), 0, "TG(4)"), NAV, SYM, FUN, NEW]
    out = layout_diff(old, PLAIN, new, PLAIN)
    assert "- **Layer 0** (L0.0): `TG Layer 4` → `TG Layer 4`" in out and "`TG(4)`" not in out


@pytest.mark.parametrize("meta", ['{"layers": [{"position": Infinity, "title": "Nav"}]}', '{"layers": [{"position": NaN, "title": "Nav"}]}'])
def test_diff_non_finite_position_is_ignored(layout_diff, meta):
    out = layout_diff(FOUR, meta, [base_keys(), edited(NAV), SYM, FUN], FOUR_NAMES)
    assert out == "- **Nav** (L0.0): `←` → `Home`"


def test_diff_non_dict_meta_flag(tmp_path, capsys):
    km, meta = tmp_path / "keymap.c", tmp_path / "meta.json"
    km.write_text(make_keymap(FOUR))
    meta.write_text("[1, 2]")
    assert ol.main(["diff", str(km), str(km), "--meta", str(meta)]) == 0
    assert capsys.readouterr().out.strip() == "No keymap changes."


# --- keys that press themselves, in lint and render (#12) ---------------------------

SELF_PRESSING = {
    "tap": lambda src: src.replace("register_code16(KC_DLR)", "register_code16(DUAL_FUNC_0)"),
    "hold": lambda src: src.replace("register_code16(KC_LEFT_GUI)", "register_code16(DUAL_FUNC_0)"),
    "each other": lambda src: src.replace("register_code16(KC_DLR)", "register_code16(DUAL_FUNC_1)").replace("register_code16(KC_RIGHT_CTRL)", "register_code16(DUAL_FUNC_0)"),
}


@pytest.mark.parametrize("how", SELF_PRESSING)
def test_lint_survives_a_dual_function_that_presses_itself(tmp_path, how):
    km = tmp_path / "keymap.c"
    km.write_text(SELF_PRESSING[how](make_keymap(FOUR)))
    assert ol.main(["lint", "--keymap", str(km), "--meta", "/x"]) in (0, 1)


@pytest.mark.parametrize("how", SELF_PRESSING)
def test_render_survives_a_dual_function_that_presses_itself(tmp_path, how):
    km = tmp_path / "keymap.c"
    km.write_text(SELF_PRESSING[how](make_keymap(FOUR)))
    assert ol.main(["render", "--keymap", str(km), "--meta", "/x", "--out", str(tmp_path / "docs"), "--no-svg"]) == 0
    assert (tmp_path / "docs/keymap.md").exists()


@pytest.mark.parametrize("how", SELF_PRESSING)
def test_diff_survives_a_dual_function_that_presses_itself(layout_diff, how):
    src = SELF_PRESSING[how](make_keymap(FOUR))
    assert layout_diff(src, FOUR_NAMES, src, FOUR_NAMES) == "No keymap changes."


def test_targets_still_follow_a_dual_function_hold(four_layers):
    assert ol.Labeller(four_layers).targets("DUAL_FUNC_1") == [("MO", 3)]


def test_render_non_dict_meta(tmp_path):
    km, meta = tmp_path / "keymap.c", tmp_path / "meta.json"
    km.write_text(make_keymap(FOUR))
    meta.write_text("[1, 2]")
    assert ol.main(["render", "--keymap", str(km), "--meta", str(meta), "--out", str(tmp_path / "docs"), "--no-svg"]) == 0


def test_diff_dance_body_change_with_equal_legends_says_so(layout_diff):
    old = danced("TD(DANCE_0)", dances(DANCE_0=("KC_1", "KC_LSFT", "", "")))
    new = danced("TD(DANCE_0)", dances(DANCE_0=("KC_1", "KC_RSFT", "", "")))
    expected = "- **Base** (L1.2): `1/Shift` → `1/Shift`  (tap dance body changed)"
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == expected
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES, "--raw") == expected


def test_diff_dual_function_body_change_with_equal_legends_says_so(layout_diff):
    old = export(FOUR).replace("KC_LEFT_GUI", "KC_LSFT")
    new = export(FOUR).replace("KC_LEFT_GUI", "KC_RSFT")
    expected = "- **Base** (L1.2): `$/Shift` → `$/Shift`  (dual-function body changed)"
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES) == expected
    assert layout_diff(old, FOUR_NAMES, new, FOUR_NAMES, "--raw") == expected


def test_diff_renumbered_body_change_with_equal_legends_says_so(layout_diff):
    old = export(FOUR).replace("KC_LEFT_GUI", "KC_LSFT")
    new = renumber_duals(export(FOUR).replace("KC_LEFT_GUI", "KC_RSFT"))
    out = layout_diff(old, FOUR_NAMES, new, FOUR_NAMES)
    assert out == "- **Base** (L1.2): `$/Shift` → `$/Shift`  (dual-function body changed)"
    with_raw = layout_diff(old, FOUR_NAMES, new, FOUR_NAMES, "--raw")
    assert with_raw == "- **Base** (L1.2): `$/Shift` → `$/Shift`  (`DUAL_FUNC_0` → `DUAL_FUNC_1`)  (dual-function body changed)"
