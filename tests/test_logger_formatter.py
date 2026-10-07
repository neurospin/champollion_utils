"""Tests for the default formatter installed by champollion_utils.logger.setup_logging.

TASK-002. Requirements: REQ-LOGGER-11 to REQ-LOGGER-16 and REQ-LOGGER-25 (see .alm/REQUIREMENTS.md).

Provenance is checked against a throwaway package written to tmp_path, so the
module's import name (``pkg.emitter``) differs from its file stem
(``emitter``). A formatter that used ``record.module`` or a file path would
fail these tests.
"""

import importlib
import io
import logging
import re
import sys
import textwrap
import time
import uuid

import pytest

ALL_LEVELS = [
    ("DEBUG", "out"),
    ("INFO", "out"),
    ("LOG", "out"),
    ("OUTPUT", "out"),
    ("WARNING", "err"),
    ("ERROR", "err"),
    ("FAIL", "err"),
    ("CRITICAL", "err"),
    ("CRASH", "err"),
]

STDLIB_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# An SGR colour sequence that is not itself a reset, and the reset sequence.
SGR_COLOUR = r"\x1b\[(?!0?m)[0-9;]+m"
SGR_RESET = r"\x1b\[0?m"

EMITTER_SOURCE = textwrap.dedent(
    """
    def emit_from_function(logger, msg):
        logger.output(msg)


    class Emitter:
        def emit(self, logger, msg):
            logger.output(msg)


    class Stage:
        def explode(self):
            raise ValueError("inner-marker")


    def run_stage():
        Stage().explode()
    """
)


def _import_logger_module():
    """Import the module under test inside the test body, not at collection."""
    return importlib.import_module("champollion_utils.logger")


def _level(logger_mod, name):
    if name in STDLIB_LEVELS:
        return getattr(logging, name)
    return getattr(logger_mod, name)


