#!/usr/bin/env python3
"""Read an Oryx-generated keymap.c and make it visible.

Subcommands:
  render  keymap.c + keyboard info -> docs/keymap.md, keymap.yaml, keymap.svg
  diff    key-by-key changes between two keymap.c versions (files or git refs)
  lint    sanity checks: unreachable layers, dangling layer keys, traps
  parse   dump the parsed keymap as JSON (debugging)

Only the standard library is needed. `render` shells out to keymap-drawer
(https://github.com/caksoylar/keymap-drawer) for the SVG when it is installed.

The parser targets the keymap.c that Oryx generates. It also copes with most
hand-written QMK keymaps, but Oryx's conventions (numeric layer indices,
DUAL_FUNC_n defines, ledmap arrays) are what it is tuned for.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# C source helpers
# --------------------------------------------------------------------------


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments while leaving string and char literals intact."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i : j + 1])
            i = j + 1
        elif c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


_PAIRS = {"(": ")", "{": "}", "[": "]"}


def balanced(src: str, open_idx: int) -> tuple[str, int]:
    """Return (inner text, index after the closer) for the bracket at open_idx."""
    opener = src[open_idx]
    closer = _PAIRS[opener]
    depth = 0
    for j in range(open_idx, len(src)):
        ch = src[j]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return src[open_idx + 1 : j], j + 1
    raise ValueError(f"unbalanced {opener!r} at offset {open_idx}")


def split_args(s: str) -> list[str]:
    """Split on commas that are not nested inside brackets."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [" ".join(p.split()) for p in parts if p.strip()]


def call(expr: str) -> tuple[str, list[str]] | None:
    """Parse FUNC(arg, arg) -> ("FUNC", [args]); None when expr is not a call."""
    m = re.fullmatch(r"(\w+)\s*\((.*)\)", expr.strip(), re.S)
    if not m:
        return None
    return m.group(1), split_args(m.group(2))


# --------------------------------------------------------------------------
# Keymap model
# --------------------------------------------------------------------------


@dataclass
class Layer:
    index: int
    ident: str
    keys: list[str]
    name: str = ""


@dataclass
class Keymap:
    layout_macro: str
    layers: list[Layer]
    row_sizes: list[int] = field(default_factory=list)  # keys per source line, as Oryx prints rows
    defines: dict[str, str] = field(default_factory=dict)
    dual_funcs: dict[str, tuple[str, str]] = field(default_factory=dict)
    ledmap: dict[int, list[tuple[int, int, int]]] = field(default_factory=dict)
    tri_layers: list[tuple[str, str, str]] = field(default_factory=list)  # update_tri_layer_state(a, b, c)

    def layer_index(self, arg: str) -> int | None:
        """Layer number for a layer argument: a digit, or a name such as _LOWER."""
        arg = self.resolve(arg.strip())
        if arg.isdigit():
            return int(arg)
        for layer in self.layers:
            if layer.ident == arg:
                return layer.index
        return None

    def resolve(self, kc: str, depth: int = 0) -> str:
        """Expand simple object-like #defines (e.g. DUAL_FUNC_0 -> LT(14, KC_8))."""
        if depth < 8 and kc in self.defines and kc not in self.dual_funcs:
            return self.resolve(self.defines[kc], depth + 1)
        return kc

    def to_json(self) -> dict:
        return {
            "layout_macro": self.layout_macro,
            "layers": [{"index": l.index, "ident": l.ident, "name": l.name, "keys": l.keys} for l in self.layers],
            "dual_funcs": {k: {"tap": t, "hold": h} for k, (t, h) in self.dual_funcs.items()},
            "ledmap": {str(k): v for k, v in self.ledmap.items()},
        }


_LAYER_CALLS = {"layer_on": "MO", "layer_move": "TO", "layer_invert": "TG"}


def _branch_action(body: str) -> str:
    """First action in a dual-function branch, expressed as a keycode."""
    m = re.search(r"\b(register_code16|layer_on|layer_move|layer_invert)\(\s*([^;]+?)\s*\)\s*;", body)
    if not m:
        return ""
    fn, arg = m.groups()
    return arg if fn == "register_code16" else f"{_LAYER_CALLS[fn]}({arg})"


def parse_keymap(text: str) -> Keymap:
    src = strip_comments(text)

    defines = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+(.+?)[ \t]*$", src, re.M)
    }

    m = re.search(r"keymaps\s*\[\s*\]\s*\[[^\]]*\]\s*\[[^\]]*\]\s*=\s*\{", src)
    if not m:
        raise ValueError("could not find the `keymaps[][MATRIX_ROWS][MATRIX_COLS]` array")
    block, _ = balanced(src, m.end() - 1)

    layers: list[Layer] = []
    layout_macro = ""
    row_sizes: list[int] = []
    for lm in re.finditer(r"\[\s*(\w+)\s*\]\s*=\s*(\w+)\s*\(", block):
        inner, _ = balanced(block, lm.end() - 1)
        ident, layout_macro = lm.group(1), lm.group(2)
        index = int(ident) if ident.isdigit() else len(layers)
        keys = split_args(inner)
        layers.append(Layer(index=index, ident=ident, keys=keys))
        if not row_sizes:
            sizes = [len(split_args(line)) for line in inner.splitlines() if line.strip()]
            row_sizes = sizes if sum(sizes) == len(keys) and all(sizes) else []
    if not layers:
        raise ValueError("keymaps array contains no LAYOUT(...) entries")
    layers.sort(key=lambda l: l.index)

    # Oryx "dual function" keys: LT(dummy, KC) intercepted in process_record_user.
    # The tap branch and hold branch each either register a keycode or switch a layer.
    dual: dict[str, tuple[str, str]] = {}
    for name in (n for n in defines if n.startswith("DUAL_FUNC_")):
        cm = re.search(rf"case\s+{name}\s*:.*?tap\.count[^{{]*\{{", src, re.S)
        if not cm:
            continue
        tap_body, after = balanced(src, cm.end() - 1)
        em = re.match(r"\s*else\s*\{", src[after:])
        hold_body = balanced(src, after + em.end() - 1)[0] if em else ""
        tap, hold = _branch_action(tap_body), _branch_action(hold_body)
        if tap or hold:
            dual[name] = (tap or "KC_NO", hold or "")

    ledmap: dict[int, list[tuple[int, int, int]]] = {}
    lm = re.search(r"ledmap\s*\[\s*\]\s*\[[^\]]*\]\s*\[\s*3\s*\]\s*=\s*\{", src)
    if lm:
        lblock, _ = balanced(src, lm.end() - 1)
        for em in re.finditer(r"\[\s*(\d+)\s*\]\s*=\s*\{", lblock):
            inner, _ = balanced(lblock, em.end() - 1)
            triples = re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}", inner)
            ledmap[int(em.group(1))] = [tuple(int(v) for v in t) for t in triples]

    tri = [tuple(split_args(m.group(1))[1:4]) for m in re.finditer(r"update_tri_layer_state\s*\(([^;]*)\)\s*;", src)]
    tri = [t for t in tri if len(t) == 3]

    return Keymap(layout_macro=layout_macro, layers=layers, row_sizes=row_sizes, defines=defines, dual_funcs=dual, ledmap=ledmap, tri_layers=tri)


