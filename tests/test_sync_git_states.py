"""scripts/sync.sh in awkward git states (#19), run for real with a stubbed oryx_fetch.py.

Each test builds throwaway repos, cut off from the host's git config (signing,
identity, hooks), and checks the exit code, the message and what is left on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="needs git and bash")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
GIT = ["git", "-c", "commit.gpgsign=false"]

FETCH_STUB = (
    'import os, shutil, sys\n'
    'out = sys.argv[sys.argv.index("--out") + 1]\n'
    'for name in os.listdir(out):\n'
    '    if not name.startswith("."):\n'
    '        os.remove(os.path.join(out, name))\n'
    'for name in os.listdir(os.environ["FAKE_EXPORT"]):\n'
    '    shutil.copy(os.path.join(os.environ["FAKE_EXPORT"], name), out)\n'
    'print("revision=r2\\nqmk_version=25.0\\nchange_note=Test export")\n'
)

KEYMAP = 'const char x[] = "a  b";\n' + "// pad\n" * 5 + "int custom_hook;\n" + "// pad\n" * 5 + "int tail;\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=check, env={**os.environ, **GIT_ENV})


def make_repo(dest: Path, extra: dict[str, str] | None = None) -> Path:
    shutil.copytree(ROOT / "scripts", dest / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "scripts/oryx_fetch.py").write_text(FETCH_STUB)
    (dest / "oryx.conf").write_text("ORYX_LAYOUT_ID=default\nKEYBOARD=voyager\n")
    (dest / "layout").mkdir()
    (dest / "layout/keymap.c").write_text(KEYMAP)
    (dest / "layout/.oryx.json").write_text("{}")
    for name, text in (extra or {}).items():
        write(dest / name, text)
    git(dest, "init", "-q")
    git(dest, "checkout", "-qb", "main")
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "first export")
    return dest


def run_sync(repo: Path, tmp_path: Path, files: dict[str, str], *args: str, cache: Path | None = None) -> subprocess.CompletedProcess:
    export = tmp_path / "export"
    shutil.rmtree(export, ignore_errors=True)
    export.mkdir()
    for name, text in files.items():
        (export / name).write_text(text)
    env = {**os.environ, **GIT_ENV, "PYTHON": sys.executable, "FAKE_EXPORT": str(export)}
    env.pop("CACHE_DIR", None)
    if cache is not None:
        env["CACHE_DIR"] = str(cache)
    return subprocess.run(["bash", str(repo / "scripts/sync.sh"), *args], capture_output=True, text=True, env=env, cwd=repo)


def export_of(keymap: str = KEYMAP) -> dict[str, str]:
    # Real exports change .oryx.json (the revision) every time; first-sync branching relies on it.
    return {"keymap.c": keymap, ".oryx.json": json.dumps({"keymap_sha": hashlib.sha1(keymap.encode()).hexdigest()})}


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env={**os.environ, **GIT_ENV})
    return bare


# --- 1. the remote oryx branch was recreated ----------------------------------------


def seeded(tmp_path, origin) -> Path:
    """A repo whose first sync pushed oryx to origin, with a second export synced on top."""
    repo = make_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail1")), "--push")
    assert res.returncode == 0, res.stderr
    return repo


def test_remote_oryx_rewritten_is_fetched_and_reported(tmp_path, origin):
    repo = seeded(tmp_path, origin)
    # Someone else force-pushes oryx back one commit; our tracking ref doesn't know yet.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env={**os.environ, **GIT_ENV})
    git(other, "push", "-q", "--force", "origin", "origin/oryx~1:refs/heads/oryx")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail2")))
    assert res.returncode == 0, res.stderr
    assert "origin/oryx was rewritten" in res.stderr
    assert "tail2" in (repo / "layout/keymap.c").read_text()


def test_remote_oryx_recreated_with_unrelated_history_is_reconciled(tmp_path, origin):
    repo = seeded(tmp_path, origin)
    # Delete the remote branch and recreate it from scratch (a root commit).
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "layout").mkdir()
    (other / "layout/keymap.c").write_text(KEYMAP.replace("tail", "recreated"))
    git(other, "add", "-A")
    git(other, "commit", "-qm", "oryx: recreated")
    git(other, "push", "-q", "--force", str(origin), "HEAD:refs/heads/oryx")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail3")))
    assert res.returncode == 0, res.stderr
    assert "unrelated histories" in res.stderr and "origin/oryx" in res.stderr
    assert "tail3" in (repo / "layout/keymap.c").read_text()
    assert git(repo, "status", "--porcelain").stdout == ""


def test_recreated_remote_with_no_local_oryx_recovers_in_ci_and_stays_ordinary(tmp_path, origin):
    """A fresh CI checkout has no local oryx; the recreated remote branch shares no history with main."""
    seeded(tmp_path, origin)
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    (other / "layout").mkdir()
    (other / "layout/keymap.c").write_text("recreated\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "oryx: recreated")
    git(other, "push", "-q", "--force", str(origin), "HEAD:refs/heads/oryx")
    clone = tmp_path / "ci"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(clone)], check=True, env={**os.environ, **GIT_ENV})
    assert git(clone, "branch", "--list", "oryx").stdout == ""
    remote_before = git(clone, "rev-parse", "origin/oryx").stdout.strip()
    res = run_sync(clone, tmp_path, export_of(KEYMAP.replace("tail", "tail11")), "--push", "--ci")
    assert res.returncode == 0, res.stderr
    assert "unrelated histories" in res.stderr
    assert "tail11" in (clone / "layout/keymap.c").read_text()
    # The remote is an ancestor of the new oryx (so the push was a plain fast-forward) and of main.
    assert git(clone, "merge-base", "--is-ancestor", remote_before, "oryx", check=False).returncode == 0
    assert git(origin, "rev-parse", "oryx").stdout == git(clone, "rev-parse", "oryx").stdout
    res = run_sync(clone, tmp_path, export_of(KEYMAP.replace("tail", "tail12")), "--push", "--ci")
    assert res.returncode == 0, res.stderr
    assert "unrelated" not in res.stderr and "rewritten" not in res.stderr
    assert "tail12" in (clone / "layout/keymap.c").read_text()


# --- 2. a merge that fails without a conflict ---------------------------------------


@pytest.mark.parametrize("flags", [[], ["--ci"]])
def test_untracked_file_the_export_adds_is_refused_before_the_merge(tmp_path, flags):
    repo = make_repo(tmp_path / "repo")
    (repo / "layout/foo.h").write_text("// mine\n")
    head = git(repo, "rev-parse", "HEAD").stdout
    files = {**export_of(KEYMAP.replace("tail", "tail4")), "foo.h": "// oryx\n"}
    res = run_sync(repo, tmp_path, files, *flags)
    assert res.returncode == 1, res.stderr
    assert "layout/foo.h" in res.stderr
    assert "Merge conflict" not in res.stderr
    # Nothing half-done: no merge state, main untouched, the file as it was, the export kept on oryx.
    assert not (repo / ".git/MERGE_HEAD").exists()
    assert git(repo, "rev-parse", "HEAD").stdout == head
    assert (repo / "layout/foo.h").read_text() == "// mine\n"
    assert git(repo, "show", "oryx:layout/foo.h").stdout == "// oryx\n"


def test_ignored_file_the_export_adds_is_refused(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / ".git/info/exclude").write_text("layout/foo.h\n")
    (repo / "layout/foo.h").write_text("// mine\n")
    res = run_sync(repo, tmp_path, {**export_of(KEYMAP.replace("tail", "t")), "foo.h": "// oryx\n"})
    assert res.returncode == 1, res.stderr
    assert "layout/foo.h" in res.stderr
    assert (repo / "layout/foo.h").read_text() == "// mine\n"


@pytest.mark.parametrize("name", [".keymap.c.swp", "keymap.c~", "keymap.c.orig", ".#keymap.c", "compile_commands.json"])
def test_unrelated_untracked_files_in_layout_do_not_block_sync(tmp_path, name):
    repo = make_repo(tmp_path / "repo")
    (repo / "layout" / name).write_text("x")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "t")))
    assert res.returncode == 0, res.stderr
    assert (repo / "layout" / name).read_text() == "x"


@pytest.mark.parametrize("flags", [[], ["--ci"]])
def test_merge_failure_without_conflict_is_not_reported_as_one(tmp_path, flags):
    """Unrelated histories: git refuses before touching anything, so there is no MERGE_HEAD to abort."""
    repo = make_repo(tmp_path / "repo")
    # A local oryx branch that is a root commit, sharing no history with main.
    git(repo, "checkout", "-q", "--orphan", "oryx")
    git(repo, "commit", "-qm", "orphan", "--allow-empty")
    git(repo, "checkout", "-q", "main")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail5")), *flags)
    assert res.returncode == 1, res.stderr
    assert "Merge conflict" not in res.stderr
    assert "unrelated histories" in res.stderr
    assert "main" in res.stderr
    assert not (repo / ".git/MERGE_HEAD").exists()


@pytest.mark.parametrize("flags", [[], ["--ci"]])
def test_failing_merge_hook_is_not_reported_as_a_conflict(tmp_path, flags):
    """A hook that rejects the merge commit leaves MERGE_HEAD but no unmerged files."""
    repo = make_repo(tmp_path / "repo")
    (repo / "layout/keymap.c").write_text(KEYMAP.replace("int custom_hook;", "int custom_hook; // [custom]"))
    git(repo, "commit", "-qam", "custom edit")
    hook = repo / ".git/hooks/pre-merge-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    head = git(repo, "rev-parse", "HEAD").stdout
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace('"a  b"', '"a b"')), *flags)
    assert res.returncode == 1, res.stderr
    assert "hook says no" in res.stderr
    assert "Merge conflict" not in res.stderr
    assert not (repo / ".git/MERGE_HEAD").exists()
    assert git(repo, "rev-parse", "HEAD").stdout == head
    assert git(repo, "status", "--porcelain").stdout == ""


def test_crlf_export_from_an_older_sync_merges_as_text(tmp_path):
    """oryx holds a CRLF export (made before the fetch normalised); the new one is LF."""
    repo = make_repo(tmp_path / "repo")
    git(repo, "config", "core.autocrlf", "false")
    crlf = KEYMAP.replace("\n", "\r\n")
    (repo / "layout/keymap.c").write_bytes(crlf.encode())
    git(repo, "commit", "-qam", "older CRLF export")
    git(repo, "branch", "oryx")
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n")  # added later, as in an older clone
    git(repo, "add", ".gitattributes")
    (repo / "layout/keymap.c").write_bytes(crlf.replace("int custom_hook;", "int custom_hook; // [custom]").encode())
    git(repo, "commit", "-qam", "custom edit")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail10")))
    assert res.returncode == 0, res.stderr
    assert "tail10" in (repo / "layout/keymap.c").read_text()
    assert "// [custom]" in (repo / "layout/keymap.c").read_text()


def test_real_conflict_still_exits_3_and_ci_aborts(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "layout/keymap.c").write_text(KEYMAP.replace("int tail;", "int mine;"))
    git(repo, "commit", "-qam", "custom edit")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("int tail;", "int theirs;")), "--ci")
    assert res.returncode == 3, res.stderr
    assert "keymap.c" in res.stderr
    assert not (repo / ".git/MERGE_HEAD").exists()
    assert git(repo, "status", "--porcelain").stdout == ""


# --- 3. a stale worktree directory --------------------------------------------------


def test_stale_worktree_after_moving_the_repo_recovers(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "oryx")
    wt = repo / ".cache/oryx-worktree"
    git(repo, "worktree", "add", "-q", str(wt), "oryx")  # a sync that was killed here
    moved = tmp_path / "moved"
    shutil.move(str(repo), str(moved))
    sibling = moved / ".cache/oryx-worktree-keep"
    sibling.mkdir()
    (sibling / "precious").write_text("x")
    res = run_sync(moved, tmp_path, export_of(KEYMAP.replace("tail", "tail6")))
    assert res.returncode == 0, res.stderr
    assert "stale" in res.stderr
    assert "tail6" in (moved / "layout/keymap.c").read_text()
    assert (sibling / "precious").read_text() == "x"
    assert not (moved / ".cache/oryx-worktree").exists()


def test_unrelated_directory_at_the_worktree_path_is_left_alone(tmp_path):
    repo = make_repo(tmp_path / "repo")
    wt = repo / ".cache/oryx-worktree"
    wt.mkdir(parents=True)
    (wt / "notes.txt").write_text("not sync's")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail7")))
    assert res.returncode == 1
    assert str(wt) in res.stderr
    assert (wt / "notes.txt").read_text() == "not sync's"


def test_symlink_at_the_worktree_path_is_not_followed(tmp_path):
    repo = make_repo(tmp_path / "repo")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").write_text("x")
    (repo / ".cache").mkdir()
    (repo / ".cache/oryx-worktree").symlink_to(target)
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail8")))
    assert res.returncode == 1
    assert (target / "keep").read_text() == "x"
    assert (repo / ".cache/oryx-worktree").is_symlink()


@pytest.mark.parametrize("relative", [False, True])
def test_worktree_of_another_live_repo_is_left_alone(tmp_path, relative):
    """A shared CACHE_DIR must not let one repo delete another's live worktree."""
    cache = tmp_path / "shared-cache"
    a = make_repo(tmp_path / "a")
    git(a, "branch", "oryx")
    git(a, "-c", f"worktree.useRelativePaths={'true' if relative else 'false'}", "worktree", "add", "-q", str(cache / "oryx-worktree"), "oryx")
    gitfile = (cache / "oryx-worktree/.git").read_text()
    assert gitfile.startswith("gitdir: ") and gitfile.split(": ", 1)[1].startswith("/") != relative
    (cache / "oryx-worktree/uncommitted.txt").write_text("precious")
    b = make_repo(tmp_path / "b")
    res = run_sync(b, tmp_path, export_of(KEYMAP.replace("tail", "tail9")), cache=cache)
    assert res.returncode == 1
    assert (cache / "oryx-worktree/uncommitted.txt").read_text() == "precious"
    assert (cache / "oryx-worktree/layout/keymap.c").exists()


