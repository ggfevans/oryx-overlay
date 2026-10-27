"""Every third-party action is pinned to a commit SHA, with its tag as a comment."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+?)(?:\s*#\s*(\S+))?\s*$")


def test_actions_pinned_to_sha_with_version_comment():
    bad = []
    files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert files
    for f in files:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            m = USES.match(line)
            if not m or m.group(1).startswith("./"):
                continue
            ref, comment = m.groups()
            if not re.search(r"@[0-9a-f]{40}$", ref) or not (comment and re.match(r"v\d", comment)):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert bad == [], "pin to a 40-hex SHA with a '# vX.Y.Z' comment: " + "; ".join(bad)


def test_dependabot_watches_actions():
    text = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in text