def assign_layer_names(km: Keymap, meta: dict | None) -> None:
    """Name layers from Oryx metadata; fall back to L0, L1, ... Names are unique."""
    titles = {}
    for entry in (meta or {}).get("layers", []) or []:
        title = (entry.get("title") or "").strip()
        if title and title.lower() != "layer":
            titles[int(entry.get("position", -1))] = title
    seen: set[str] = set()
    for layer in km.layers:
        name = titles.get(layer.index) or (layer.ident if not layer.ident.isdigit() else f"L{layer.index}")
        if name in seen:
            name = f"{name} {layer.index}"
        seen.add(name)
        layer.name = name


# --------------------------------------------------------------------------
# Keycode -> human legend
# --------------------------------------------------------------------------

MOD_ORDER = ["ctrl", "alt", "shift", "gui"]
MOD_TEXT = {
    "mac": {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "gui": "⌘", "join": ""},
    "pc": {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "gui": "Super", "join": "+"},
}
MOD_ALIASES = {
    "LCTL": {"ctrl"}, "RCTL": {"ctrl"}, "C": {"ctrl"}, "LCTRL": {"ctrl"}, "RCTRL": {"ctrl"},
    "LSFT": {"shift"}, "RSFT": {"shift"}, "S": {"shift"}, "LSHIFT": {"shift"}, "RSHIFT": {"shift"},
    "LALT": {"alt"}, "RALT": {"alt"}, "A": {"alt"}, "LOPT": {"alt"}, "ROPT": {"alt"}, "ALGR": {"alt"},
    "LGUI": {"gui"}, "RGUI": {"gui"}, "G": {"gui"}, "LCMD": {"gui"}, "RCMD": {"gui"}, "LWIN": {"gui"}, "RWIN": {"gui"},
    "MEH": {"ctrl", "alt", "shift"}, "HYPR": {"ctrl", "alt", "shift", "gui"}, "ALL": {"ctrl", "alt", "shift", "gui"},
    "C_S": {"ctrl", "shift"}, "LCA": {"ctrl", "alt"}, "LSA": {"shift", "alt"}, "RSA": {"shift", "alt"},
    "LCS": {"ctrl", "shift"}, "RCS": {"ctrl", "shift"}, "LAG": {"alt", "gui"}, "RAG": {"alt", "gui"},
    "LCG": {"ctrl", "gui"}, "RCG": {"ctrl", "gui"}, "LSG": {"shift", "gui"}, "RSG": {"shift", "gui"},
    "SGUI": {"shift", "gui"}, "SCMD": {"shift", "gui"}, "SWIN": {"shift", "gui"},
}
MOD_KEYS = {
    "LEFT_SHIFT": "shift", "LSFT": "shift", "LSHIFT": "shift", "RIGHT_SHIFT": "shift", "RSFT": "shift",
    "LEFT_CTRL": "ctrl", "LCTL": "ctrl", "RIGHT_CTRL": "ctrl", "RCTL": "ctrl",
    "LEFT_ALT": "alt", "LALT": "alt", "LOPT": "alt", "RIGHT_ALT": "alt", "RALT": "alt", "ALGR": "alt",
    "LEFT_GUI": "gui", "LGUI": "gui", "LCMD": "gui", "RIGHT_GUI": "gui", "RGUI": "gui", "RCMD": "gui",
}

