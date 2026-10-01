"""Staging tests for scripts/build.sh (no QMK toolchain needed).

Runs `build.sh --stage-only` against a small fake ZSA QMK tree (a real git
repository) and checks that each build starts from ZSA's tree: stale keymaps and
modules from earlier builds are gone, custom modules go to a userspace outside
the tree, ZSA's own modules can't be replaced, and paths with spaces are handled.
"""

from __future__ import annotations

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


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_repo(dest: Path, modules: list[str] | None = None, qmk_version: str = "25.0") -> Path:
    """Copy the parts of this repo build.sh needs, with the given custom/keymap.json modules."""
    for name in ("scripts", "layout", "custom"):
        shutil.copytree(ROOT / name, dest / name)
    shutil.copy(ROOT / "oryx.conf", dest / "oryx.conf")
    meta = json.loads((dest / "layout/.oryx.json").read_text())
    meta["qmk_version"] = qmk_version
    (dest / "layout/.oryx.json").write_text(json.dumps(meta))
    (dest / "custom/keymap.json").write_text(json.dumps({"modules": modules or []}))
    return dest


def make_tree(path: Path, v25: bool = True) -> Path:
    """A fake ZSA QMK checkout: enough for build.sh to stage into."""
    write(path / "Makefile", "all:\n")
    write(path / ".gitignore", "/keyboards/zsa/**/keymaps/**\n!/keyboards/zsa/**/keymaps/default\n!/keyboards/zsa/**/keymaps/default/**\n*.bin\n.build/\n")
    if v25:
        for rev in ("reva", "revb"):
            write(path / f"keyboards/zsa/moonlander/{rev}/keyboard.json", "{}")
        write(path / "lib/python/qmk/community_modules.py")
        write(path / "modules/zsa/oryx/qmk_module.json", '{"module_name": "Oryx"}')
        write(path / "modules/zsa/oryx/oryx.c", "/* ZSA's */\n")
        write(path / "modules/zsa/defaults/qmk_module.json", '{"module_name": "Defaults"}')
    else:
        write(path / "keyboards/zsa/moonlander/keyboard.json", "{}")
        for board in ("ergodox_ez", "ergodox_ez/stm32", "ergodox_ez/stm32/glow", "ergodox_ez/stm32/shine"):
            write(path / f"keyboards/zsa/{board}/info.json", "{}")
    write(path / "keyboards/zsa/moonlander/keymaps/default/keymap.c", "/* default */\n")
    write(path / "keyboards/zsa/voyager/keyboard.json", "{}")
    env = {**os.environ, **GIT_ENV}
    branch = "firmware25" if v25 else "firmware24"
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "https://github.com/zsa/qmk_firmware.git"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "zsa"], check=True, env=env)
    return path


def run(repo: Path, cache: Path | None = None, **env_extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **GIT_ENV, "PYTHON": sys.executable, "KEYBOARD": "moonlander/reva", **env_extra}
    env.pop("CACHE_DIR", None)
    if cache is not None:
        env["CACHE_DIR"] = str(cache)
    return subprocess.run(["bash", str(repo / "scripts/build.sh"), "--stage-only"],
                          capture_output=True, text=True, env=env)


def staged(result: subprocess.CompletedProcess) -> dict[str, str]:
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def porcelain(tree: Path) -> str:
    return subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout


def test_reset_removes_leftovers_and_stages_modules_outside_tree(tmp_path):
    repo = make_repo(tmp_path / "repo", modules=["oryx_overlay/screensaver"])
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    # Leftovers from older builds: a keymap at the family level (#5), copied modules
    # (#7), an overwritten ZSA module, and a keymap for another board.
    write(tree / "keyboards/zsa/moonlander/keymaps/oryx-overlay/config.h", '#define SERIAL_NUMBER "stale"\n')
    write(tree / "keyboards/zsa/voyager/keymaps/oryx-overlay/keymap.c")
    write(tree / "modules/oryx_overlay/screensaver/qmk_module.json", "{}")
    write(tree / "modules/zsa/oryx/oryx.c", "/* replaced */\n")
    write(tree / "modules/zsa/oryx/extra.c")

    out = staged(run(repo, tmp_path / "cache"))

    assert not (tree / "keyboards/zsa/moonlander/keymaps/oryx-overlay").exists()
    assert not (tree / "keyboards/zsa/voyager/keymaps/oryx-overlay").exists()
    assert not (tree / "modules/oryx_overlay").exists()
    assert not (tree / "modules/zsa/oryx/extra.c").exists()
    assert (tree / "modules/zsa/oryx/oryx.c").read_text() == "/* ZSA's */\n"
    assert porcelain(tree) == ""

    km = Path(out["keymap"])
    assert km == tree / "keyboards/zsa/moonlander/reva/keymaps/oryx-overlay"
    assert "oryx_overlay/screensaver" in json.loads((km / "keymap.json").read_text())["modules"]

    us = Path(out["userspace"])
    assert us == tmp_path / "cache/userspace-firmware25"
    assert json.loads((us / "qmk.json").read_text())["userspace_version"] == "1.0"
    assert (us / "modules/oryx_overlay/screensaver/qmk_module.json").is_file()
    assert (us / "modules/example/hello_overlay/qmk_module.json").is_file()


def test_serial_number_comes_from_current_layout(tmp_path):
    repo = make_repo(tmp_path / "repo")
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    write(tree / "keyboards/zsa/moonlander/keymaps/oryx-overlay/config.h", '#define SERIAL_NUMBER "stale"\n')
    km = Path(staged(run(repo, tmp_path / "cache"))["keymap"])
    serials = [p.read_text() for p in (tree / "keyboards").rglob("config.h") if "SERIAL_NUMBER" in p.read_text()]
    assert len(serials) == 1 and "stale" not in serials[0]
    assert (km / "config.h").read_text().startswith((repo / "layout/config.h").read_text())


