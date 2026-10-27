"""Every third-party action is pinned to a commit SHA, with its tag as a comment."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Any line that mentions `uses:` as a key; validated below, never skipped.
USES = re.compile(r"^\s*(?:-\s*)?["']?uses["']?\s*:\s*(.*)$")


def unpinned(text, name="workflow"):
    """Return a description of each `uses:` line in text that isn't properly pinned."""
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        m = USES.match(line)
        if not m:
            continue
        value, _, comment = m.group(1).partition("#")
        ref = value.strip().strip("'\"")
        if ref.startswith("./"):
            continue
        if not re.fullmatch(r"[^\s@]+@[0-9a-f]{40}", ref) or not re.fullmatch(r"v\d+\.\d+\.\d+", comment.strip()):
            bad.append(f"{name}:{n}: {line.strip()}")
    return bad


def test_actions_pinned_to_sha_with_version_comment():
    wf = ROOT / ".github" / "workflows"
    files = sorted(list(wf.glob("*.yml")) + list(wf.glob("*.yaml")))
    assert files
    bad = [b for f in files for b in unpinned(f.read_text(), f.name)]
    assert bad == [], "pin to a 40-hex SHA with a '# vX.Y.Z' comment: " + "; ".join(bad)


SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def test_checker_accepts_pinned_and_local():
    assert unpinned(f"- uses: actions/checkout@{SHA} # v7.0.1\n    uses: ./.github/workflows/build.yml\n") == []


def test_checker_rejects_tag_with_explanatory_comment():
    assert unpinned("- uses: actions/checkout@v7 # checkout for tests\n")


def test_checker_rejects_sha_without_version_comment():
    assert unpinned(f"- uses: actions/checkout@{SHA}\n")
    assert unpinned(f"- uses: actions/checkout@{SHA} # latest\n")


def test_checker_rejects_quoted_key():
    assert unpinned('- "uses": actions/checkout@v7\n')
    assert unpinned("- 'uses': actions/checkout@v7\n")
    assert unpinned(f'- "uses": actions/checkout@{SHA} # v7.0.1\n') == []


def test_checker_requires_full_release_tag():
    assert unpinned(f"- uses: actions/checkout@{SHA} # v7\n")
    assert unpinned(f"- uses: actions/checkout@{SHA} # note v7.0.1\n")


def test_checker_rejects_odd_forms():
    assert unpinned("- uses: actions/checkout@v7\n")
    assert unpinned("- uses: actions/checkout\n")


def test_dependabot_watches_actions():
    text = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in text
