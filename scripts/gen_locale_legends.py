#!/usr/bin/env python3
"""Regenerate scripts/locale_legends.json from QMK's keymap_extras/keymap_*.h.

Locale keycodes (DE_Z, FR_EACU, ...) are #defines onto the US keycodes, with the
character each one types in a trailing comment. The table is checked in because
`make lint`, `make diff` and sync commit messages all run without a QMK tree.

It holds, per prefix: `legends`, keycode name -> legend, and `layouts`, one map per
header of value -> legend, for the names in an Oryx i18n.h that no header has.

Usage: python3 scripts/gen_locale_legends.py [QMK_TREE]
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

from oryx_layout import canon_value

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).with_name("locale_legends.json")
FIRMWARE = "qmk_firmware-firmware25"  # the tree to read by default

DEFINE = re.compile(r"^#define[ \t]+([A-Z0-9]+)_(\w+)[ \t]+([^/\n]*)(?://[ \t]*(.*?))?[ \t]*$", re.M)
# Not locales: keymap_us.h's US aliases, each header's QMK_ version, Plover's steno keys.
SKIP_PREFIXES = {"KC", "QMK", "PD", "PV"}
MAX_LEGEND = 4  # longer comments are prose, not something that fits on a key

# Names in QMK's older headers, which exports without an i18n.h use. Each was matched by
# value: old keymap_german.h and keymap_french.h (QMK 0.7.100) against today's. They win
# over a name today's headers also have: DE_AE was Ä, but keymap_german_mac_iso.h's is Æ.
# Left out: FR_CCIRC (nothing today has its value) and FR_QUOT (a current name that used
# to mean the key FR_DQUO is now).
ALIASES = {
    "DE": {
        "AE": "ADIA", "OE": "ODIA", "UE": "UDIA", "LESS": "LABK", "MORE": "RABK", "QST": "QUES", "DQOT": "DQUO",
        "PARA": "SECT", "RING": "DEG", "SQ2": "SUP2", "SQ3": "SUP3",
    },
    "FR": {
        "AMP": "AMPR", "APOS": "QUOT", "LESS": "LABK", "GRTR": "RABK", "OVRR": "DEG", "UMLT": "DIAE", "MU": "MICR",
        "BULT": "CURR",
    },
}


def legend(comment: str) -> str | None:
    """What a header comment says the key types, or None if it isn't drawable."""
    if comment.startswith("(backslash"):  # the headers can't end a // line with one
        return "\\"
    text = re.sub(r"\s*\(.*\)$", "", comment).strip()  # "^ (dead)" -> "^"
    if not text or len(text) > MAX_LEGEND or not text.isprintable():
        return None
    # A combining mark alone has nothing to sit on, and no width in keymap.md's grids.
    return None if all(unicodedata.combining(c) for c in text) else text


def defines(path: Path) -> list[tuple[str, str, str, str]]:
    """(prefix, name, value, comment) of a header's locale keycodes, with values
    expanded down to US keycodes: DE_EXLM is "S(1)", not "S(DE_1)"."""
    seen: dict[str, str] = {}
    out = []
    for prefix, name, value, comment in DEFINE.findall(path.read_text(encoding="utf-8")):
        if prefix in SKIP_PREFIXES:
            continue
        value = canon_value(re.sub(r"\b[A-Z0-9]+_\w+", lambda m: seen.get(m.group(), m.group()), value))
        seen[f"{prefix}_{name}"] = value
        out.append((prefix, name, value, comment))
    return out


def headers_in(folder: Path) -> list[Path]:
    """Shortest name first, so the plain national layout leads those sharing a prefix."""
    return sorted(folder.glob("keymap_*.h"), key=lambda p: (len(p.name), p.name))


def table(headers: Path) -> dict[str, dict[str, str]]:
    """{prefix: {name: legend}}. Headers can share a prefix (keymap_french.h,
    keymap_canadian_french.h, keymap_french_afnor.h are all FR_), and without an i18n.h
    nothing says which is meant, so where they disagree the shortest header name wins:
    the plain national layout. A key with no drawable legend is called by its name."""
    found: dict[str, dict[str, str]] = {}
    named: dict[str, dict[str, str]] = {}
    for path in headers_in(headers):
        for prefix, name, _, comment in defines(path):
            named.setdefault(prefix, {}).setdefault(name, name)
            if text := legend(comment):
                found.setdefault(prefix, {}).setdefault(name, text)
    out = {prefix: {**named[prefix], **found.get(prefix, {})} for prefix in sorted(named)}
    for prefix, names in ALIASES.items():
        legends = out.get(prefix, {})
        legends.update({old: legends[new] for old, new in names.items() if new in legends})
    return out


def layouts(headers: Path) -> dict[str, list[dict[str, str]]]:
    """{prefix: [{value: legend}, ...]}, one map per distinct header, plain layout first."""
    out: dict[str, list[dict[str, str]]] = {}
    for path in headers_in(headers):
        found: dict[str, dict[str, str]] = {}
        named: dict[str, dict[str, str]] = {}
        for prefix, name, value, comment in defines(path):
            named.setdefault(prefix, {}).setdefault(value, name)
            if text := legend(comment):
                found.setdefault(prefix, {}).setdefault(value, text)
        for prefix in named:
            layout = {**named[prefix], **found.get(prefix, {})}
            if layout not in out.setdefault(prefix, []):
                out[prefix].append(layout)
    return dict(sorted(out.items()))


def main() -> int:
    tree = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / FIRMWARE
    headers = tree / "quantum" / "keymap_extras"
    if not headers.is_dir():
        print(f"error: {headers} not found (run 'make update-qmk')", file=sys.stderr)
        return 1
    locales, maps = table(headers), layouts(headers)
    note = f"Generated by scripts/gen_locale_legends.py from {tree.resolve().name} quantum/keymap_extras/keymap_*.h, GPL-2.0-or-later."

    def block(data: dict) -> str:
        return ",\n".join(f"{json.dumps(k)}:" + json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True) for k, v in data.items())

    OUT.write_text(f'{{"_source": {json.dumps(note)},\n"legends": {{\n{block(locales)}\n}},\n"layouts": {{\n{block(maps)}\n}}}}\n', encoding="utf-8")
    print(f"wrote {OUT} ({sum(map(len, locales.values()))} keycodes, {sum(map(len, maps.values()))} layouts, {len(locales)} prefixes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
