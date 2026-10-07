"""Unified logging levels for the Champollion projects.

Importing this module registers four levels next to the stdlib ones and adds
one helper method per level to ``logging.Logger`` and ``logging.LoggerAdapter``:

    DEBUG 10 < INFO 20 < LOG 22 < OUTPUT 25 < WARNING 30 < ERROR 40
             < FAIL 45 < CRITICAL 50 < CRASH 55

    logger.trace(msg)   -> LOG     progress messages
    logger.output(msg)  -> OUTPUT  user-facing results
    logger.fail(msg)    -> FAIL    a step failed
    logger.crash(msg)   -> CRASH   uncaught exception, the process dies

Importing has no other side effect: no handler is added and no level is set.
``setup_logging`` routes records below WARNING to stdout and the others to
stderr, as ``HH:MM:SS LEVEL    module::qualname | message`` lines.
"""

import logging
import os
import sys
import warnings
from functools import partialmethod

LOG = 22
OUTPUT = 25
FAIL = 45
CRASH = 55

STDERR_THRESHOLD = logging.WARNING

_LEVELS = {"LOG": LOG, "OUTPUT": OUTPUT, "FAIL": FAIL, "CRASH": CRASH}
_HELPERS = {"trace": LOG, "output": OUTPUT, "fail": FAIL, "crash": CRASH}
_INSTALLED_MARKER = "_champollion_logger_helpers"
_HANDLER_MARKER = "_champollion_logger_handler"


