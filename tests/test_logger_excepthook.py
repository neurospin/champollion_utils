"""Tests for champollion_utils.logger.install_excepthook.

TASK-003. Requirements: REQ-LOGGER-26 to REQ-LOGGER-32 (see .alm/REQUIREMENTS.md).

Each test runs a small driver script in a fresh interpreter, because the
behaviour under test (an uncaught exception ending a thread or the process,
and the resulting exit status) cannot be observed safely inside pytest's own
process. The exception is raised from a throwaway package written to tmp_path
(``crashpkg.stage``), so the expected provenance is an import name plus a
class-qualified function name: ``crashpkg.stage::Stage.explode``.

The driver prints READY_MARKER to stdout just before raising. Every test
checks for it first, so a driver that dies earlier (for example because
install_excepthook does not exist yet) can never pass by accident.
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
READY_MARKER = "DRIVER-READY-7f3c"
INNERMOST = "crashpkg.stage::Stage.explode"

# One formatted CRASH record line: "HH:MM:SS CRASH    <provenance> | <message>".
CRASH_LINE = re.compile(r"^\d{2}:\d{2}:\d{2} CRASH\s+(\S+) \| ", re.MULTILINE)
TRACEBACK_HEADER = "Traceback (most recent call last):"

STAGE_SOURCE = textwrap.dedent(
    """
    import threading


    class Stage:
        def explode(self):
            raise ValueError("inner-marker")


    def run_stage():
        Stage().explode()


    def interrupt():
        raise KeyboardInterrupt


    def run_stage_in_thread():
        worker = threading.Thread(target=run_stage, name="crash-worker")
        worker.start()
        worker.join()
    """
)


def _write_package(tmp_path: Path) -> None:
    package = tmp_path / "crashpkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "stage.py").write_text(STAGE_SOURCE)


def _run_driver(
    tmp_path: Path, *, installs: int, action: str
) -> subprocess.CompletedProcess:
    """Run a driver that sets up logging, installs the hook `installs` times, then runs `action`.

    `action` is a call on crashpkg.stage, e.g. "run_stage()".
    """
    _write_package(tmp_path)
    install_lines = "\n".join(
        "champollion_logger.install_excepthook()" for _ in range(installs)
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        textwrap.dedent(
            """
            import sys

            from champollion_utils import logger as champollion_logger
            from crashpkg import stage

            champollion_logger.setup_logging()
            {install_lines}
            print({marker!r}, flush=True)
            stage.{action}
            """
        ).format(install_lines=install_lines, marker=READY_MARKER, action=action)
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_DIR), str(tmp_path)])
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [sys.executable, str(driver)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert READY_MARKER in result.stdout, (
        f"driver did not reach the raise point (exit {result.returncode}); stderr:\n{result.stderr}"
    )
    return result


# REQ-LOGGER-26
def test_uncaught_exception_in_main_thread_logs_one_crash_record_with_innermost_provenance(
    tmp_path,
):
    result = _run_driver(tmp_path, installs=1, action="run_stage()")

    provenances = CRASH_LINE.findall(result.stderr)
    assert provenances == [INNERMOST], result.stderr


# REQ-LOGGER-27
def test_uncaught_exception_in_main_thread_writes_traceback_once_after_crash_line(
    tmp_path,
):
    result = _run_driver(tmp_path, installs=1, action="run_stage()")

    stderr = result.stderr
    crash = CRASH_LINE.search(stderr)
    assert crash is not None, stderr
    assert stderr.count(TRACEBACK_HEADER) == 1, stderr
    assert stderr.index(TRACEBACK_HEADER) > crash.start(), stderr
    assert "ValueError: inner-marker" in stderr[crash.start() :], stderr


# REQ-LOGGER-28
def test_uncaught_exception_in_main_thread_exits_with_status_1(tmp_path):
    result = _run_driver(tmp_path, installs=1, action="run_stage()")

    assert result.returncode == 1, result.stderr


# REQ-LOGGER-29
def test_uncaught_exception_in_thread_logs_one_crash_record_with_innermost_provenance(
    tmp_path,
):
    result = _run_driver(tmp_path, installs=1, action="run_stage_in_thread()")

    provenances = CRASH_LINE.findall(result.stderr)
    assert provenances == [INNERMOST], result.stderr


# REQ-LOGGER-30
def test_keyboard_interrupt_is_not_logged_as_crash(tmp_path):
    result = _run_driver(tmp_path, installs=1, action="interrupt()")

    assert CRASH_LINE.search(result.stderr) is None, result.stderr


# REQ-LOGGER-31
def test_keyboard_interrupt_exit_status_matches_process_without_hook(tmp_path):
    hooked = _run_driver(tmp_path, installs=1, action="interrupt()")
    baseline = _run_driver(tmp_path, installs=0, action="interrupt()")

    assert hooked.returncode == baseline.returncode, (hooked.stderr, baseline.stderr)


# REQ-LOGGER-32
def test_installing_hook_twice_logs_one_crash_record(tmp_path):
    result = _run_driver(tmp_path, installs=2, action="run_stage()")

    assert len(CRASH_LINE.findall(result.stderr)) == 1, result.stderr