BASIC = {
    "SPACE": "Space", "SPC": "Space", "ENTER": "Enter", "ENT": "Enter", "BSPC": "⌫", "BACKSPACE": "⌫",
    "DELETE": "Del", "DEL": "Del", "ESCAPE": "Esc", "ESC": "Esc", "TAB": "Tab",
    "CAPS": "Caps", "CAPS_LOCK": "Caps", "CAPSLOCK": "Caps",
    "LEFT": "←", "RIGHT": "→", "RGHT": "→", "UP": "↑", "DOWN": "↓",
    "HOME": "Home", "END": "End", "PAGE_UP": "PgUp", "PGUP": "PgUp", "PAGE_DOWN": "PgDn", "PGDN": "PgDn",
    "INSERT": "Ins", "INS": "Ins", "APPLICATION": "Menu", "APP": "Menu", "PSCR": "PrtSc", "PRINT_SCREEN": "PrtSc",
    "MINUS": "-", "MINS": "-", "EQUAL": "=", "EQL": "=", "LBRC": "[", "LEFT_BRACKET": "[", "RBRC": "]",
    "RIGHT_BRACKET": "]", "BSLS": "\\", "BACKSLASH": "\\", "SCLN": ";", "SEMICOLON": ";", "QUOTE": "'",
    "QUOT": "'", "GRAVE": "`", "GRV": "`", "COMMA": ",", "COMM": ",", "DOT": ".", "SLASH": "/", "SLSH": "/",
    "NONUS_BACKSLASH": "\\", "NUBS": "\\", "NONUS_HASH": "#", "NUHS": "#",
    "EXLM": "!", "AT": "@", "HASH": "#", "DLR": "$", "DOLLAR": "$", "PERC": "%", "PERCENT": "%", "CIRC": "^",
    "AMPR": "&", "ASTR": "*", "LPRN": "(", "RPRN": ")", "UNDS": "_", "PLUS": "+", "LCBR": "{", "RCBR": "}",
    "PIPE": "|", "COLN": ":", "DQUO": '"', "DQT": '"', "TILD": "~", "LABK": "<", "LT": "<", "RABK": ">",
    "GT": ">", "QUES": "?",
    "AUDIO_VOL_UP": "Vol+", "VOLU": "Vol+", "AUDIO_VOL_DOWN": "Vol−", "VOLD": "Vol−", "AUDIO_MUTE": "Mute",
    "MUTE": "Mute", "MEDIA_PLAY_PAUSE": "Play", "MPLY": "Play", "MEDIA_NEXT_TRACK": "Next", "MNXT": "Next",
    "MEDIA_PREV_TRACK": "Prev", "MPRV": "Prev", "MEDIA_STOP": "Stop", "MSTP": "Stop",
    "BRIGHTNESS_UP": "Bri+", "BRIU": "Bri+", "BRIGHTNESS_DOWN": "Bri−", "BRID": "Bri−",
    "MS_UP": "M↑", "MS_DOWN": "M↓", "MS_LEFT": "M←", "MS_RIGHT": "M→",
    "MS_BTN1": "Click", "BTN1": "Click", "MS_BTN2": "R-Click", "BTN2": "R-Click", "MS_BTN3": "M-Click",
    "BTN3": "M-Click", "MS_WH_UP": "Wh↑", "WH_U": "Wh↑", "MS_WH_DOWN": "Wh↓", "WH_D": "Wh↓",
    "MS_WH_LEFT": "Wh←", "MS_WH_RIGHT": "Wh→", "MS_DBL_CLICK": "Dbl-Click",
    "QK_BOOT": "Boot", "QK_BOOTLOADER": "Boot", "QK_LLCK": "Layer Lock", "QK_LAYER_LOCK": "Layer Lock",
    "CW_TOGG": "Caps Word", "QK_CAPS_WORD_TOGGLE": "Caps Word", "QK_REP": "Repeat", "QK_REPEAT_KEY": "Repeat",
    "QK_AREP": "Alt-Rep", "QK_ALT_REPEAT_KEY": "Alt-Rep", "TOGGLE_LAYER_COLOR": "Layer LEDs",
    "RGB_TOG": "RGB", "RGB_MOD": "RGB Mode", "RGB_MODE_FORWARD": "RGB Mode", "RGB_SLD": "RGB Solid",
    "RGB_VAI": "RGB Bri+", "RGB_VAD": "RGB Bri−", "RGB_HUI": "Hue+", "RGB_HUD": "Hue−",
    "RGB_SPI": "RGB Spd+", "RGB_SPD": "RGB Spd−", "RGB_SAI": "Sat+", "RGB_SAD": "Sat−",
    "HYPR": "Hyper", "MEH": "Meh",
    "WWW_BACK": "Back", "WWW_FORWARD": "Fwd", "WWW_REFRESH": "Reload", "WWW_SEARCH": "Search",
    "AU_TOGG": "Audio", "MU_TOGG": "Music", "MU_NEXT": "Music Mode", "SYSTEM_SLEEP": "Sleep",
}


KEYPAD = {
    "PLUS": "+", "MINUS": "-", "ASTERISK": "*", "SLASH": "/", "DOT": ".", "ENTER": "Enter",
    "EQUAL": "=", "COMMA": ",", **{str(d): str(d) for d in range(10)},
}


@dataclass
class Legend:
    tap: str = ""
    hold: str = ""
    kind: str = ""  # "", trans, none, layer, mod, special

    def text(self) -> str:
        if self.kind == "trans":
            return "▽"
        if self.kind == "none":
            return "·"
        return f"{self.tap}/{self.hold}" if self.hold else self.tap


LAYER_FUNCS = {"MO": "", "TG": "toggle", "TO": "go to", "TT": "tap-toggle", "OSL": "one-shot", "DF": "default"}


class Labeller:
    def __init__(self, km: Keymap, style: str = "pc"):
        self.km = km
        self.mods = MOD_TEXT[style]
        self.names = {l.index: l.name or f"L{l.index}" for l in km.layers}

    def layer(self, arg: str) -> str:
        idx = self.km.layer_index(arg)
        if idx is None:
            return self.km.resolve(arg.strip())
        return self.names.get(idx, f"L{idx}")

    def modset(self, expr: str) -> set[str]:
        out: set[str] = set()
        for tok in re.split(r"[|\s]+", expr):
            tok = tok.strip().removeprefix("MOD_")
            if tok in MOD_ALIASES:
                out |= MOD_ALIASES[tok]
            elif tok in MOD_KEYS:
                out.add(MOD_KEYS[tok])
        return out

    def modtext(self, mods: set[str]) -> str:
        if mods == {"ctrl", "alt", "shift", "gui"}:
            return "Hyper"
        if mods == {"ctrl", "alt", "shift"}:
            return "Meh"
        return self.mods["join"].join(self.mods[m] for m in MOD_ORDER if m in mods)

    def basic(self, kc: str) -> str:
        kc = kc.strip()
        if kc in BASIC:
            return BASIC[kc]
        kc = kc.removeprefix("KC_")
        if kc in MOD_KEYS:
            return self.mods[MOD_KEYS[kc]]
        if kc in BASIC:
            return BASIC[kc]
        if re.fullmatch(r"[A-Z0-9]", kc) or re.fullmatch(r"F\d{1,2}", kc):
            return kc
        if m := re.fullmatch(r"KP_(\w+)", kc):
            return KEYPAD.get(m.group(1), m.group(1).title())
        if m := re.fullmatch(r"ST_MACRO_(\d+)", kc):
            return f"Macro {m.group(1)}"
        if m := re.fullmatch(r"HSV_(\d+)_(\d+)_(\d+)", kc):
            return "Colour"
        return kc.replace("_", " ").title() if len(kc) > 3 else kc

    def legend(self, raw: str) -> Legend:
        kc = raw.strip()
        if kc in ("KC_TRANSPARENT", "KC_TRNS", "_______", "TRANSPARENT"):
            return Legend(kind="trans")
        if kc in ("KC_NO", "XXXXXXX"):
            return Legend(kind="none")
        if kc in self.km.dual_funcs:
            tap, hold = self.km.dual_funcs[kc]
            hold_lg = self.legend(hold) if hold else Legend()
            kind = "layer" if hold_lg.kind == "layer" else "mod"
            return Legend(self.legend(tap).text(), hold_lg.tap if hold_lg.kind == "layer" else hold_lg.text(), kind)
        kc = self.km.resolve(kc)
        c = call(kc)
        if c is None:
            return Legend(self.basic(kc))
        fn, args = c
        if fn in LAYER_FUNCS and len(args) == 1:
            return Legend(self.layer(args[0]), LAYER_FUNCS[fn], "layer")
        if fn == "LT" and len(args) == 2:
            return Legend(self.legend(args[1]).text(), self.layer(args[0]), "layer")
        if fn == "MT" and len(args) == 2:
            return Legend(self.legend(args[1]).text(), self.modtext(self.modset(args[0])), "mod")
        if fn.endswith("_T") and len(args) == 1:
            return Legend(self.legend(args[0]).text(), self.modtext(self.modset(fn[:-2])), "mod")
        if fn == "OSM" and len(args) == 1:
            return Legend(self.modtext(self.modset(args[0])), "one-shot", "mod")
        if fn == "TD":
            n = re.sub(r"\D", "", args[0]) if args else ""
            return Legend(f"Dance {n}".strip(), "", "special")
        if fn in MOD_ALIASES and len(args) == 1:
            mods, inner = set(MOD_ALIASES[fn]), args[0]
            while (nc := call(inner)) and nc[0] in MOD_ALIASES and len(nc[1]) == 1:
                mods |= MOD_ALIASES[nc[0]]
                inner = nc[1][0]
            prefix = self.modtext(mods)
            sep = self.mods["join"] if self.mods["join"] and prefix not in ("Hyper", "Meh") else ("+" if prefix in ("Hyper", "Meh") else "")
            return Legend(f"{prefix}{sep}{self.legend(inner).text()}")
        return Legend(kc.replace("_", " "), "", "special")

    def targets(self, raw: str) -> list[tuple[str, int]]:
        """Layer-changing behaviour of a key: [(func, layer index)]."""
        raw = raw.strip()
        if raw in self.km.dual_funcs:
            return [t for part in self.km.dual_funcs[raw] if part for t in self.targets(part)]
        if raw.startswith("DUAL_FUNC_"):
            return []  # unparsed Oryx dual function: its LT() layer is a dummy
        kc = self.km.resolve(raw)
        c = call(kc)
        if not c:
            return []
        fn, args = c
        if (fn in LAYER_FUNCS and len(args) == 1) or (fn == "LT" and len(args) == 2):
            idx = self.km.layer_index(args[0])
            if idx is not None:
                return [(fn, idx)]
        return []


