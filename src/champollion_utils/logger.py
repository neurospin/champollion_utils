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
stderr.
"""

import logging
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


def make_default_formatter(stream_name: str) -> logging.Formatter:
    """Return the formatter used for the stdout or stderr handler."""
    return logging.Formatter("%(levelname)s: %(message)s")


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
