"""
Run-log capture at file-descriptor level (RUNLOG epic, TASK-RUNLOG-BDRABCZUK-CAF38C2CF337).

While a RunLogCapture is active, every line written to file descriptors 1 and 2 (own prints,
logger records set up by init_process, child processes such as ScriptBuilder.execute_command
and its "Running command:" lines) is copied once into the run-log file and still forwarded unchanged to
the terminal. The file starts with a header (argv, cwd, start time) and ends with a footer (exit
code, elapsed seconds). File descriptors 1 and 2 and sys.stdout/sys.stderr are restored on exit.

Children are real subprocesses (sys.executable -c ...). The run-log path is a nested path
under tmp_path whose parent directories do not exist yet. Every test restores fds 1 and 2 and the sys streams
afterwards, even when the capture under test fails.
"""

import io
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from os.path import dirname, exists, isfile

import pytest

from champollion_utils import run_log
from champollion_utils.process import init_process
from champollion_utils.run_log import RunLogCapture
from champollion_utils.script_builder import ScriptBuilder

TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
EXIT_CODE_RE = re.compile(r"exit[ _-]?code\D{0,15}?(-?\d+)", re.IGNORECASE)
ELAPSED_RE = re.compile(r"(?:elapsed|duration)\D{0,15}?(\d+(?:\.\d+)?)", re.IGNORECASE)


@pytest.fixture(autouse=True)  # noqa: V103 - autouse, restores the standard streams
def restore_standard_streams():
    """Put fds 1 and 2 and sys.stdout/sys.stderr back as they were, whatever the test did."""
    saved_out, saved_err = os.dup(1), os.dup(2)
    sys_out, sys_err = sys.stdout, sys.stderr
    try:
        yield
    finally:
        sys.stdout, sys.stderr = sys_out, sys_err
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)


@pytest.fixture
def log_path(tmp_path):
    """Run-log path under tmp_path; its parent directories do not exist yet."""
    return str(tmp_path / "derivatives" / "logs" / "run_20261022T093000.log")


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _log_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as stream:
        return stream.read().splitlines()


def _text_after(lines: list[str], marker: str) -> str:
    return "\n".join(lines[lines.index(marker) + 1 :])


def _local_now() -> datetime:
    """Naive local time, as the header writes it."""
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


def _fd_identity(fd: int) -> tuple[int, int]:
    status = os.fstat(fd)
    return status.st_dev, status.st_ino


class _CommandRunner(ScriptBuilder):
    def run(self) -> int:
        return 0


# REQ-RUNLOG-BDRABCZUK-F80C2C1B32E0: every fd 1/2 line is copied into the log once, within 1 s.


def test_child_process_stdout_and_stderr_lines_reach_the_log_once(log_path):
    with RunLogCapture(log_path):
        subprocess.run(
            _child(
                "import sys; print('child-out-marker'); print('child-err-marker', file=sys.stderr)"
            ),
            check=True,
        )
    lines = _log_lines(log_path)
    assert lines.count("child-out-marker") == 1
    assert lines.count("child-err-marker") == 1


def test_own_prints_reach_the_log_once(log_path):
    with RunLogCapture(log_path):
        print("own-out-marker")
        print("own-err-marker", file=sys.stderr)
        os.write(1, b"own-fd1-marker\n")
        os.write(2, b"own-fd2-marker\n")
    lines = _log_lines(log_path)
    for marker in (
        "own-out-marker",
        "own-err-marker",
        "own-fd1-marker",
        "own-fd2-marker",
    ):
        assert lines.count(marker) == 1, marker


def test_unified_logger_records_reach_the_log_once(log_path):
    init_process()
    logger = logging.getLogger("run_log_test")
    with RunLogCapture(log_path):
        logger.info("logger-info-marker")
        logger.warning("logger-warning-marker")
    lines = _log_lines(log_path)
    assert sum("logger-info-marker" in line for line in lines) == 1
    assert sum("logger-warning-marker" in line for line in lines) == 1


def test_execute_command_line_and_child_output_reach_the_log(log_path):
    cmd = _child("print('exec-child-marker')")
    with RunLogCapture(log_path):
        assert _CommandRunner("runlog-test", "run-log test").execute_command(cmd) == 0
    lines = _log_lines(log_path)
    assert lines.count("Running command:") == 1
    assert lines.count(" ".join(cmd)) == 1
    assert lines.count("exec-child-marker") == 1


