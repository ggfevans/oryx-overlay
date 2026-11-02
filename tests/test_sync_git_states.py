"""scripts/sync.sh in awkward git states (#19), run for real with a stubbed oryx_fetch.py.

Each test builds throwaway repos, cut off from the host's git config (signing,
identity, hooks), and checks the exit code, the message and what is left on disk.
"""

from __future__ import annotations

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
    'for name in os.listdir(os.environ["FAKE_EXPORT"]):\n'
    '    shutil.copy(os.path.join(os.environ["FAKE_EXPORT"], name), out)\n'
    'print("revision=r2\\nqmk_version=25.0\\nchange_note=Test export")\n'
)

KEYMAP = 'const char x[] = "a  b";\n' + "// pad\n" * 5 + "int custom_hook;\n" + "// pad\n" * 5 + "int tail;\n"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=check, env={**os.environ, **GIT_ENV})


def make_repo(dest: Path) -> Path:
    shutil.copytree(ROOT / "scripts", dest / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "scripts/oryx_fetch.py").write_text(FETCH_STUB)
    (dest / "oryx.conf").write_text("ORYX_LAYOUT_ID=default\nKEYBOARD=voyager\n")
    (dest / "layout").mkdir()
    (dest / "layout/keymap.c").write_text(KEYMAP)
    (dest / "layout/.oryx.json").write_text("{}")
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
    return {"keymap.c": keymap, ".oryx.json": "{}"}


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


# --- 2. a merge that fails without a conflict ---------------------------------------


@pytest.mark.parametrize("flags", [[], ["--ci"]])
def test_untracked_file_under_layout_is_refused_up_front(tmp_path, flags):
    repo = make_repo(tmp_path / "repo")
    (repo / "layout/foo.h").write_text("// mine\n")
    files = {**export_of(KEYMAP.replace("tail", "tail4")), "foo.h": "// oryx\n"}
    res = run_sync(repo, tmp_path, files, *flags)
    assert res.returncode == 1, res.stderr
    assert "layout/foo.h" in res.stderr
    assert "Merge conflict" not in res.stderr
    # Nothing half-done: no oryx branch, no merge state, the file is as it was.
    assert git(repo, "branch", "--list", "oryx").stdout == ""
    assert not (repo / ".git/MERGE_HEAD").exists()
    assert (repo / "layout/foo.h").read_text() == "// mine\n"


@pytest.mark.parametrize("flags", [[], ["--ci"]])
def test_merge_failure_without_conflict_is_not_reported_as_one(tmp_path, flags):
    """Unrelated histories: git refuses before touching anything, so there is no MERGE_HEAD to abort."""
    repo = make_repo(tmp_path / "repo")
    git(repo, "branch", "oryx")
    # Make the local oryx branch a root commit with no shared history with main.
    git(repo, "checkout", "-q", "--orphan", "tmp-orphan")
    git(repo, "commit", "-qm", "orphan", "--allow-empty")
    git(repo, "branch", "-M", "oryx-orphan")
    git(repo, "checkout", "-q", "main")
    git(repo, "branch", "-D", "oryx")
    git(repo, "branch", "-m", "oryx-orphan", "oryx")
    res = run_sync(repo, tmp_path, export_of(KEYMAP.replace("tail", "tail5")), *flags)
    assert res.returncode == 1, res.stderr
    assert "Merge conflict" not in res.stderr
    assert "unrelated histories" in res.stderr
    assert "main" in res.stderr
    assert not (repo / ".git/MERGE_HEAD").exists()


def test_real_conflict_still_exits_3_and_ci_aborts(tmp_path):
    repo = make_repo(tmp_path / "repo")
    git(repo, "checkout", "-q", "-b", "side")
    git(repo, "checkout", "-q", "main")
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


def test_worktree_of_another_live_repo_is_left_alone(tmp_path):
    """A shared CACHE_DIR must not let one repo delete another's live worktree."""
    cache = tmp_path / "shared-cache"
    a = make_repo(tmp_path / "a")
    git(a, "branch", "oryx")
    git(a, "worktree", "add", "-q", str(cache / "oryx-worktree"), "oryx")
    b = make_repo(tmp_path / "b")
    res = run_sync(b, tmp_path, export_of(KEYMAP.replace("tail", "tail9")), cache=cache)
    assert res.returncode == 1
    assert (cache / "oryx-worktree/.git").is_file()
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
