"""Capture a script run into a run-log file at file-descriptor level.

While a ``RunLogCapture`` is active, everything written to file descriptors 1 and 2 (own prints,
unified-logger records, child processes) is copied line by line into the run-log file and forwarded
unchanged to where fds 1 and 2 pointed before. The caller chooses the log path.
"""

import io
import os
import shlex
import sys
import threading
import time
from datetime import datetime
from os.path import dirname
from typing import BinaryIO

RUN_LOG_READ_CHUNK_BYTES = 65536
RUN_LOG_JOIN_TIMEOUT_S = 5.0
_STD_FDS = (1, 2)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _flush_quietly(stream) -> None:
    try:
        stream.flush()
    except (OSError, ValueError, AttributeError):
        pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _compute_exit_code(exit_code: int, exc: BaseException | None) -> int:
    if exc is None:
        return int(exit_code)
    if isinstance(exc, SystemExit):
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    return 1


class RunLogCapture:
    """Context manager copying file descriptors 1 and 2 into a run-log file.

    Args:
        log_path: path of the run-log file; missing parent directories are created.
        started_at: start time written to the header; None means the time of ``__enter__``.

    Attributes:
        exit_code: exit code written to the footer when the block ends without an exception.
    """

    def __init__(self, log_path: str, *, started_at: datetime | None = None) -> None:
        self.log_path = log_path
        self.exit_code = 0
        self._started_at = started_at
        self._active = False
        self._lock = threading.Lock()
        self._log: BinaryIO | None = None
        self._log_closed = False
        self._orig_fds: dict[int, int] = {}
        self._orig_sys: tuple = ()
        self._readers: dict[int, threading.Thread] = {}
        self._t0 = 0.0

    def __enter__(self) -> "RunLogCapture":  # noqa: PYI034 - typing.Self needs 3.11; the floor is 3.10
        if self._active:
            raise RuntimeError(f"RunLogCapture for {self.log_path} is already active")
        os.makedirs(dirname(self.log_path) or ".", exist_ok=True)
        self._log = open(self.log_path, "ab", buffering=0)
        self._log_closed = False
        read_ends: dict[int, int] = {}
        try:
            self._log.write(self._header().encode("utf-8"))
            _flush_quietly(sys.stdout)
            _flush_quietly(sys.stderr)
            self._orig_sys = (sys.stdout, sys.stderr)
            for fd in _STD_FDS:
                self._orig_fds[fd] = os.dup(fd)
            self._t0 = time.monotonic()
            for fd in _STD_FDS:
                read_end, write_end = os.pipe()
                read_ends[fd] = read_end
                os.dup2(write_end, fd)
                os.close(write_end)
            sys.stdout = self._fd_stream(1, self._orig_sys[0])
            sys.stderr = self._fd_stream(2, self._orig_sys[1])
            for fd in _STD_FDS:
                reader = threading.Thread(
                    target=self._read_loop,
                    args=(read_ends.pop(fd), self._orig_fds[fd]),
                    name=f"run-log-fd{fd}",
                )
                reader.daemon = True
                reader.start()
                self._readers[fd] = reader
        except BaseException:
            for read_end in read_ends.values():
                os.close(read_end)
            self._restore_streams()
            for fd in _STD_FDS:
                if fd not in self._readers and fd in self._orig_fds:
                    os.close(self._orig_fds.pop(fd))
            self._log.close()
            raise
        self._active = True
        return self

    def __exit__(self, _exc_type, exc, _tb) -> bool:
        code = _compute_exit_code(self.exit_code, exc)
        try:
            self._restore_streams()
            stuck = []
            for fd, reader in self._readers.items():
                reader.join(RUN_LOG_JOIN_TIMEOUT_S)
                if reader.is_alive():
                    stuck.append(fd)
            self._write_footer(code, stuck)
        finally:
            self._readers = {}
            self._orig_fds = {}
            self._active = False
        return False

    def _header(self) -> str:
        started_at = self._started_at or datetime.now().astimezone()
        return (
            "==== champollion run log ====\n"
            f"argv: {shlex.join(sys.argv)}\n"
            f"cwd: {os.getcwd()}\n"
            f"start: {started_at.strftime(_TIME_FORMAT)}\n"
            f"pid: {os.getpid()}\n"
            "==============================\n"
        )

    @staticmethod
    def _fd_stream(fd: int, replaced) -> io.TextIOWrapper:
        return io.TextIOWrapper(
            io.FileIO(fd, "w", closefd=False),
            encoding=getattr(replaced, "encoding", None) or "utf-8",
            errors="backslashreplace",
            line_buffering=True,
            write_through=True,
        )

    def _restore_streams(self) -> None:
        """Flush the replacement sys streams, put the saved ones back and re-point fds 1/2."""
        if not self._orig_sys:
            return
        _flush_quietly(sys.stdout)
        _flush_quietly(sys.stderr)
        sys.stdout, sys.stderr = self._orig_sys
        self._orig_sys = ()
        for fd, orig_fd in self._orig_fds.items():
            os.dup2(orig_fd, fd)

    def _append_to_log(self, data: bytes) -> None:
        with self._lock:
            if self._log_closed:
                return
            try:
                self._log.write(data)
            except OSError:
                self._log_closed = True

    def _read_loop(self, read_end: int, orig_fd: int) -> None:
        """Forward each chunk to orig_fd unchanged and append its complete lines to the log."""
        forwarding = True
        pending = b""
        try:
            while True:
                chunk = os.read(read_end, RUN_LOG_READ_CHUNK_BYTES)
                if not chunk:
                    break
                if forwarding:
                    try:
                        _write_all(orig_fd, chunk)
                    except OSError:
                        forwarding = False
                pending += chunk
                cut = pending.rfind(b"\n") + 1
                if cut:
                    self._append_to_log(pending[:cut])
                    pending = pending[cut:]
            if pending:
                self._append_to_log(pending + b"\n")
        finally:
            os.close(read_end)
            os.close(orig_fd)

    def _write_footer(self, code: int, stuck_fds: list[int]) -> None:
        notes = "".join(
            f"note: output of fd {fd} still open by a child process after {RUN_LOG_JOIN_TIMEOUT_S:g} s is not logged\n"
            for fd in stuck_fds
        )
        footer = (
            "==== end of run ====\n"
            f"end: {datetime.now().astimezone().strftime(_TIME_FORMAT)}\n"
            f"{notes}"
            f"exit code: {code}\n"
            f"elapsed: {time.monotonic() - self._t0:.3f} s\n"
        )
        with self._lock:
            try:
                if self._log_closed:
                    raise OSError("run log no longer writable")
                self._log.write(footer.encode("utf-8"))
            except OSError as error:
                try:
                    os.write(2, f"run-log: footer not written: {error}\n".encode())
                except OSError:
                    pass
            finally:
                self._log_closed = True
                self._log.close()