def test_child_line_reaches_the_log_within_one_second_while_active(log_path, tmp_path):
    written = tmp_path / "written"
    code = f"import sys, time\nprint('early-marker', flush=True)\nopen({str(written)!r}, 'w').close()\ntime.sleep(30)\n"
    with RunLogCapture(log_path):
        child = subprocess.Popen(_child(code))
        try:
            start = time.monotonic()
            while not written.exists() and time.monotonic() - start < 20:
                time.sleep(0.01)
            assert written.exists(), "child never wrote its line"
            written_at = time.monotonic()
            seen = False
            while not seen and time.monotonic() - written_at <= 1.0:
                seen = isfile(log_path) and "early-marker" in _log_lines(log_path)
                time.sleep(0.02)
            assert seen, "line not in the run log within one second of the write"
            assert child.poll() is None, (
                "child exited before the check: not a live copy"
            )
        finally:
            child.kill()
            child.wait()


# REQ-RUNLOG-BDRABCZUK-6112995E7BED: file created with missing parents, header first.


def test_log_file_created_with_missing_parent_directories(log_path):
    assert not exists(dirname(log_path))
    with RunLogCapture(log_path):
        assert isfile(log_path)


def test_header_names_argv_cwd_and_start_time_before_captured_output(
    log_path, tmp_path, monkeypatch
):
    argv = ["champollion-run", "--stage", "morphologist", "/data/root-argv-marker"]
    monkeypatch.setattr(sys, "argv", argv)
    run_dir = tmp_path / "run-cwd"
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)
    cwd = os.getcwd()
    before = _local_now().replace(microsecond=0)
    with RunLogCapture(log_path):
        print("first-captured-line")
    after = _local_now()
    lines = _log_lines(log_path)
    header = "\n".join(lines[: lines.index("first-captured-line")])
    for arg in argv:
        assert arg in header, arg
    assert cwd in header
    stamps = [
        datetime.fromisoformat(s.replace(" ", "T"))
        for s in TIMESTAMP_RE.findall(header)
    ]
    assert any(before <= s <= after + timedelta(seconds=1) for s in stamps), stamps


# REQ-RUNLOG-BDRABCZUK-360C6EA37D76: footer with exit code and elapsed seconds.


def _footer_exit_code(lines: list[str], last_body_line: str) -> int:
    codes = EXIT_CODE_RE.findall(_text_after(lines, last_body_line))
    assert codes, "no exit code in the footer"
    return int(codes[-1])


def test_footer_gives_exit_code_value_and_elapsed_seconds(log_path):
    with RunLogCapture(log_path) as capture:
        print("body-line")
        time.sleep(0.3)
        capture.exit_code = 7
    lines = _log_lines(log_path)
    assert _footer_exit_code(lines, "body-line") == 7
    elapsed = ELAPSED_RE.findall(_text_after(lines, "body-line"))
    assert elapsed, "no elapsed seconds in the footer"
    assert 0.3 <= float(elapsed[-1]) < 60


def test_footer_exit_code_defaults_to_zero(log_path):
    with RunLogCapture(log_path):
        print("body-line")
    assert _footer_exit_code(_log_lines(log_path), "body-line") == 0


def test_footer_exit_code_is_1_after_an_uncaught_exception(log_path):
    with pytest.raises(RuntimeError, match="boom"), RunLogCapture(log_path):
        print("body-line")
        raise RuntimeError("boom")
    assert _footer_exit_code(_log_lines(log_path), "body-line") == 1


def test_footer_exit_code_is_the_systemexit_code(log_path):
    with pytest.raises(SystemExit), RunLogCapture(log_path):
        print("body-line")
        sys.exit(3)
    assert _footer_exit_code(_log_lines(log_path), "body-line") == 3


# REQ-RUNLOG-BDRABCZUK-1208949246AC: lines still reach the original fd target, unchanged.


def test_stdout_lines_still_reach_the_original_stdout_unchanged(log_path, capfd):
    capfd.readouterr()
    with RunLogCapture(log_path):
        subprocess.run(_child("print('fwd-child-out')"), check=True)
        print("fwd-own-out")
    out, err = capfd.readouterr()
    assert out.splitlines().count("fwd-child-out") == 1
    assert out.splitlines().count("fwd-own-out") == 1
    assert "fwd-child-out" not in err
    assert "fwd-own-out" not in err


def test_stderr_lines_still_reach_the_original_stderr_unchanged(log_path, capfd):
    capfd.readouterr()
    with RunLogCapture(log_path):
        subprocess.run(
            _child("import sys; print('fwd-child-err', file=sys.stderr)"), check=True
        )
        print("fwd-own-err", file=sys.stderr)
    out, err = capfd.readouterr()
    assert err.splitlines().count("fwd-child-err") == 1
    assert err.splitlines().count("fwd-own-err") == 1
    assert "fwd-child-err" not in out
    assert "fwd-own-err" not in out