# --- 4. whitespace-only Oryx changes ------------------------------------------------


def test_whitespace_only_oryx_change_reaches_a_locally_edited_file(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "layout/keymap.c").write_text(KEYMAP.replace("int custom_hook;", "int custom_hook; // [custom]"))
    git(repo, "commit", "-qam", "custom edit")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace('"a  b"', '"a b"')))
    assert res.returncode == 0, res.stderr
    text = (repo / "layout/keymap.c").read_text()
    assert '"a b"' in text and "// [custom]" in text


# --- merges and rebases the user already has in progress ----------------------------


def pending_merge(repo: Path, kind: str) -> None:
    """Leave a merge in progress whose only changes are under docs/ (which the dirty guard skips)."""
    write(repo / "docs/keymap.md", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs")
    git(repo, "checkout", "-qb", "other")
    write(repo / "docs/keymap.md", "other\n")
    git(repo, "commit", "-qam", "docs on other")
    git(repo, "checkout", "-q", "main")
    if kind == "resolved":
        write(repo / "docs/keymap.md", "main\n")
        git(repo, "commit", "-qam", "docs on main")
        assert git(repo, "merge", "other", check=False).returncode != 0
        write(repo / "docs/keymap.md", "my resolution\n")
        git(repo, "add", "docs/keymap.md")
    elif kind == "unresolved":
        write(repo / "docs/keymap.md", "main\n")
        git(repo, "commit", "-qam", "docs on main")
        assert git(repo, "merge", "other", check=False).returncode != 0
    elif kind == "no-commit":
        git(repo, "merge", "--no-commit", "--no-ff", "other")
    else:
        git(repo, "merge", "-s", "ours", "--no-commit", "other")
    assert (repo / ".git/MERGE_HEAD").exists()


@pytest.mark.parametrize("flags", [[], ["--ci"]])
@pytest.mark.parametrize("kind", ["resolved", "unresolved", "no-commit", "ours"])
def test_merge_in_progress_is_left_alone(tmp_path, kind, flags):
    repo = make_repo(tmp_path / "repo")
    pending_merge(repo, kind)
    before = (git(repo, "status", "--porcelain").stdout, (repo / "docs/keymap.md").read_text(), (repo / ".git/MERGE_HEAD").read_text())
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "t")), *flags)
    assert res.returncode == 1, res.stderr
    assert "already in progress" in res.stderr and "MERGE_HEAD" in res.stderr
    after = (git(repo, "status", "--porcelain").stdout, (repo / "docs/keymap.md").read_text(), (repo / ".git/MERGE_HEAD").read_text())
    assert after == before
    assert git(repo, "branch", "--list", "oryx").stdout == ""


