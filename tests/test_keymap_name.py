"""KEYMAP_NAME validation and the flash deny list (#10)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def load(tmp_path, conf_name=None, env_name=None):
    """Run load_config in a scratch copy; KEYMAP_NAME comes from oryx.conf and/or the environment."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts", dirs_exist_ok=True)
    conf = 'ORYX_LAYOUT_ID="abc12"\nKEYBOARD="moonlander/reva"\n'
    if conf_name is not None:
        conf += f"KEYMAP_NAME='{conf_name}'\n"
    (tmp_path / "oryx.conf").write_text(conf)
    env = dict(ENV, HOME=str(tmp_path))
    if env_name is not None:
        env["KEYMAP_NAME"] = env_name
    return subprocess.run(["bash", "-c", 'source scripts/lib.sh; load_config; printf "%s" "$KEYMAP_NAME"'],
                          cwd=tmp_path, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("name", ["oryx-overlay", "my_map2"])
def test_valid_names_accepted(tmp_path, name):
    for kw in ({"conf_name": name}, {"env_name": name}):
        r = load(tmp_path, **kw)
        assert r.returncode == 0, r.stderr
        assert r.stdout == name


def test_default_name_when_unset(tmp_path):
    r = load(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "oryx-overlay"


@pytest.mark.parametrize("name", ["x:dfu-util", "../../..", "a/b", "a b", "a;b"])
def test_bad_names_rejected_from_conf_and_env(tmp_path, name):
    for kw in ({"conf_name": name}, {"env_name": name}):
        r = load(tmp_path, **kw)
        assert r.returncode != 0
        assert "KEYMAP_NAME" in r.stderr and name in r.stderr
        assert "A-Za-z0-9_-" in r.stderr


def test_empty_name_in_conf_is_rejected(tmp_path):
    r = load(tmp_path, conf_name="")
    assert r.returncode != 0
    assert "KEYMAP_NAME" in r.stderr


def test_settings_deny_other_flash_targets():
    deny = json.loads((ROOT / ".claude/settings.json").read_text())["permissions"]["deny"]
    for pat in ("make *:dfu*", "make *:teensy*", "make *:avrdude*", "make *:st-*"):
        assert f"Bash({pat})" in deny
