"""`make docker-*` forwards env overrides and git identity into the container (#9)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}

pytestmark = pytest.mark.skipif(not (shutil.which("make") and shutil.which("git")), reason="needs make and git")


def sandbox(tmp_path):
    """A non-git copy of Makefile + lib.sh, so the repo's own .git/config is never an input."""
    box = tmp_path / "box"
    (box / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "Makefile", box / "Makefile")
    shutil.copy(ROOT / "scripts/lib.sh", box / "scripts/lib.sh")
    return box


def host_env(tmp_path, extra=None, name="Ada O'Neil", email="ada@example.com", cfg=None):
    """Environment with an isolated HOME holding (or lacking) a git identity."""
    env = dict(BASE_ENV, HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    for key, val in (("user.name", name), ("user.email", email)):
        if val:
            subprocess.run(["git", "config", "--global", key, val], env=env, check=True)
    for key, val in (cfg or {}).items():
        subprocess.run(["git", "config", "--global", key, val], env=env, check=True)
    env.update(extra or {})
    return env


def dry_run(tmp_path, extra=None, **ident):
    r = subprocess.run(["make", "-n", "docker-build"], cwd=sandbox(tmp_path), capture_output=True, text=True,
                       env=host_env(tmp_path, extra, **ident))
    assert r.returncode == 0, r.stderr
    joined = r.stdout.replace("\\\n", " ")  # fold backslash continuations into one line
    return next(line for line in joined.splitlines() if " run " in line and "-e HOME=/tmp" in line)


def flags(cmd):
    return re.findall(r"-e (\S+)", cmd)


def fake_docker(tmp_path):
    """A docker stand-in that prints the identity it was handed in its environment."""
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\n[ "$1" = run ] && printf "%s|%s" "$GIT_AUTHOR_NAME" "$GIT_COMMITTER_EMAIL"\nexit 0\n')
    fake.chmod(0o755)
    return fake


def test_set_config_keys_are_forwarded(tmp_path):
    cmd = dry_run(tmp_path, {"KEYBOARD": "moonlander/revb", "LEGEND_STYLE": "mac"})
    assert "KEYBOARD" in flags(cmd) and "LEGEND_STYLE" in flags(cmd)
    assert "KEYMAP_NAME" not in flags(cmd)  # only when set


def test_every_lib_config_key_is_covered(tmp_path):
    keys = re.search(r'^CONFIG_KEYS="(.*)"$', (ROOT / "scripts/lib.sh").read_text(), re.M).group(1).split()
    cmd = dry_run(tmp_path, {k: "x" for k in keys})
    assert set(keys) <= set(flags(cmd))


def test_git_identity_comes_from_host_config(tmp_path):
    cmd = dry_run(tmp_path)
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        assert k in flags(cmd)


def test_no_git_identity_means_nothing_forwarded(tmp_path):
    cmd = dry_run(tmp_path, name=None, email=None)
    assert not [f for f in flags(cmd) if f.startswith("GIT_")]


def test_identity_value_survives_quoting(tmp_path):
    r = subprocess.run(["make", f"DOCKER={fake_docker(tmp_path)}", "docker-build"], cwd=sandbox(tmp_path),
                       capture_output=True, text=True, env=host_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "Ada O'Neil|ada@example.com" in r.stdout


def test_environment_identity_wins(tmp_path):
    r = subprocess.run(["make", f"DOCKER={fake_docker(tmp_path)}", "docker-build"], cwd=sandbox(tmp_path),
                       capture_output=True, text=True, env=host_env(tmp_path, {"GIT_AUTHOR_NAME": "Env Person"}))
    assert r.returncode == 0, r.stderr
    assert "Env Person|" in r.stdout


def test_anchored_to_makefile_dir_and_errors_when_keys_missing(tmp_path):
    env = host_env(tmp_path)
    # -C from elsewhere still finds lib.sh
    r = subprocess.run(["make", "-n", "-f", str(ROOT / "Makefile"), "-C", str(tmp_path), "docker-build"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "-e HOME=/tmp" in r.stdout, r.stderr
    # a reformatted lib.sh fails docker-* loudly, but not other targets
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/lib.sh").write_text("CONFIG_KEYS=\"\"\n")
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    r = subprocess.run(["make", "-n", "docker-build"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "CONFIG_KEYS" in r.stderr
    r = subprocess.run(["make", "help"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert r.returncode == 0


def test_repo_path_with_apostrophe_and_space(tmp_path):
    repo = tmp_path / "it's a repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", repo / "Makefile")
    shutil.copy(ROOT / "scripts/lib.sh", repo / "scripts/lib.sh")
    r = subprocess.run(["make", "-n", "docker-build"], cwd=repo, capture_output=True, text=True,
                       env=host_env(tmp_path, {"KEYBOARD": "moonlander/revb"}))
    assert r.returncode == 0, r.stderr
    assert "-e KEYBOARD" in r.stdout


def test_committer_falls_back_to_host_config_independently(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\n[ "$1" = run ] && printf "%s|%s|%s|%s" "$GIT_AUTHOR_NAME" '
                    '"$GIT_COMMITTER_NAME" "$GIT_AUTHOR_EMAIL" "$GIT_COMMITTER_EMAIL"\nexit 0\n')
    fake.chmod(0o755)
    r = subprocess.run(["make", f"DOCKER={fake}", "docker-build"], cwd=sandbox(tmp_path), capture_output=True, text=True,
                       env=host_env(tmp_path, {"GIT_AUTHOR_NAME": "Env Person"}))
    assert r.returncode == 0, r.stderr
    assert "Env Person|Ada O'Neil|ada@example.com|ada@example.com" in r.stdout


def test_role_specific_config_wins_over_user(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text('#!/bin/sh\n[ "$1" = run ] && printf "%s|%s" "$GIT_AUTHOR_EMAIL" "$GIT_COMMITTER_EMAIL"\nexit 0\n')
    fake.chmod(0o755)
    r = subprocess.run(["make", f"DOCKER={fake}", "docker-build"], cwd=sandbox(tmp_path), capture_output=True,
                       text=True, env=host_env(tmp_path, cfg={"author.email": "author@example.com"}))
    assert r.returncode == 0, r.stderr
    assert "author@example.com|ada@example.com" in r.stdout