def test_rebase_in_progress_is_left_alone(tmp_path):
    repo = make_repo(tmp_path / "repo")
    write(repo / "docs/keymap.md", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "docs")
    git(repo, "checkout", "-qb", "other")
    write(repo / "docs/keymap.md", "other\n")
    git(repo, "commit", "-qam", "other")
    git(repo, "checkout", "-q", "main")
    write(repo / "docs/keymap.md", "main\n")
    git(repo, "commit", "-qam", "main")
    git(repo, "checkout", "-q", "other")
    assert git(repo, "rebase", "main", check=False).returncode != 0
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "HEAD"
    (repo / "docs/keymap.md").write_text("resolved\n")
    git(repo, "add", "-A")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "t")))
    assert res.returncode == 1 and "rebase is already in progress" in res.stderr
    assert (repo / "docs/keymap.md").read_text() == "resolved\n"


# --- the collision check: renames, and files you stopped tracking -------------------


def test_file_renamed_into_an_untracked_path_is_a_clash(tmp_path, origin):
    repo = make_repo(tmp_path / "repo", {"layout/old.h": "// same\n"})
    git(repo, "remote", "add", "origin", str(origin))
    (repo / "layout/new.h").write_text("// mine\n")
    res = run_sync(repo, tmp_path, {**export_of(KEYMAP.replace("tail", "t")), "new.h": "// same\n"}, "--push")
    assert res.returncode == 1, res.stderr
    assert "layout/new.h" in res.stderr
    assert (repo / "layout/new.h").read_text() == "// mine\n"
    assert git(origin, "branch", "--list", "oryx").stdout == ""  # nothing was pushed