# --------------------------------------------------------------------------
# Physical geometry (from `qmk info -f json`)
# --------------------------------------------------------------------------


@dataclass
class Geometry:
    keys: list[dict]  # info.json layout entries {matrix: [r, c], x, y}; empty without info
    leds: list[list[int]]  # led index -> matrix [r, c]
    grid: list[list[int | None]]  # printed rows of layout indices, halves side by side
    split_col: int  # printed column where the right half starts
    pos: dict[int, str]  # layout index -> "L2.3"

    @classmethod
    def build(cls, info: dict, km: "Keymap") -> Geometry | None:
        """Lay keys out in rows the way Oryx prints them, split into halves.

        Rows come from the line structure of the LAYOUT(...) call (Oryx prints
        one physical row per line); horizontal slots come from the x positions
        in the keyboard's info.json when available.
        """
        nkeys = len(km.layers[0].keys)
        layouts = (info or {}).get("layouts", {})
        chosen = layouts.get(km.layout_macro) or next(
            (v for v in layouts.values() if len(v.get("layout", [])) == nkeys), None
        )
        keys = chosen["layout"] if chosen and len(chosen.get("layout", [])) == nkeys else []
        leds = [e.get("matrix") for e in (info or {}).get("rgb_matrix", {}).get("layout", [])] if keys else []
        sizes = km.row_sizes or ([10] * (nkeys // 10) + ([nkeys % 10] if nkeys % 10 else []))

        halves: list[tuple[list[int], list[int]]] = []
        i = 0
        for n in sizes:
            idx = list(range(i, i + n))
            halves.append((idx[: n // 2], idx[n // 2 :]))
            i += n

        def place(rows: list[list[int]]) -> list[list[int | None]]:
            if not keys:
                return [list(r) for r in rows]
            base = min((keys[k]["x"] for r in rows for k in r), default=0)
            out = []
            for r in rows:
                slots: list[int | None] = []
                for k in sorted(r, key=lambda k: keys[k]["x"]):
                    col = max(int(round(keys[k]["x"] - base)), len(slots))
                    slots.extend([None] * (col - len(slots)))
                    slots.append(k)
                out.append(slots)
            return out

        left = place([l for l, _ in halves])
        right = place([r for _, r in halves])
        lw = max((len(r) for r in left), default=0)
        grid: list[list[int | None]] = []
        pos: dict[int, str] = {}
        # Positions count keys within each half-row in Oryx's order, so they don't
        # depend on info.json (sync commit messages and docs always agree).
        for ri, (lsrc, rsrc) in enumerate(halves):
            for ci, k in enumerate(lsrc):
                pos[k] = f"L{ri}.{ci}"
            for ci, k in enumerate(rsrc):
                pos[k] = f"R{ri}.{ci}"
        for lrow, rrow in zip(left, right):
            grid.append(lrow + [None] * (lw - len(lrow)) + rrow)
        return cls(keys=keys, leds=leds, grid=grid, split_col=lw, pos=pos)

    def led_to_key(self) -> dict[int, int]:
        by_matrix = {tuple(k["matrix"]): i for i, k in enumerate(self.keys)}
        return {led: by_matrix[tuple(m)] for led, m in enumerate(self.leds) if m and tuple(m) in by_matrix}


def hsv_hex(h: int, s: int, v: int) -> str | None:
    if v == 0:
        return None
    r, g, b = colorsys.hsv_to_rgb(h / 255, s / 255, 1.0)
    return f"{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def key_colours(km: Keymap, geo: Geometry) -> dict[int, dict[int, str]]:
    """layer index -> {layout index: rrggbb} from the Oryx ledmap."""
    if not geo.leds:
        return {}
    l2k = geo.led_to_key()
    out: dict[int, dict[int, str]] = {}
    for layer, triples in km.ledmap.items():
        colours = {}
        for led, hsv in enumerate(triples):
            if led in l2k and (hx := hsv_hex(*hsv)):
                colours[l2k[led]] = hx
        if colours:
            out[layer] = colours
    return out


# --------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------


def lint(km: Keymap, lab: Labeller) -> list[tuple[str, str]]:
    """Return [(level, message)] where level is 'error' or 'warn'."""
    issues: list[tuple[str, str]] = []
    valid = {l.index for l in km.layers}
    reached: dict[int, set[str]] = {i: set() for i in valid}
    for layer in km.layers:
        for raw in layer.keys:
            for fn, tgt in lab.targets(raw):
                if tgt not in valid:
                    issues.append(("error", f"{layer.name}: `{raw}` points at layer {tgt}, which does not exist"))
                elif tgt != layer.index:
                    reached[tgt].add(fn)
    for _a, _b, c in km.tri_layers:
        idx = km.layer_index(c)
        if idx in reached:
            reached[idx].add("TRI")
    for layer in km.layers[1:]:
        how = reached.get(layer.index, set())
        if not how:
            issues.append(("warn", f"{layer.name}: no key reaches this layer (fine if a macro, combo or custom code does)"))
        elif how <= {"TO", "TG", "DF"}:
            lower = [l for l in km.layers if l.index < layer.index][::-1]

            def effective(ki: int, raw: str) -> str:
                # A transparent key acts as the nearest lower layer's key at that
                # position, so a TG() underneath is a way out too.
                for other in lower:
                    if lab.legend(raw).kind != "trans":
                        break
                    raw = other.keys[ki] if ki < len(other.keys) else raw
                return raw

            exits = any(
                fn in ("TO", "TG", "DF") and (tgt != layer.index or fn == "TG")
                for ki, raw in enumerate(layer.keys)
                for fn, tgt in lab.targets(effective(ki, raw))
            )
            if not exits:
                issues.append(("warn", f"{layer.name}: entered with {'/'.join(sorted(how))} but has no TO/TG key to leave"))
    base = km.layers[0]
    trans = sum(1 for k in base.keys if lab.legend(k).kind == "trans")
    if trans:
        issues.append(("warn", f"{base.name}: {trans} transparent key(s) on the base layer do nothing"))
    return issues


# --------------------------------------------------------------------------
# Output: markdown view
# --------------------------------------------------------------------------

CELL = 9


def _cell(text: str) -> str:
    text = text if len(text) <= CELL else text[: CELL - 1] + "…"
    return text.center(CELL)


def grid_text(layer: Layer, lab: Labeller, geo: Geometry) -> str:
    legends = [lab.legend(k).text() for k in layer.keys]
    lines = []
    for row in geo.grid:
        left = "".join(f"[{_cell(legends[i])}]" if i is not None else " " * (CELL + 2) for i in row[: geo.split_col])
        right = "".join(f"[{_cell(legends[i])}]" if i is not None else " " * (CELL + 2) for i in row[geo.split_col :])
        lines.append(f"{left}    {right}".rstrip())
    return "\n".join(lines)


def access_map(km: Keymap, lab: Labeller, geo: Geometry) -> list[dict]:
    """For each layer, the keys on other layers that lead to it."""
    out = []
    for layer in km.layers:
        how = []
        for other in km.layers:
            if other.index == layer.index:
                continue
            for ki, raw in enumerate(other.keys):
                for fn, tgt in lab.targets(raw):
                    if tgt == layer.index:
                        lg = lab.legend(raw)
                        what = f"hold {lg.tap}" if lg.hold == layer.name else FUNC_WORDS.get(fn, fn)
                        how.append(f"{what} on {other.name} ({geo.pos.get(ki, f'#{ki}')})")
        for a, b, c in km.tri_layers:
            if km.layer_index(c) == layer.index:
                how.append(f"hold {lab.layer(a)} and {lab.layer(b)} together (tri-layer)")
        if layer is km.layers[0]:
            how.insert(0, "base layer")
        out.append({"name": layer.name, "index": layer.index, "how": how})
    return out


FUNC_WORDS = {
    "MO": "hold the layer key",
    "LT": "hold the layer key",
    "TT": "hold or double-tap the layer key",
    "OSL": "tap the one-shot key",
    "TG": "toggle key",
    "TO": "switch key",
    "DF": "default-layer key",
}


def render_markdown(km: Keymap, lab: Labeller, geo: Geometry, meta: dict, svg_name: str | None, light: dict | None = None) -> str:
    title = meta.get("title") or "Keymap"
    out = [f"# {title}", ""]
    facts = []
    if meta.get("layout_id"):
        facts.append(f"Oryx layout `{meta['layout_id']}`")
    if meta.get("revision"):
        facts.append(f"revision `{meta['revision']}`")
    if meta.get("qmk_version"):
        facts.append(f"firmware v{meta['qmk_version']}")
    if facts:
        out += [" · ".join(facts), ""]
    out += [
        "> Generated by `make render` from `layout/keymap.c`. Do not edit by hand.",
        "> `▽` = transparent (falls through), `·` = no key, `tap/hold` = dual role.",
        "> Positions read `L2.3` = left half, row 2, fourth key from the left (all 0-based).",
        "",
    ]
    if svg_name:
        out += [f"![{title}]({svg_name})", ""]

    out += ["## Layer access", ""]
    for entry in access_map(km, lab, geo):
        how = "; ".join(entry["how"]) or "not reachable from any key"
        out.append(f"- **{entry['name']}** (layer {entry['index']}): {how}")
    out.append("")

    out += lighting_markdown(light)

    issues = lint(km, lab)
    if issues:
        out += ["## Checks", ""] + [f"- {'❌' if lvl == 'error' else '⚠️'} {msg}" for lvl, msg in issues] + [""]

    for layer in km.layers:
        out += [f"## {layer.name} (layer {layer.index})", "", "```text", grid_text(layer, lab, geo), "```", ""]
        special = []
        for ki, raw in enumerate(layer.keys):
            lg = lab.legend(raw)
            if lg.hold or lg.kind == "special" or raw in km.dual_funcs:
                where = geo.pos.get(ki, f"#{ki}")
                special.append(f"| {where} | {lg.text()} | `{raw}` |")
        if special:
            out += ["| Pos | Legend | Keycode |", "|---|---|---|", *special, ""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# Output: keymap-drawer YAML + SVG
# --------------------------------------------------------------------------

SVG_STYLE = """
/* oryx-overlay: keys are tinted with the colour Oryx lights them */
svg.keymap { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
text.label { font-size: 15px; font-weight: 700; letter-spacing: 0.02em; }
text.hold { font-size: 9.5px; letter-spacing: 0.03em; opacity: 0.75; }
text.footer { font-size: 12px; opacity: 0.6; }
rect.key { stroke-width: 1; }
rect.key.trans { opacity: 0.55; }
text.trans { opacity: 0.45; }
rect.key.layer { stroke: #6d5dfc; stroke-width: 2; }
"""


def drawer_yaml(km: Keymap, lab: Labeller, geo: Geometry, colours: dict[int, dict[int, str]]) -> tuple[str, str]:
    """Return (keymap YAML, extra CSS) for keymap-drawer."""
    held: dict[int, set[int]] = {}
    for layer in km.layers:
        for ki, raw in enumerate(layer.keys):
            for fn, tgt in lab.targets(raw):
                if fn in ("MO", "LT", "TT", "OSL") and tgt != layer.index:
                    held.setdefault(tgt, set()).add(ki)

    css = [SVG_STYLE]
    used: set[str] = set()
    layers_yaml = []
    for layer in km.layers:
        lines = [f"  {json.dumps(layer.name, ensure_ascii=False)}:"]
        for ki, raw in enumerate(layer.keys):
            lg = lab.legend(raw)
            classes = []
            if lg.kind == "trans":
                classes.append("trans")
            elif lg.kind in ("layer", "mod", "special"):
                classes.append(lg.kind)
            if ki in held.get(layer.index, set()):
                classes.append("held")
            hx = colours.get(layer.index, {}).get(ki)
            if hx:
                classes.append(f"led-{hx}")
                used.add(hx)
            entry = {"t": "▽" if lg.kind == "trans" else ("" if lg.kind == "none" else lg.tap)}
            if lg.hold:
                entry["h"] = lg.hold
            if classes:
                entry["type"] = " ".join(classes)
            lines.append(f"  - {json.dumps(entry, ensure_ascii=False)}")
        layers_yaml.append("\n".join(lines))

    for hx in sorted(used):
        r, g, b = (int(hx[i : i + 2], 16) for i in (0, 2, 4))
        css.append(
            f"rect.key.led-{hx}, rect.key.led-{hx}.held {{ fill: rgba({r},{g},{b},0.30); stroke: rgba({r},{g},{b},0.95); stroke-width: 1.5; opacity: 1; }}"
        )
    return "layers:\n" + "\n".join(layers_yaml) + "\n", "\n".join(css)


def run_drawer(yaml_path: Path, info_path: Path, geo_macro: str, css: str, footer: str, out: Path, select: str | None, columns: int = 1) -> bool:
    exe = shutil.which("keymap")
    if not exe:
        print("note: keymap-drawer not installed (pip install keymap-drawer); skipping SVG", file=sys.stderr)
        return False
    config = {
        "draw_config": {
            "dark_mode": "auto",
            "key_w": 62,
            "key_h": 58,
            "key_rx": 7,
            "key_ry": 7,
            "split_gap": 40,
            "n_columns": columns,
            "append_colon_to_layer_header": False,
            "svg_extra_style": css,
            "footer_text": footer,
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as cfg:
        json.dump(config, cfg, ensure_ascii=False)  # JSON is valid YAML
    cmd = [exe, "-c", cfg.name, "draw", str(yaml_path), "-j", str(info_path), "-l", geo_macro, "-o", str(out)]
    if select:
        cmd[cmd.index("-o"):cmd.index("-o")] = ["-s", select]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    Path(cfg.name).unlink(missing_ok=True)
    if res.returncode != 0:
        print(f"keymap-drawer failed:\n{res.stderr}", file=sys.stderr)
        return False
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def read_source(spec: str) -> str:
    """A path, or REF:path read from git (e.g. HEAD~1:layout/keymap.c)."""
    p = Path(spec)
    if p.exists():
        return p.read_text(encoding="utf-8")
    if ":" in spec:
        res = subprocess.run(["git", "show", spec], capture_output=True, text=True, encoding="utf-8")
        if res.returncode == 0:
            return res.stdout
        raise SystemExit(f"git show {spec} failed: {res.stderr.strip()}")
    raise SystemExit(f"no such file: {spec}")


def load_json(path: str | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def load_all(args) -> tuple[Keymap, Labeller, Geometry, dict, dict]:
    km = parse_keymap(read_source(args.keymap))
    meta = load_json(args.meta)
    assign_layer_names(km, meta)
    info = load_json(args.info)
    geo = Geometry.build(info, km)
    return km, Labeller(km, args.style), geo, meta, info


# --------------------------------------------------------------------------
# Lighting: which RGB Matrix effects the firmware has, for the viewer's gallery
# --------------------------------------------------------------------------

EFFECTS_JS = Path(__file__).parent / "rgb_effects.js"


def effect_catalogue() -> list[tuple[str, str]]:
    """(key, label) for every ported effect, in QMK's RGB Mode cycle order."""
    text = EFFECTS_JS.read_text(encoding="utf-8")
    start = text.index("const EFFECTS = [")
    body = text[start:text.index("].map(([key, label, opts, run])", start)]
    return re.findall(r'\[\s*"([a-z_]+)",\s*"([^"]+)"', body)


def rgb_config_ops(text: str) -> list[tuple[str, str, str]]:
    """(op, NAME, value) for #define/#undef lines in a config.h, in order."""
    src = strip_comments(text or "")
    return [(m.group(1), m.group(2), (m.group(3) or "").strip())
            for m in re.finditer(r"^[ \t]*#[ \t]*(define|undef)[ \t]+(\w+)(?:[ \t]+(.*))?$", src, re.M)]


def tap_keycode(km: Keymap, raw: str) -> str:
    """The basic keycode a key sends when tapped (KC_A for MT(MOD_LGUI, KC_A))."""
    raw = raw.strip()
    if raw in km.dual_funcs:
        raw = km.dual_funcs[raw][0]
    kc = km.resolve(raw)
    for _ in range(4):
        c = call(kc)
        if not c or not c[1]:
            break
        kc = km.resolve(c[1][-1])
    return kc


def lighting_data(km: Keymap, lab: Labeller, geo: Geometry, info: dict, configs: list[str]) -> dict | None:
    rgb = (info or {}).get("rgb_matrix") or {}
    leds = rgb.get("layout") or []
    if not leds or not geo.keys:
        return None
    board = {k for k, on in (rgb.get("animations") or {}).items() if on}
    enabled = set(board)
    startup = {}
    timeout = None
    for text in configs:  # Oryx's config.h, then custom/config.h (appended after it at build time)
        for op, name, value in rgb_config_ops(text):
            if name.startswith("ENABLE_RGB_MATRIX_"):
                key = name.removeprefix("ENABLE_RGB_MATRIX_").lower()
                (enabled.add if op == "define" else enabled.discard)(key)
            elif name == "RGB_MATRIX_TIMEOUT":
                timeout = int(value) if op == "define" and value.isdigit() else None
            elif name in ("RGB_MATRIX_STARTUP_SPD", "RGB_MATRIX_DEFAULT_SPD", "RGB_MATRIX_STARTUP_HUE",
                          "RGB_MATRIX_DEFAULT_HUE", "RGB_MATRIX_STARTUP_SAT", "RGB_MATRIX_DEFAULT_SAT") and op == "define":
                if value.isdigit():
                    startup[name.split("_")[-1].lower()] = int(value)
    catalogue = effect_catalogue()
    cycle = [k for k, _ in catalogue if k == "solid_color" or k in enabled]
    effects = []
    for key, label in catalogue:
        if key != "solid_color" and key not in board and key not in enabled:
            continue  # not something this board can run
        status = "on" if key in cycle else ("oryx" if key in board else "off")
        effects.append({"key": key, "label": label, "status": status,
                        "define": f"ENABLE_RGB_MATRIX_{key.upper()}",
                        "cycle": cycle.index(key) if key in cycle else None})

    by_matrix = {tuple(l["matrix"]): i for i, l in enumerate(leds) if l.get("matrix")}
    base = km.layers[0]
    keys = []
    for ki, k in enumerate(geo.keys):
        raw = base.keys[ki] if ki < len(base.keys) else ""
        lg = lab.legend(raw) if raw else Legend(kind="none")
        keys.append({
            "x": k["x"], "y": k["y"], "w": k.get("w", 1), "h": k.get("h", 1),
            "r": k.get("r", 0), "rx": k.get("rx", k["x"]), "ry": k.get("ry", k["y"]),
            "led": by_matrix.get(tuple(k.get("matrix") or ())),
            "label": "" if lg.kind in ("trans", "none") else lg.tap,
            "kc": tap_keycode(km, raw) if raw else "",
        })
    def find_keys(codes: tuple[str, ...]) -> list[str]:
        return [f"{layer.name} {geo.pos.get(ki, f'#{ki}')}"
                for layer in km.layers for ki, raw in enumerate(layer.keys) if km.resolve(raw) in codes]

    mode_keys = find_keys(("RGB_MOD", "RGB_MODE_FORWARD", "RM_NEXT", "QK_RGB_MATRIX_MODE_NEXT"))
    toggle_keys = find_keys(("TOGGLE_LAYER_COLOR",))
    return {
        "leds": [{"x": l.get("x", 0), "y": l.get("y", 0), "flags": l.get("flags", 0), "matrix": l.get("matrix")} for l in leds],
        "center": rgb.get("center_point") or [112, 32],
        "matrix": [info["matrix_size"]["rows"], info["matrix_size"]["cols"]] if info.get("matrix_size") else None,
        "keys": keys,
        "effects": effects,
        "startup": {"speed": startup.get("spd", 127), "h": startup.get("hue", 0), "s": startup.get("sat", 255)},
        "modeKeys": mode_keys,
        "toggleKeys": toggle_keys,
        # Oryx's rgb_matrix_indicators_user paints every LED of a coloured layer (uncoloured keys go dark),
        # so effects only show on layers without colours, or after Toggle Layer Colors.
        "oryxColours": [lab.names[i] for i in sorted(km.ledmap)],
        "baseColoured": km.layers[0].index in km.ledmap,
        "timeoutMs": timeout,
    }


def lighting_markdown(light: dict | None) -> list[str]:
    if not light:
        return []
    on = [e["label"] for e in light["effects"] if e["status"] == "on"]
    off = [e for e in light["effects"] if e["status"] != "on"]
    out = ["## Lighting", "",
           f"RGB Matrix effects in this firmware, in RGB Mode cycle order ({len(on)}): " + ", ".join(on) + ".", ""]
    if light["modeKeys"]:
        out += ["RGB Mode key: " + "; ".join(light["modeKeys"]) + ".", ""]
    if off:
        out += ["Not in this firmware (turn on in Oryx, or add the define to `custom/config.h`): "
                + ", ".join(f"{e['label']} (`{e['define']}`)" for e in off) + ".", ""]
    if light["oryxColours"]:
        out += ["Layers with Oryx key colours cover the effect completely while active (uncoloured keys go dark): "
                + ", ".join(light["oryxColours"]) + "."
                + (" The base layer is one of them, so effects only show after Toggle Layer Colors"
                   + (f" ({'; '.join(light['toggleKeys'])})." if light["toggleKeys"] else " (no such key in this layout yet).")
                   if light["baseColoured"] else ""), ""]
    if light["timeoutMs"]:
        out += [f"LEDs switch off after {light['timeoutMs'] // 1000} s idle (`RGB_MATRIX_TIMEOUT`).", ""]
    out += ["Preview every effect on this board in `docs/index.html` (Lighting tab).", ""]
    return out


def dominant_colour(colours: dict[int, str]) -> str | None:
    if not colours:
        return None
    counts: dict[str, int] = {}
    for hx in colours.values():
        counts[hx] = counts.get(hx, 0) + 1
    return "#" + max(counts, key=counts.get)


def render_viewer(km: Keymap, lab: Labeller, geo: Geometry, meta: dict, colours: dict[int, dict[int, str]], svgs: list[str], light: dict | None = None) -> str:
    template = (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")
    chips = [("board", meta.get("geometry") or km.layout_macro.removeprefix("LAYOUT_") or "?")]
    if meta.get("qmk_version"):
        chips.append(("firmware", f"v{meta['qmk_version']}"))
    if meta.get("revision"):
        chips.append(("revision", meta["revision"]))
    chips.append(("layers", str(len(km.layers))))
    layers = []
    for layer, svg in zip(km.layers, svgs):
        keys = []
        for ki, raw in enumerate(layer.keys):
            lg = lab.legend(raw)
            resolved = km.resolve(raw)
            if raw in km.dual_funcs:
                tap, hold = km.dual_funcs[raw]
                resolved = f"tap {tap}, hold {hold}" if hold else f"tap {tap}"
            keys.append({"pos": geo.pos.get(ki, f"#{ki}"), "raw": raw, "resolved": resolved, "tap": lg.tap, "hold": lg.hold, "kind": lg.kind})
        layers.append({"name": layer.name, "index": layer.index, "colour": dominant_colour(colours.get(layer.index, {})), "svg": svg, "keys": keys})
    data = {
        "title": meta.get("title") or "Keymap",
        "chips": chips,
        "layers": layers,
        "access": access_map(km, lab, geo),
        "checks": lint(km, lab),
        "lighting": light,
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = (meta.get("title") or "Keymap").replace("&", "&amp;").replace("<", "&lt;")
    engine = EFFECTS_JS.read_text(encoding="utf-8").replace("</", "<\\/") if light else ""
    return template.replace("__TITLE__", title).replace("/*__RGB_ENGINE__*/", engine).replace("__DATA__", blob)


def cmd_render(args) -> int:
    km, lab, geo, meta, info = load_all(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("keymap.svg", "keymap-base.svg", "keymap.yaml", "index.html"):
        (out / stale).unlink(missing_ok=True)
    colours = key_colours(km, geo)
    configs = []
    for cfg in [str(Path(args.keymap).parent / "config.h") if Path(args.keymap).exists() else None, args.custom_config]:
        if cfg and Path(cfg).exists():
            configs.append(Path(cfg).read_text(encoding="utf-8"))
    light = lighting_data(km, lab, geo, info, configs)

    svg_ok, failed = False, False
    if geo.keys and not args.no_svg:
        yaml_text, css = drawer_yaml(km, lab, geo, colours)
        yaml_path = out / "keymap.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")
        macro = next(k for k, v in info["layouts"].items() if v["layout"] is geo.keys)
        footer = " · ".join(x for x in [meta.get("title"), f"firmware v{meta['qmk_version']}" if meta.get("qmk_version") else None, "drawn by oryx-overlay + keymap-drawer"] if x)
        info_path = Path(args.info)
        # All or nothing: a half-written set of drawings is worse than none.
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = Path(tmp)
            ok = run_drawer(yaml_path, info_path, macro, css, footer, tmpd / "keymap.svg", None, columns=2 if len(km.layers) > 2 else 1)
            ok = ok and run_drawer(yaml_path, info_path, macro, css, "", tmpd / "keymap-base.svg", km.layers[0].name)
            svgs = []
            for layer in km.layers:
                if not ok:
                    break
                one = tmpd / f"layer-{layer.index}.svg"
                ok = run_drawer(yaml_path, info_path, macro, css, "", one, layer.name)
                if ok:
                    svgs.append(one.read_text(encoding="utf-8"))
            if ok:
                for name in ("keymap.svg", "keymap-base.svg"):
                    (out / name).write_text((tmpd / name).read_text(encoding="utf-8"), encoding="utf-8")
                (out / "index.html").write_text(render_viewer(km, lab, geo, meta, colours, svgs, light), encoding="utf-8")
                svg_ok = True
            else:
                failed = True
                yaml_path.unlink(missing_ok=True)
    elif not geo.keys:
        print("note: no keyboard info (needs the qmk CLI; see `make doctor`); text view only, no SVG", file=sys.stderr)

    (out / "keymap.md").write_text(render_markdown(km, lab, geo, meta, "keymap.svg" if svg_ok else None, light), encoding="utf-8")
    written = [p.name for p in (out / "keymap.md", out / "keymap.svg", out / "index.html") if p.exists()]
    print(f"wrote {out}/: {', '.join(written)}")
    if failed:
        print("error: drawing failed; wrote the text view only", file=sys.stderr)
        return 1
    return 0


def cmd_diff(args) -> int:
    old = parse_keymap(read_source(args.old))
    new = parse_keymap(read_source(args.new))
    meta = load_json(args.meta)
    assign_layer_names(old, meta)
    assign_layer_names(new, meta)
    info = load_json(args.info)
    geo = Geometry.build(info, new)
    lo, ln = Labeller(old, args.style), Labeller(new, args.style)
    olds = {l.index: l for l in old.layers}
    lines: list[str] = []
    for layer in new.layers:
        prev = olds.pop(layer.index, None)
        if prev is None:
            lines.append(f"- **{layer.name}**: new layer")
            continue
        for ki, (a, b) in enumerate(zip(prev.keys, layer.keys)):
            la, lb = lo.legend(a).text(), ln.legend(b).text()
            if a != b or la != lb:
                where = geo.pos.get(ki, f"#{ki}")
                lines.append(f"- **{layer.name}** {where}: `{la}` → `{lb}`" + (f"  (`{a}` → `{b}`)" if args.raw else ""))
    for layer in olds.values():
        lines.append(f"- **{layer.name}**: layer removed")
    if old.ledmap != new.ledmap:
        changed = sorted(set(old.ledmap) ^ set(new.ledmap) | {k for k in old.ledmap if old.ledmap.get(k) != new.ledmap.get(k)})
        lines.append(f"- LED colours changed on layer(s) {', '.join(str(c) for c in changed)}")
    print("\n".join(lines) if lines else "No keymap changes.")
    return 0


def cmd_lint(args) -> int:
    km, lab, _, _, _ = load_all(args)
    issues = lint(km, lab)
    for lvl, msg in issues:
        print(f"{lvl}: {msg}")
    if not issues:
        print("ok: no issues")
    return 1 if any(lvl == "error" for lvl, _ in issues) else 0


def cmd_parse(args) -> int:
    km, *_ = load_all(args)
    json.dump(km.to_json(), sys.stdout, indent=2)
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--style", choices=["pc", "mac"], default="pc", help="modifier legends: pc (Ctrl/Alt/Super) or mac (⌃⌥⌘)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, keymap=True):
        if keymap:
            p.add_argument("--keymap", default="layout/keymap.c", help="keymap.c path or REF:path")
        p.add_argument("--meta", default="layout/.oryx.json", help="Oryx metadata written by oryx-fetch.sh")
        p.add_argument("--info", default=None, help="`qmk info -f json` output for the keyboard (scripts pass build/info-<board>.json)")

    p = sub.add_parser("render", help="write keymap.md (+ keymap.yaml, keymap.svg with keymap-drawer)")
    common(p)
    p.add_argument("--out", default="docs")
    p.add_argument("--no-svg", action="store_true")
    p.add_argument("--custom-config", default="custom/config.h", help="your config.h overlay (effects you enable there count as built in)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("diff", help="key-level changes between two keymap.c versions")
    p.add_argument("old", help="path or git REF:path, e.g. HEAD~1:layout/keymap.c")
    p.add_argument("new", nargs="?", default="layout/keymap.c")
    p.add_argument("--raw", action="store_true", help="also show raw keycodes")
    common(p, keymap=False)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("lint", help="check for unreachable layers, dangling layer keys, traps")
    common(p)
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("parse", help="dump parsed keymap as JSON")
    common(p)
    p.set_defaults(func=cmd_parse)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
