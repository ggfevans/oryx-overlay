"""Line-ending guards (#8): a CRLF checkout on Windows must not break the scripts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

needs_git = pytest.mark.skipif(not (shutil.which("git") and (ROOT / ".git").exists()), reason="needs a git checkout")


@needs_git
@pytest.mark.parametrize("path", [
    "scripts/doctor.sh", "scripts/lib.sh", "Makefile", "oryx.conf", "layout/keymap.c",
    "custom/config.h", "docker/Dockerfile", ".github/workflows/build.yml",
])
def test_text_files_resolve_to_lf(path):
    out = subprocess.run(["git", "check-attr", "eol", "text", "--", path], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    assert f"{path}: eol: lf" in out
    assert f"{path}: text: auto" in out


@needs_git
def test_images_are_binary():
    out = subprocess.run(["git", "check-attr", "binary", "--", "docs/assets/lighting.png"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    assert "binary: set" in out


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_load_config_tolerates_crlf_oryx_conf(tmp_path):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "oryx.conf").write_bytes(b'ORYX_LAYOUT_ID="abc12"\r\nKEYBOARD="moonlander/reva"\r\n')
    r = subprocess.run(
        ["bash", "-c", 'source scripts/lib.sh; load_config; printf "[%s][%s]" "$ORYX_LAYOUT_ID" "$KEYBOARD"'],
        cwd=tmp_path, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "[abc12][moonlander/reva]"
