"""Tests for champollion_utils.init_process, the shared per-process setup.

TASK-004. Requirements: REQ-LOGGER-33, 34, 35, 37, 38, 39 (see .alm/REQUIREMENTS.md).

init_process configures the root logger and installs the crash hook, both
process-wide side effects. Every behavioural test therefore runs a small
driver script in a fresh interpreter, so the pytest process's own root logger
and sys.excepthook are never touched. The driver prints READY_MARKER right
after its init_process() calls; every test checks for it first, so a driver
that dies earlier (for example because init_process does not exist yet) can
never pass by accident.
"""

import importlib
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
READY_MARKER = "DRIVER-READY-4b9e"

# "HH:MM:SS LEVEL    <provenance> | <message>"
CRASH_LINE = re.compile(r"^\d{2}:\d{2}:\d{2} CRASH\s+\S+ \| ", re.MULTILINE)


def _run_driver(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    """Run `body` in a fresh interpreter whose cwd is an empty directory.

    The driver script lives outside that directory, in tmp_path/driver.
    """
    driver_dir = tmp_path / "driver"
    work_dir = tmp_path / "work"
    driver_dir.mkdir()
    work_dir.mkdir()
    driver = driver_dir / "driver.py"
    driver.write_text(textwrap.dedent(body).replace("READY_MARKER", repr(READY_MARKER)))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [sys.executable, str(driver)],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert READY_MARKER in result.stdout, (
        f"driver did not get past init_process (exit {result.returncode}); stderr:\n{result.stderr}"
    )
    return result


def _marker_value(stdout: str, key: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1 :]
    raise AssertionError(f"{key}= not found in driver stdout:\n{stdout}")


# REQ-LOGGER-33
def test_init_process_called_twice_emits_one_stdout_line_per_record(tmp_path):
    result = _run_driver(
        tmp_path,
        """
        import logging

        import champollion_utils

        champollion_utils.init_process()
        champollion_utils.init_process()
        print(READY_MARKER, flush=True)
        logging.getLogger("demo.stage").output("twice-marker-91")
        """,
    )

    lines = [line for line in result.stdout.splitlines() if "twice-marker-91" in line]
    assert len(lines) == 1, result.stdout
    assert re.match(r"^\d{2}:\d{2}:\d{2} OUTPUT\s+\S+ \| twice-marker-91$", lines[0]), (
        lines[0]
    )


# REQ-LOGGER-34
def test_init_process_returns_root_logger(tmp_path):
    result = _run_driver(
        tmp_path,
        """
        import logging

        import champollion_utils

        returned = champollion_utils.init_process()
        print(READY_MARKER, flush=True)
        print("IS_ROOT=" + str(returned is logging.getLogger()), flush=True)
        """,
    )

    assert _marker_value(result.stdout, "IS_ROOT") == "True", result.stdout


# REQ-LOGGER-35
def test_init_process_installs_crash_hook_one_crash_line(tmp_path):
    result = _run_driver(
        tmp_path,
        """
        import champollion_utils


        def explode():
            raise ValueError("crash-marker-35")


        champollion_utils.init_process()
        print(READY_MARKER, flush=True)
        explode()
        """,
    )

    assert len(CRASH_LINE.findall(result.stderr)) == 1, result.stderr
    assert "crash-marker-35" in result.stderr, result.stderr


# REQ-LOGGER-37
def test_package_exports_init_process():
    package = importlib.import_module("champollion_utils")

    assert "init_process" in package.__all__
    assert callable(getattr(package, "init_process", None))


# REQ-LOGGER-38
def test_init_process_registers_no_profiler(tmp_path):
    result = _run_driver(
        tmp_path,
        """
        import sys

        import champollion_utils

        champollion_utils.init_process()
        print(READY_MARKER, flush=True)
        print("GETPROFILE=" + repr(sys.getprofile()), flush=True)
        monitoring = getattr(sys, "monitoring", None)
        tool = None if monitoring is None else monitoring.get_tool(monitoring.PROFILER_ID)
        print("PROFILER_TOOL=" + repr(tool), flush=True)
        """,
    )

    assert _marker_value(result.stdout, "GETPROFILE") == "None", result.stdout
    assert _marker_value(result.stdout, "PROFILER_TOOL") == "None", result.stdout


# REQ-LOGGER-39
def test_init_process_creates_no_file_in_cwd(tmp_path):
    result = _run_driver(
        tmp_path,
        """
        import json
        import logging
        import os

        import champollion_utils

        before = sorted(os.listdir("."))
        champollion_utils.init_process()
        logging.getLogger("demo.stage").output("file-marker-39")
        logging.getLogger("demo.stage").fail("file-marker-39")
        after = sorted(os.listdir("."))
        print(READY_MARKER, flush=True)
        print("CWD_DIFF=" + json.dumps(sorted(set(after) - set(before))), flush=True)
        """,
    )

    assert json.loads(_marker_value(result.stdout, "CWD_DIFF")) == [], result.stdout