def test_file_you_stopped_tracking_is_not_a_clash(tmp_path):
    repo = make_repo(tmp_path / "repo", {"layout/foo.h": "// oryx\n"})
    git(repo, "rm", "-q", "--cached", "layout/foo.h")
    (repo / ".git/info/exclude").write_text("layout/foo.h\n")
    git(repo, "commit", "-qm", "stop tracking foo.h")
    res = run_sync(repo, tmp_path, {**export_of(KEYMAP.replace("tail", "t")), "foo.h": "// oryx\n"})
    assert res.returncode == 0, res.stderr
    assert (repo / "layout/foo.h").read_text() == "// oryx\n"


def test_crlf_gitfile_of_a_live_worktree_is_left_alone(tmp_path):
    cache = tmp_path / "shared-cache"
    a = make_repo(tmp_path / "a")
    git(a, "branch", "oryx")
    git(a, "worktree", "add", "-q", str(cache / "oryx-worktree"), "oryx")
    gitfile = cache / "oryx-worktree/.git"
    gitfile.write_bytes(gitfile.read_bytes().replace(b"\n", b"\r\n"))
    (cache / "oryx-worktree/uncommitted.txt").write_text("precious")
    b = make_repo(tmp_path / "b")
    res = run_sync(b, tmp_path, export_of(KEYMAP.replace("tail", "t")), cache=cache)
    assert res.returncode == 1
    assert (cache / "oryx-worktree/uncommitted.txt").read_text() == "precious"