# REQ-RUNLOG-BDRABCZUK-B4F6CF03FE50: fds 1/2 and sys.stdout/sys.stderr restored on exit.


def _standard_streams() -> tuple:
    return _fd_identity(1), _fd_identity(2), sys.stdout, sys.stderr


def test_fds_and_sys_streams_restored_after_normal_exit(log_path):
    before = _standard_streams()
    with RunLogCapture(log_path):
        print("inside-line")
    assert _standard_streams() == before
    os.write(1, b"after-exit-marker\n")
    assert "after-exit-marker" not in _log_lines(log_path)


def test_fds_and_sys_streams_restored_after_an_exception(log_path):
    before = _standard_streams()
    with pytest.raises(ValueError), RunLogCapture(log_path):
        print("inside-line")
        raise ValueError("inside")
    assert _standard_streams() == before
    os.write(2, b"after-exit-marker\n")
    assert "after-exit-marker" not in _log_lines(log_path)


# Edge paths of the capture (exit codes, failures while starting, writing or forwarding).


@pytest.mark.parametrize(("code", "expected"), [(None, 0), ("fatal: bad input", 1)])
def test_footer_exit_code_for_non_int_systemexit_codes(log_path, code, expected):
    with pytest.raises(SystemExit), RunLogCapture(log_path):
        print("body-line")
        raise SystemExit(code)
    assert _footer_exit_code(_log_lines(log_path), "body-line") == expected


def test_entering_an_active_capture_again_raises(log_path):
    capture = RunLogCapture(log_path)
    with capture, pytest.raises(RuntimeError, match="already active"):
        capture.__enter__()


def test_failure_before_the_redirect_closes_the_log_and_leaves_streams_alone(
    log_path, monkeypatch
):
    before = _standard_streams()
    monkeypatch.setattr(
        RunLogCapture, "_header", lambda self: (_ for _ in ()).throw(OSError("header"))
    )
    with pytest.raises(OSError, match="header"):
        RunLogCapture(log_path).__enter__()
    assert _standard_streams() == before


def test_failure_after_the_redirect_restores_fds_and_sys_streams(log_path, monkeypatch):
    before = _standard_streams()

    def refuse_start(self):
        raise RuntimeError("no thread")

    monkeypatch.setattr(run_log.threading.Thread, "start", refuse_start)
    with pytest.raises(RuntimeError, match="no thread"):
        RunLogCapture(log_path).__enter__()
    assert _standard_streams() == before


def test_unflushable_sys_stream_is_ignored(log_path, monkeypatch):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdout", closed)
    with RunLogCapture(log_path):
        os.write(1, b"after-closed-stream\n")
    assert _log_lines(log_path).count("after-closed-stream") == 1


def test_trailing_text_without_newline_is_logged_as_a_line(log_path):
    with RunLogCapture(log_path):
        os.write(1, b"no-newline-tail")
    lines = _log_lines(log_path)
    assert lines.count("no-newline-tail") == 1
    assert _footer_exit_code(lines, "no-newline-tail") == 0


def test_line_still_logged_when_forwarding_to_the_terminal_fails(log_path, monkeypatch):
    def broken_terminal(fd, data):
        raise OSError("terminal gone")

    monkeypatch.setattr(run_log, "_write_all", broken_terminal)
    with RunLogCapture(log_path):
        os.write(1, b"logged-not-forwarded\n")
    assert _log_lines(log_path).count("logged-not-forwarded") == 1


class _UnwritableLog:
    def write(self, data):
        raise OSError("disk full")

    def close(self):
        pass


def test_unwritable_log_reports_the_missing_footer_on_stderr(log_path, capfd):
    capfd.readouterr()
    with RunLogCapture(log_path) as capture:
        capture._log = _UnwritableLog()
        print("body-line")
    _, err = capfd.readouterr()
    assert "run-log: footer not written" in err


def test_footer_notes_an_fd_still_held_by_a_child(log_path, monkeypatch):
    monkeypatch.setattr(run_log, "RUN_LOG_JOIN_TIMEOUT_S", 0.2)
    with RunLogCapture(log_path):
        child = subprocess.Popen(_child("import time; time.sleep(2)"))
    try:
        assert any(
            "still open by a child process" in line for line in _log_lines(log_path)
        )
    finally:
        child.wait()