def test_deleted_module_fails_clearly(tmp_path):
    repo = make_repo(tmp_path / "repo", modules=["oryx_overlay/screensaver"])
    make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    staged(run(repo, tmp_path / "cache"))
    shutil.rmtree(repo / "custom/modules/oryx_overlay")
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0
    assert "community module not found: oryx_overlay/screensaver" in result.stderr


def test_custom_module_cannot_replace_zsa_module(tmp_path):
    repo = make_repo(tmp_path / "repo")
    write(repo / "custom/modules/zsa/oryx/qmk_module.json", "{}")
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0
    assert "owner name ZSA's firmware already ships" in result.stderr
    assert (tree / "modules/zsa/oryx/oryx.c").read_text() == "/* ZSA's */\n"


def test_board_family_is_rejected(tmp_path):
    repo = make_repo(tmp_path / "repo")
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    result = run(repo, tmp_path / "cache", KEYBOARD="moonlander")
    assert result.returncode != 0
    assert "moonlander/reva moonlander/revb" in result.stderr
    assert not (tree / "keyboards/zsa/moonlander/keymaps/oryx-overlay").exists()


def test_tree_must_be_its_own_git_checkout(tmp_path):
    repo = make_repo(tmp_path / "repo")
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    shutil.rmtree(tree / ".git")
    subprocess.run(["git", "init", "-q", str(tmp_path / "cache")], check=True)  # an enclosing repo
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0
    assert "not a git checkout" in result.stderr
    assert (tree / "keyboards/zsa/voyager/keyboard.json").exists()


def test_repo_path_with_space_uses_user_cache(tmp_path):
    repo = make_repo(tmp_path / "my projects/oryx overlay")
    xdg = tmp_path / "xdg"
    cache = subprocess.run(
        ["bash", "-c", 'source "$1/scripts/lib.sh" && printf %s "$CACHE_DIR"', "_", str(repo)],
        capture_output=True, text=True, check=True,
        env={**{k: v for k, v in os.environ.items() if k != "CACHE_DIR"}, "XDG_CACHE_HOME": str(xdg)},
    ).stdout
    assert cache.startswith(str(xdg / "oryx-overlay") + "/") and " " not in cache
    make_tree(Path(cache) / "qmk_firmware-firmware25")
    out = staged(run(repo, XDG_CACHE_HOME=str(xdg)))
    assert out["keymap"].startswith(cache + "/")


def test_cache_dir_with_space_fails_early(tmp_path):
    repo = make_repo(tmp_path / "repo")
    result = run(repo, tmp_path / "a cache")
    assert result.returncode != 0
    assert "contains whitespace" in result.stderr and "CACHE_DIR" in result.stderr


def test_firmware24_has_no_userspace(tmp_path):
    repo = make_repo(tmp_path / "repo", qmk_version="24.0")
    (repo / "layout/keymap.json").unlink()  # v24 exports list no modules
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware24", v25=False)
    out = staged(run(repo, tmp_path / "cache"))
    assert out["keymap"] == str(tree / "keyboards/zsa/moonlander/keymaps/oryx-overlay")
    assert out["userspace"] == ""
    assert porcelain(tree) == ""

    (repo / "custom/keymap.json").write_text('{"modules": ["oryx_overlay/screensaver"]}')
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0
    assert "need firmware v25+" in result.stderr

    result = run(repo, tmp_path / "cache", KEYBOARD="ergodox_ez")
    assert result.returncode != 0
    assert "one of: ergodox_ez/stm32/glow ergodox_ez/stm32/shine\n" in result.stderr


def test_tree_from_another_repository_or_branch_is_refused(tmp_path):
    repo = make_repo(tmp_path / "repo")
    tree = make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "-C", str(tree), "remote", "set-url", "origin", "https://example.com/other.git"], check=True, env=env)
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0 and "not https://github.com/zsa/qmk_firmware.git on firmware25" in result.stderr
    subprocess.run(["git", "-C", str(tree), "remote", "set-url", "origin", "https://github.com/zsa/qmk_firmware"], check=True, env=env)
    subprocess.run(["git", "-C", str(tree), "checkout", "-qb", "firmware24"], check=True, env=env)
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0 and "on firmware24" in result.stderr


def test_concurrent_build_is_refused_and_dead_lock_is_reported(tmp_path):
    repo = make_repo(tmp_path / "repo")
    make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    lock = tmp_path / "cache/.build-lock-firmware25"
    host = subprocess.run(["uname", "-n"], capture_output=True, text=True, check=True).stdout.strip()
    os.symlink(f"{os.getpid()}@{host}", lock)  # a live owner: this test
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0 and "another build" in result.stderr
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock.unlink()
    os.symlink(f"{dead.pid}@{host}", lock)
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0 and "no longer running" in result.stderr and "rm -f" in result.stderr
    assert os.path.islink(lock)  # never taken over automatically
    lock.unlink()
    staged(run(repo, tmp_path / "cache"))
    assert not os.path.lexists(lock)  # released on exit


def test_directory_at_lock_path_is_refused_not_used(tmp_path):
    repo = make_repo(tmp_path / "repo")
    make_tree(tmp_path / "cache/qmk_firmware-firmware25")
    lock = tmp_path / "cache/.build-lock-firmware25"
    lock.mkdir()
    result = run(repo, tmp_path / "cache")
    assert result.returncode != 0 and "unexpected directory" in result.stderr
    assert lock.is_dir() and not any(lock.iterdir())  # left alone, nothing linked inside
