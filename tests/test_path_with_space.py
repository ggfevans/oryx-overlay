"""Named claim: a cache path with whitespace stops build.sh and doctor.sh early."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_script(name: str, cache_dir: Path):
    env = dict(os.environ, CACHE_DIR=str(cache_dir))
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / name)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_build_refuses_cache_path_with_space(tmp_path: Path):
    proc = run_script("build.sh", tmp_path / "has space")
    assert proc.returncode == 1
    assert "whitespace" in proc.stderr
    assert "CACHE_DIR" in proc.stderr


def test_doctor_flags_cache_path_with_space(tmp_path: Path):
    proc = run_script("doctor.sh", tmp_path / "has space")
    assert proc.returncode == 1
    assert "whitespace" in proc.stdout
    assert "docker-build" in proc.stdout


def test_doctor_quiet_about_normal_cache_path(tmp_path: Path):
    proc = run_script("doctor.sh", tmp_path / "cache")
    assert "whitespace" not in proc.stdout