@pytest.fixture
def fresh_logger():
    """A uniquely-named stdlib logger, cleaned of handlers afterwards."""
    logger = logging.getLogger(f"test_champollion_formatter.{uuid.uuid4().hex}")
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def emitter_module(tmp_path, monkeypatch):
    """Import a throwaway ``<pkg>.emitter`` module from a real file on disk."""
    package = f"champollion_fmt_pkg_{uuid.uuid4().hex}"
    package_dir = tmp_path / package
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "emitter.py").write_text(EMITTER_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module(f"{package}.emitter")
    yield module
    sys.modules.pop(f"{package}.emitter", None)
    sys.modules.pop(package, None)


def _configured(logger_mod, logger):
    logger_mod.setup_logging(logger, level=1)
    return logger


def _line_with(text, marker):
    lines = [line for line in text.splitlines() if marker in line]
    assert len(lines) == 1, f"expected one line containing {marker!r}, got {text!r}"
    return lines[0]


class _TTYStream(io.StringIO):
    def isatty(self):
        return True


# REQ-LOGGER-11: level name and verbatim message on one line (green on arrival;
# guards the MCP server's "Fold N/M" stdout parsing).
@pytest.mark.parametrize("level_name, stream", ALL_LEVELS)
def test_default_format_keeps_level_name_and_message_verbatim(
    fresh_logger, capsys, level_name, stream
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)
    message = f"Fold 3/56 done [{uuid.uuid4().hex}]"

    fresh_logger.log(_level(logger_mod, level_name), message)

    line = _line_with(getattr(capsys.readouterr(), stream), message)
    assert level_name in line


# REQ-LOGGER-12: caller provenance <module __name__>::<function __qualname__>
def test_default_format_shows_module_and_function_provenance(
    fresh_logger, capsys, emitter_module
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)
    message = f"provenance-marker-{uuid.uuid4().hex}"

    emitter_module.emit_from_function(fresh_logger, message)

    line = _line_with(capsys.readouterr().out, message)
    assert f"{emitter_module.__name__}::emit_from_function" in line


# REQ-LOGGER-12: the qualified name includes the enclosing class
def test_default_format_shows_method_qualname_provenance(
    fresh_logger, capsys, emitter_module
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)
    message = f"provenance-marker-{uuid.uuid4().hex}"

    emitter_module.Emitter().emit(fresh_logger, message)

    line = _line_with(capsys.readouterr().out, message)
    assert f"{emitter_module.__name__}::Emitter.emit" in line


# REQ-LOGGER-13: CRASH provenance is the innermost traceback frame, not the caller
def test_crash_format_shows_innermost_frame_provenance(
    fresh_logger, capsys, emitter_module
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)

    try:
        emitter_module.run_stage()
    except ValueError:
        fresh_logger.crash("crash-marker", exc_info=True)

    err = capsys.readouterr().err
    expected = f"{emitter_module.__name__}::Stage.explode"
    assert any(expected in line for line in err.splitlines()), err


# REQ-LOGGER-14: the formatted traceback follows the message line
# (green on arrival: stdlib Formatter already appends exc_text).
def test_crash_format_appends_traceback_after_message(
    fresh_logger, capsys, emitter_module
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)

    try:
        emitter_module.run_stage()
    except ValueError:
        fresh_logger.crash("crash-marker", exc_info=True)

    lines = capsys.readouterr().err.splitlines()
    message_index = next(i for i, line in enumerate(lines) if "crash-marker" in line)
    after = lines[message_index + 1 :]
    assert "Traceback (most recent call last):" in after
    assert "ValueError: inner-marker" in after


# REQ-LOGGER-15: TTY destination -> ANSI colour before the level name, reset after
@pytest.mark.parametrize("level_name, stream", ALL_LEVELS)
def test_tty_stream_gets_ansi_colour_around_level_name(
    fresh_logger, monkeypatch, level_name, stream
):
    logger_mod = _import_logger_module()
    monkeypatch.delenv("NO_COLOR", raising=False)
    fake = _TTYStream()
    monkeypatch.setattr(sys, "stdout" if stream == "out" else "stderr", fake)
    _configured(logger_mod, fresh_logger)
    message = f"tty-marker-{uuid.uuid4().hex}"

    fresh_logger.log(_level(logger_mod, level_name), message)

    line = _line_with(fake.getvalue(), message)
    pattern = SGR_COLOUR + r"[^\x1b]*" + re.escape(level_name) + r"[^\x1b]*" + SGR_RESET
    assert re.search(pattern, line), repr(line)


# REQ-LOGGER-16: non-TTY destination -> no ANSI escape at all (green on arrival)
@pytest.mark.parametrize("level_name, stream", ALL_LEVELS)
def test_non_tty_stream_gets_no_ansi_escape(fresh_logger, capsys, level_name, stream):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)
    message = f"plain-marker-{uuid.uuid4().hex}"

    fresh_logger.log(_level(logger_mod, level_name), message)

    written = getattr(capsys.readouterr(), stream)
    assert message in written
    assert "\x1b" not in written


# REQ-LOGGER-25: non-TTY line begins with local HH:MM:SS, one space, level name
@pytest.mark.parametrize("level_name, stream", ALL_LEVELS)
def test_default_format_line_starts_with_time_and_level_name(
    fresh_logger, capsys, level_name, stream
):
    logger_mod = _import_logger_module()
    _configured(logger_mod, fresh_logger)
    message = f"time-marker-{uuid.uuid4().hex}"
    created = 1_700_000_000.25
    record = fresh_logger.makeRecord(
        fresh_logger.name,
        _level(logger_mod, level_name),
        __file__,
        1,
        message,
        None,
        None,
    )
    record.created = created
    record.msecs = 250.0

    fresh_logger.handle(record)

    line = _line_with(getattr(capsys.readouterr(), stream), message)
    expected = time.strftime("%H:%M:%S", time.localtime(created)) + " " + level_name
    assert line.startswith(expected), repr(line)