def _install_levels_and_helpers():
    """Register the custom level names and attach one helper per level.

    Helpers are partials of ``log`` so that the record's caller information
    (funcName, module, lineno) points at the caller, not at this module.
    A helper name already defined by another library is left untouched.
    """
    for name, level in _LEVELS.items():
        logging.addLevelName(level, name)
    for cls in (logging.Logger, logging.LoggerAdapter):
        if getattr(cls, _INSTALLED_MARKER, False):
            continue
        for method, level in _HELPERS.items():
            if hasattr(cls, method):
                warnings.warn(
                    f"{cls.__name__}.{method} already exists; "
                    f"champollion_utils.logger leaves it unchanged",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            setattr(cls, method, partialmethod(cls.log, level))
        setattr(cls, _INSTALLED_MARKER, True)


_install_levels_and_helpers()


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is when a record is emitted."""

    def __init__(self):
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value):
        pass


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever sys.stderr is when a record is emitted."""

    def __init__(self):
        super().__init__(sys.stderr)

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


# ANSI SGR code per level; a level without an entry takes the nearest one below.
_LEVEL_COLOURS = {
    logging.DEBUG: "2",
    logging.INFO: "34",
    LOG: "36",
    OUTPUT: "32",
    logging.WARNING: "33",
    logging.ERROR: "31",
    FAIL: "1;31",
    logging.CRITICAL: "1;35",
    CRASH: "1;37;41",
}
_DIM = "2"
_RESET = "\x1b[0m"
_LEVEL_WIDTH = 8


def _sgr(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}{_RESET}"


def _level_colour(levelno: int) -> str:
    below = [level for level in _LEVEL_COLOURS if level <= levelno]
    return _LEVEL_COLOURS[max(below)] if below else _LEVEL_COLOURS[logging.DEBUG]


def _module_name(frame) -> str:
    """Import name of the frame's module (python -m name or file stem for __main__)."""
    name = frame.f_globals.get("__name__", "?")
    if name == "__main__":
        spec = frame.f_globals.get("__spec__")
        if spec is not None and spec.name:
            return spec.name
        return os.path.splitext(os.path.basename(frame.f_code.co_filename))[0]
    return name


def _qualname(frame) -> str:
    """Qualified name of the frame's function (co_qualname on 3.11+)."""
    code = frame.f_code
    qualname = getattr(code, "co_qualname", None)
    if qualname:
        return qualname
    # Python 3.10: look for the function among module globals and class members
    for obj in list(frame.f_globals.values()):
        if getattr(obj, "__code__", None) is code:
            return obj.__qualname__
        if isinstance(obj, type):
            for member in vars(obj).values():
                func = getattr(member, "__func__", member)
                if getattr(func, "__code__", None) is code:
                    return func.__qualname__
    return code.co_name


def _frame_provenance(frame) -> str:
    return f"{_module_name(frame)}::{_qualname(frame)}"


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _record_provenance(record: logging.LogRecord) -> str:
    """module::qualname of the code that logged the record.

    CRASH records with exception info point at the innermost frame that raised.
    Other records point at the caller, found on the stack while it is still
    logging; when the frame is gone the record's own fields are used.
    """
    if record.levelno == CRASH and record.exc_info and record.exc_info[2] is not None:
        tb = record.exc_info[2]
        while tb.tb_next is not None:
            tb = tb.tb_next
        return _frame_provenance(tb.tb_frame)

    frame = sys._getframe(1)
    while frame is not None:
        code = frame.f_code
        if (
            frame.f_lineno == record.lineno
            and code.co_name == record.funcName
            and _same_file(code.co_filename, record.pathname)
        ):
            return _frame_provenance(frame)
        frame = frame.f_back

    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and _same_file(path, record.pathname):
            return f"{name}::{record.funcName}"
    return f"{record.module}::{record.funcName}"


class _ChampollionFormatter(logging.Formatter):
    """`HH:MM:SS LEVEL    module::qualname | message`, level coloured on a TTY.

    The message is kept verbatim and last; a traceback follows unprefixed.
    """

    def __init__(self, stream_name: str):
        super().__init__(datefmt="%H:%M:%S")
        self._stream_name = stream_name

    def _use_colour(self) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if self._stream_name not in ("stdout", "stderr"):
            return False
        isatty = getattr(getattr(sys, self._stream_name, None), "isatty", None)
        try:
            return bool(isatty()) if callable(isatty) else False
        except (OSError, ValueError):
            # closed or detached stream
            return False

    def formatMessage(self, record: logging.LogRecord) -> str:
        level = record.levelname
        padding = " " * max(_LEVEL_WIDTH - len(level), 0)
        provenance = _record_provenance(record)
        if self._use_colour():
            level = _sgr(_level_colour(record.levelno), level)
            provenance = _sgr(_DIM, provenance)
        timestamp = self.formatTime(record, self.datefmt)
        return f"{timestamp} {level}{padding} {provenance} | {record.message}"


def make_default_formatter(stream_name: str) -> logging.Formatter:
    """Return the formatter used for the stdout or stderr handler."""
    return _ChampollionFormatter(stream_name)


def setup_logging(
    logger: logging.Logger | None = None,
    *,
    level: int = logging.INFO,
    propagate: bool = False,
    formatter: logging.Formatter | None = None,
) -> logging.Logger:
    """Route a logger's records to stdout (below WARNING) or stderr (WARNING and above).

    Args:
        logger: logger to configure; the root logger when None.
        level: threshold set on the logger.
        propagate: propagate flag set on a non-root logger.
        formatter: formatter for both handlers; make_default_formatter when None.

    Calling it again replaces the handlers it added before and leaves other
    handlers alone.

    Returns:
        The configured logger.
    """
    if logger is None:
        logger = logging.getLogger()

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    for handler, stream_name in (
        (_StdoutHandler(), "stdout"),
        (_StderrHandler(), "stderr"),
    ):
        if stream_name == "stdout":
            handler.addFilter(lambda record: record.levelno < STDERR_THRESHOLD)
        else:
            handler.addFilter(lambda record: record.levelno >= STDERR_THRESHOLD)
        handler.setFormatter(formatter or make_default_formatter(stream_name))
        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)

    logger.setLevel(level)
    if logger is not logging.getLogger():
        logger.propagate = propagate
    return logger
