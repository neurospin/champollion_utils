"""Tests for champollion_utils.logger custom levels and stream routing.

Requirements: REQ-LOGGER-01, 03, 04, 05, 08, 09, 10 (see .alm/REQUIREMENTS.md).
"""

import importlib
import logging
import uuid

import pytest

CUSTOM_LEVELS = ["CRASH", "FAIL", "LOG", "OUTPUT"]


def _import_logger_module():
    """Import the module under test inside the test body, not at collection.

    A missing module then fails the test itself (red), instead of
    surfacing as a collection or fixture error.
    """
    return importlib.import_module("champollion_utils.logger")


@pytest.fixture
def fresh_logger():
    """A uniquely-named stdlib logger, cleaned of handlers afterwards."""
    logger = logging.getLogger(f"test_champollion_logger.{uuid.uuid4().hex}")
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


STDLIB_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _level(logger_mod, name):
    if name in STDLIB_LEVELS:
        return getattr(logging, name)
    return getattr(logger_mod, name)


# REQ-LOGGER-01
@pytest.mark.parametrize("name", CUSTOM_LEVELS)
def test_custom_level_name_registered(name):
    logger_mod = _import_logger_module()
    value = getattr(logger_mod, name)
    assert isinstance(value, int)
    assert logging.getLevelName(name) == value
    assert logging.getLevelName(value) == name


# REQ-LOGGER-09 (supersedes REQ-LOGGER-02)
def test_custom_level_ordering():
    logger_mod = _import_logger_module()
    assert (
        logging.INFO
        < logger_mod.LOG
        < logger_mod.OUTPUT
        < logging.WARNING
        < logger_mod.FAIL
        < logger_mod.CRASH
    )


# REQ-LOGGER-03 (crash, fail, output) and REQ-LOGGER-08 (trace)
@pytest.mark.parametrize(
    "method, level_name",
    [
        ("crash", "CRASH"),
        ("fail", "FAIL"),
        ("output", "OUTPUT"),
        ("trace", "LOG"),
    ],
)
def test_helper_method_emits_at_level(fresh_logger, caplog, method, level_name):
    logger_mod = _import_logger_module()
    caplog.set_level(1, logger=fresh_logger.name)
    getattr(fresh_logger, method)("helper message")
    records = [r for r in caplog.records if r.name == fresh_logger.name]
    assert len(records) == 1
    assert records[0].levelno == getattr(logger_mod, level_name)
    assert records[0].levelname == level_name
    assert records[0].getMessage() == "helper message"


# REQ-LOGGER-04
def test_standard_level_names_unchanged():
    _import_logger_module()  # side effect: registers custom levels
    expected = {
        10: "DEBUG",
        20: "INFO",
        30: "WARNING",
        40: "ERROR",
        50: "CRITICAL",
    }
    for value, name in expected.items():
        assert logging.getLevelName(value) == name
        assert logging.getLevelName(name) == value


# REQ-LOGGER-05
@pytest.mark.parametrize(
    "level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR]
)
def test_logger_log_keeps_stdlib_behaviour(fresh_logger, caplog, level):
    _import_logger_module()  # side effect: registers custom levels
    caplog.set_level(1, logger=fresh_logger.name)
    fresh_logger.log(level, "value %s", 42)
    records = [r for r in caplog.records if r.name == fresh_logger.name]
    assert len(records) == 1
    assert records[0].levelno == level
    assert records[0].getMessage() == "value 42"


# REQ-LOGGER-10 (supersedes REQ-LOGGER-06): each record goes to exactly one stream
@pytest.mark.parametrize(
    "level_name, stream",
    [
        ("DEBUG", "out"),
        ("INFO", "out"),
        ("LOG", "out"),
        ("OUTPUT", "out"),
        ("WARNING", "err"),
        ("FAIL", "err"),
        ("ERROR", "err"),
        ("CRASH", "err"),
        ("CRITICAL", "err"),
    ],
)
def test_setup_logging_routes_level_to_stream(fresh_logger, capsys, level_name, stream):
    logger_mod = _import_logger_module()
    logger_mod.setup_logging(fresh_logger)
    fresh_logger.setLevel(1)
    fresh_logger.propagate = False
    marker = f"routing-marker-{uuid.uuid4().hex}"

    fresh_logger.log(_level(logger_mod, level_name), marker)

    captured = capsys.readouterr()
    other = "err" if stream == "out" else "out"
    assert getattr(captured, stream).count(marker) == 1
    assert marker not in getattr(captured, other)
