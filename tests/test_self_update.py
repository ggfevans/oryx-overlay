"""Tests for scripts/self-update.sh (make self-update).

A local git repository stands in for the template upstream, with release tags.
Each user repo is a plain copy of a release with a fresh history, as "Use this
template" makes it, so nothing is shared with the upstream. No network needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="needs git and bash")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}

MANIFEST = "scripts/\nmodules/\nnew/\ntools.txt\nold.txt\ngone.txt\nCHANGELOG.md\n.oryx-overlay-manifest\n"
# A template that (wrongly) claims user-owned paths: they must still never be written.
HOSTILE = "layout/\ncustom/\ndocs/\noryx.conf\n.oryx-overlay-keep\n"
TOOLS = "".join(f"line {i}\n" for i in range(1, 21))
USER_FILES = {
    "layout/keymap.c": "/* oryx */\n",
    "custom/config.h": "#define MINE\n",
    "custom/modules/me/thing/qmk_module.json": "{}\n",
    "docs/keymap.md": "# mine\n",
    "oryx.conf": "ORYX_LAYOUT_ID=abc\n",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=check, env={**os.environ, **GIT_ENV}).stdout


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit_release(up: Path, tag: str | None, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        if text is None:
            (up / rel).unlink()
        else:
            write(up / rel, text)
    git(up, "add", "-A")
    git(up, "commit", "-qm", tag or "wip")
    if tag:
        git(up, "tag", tag)
    return git(up, "rev-parse", "HEAD").strip()


def make_upstream(path: Path) -> Path:
    """v1.0.0: the real scripts/ plus a few tooling files."""
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    shutil.copytree(ROOT / "scripts", path / "scripts")
    commit_release(path, "v1.0.0", {
        ".oryx-overlay-manifest": MANIFEST,
        "tools.txt": TOOLS,
        "old.txt": "old\n",
        "gone.txt": "gone\n",
        "modules/me/thing/qmk_module.json": "{}\n",
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 1.0.0\n\n- First.\n",
    })
    return path


def release_v2(up: Path, tools: str = TOOLS.replace("line 18\n", "line 18 (template)\n")) -> str:
    """v2.0.0: tools.txt edited, a new file, two files deleted, hostile paths, and a
    change to self-update.sh itself (appended, as if bash read past main)."""
    script = (up / "scripts/self-update.sh").read_text() + 'echo "appended line ran" >&2\n'
    files: dict[str, str | None] = {
        ".oryx-overlay-manifest": MANIFEST + HOSTILE,
        "tools.txt": tools,
        "new/added.txt": "new\n",
        "old.txt": None,
        "gone.txt": None,
        "scripts/self-update.sh": script,
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 2.0.0\n\n- Second.\n\n## 1.0.0\n\n- First.\n",
    }
    for rel in USER_FILES:
        files[rel] = "from the template\n"
    files[".oryx-overlay-keep"] = "tools.txt\n"
    return commit_release(up, "v2.0.0", files)


def make_user(path: Path, up: Path, ref: str = "v1.0.0", version: str | None = "v1.0.0") -> Path:
    """A "Use this template" copy of ref: same files, unrelated history."""
    path.mkdir()
    git(up, "archive", "-o", str(path / "t.tar"), ref)
    subprocess.run(["tar", "-xf", "t.tar"], cwd=path, check=True)
    (path / "t.tar").unlink()
    for rel, text in USER_FILES.items():
        write(path / rel, text)
    if version:
        write(path / ".oryx-overlay-version", version + "\n")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "my layout")
    return path


def run(repo: Path, up: Path, **env: str) -> subprocess.CompletedProcess:
    full = {**os.environ, **GIT_ENV, "ORYX_OVERLAY_UPSTREAM": str(up), **env}
    for k in ("TO", "FROM"):
        if k not in env:
            full.pop(k, None)
    return subprocess.run(["bash", str(repo / "scripts/self-update.sh")], cwd=repo,
                          capture_output=True, text=True, env=full)


@pytest.fixture
def up(tmp_path):
    return make_upstream(tmp_path / "upstream")


def test_unrelated_history_and_untouched_files_replaced(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    assert (user / "tools.txt").read_text() == (up / "tools.txt").read_text()
    assert (user / "scripts/self-update.sh").read_text() == (up / "scripts/self-update.sh").read_text()
    assert "appended line ran" not in r.stderr  # the rewritten script didn't run on
    assert (user / ".oryx-overlay-version").read_text() == "v2.0.0\n"
    assert "## 2.0.0" in r.stderr and "- Second." in r.stderr and "- First." not in r.stderr
    assert "template: upgrade to v2.0.0" in r.stderr
    assert git(user, "for-each-ref", "refs/oryx-overlay/") == ""  # fetched refs cleaned up
    assert git(user, "log", "--format=%s") == "my layout\n"  # left uncommitted


def test_local_edit_merges_with_template_change(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    edited = TOOLS.replace("line 2\n", "line 2 (mine)\n")
    write(user / "tools.txt", edited)
    git(user, "commit", "-qam", "tweak tools")
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    text = (user / "tools.txt").read_text()
    assert "line 2 (mine)\n" in text and "line 18 (template)\n" in text and "<<<<<<<" not in text
    assert "1 merged" in r.stderr


def test_conflicting_edits_leave_markers_and_fail(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    write(user / "tools.txt", TOOLS.replace("line 18\n", "line 18 (mine)\n"))
    git(user, "commit", "-qam", "tweak tools")
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 3, r.stderr
    text = (user / "tools.txt").read_text()
    assert "<<<<<<< yours\nline 18 (mine)\n" in text and "line 18 (template)\n>>>>>>> template v2.0.0\n" in text
    assert "Conflicts" in r.stderr and "  tools.txt\n" in r.stderr
    # Everything else still went through, and the version moved with it.
    assert (user / "new/added.txt").exists()
    assert (user / ".oryx-overlay-version").read_text() == "v2.0.0\n"
    # The suggested way out puts everything back, added files included.
    assert "git reset --hard" in r.stderr
    git(user, "reset", "-q", "--hard")
    assert git(user, "status", "--porcelain", "--untracked-files=all") == ""
    assert (user / ".oryx-overlay-version").read_text() == "v1.0.0\n"


def test_user_paths_are_never_written(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    release_v2(up)
    before = {p.relative_to(user): p.read_bytes() for d in ("layout", "custom", "docs")
              for p in (user / d).rglob("*") if p.is_file()}
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    after = {p.relative_to(user): p.read_bytes() for d in ("layout", "custom", "docs")
             for p in (user / d).rglob("*") if p.is_file()}
    assert after == before
    assert (user / "oryx.conf").read_text() == USER_FILES["oryx.conf"]
    assert not (user / ".oryx-overlay-keep").exists()
    assert "skipped layout/keymap.c" in r.stderr and "skipped oryx.conf" in r.stderr
    # The bundled module is still in custom/modules: say it shadows the template's.
    assert "custom/modules/me/thing replaces the template's modules/me/thing" in r.stderr


def test_dirty_tree_is_refused(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    release_v2(up)
    write(user / "tools.txt", "edited\n")
    r = run(user, up)
    assert r.returncode != 0 and "uncommitted changes" in r.stderr
    assert not (user / "new").exists()
    git(user, "checkout", "--", ".")
    write(user / "notes.txt", "untracked\n")
    r = run(user, up)
    assert r.returncode != 0 and "uncommitted changes" in r.stderr
    assert (user / ".oryx-overlay-version").read_text() == "v1.0.0\n"
    # docs/ is regenerated by builds, so churn there is fine.
    (user / "notes.txt").unlink()
    write(user / "docs/keymap.md", "# redrawn\n")
    assert run(user, up).returncode == 0


def test_added_file_and_deleted_files(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    write(user / "gone.txt", "gone, but I changed it\n")
    git(user, "commit", "-qam", "edit gone")
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    assert (user / "new/added.txt").read_text() == "new\n"
    assert not (user / "old.txt").exists()  # unchanged locally: removed
    assert (user / "gone.txt").read_text() == "gone, but I changed it\n"  # changed: kept
    assert "kept gone.txt" in r.stderr
    # New files are intent-to-add, so commit -a takes the whole upgrade.
    git(user, "commit", "-qam", "template: upgrade to v2.0.0")
    assert git(user, "status", "--porcelain") == ""
    assert "new/added.txt" in git(user, "ls-files")


def test_keep_file_opts_out(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    write(user / ".oryx-overlay-keep", "# mine now\ntools.txt\n")
    git(user, "add", "-A")
    git(user, "commit", "-qm", "own tools")
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    assert (user / "tools.txt").read_text() == TOOLS
    assert (user / ".oryx-overlay-keep").read_text() == "# mine now\ntools.txt\n"


def test_unknown_base_and_explicit_refs(tmp_path, up):
    user = make_user(tmp_path / "user", up, version=None)
    sha = release_v2(up)
    r = run(user, up)
    assert r.returncode != 0 and "FROM=" in r.stderr
    write(user / ".oryx-overlay-version", "v0.9.9\n")
    git(user, "add", "-A")
    git(user, "commit", "-qm", "version")
    r = run(user, up)
    assert r.returncode != 0 and "v0.9.9 (from .oryx-overlay-version)" in r.stderr and "FROM=" in r.stderr
    r = run(user, up, FROM="v0.9.8")
    assert r.returncode != 0 and "v0.9.8 (from FROM)" in r.stderr
    # A branch is recorded by commit, since branches move.
    r = run(user, up, FROM="v1.0.0", TO="main")
    assert r.returncode == 0, r.stderr
    assert (user / ".oryx-overlay-version").read_text() == sha + "\n"
    git(user, "commit", "-qam", "upgrade")
    r = run(user, up)  # newest tag is the same commit
    assert r.returncode == 0 and "Already at v2.0.0" in r.stderr


def test_manifest_covers_every_tooling_file():
    """Every tracked file is either template-owned (in the manifest) or the user's."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    lines = (ROOT / ".oryx-overlay-manifest").read_text().splitlines()
    entries = [e for e in (line.split("#", 1)[0].strip().rstrip("/") for line in lines) if e]
    user = ("layout", "custom", "docs", "oryx.conf", ".oryx-overlay-version")
    assert not [e for e in entries if e.split("/")[0] in user]
    files = git(ROOT, "ls-files").splitlines()
    uncovered = [f for f in files
                 if f.split("/")[0] not in user and not any(f == e or f.startswith(e + "/") for e in entries)]
    assert uncovered == [], "add to .oryx-overlay-manifest (template-owned) or move: " + ", ".join(uncovered)


def test_symlinked_folder_is_not_written_through(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, user / "new")  # v2 adds new/added.txt
    git(user, "add", "-A")
    git(user, "commit", "-qm", "symlinked folder")
    release_v2(up)
    r = run(user, up)
    assert r.returncode == 0, r.stderr
    assert not (outside / "added.txt").exists()
    assert "a folder above it is a symlink" in r.stderr


def test_concurrent_self_update_is_refused(tmp_path, up):
    user = make_user(tmp_path / "user", up)
    release_v2(up)
    host = subprocess.run(["uname", "-n"], capture_output=True, text=True, check=True).stdout.strip()
    lock = user / ".git/oryx-overlay-update.lock"
    os.symlink(f"{os.getpid()}@{host}", lock)  # a live owner: this test
    r = run(user, up)
    assert r.returncode != 0 and "another self-update" in r.stderr
    assert (user / ".oryx-overlay-version").read_text() == "v1.0.0\n"  # nothing touched
    lock.unlink()
    assert run(user, up).returncode == 0
    assert not os.path.lexists(lock)  # released on exit
