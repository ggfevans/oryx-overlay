#!/usr/bin/env python3
"""Download a layout's QMK source from Oryx into a directory.

Uses Oryx's public GraphQL endpoint to find the latest revision, then fetches
the source zip that Oryx's "Download source" button serves. The layout must
be public in Oryx.

Writes the *_source/ files (keymap.c, config.h, rules.mk, keymap.json, ...)
into --out, replacing what was there, plus .oryx.json with metadata. Prints
KEY=VALUE lines on stdout for scripts and CI.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

GRAPHQL = "https://oryx.zsa.io/graphql"
SOURCE = "https://oryx.zsa.io/source/{revision}"
UA = "oryx-overlay (+https://github.com/topics/oryx)"

QUERY = """
query getLayout($hashId: String!, $revisionId: String!, $geometry: String) {
  layout(hashId: $hashId, geometry: $geometry, revisionId: $revisionId) {
    title
    geometry
    revision { hashId qmkVersion title createdAt layers { title position } }
  }
}
"""


def http(url: str, data: bytes | None = None, timeout: int = 60) -> bytes:
    headers = {"User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"error: {url} returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"error: could not reach {url}: {e.reason}") from None


def fetch_layout(layout_id: str, geometry: str, revision: str = "latest") -> dict:
    body = json.dumps(
        {"query": QUERY, "variables": {"hashId": layout_id, "geometry": geometry, "revisionId": revision}}
    ).encode()
    reply = json.loads(http(GRAPHQL, body))
    layout = (reply.get("data") or {}).get("layout")
    if not layout:
        msg = "; ".join(e.get("message", "?") for e in reply.get("errors", [])) or "no data"
        raise SystemExit(
            f"error: Oryx could not find layout '{layout_id}' for geometry '{geometry}' ({msg}).\n"
            "  - Check ORYX_LAYOUT_ID: it's the part after /layouts/ in your Oryx URL.\n"
            "  - The layout must be public (Oryx > layout settings > privacy).\n"
            "  - Check KEYBOARD in oryx.conf matches the board the layout is for."
        )
    return layout


def extract_source(zip_bytes: bytes, out: Path) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and "_source/" in m.filename]
        if not members:
            raise SystemExit("error: Oryx source zip had no *_source/ folder; has the layout been compiled?")
        out.mkdir(parents=True, exist_ok=True)
        # Replace previous export, keeping dotfiles (.gitkeep, .oryx.json).
        for old in out.iterdir():
            if old.is_file() and not old.name.startswith("."):
                old.unlink()
        written = []
        root = out.resolve()
        for m in members:
            rel = m.filename.split("_source/", 1)[1].replace("\\", "/")
            if not rel or rel.startswith("/") or ".." in Path(rel).parts or Path(rel).is_absolute():
                continue
            dest = out / rel
            if not dest.resolve().is_relative_to(root):  # e.g. through a symlink
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(m).replace(b"\r\n", b"\n")
            dest.write_bytes(data)
            written.append(rel)
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout-id", required=True)
    ap.add_argument("--geometry", required=True, help="moonlander, voyager, ergodox-ez, planck-ez")
    ap.add_argument("--revision", default="latest")
    ap.add_argument("--out", default="layout", type=Path)
    args = ap.parse_args(argv)

    layout = fetch_layout(args.layout_id, args.geometry, args.revision)
    rev = layout["revision"]
    files = extract_source(http(SOURCE.format(revision=rev["hashId"])), args.out)

    note = (rev.get("title") or "").strip()
    meta = {
        "layout_id": args.layout_id,
        "title": layout.get("title") or "",
        "geometry": layout.get("geometry") or args.geometry,
        "revision": rev["hashId"],
        "revision_created": rev.get("createdAt") or "",
        "qmk_version": str(rev.get("qmkVersion") or ""),
        "change_note": note,
        "layers": [
            {"position": l.get("position"), "title": l.get("title") or ""}
            for l in sorted(rev.get("layers") or [], key=lambda l: l.get("position") or 0)
        ],
    }
    (args.out / ".oryx.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"fetched '{meta['title']}' revision {meta['revision']} (firmware v{meta['qmk_version']}): {', '.join(files)}", file=sys.stderr)
    print(f"revision={meta['revision']}")
    print(f"qmk_version={meta['qmk_version']}")
    print(f"title={meta['title']}")
    print(f"change_note={' '.join(note.split())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
