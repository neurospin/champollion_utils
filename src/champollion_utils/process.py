"""Per-process setup shared by every Champollion entry point."""

import logging

from champollion_utils.logger import install_excepthook, setup_logging


def init_process() -> logging.Logger:
    """Set up logging and the crash hook for this process; safe to call again.

    The root logger gets the stdout/stderr handlers of setup_logging, and
    uncaught exceptions are logged at CRASH.

    Returns:
        The root logger.
    """
    root = setup_logging()
    install_excepthook(root)
    # Profiling (pipeline TASK-221) hooks in here, only when CHAMPOLLION_PROFILE is set.
    return root
