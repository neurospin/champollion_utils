"""File-mode tests for champollion_utils.profiling (owner-only profiling files).

Task TASK-PROFILING-BDRABCZUK-687854868D08 (utils ledger, area PROFILING).
These tests mitigate hazard HAZ-PROFILING-BDRABCZUK-7AD5F2F70AAD: sidecars and
run.json store argv and cwd, which hold subject ids and data paths. User
decision 2026-11-17: the run directory is created with permission bits 0700
and every file profiling writes in it with permission bits 0600, whatever the
process umask. Each docstring names the Requirement title it verifies.

Every test sets a permissive umask first. A test that starts a profiling
session runs its scenario in a fresh interpreter (umask set before the import),
so the pytest process never gets a profiler, a fork hook or a SIGTERM handler.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
PROFILE_PREFIX = "CHAMPOLLION_PROFILE"
DIR_MODE = 0o700
FILE_MODE = 0o600

UMASKS = pytest.mark.parametrize("umask", [0o022, 0o000], ids=["umask022", "umask000"])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _env(**extra: str) -> dict[str, str]:
    """os.environ without CHAMPOLLION_PROFILE* keys, src on PYTHONPATH, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(PROFILE_PREFIX)}
    env["PYTHONPATH"] = str(SRC_DIR)
    env.update(extra)
    return env


def _profiled(tmp_path: Path, umask: int, body: str, **extra: str):
    """Run `body` (with `p` = champollion_utils.profiling) under CHAMPOLLION_PROFILE and `umask`.

    The script prints the run id as its last stdout line. Returns the run
    directory and the stdout lines before that last one.
    """
    profile_dir = tmp_path / "prof"
    profile_dir.mkdir(exist_ok=True)
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir(exist_ok=True)
    script = driver_dir / "probe.py"
    script.write_text(
        "import json, os, stat, sys\n"
        f"os.umask({umask:#o})\n"
        "import champollion_utils.profiling as p\n"
        + textwrap.dedent(body)
        + "print(json.dumps(os.environ[p.ENV_PROFILE_RUN]))\n"
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=work,
        env=_env(CHAMPOLLION_PROFILE=str(profile_dir), **extra),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    lines = result.stdout.strip().splitlines()
    run_id = json.loads(lines[-1])
    return profile_dir / run_id, lines[:-1]


class _Umask:
    """Set the pytest process umask for a block (in-process tests that start no session)."""

    def __init__(self, umask: int):
        self.umask = umask
        self.previous = None

    def __enter__(self):
        self.previous = os.umask(self.umask)
        return self

    def __exit__(self, *_exc):
        os.umask(self.previous)
        return False


# --------------------------------------------------------------------------
# Run directory
# --------------------------------------------------------------------------


@UMASKS
def test_run_directory_created_owner_only(tmp_path, umask):
    """Run directory created owner-only (through ensure_profiling_from_env)."""
    run_dir, _ = _profiled(tmp_path, umask, "p.ensure_profiling_from_env()\n")
    assert _mode(run_dir) == DIR_MODE, oct(_mode(run_dir))


@UMASKS
def test_make_profiling_env_creates_run_directory_owner_only(tmp_path, umask):
    """Run directory created owner-only (through make_profiling_env)."""
    from champollion_utils.profiling import ENV_PROFILE_RUN, make_profiling_env

    profile_dir = tmp_path / "prof"
    profile_dir.mkdir()
    with _Umask(umask):
        env = make_profiling_env(profile_dir, "light", {})
    run_dir = profile_dir / env[ENV_PROFILE_RUN]
    assert _mode(run_dir) == DIR_MODE, oct(_mode(run_dir))


# --------------------------------------------------------------------------
# Files in the run directory
# --------------------------------------------------------------------------


@UMASKS
def test_run_json_owner_read_write_only(tmp_path, umask):
    """run.json written owner read/write only (at start and at exit)."""
    run_dir, lines = _profiled(
        tmp_path,
        umask,
        """
        p.ensure_profiling_from_env()
        run_json = os.path.join(
            os.environ[p.ENV_PROFILE_DIR], os.environ[p.ENV_PROFILE_RUN], "run.json"
        )
        print(json.dumps(stat.S_IMODE(os.stat(run_json).st_mode)))
        """,
    )
    at_start = json.loads(lines[-1])
    assert at_start == FILE_MODE, oct(at_start)
    at_exit = _mode(run_dir / "run.json")
    assert at_exit == FILE_MODE, oct(at_exit)


@UMASKS
def test_segment_sidecar_owner_read_write_only(tmp_path, umask):
    """Segment sidecar written owner read/write only (incomplete at open, complete at exit)."""
    run_dir, lines = _profiled(
        tmp_path,
        umask,
        """
        p.ensure_profiling_from_env()
        proc = os.path.join(
            os.environ[p.ENV_PROFILE_DIR], os.environ[p.ENV_PROFILE_RUN], "proc"
        )
        name = os.environ[p.ENV_PROFILE_PARENT] + ".json"
        print(json.dumps(stat.S_IMODE(os.stat(os.path.join(proc, name)).st_mode)))
        """,
    )
    at_open = json.loads(lines[-1])
    assert at_open == FILE_MODE, oct(at_open)
    sidecars = sorted((run_dir / "proc").glob("*.json"))
    assert sidecars, "no segment sidecar written"
    for sidecar in sidecars:
        assert json.loads(sidecar.read_text())["complete"] is True
        assert _mode(sidecar) == FILE_MODE, f"{sidecar.name}: {oct(_mode(sidecar))}"


@UMASKS
def test_cprofile_dump_owner_read_write_only(tmp_path, umask):
    """cProfile dump written owner read/write only (mode cpu)."""
    run_dir, _ = _profiled(
        tmp_path,
        umask,
        "p.ensure_profiling_from_env()\n",
        CHAMPOLLION_PROFILE_MODE="cpu",
    )
    dumps = sorted((run_dir / "proc").glob("*.prof"))
    assert dumps, "no cProfile dump written"
    for dump in dumps:
        assert _mode(dump) == FILE_MODE, f"{dump.name}: {oct(_mode(dump))}"


@UMASKS
def test_boot_file_owner_read_write_only(tmp_path, umask):
    """Boot file written owner read/write only."""
    from champollion_utils.profiling import write_boot_dir

    with _Umask(umask):
        boot = write_boot_dir(tmp_path / "run")
    boot_file = boot / "sitecustomize.py"
    assert _mode(boot_file) == FILE_MODE, oct(_mode(boot_file))
