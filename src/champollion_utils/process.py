"""Per-process setup shared by every Champollion entry point."""

import logging
import os
import sys
from pathlib import Path
from types import ModuleType

from champollion_utils.logger import install_excepthook, setup_logging

# Kept as literals: importing champollion_utils.profiling here would load it in unprofiled runs.
PROFILING_MODULE = "champollion_utils.profiling"
PROFILING_BOOT_MODULE = "_champollion_profiling_core"
PROFILE_ENV_PREFIX = "CHAMPOLLION_PROFILE"
PROFILE_DIR_ENV = "CHAMPOLLION_PROFILE"
PROFILE_MODE_ENV = "CHAMPOLLION_PROFILE_MODE"

profiling_logger = logging.getLogger(PROFILING_MODULE)


def init_process() -> logging.Logger:
    """Set up logging and the crash hook for this process; safe to call again.

    The root logger gets the stdout/stderr handlers of setup_logging, and
    uncaught exceptions are logged at CRASH. Profiling is started separately,
    by start_profiling, once the command line is parsed.

    Returns:
        The root logger.
    """
    root = setup_logging()
    install_excepthook(root)
    return root


def _profile_dir_problem(path: Path) -> str | None:
    """Why ``path`` cannot be the profile dir (None when usable); creates the leaf when its parent allows it."""
    if not path.exists():
        parent = path.parent
        if not (parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)):
            return "parent directory missing or not writable"
        try:
            path.mkdir()
        except OSError as error:
            return f"cannot create directory ({error.strerror})"
        return None
    if not path.is_dir():
        return "not a directory"
    if not os.access(path, os.W_OK | os.X_OK):
        return "directory not writable"
    return None


def _drop_profiling_env(pythonpath_before: str | None) -> None:
    """Remove every CHAMPOLLION_PROFILE* key and restore PYTHONPATH, so children run unprofiled."""
    for key in [key for key in os.environ if key.startswith(PROFILE_ENV_PREFIX)]:
        del os.environ[key]
    if pythonpath_before is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = pythonpath_before


def active_profiling() -> ModuleType | None:
    """The loaded profiling module with an open session (PROFILING_MODULE, then PROFILING_BOOT_MODULE), else None.

    Never imports: only modules already in sys.modules are looked at.
    """
    for name in (PROFILING_MODULE, PROFILING_BOOT_MODULE):
        module = sys.modules.get(name)
        if module is not None and module.is_profiling_enabled():
            return module
    return None


def start_profiling(
    profile_dir: str | os.PathLike | None, mode: str | None, stage: str | None = None
) -> bool:
    """Start profiling this process when --profile DIR or CHAMPOLLION_PROFILE asks for it.

    Args:
        profile_dir: value of --profile (None: only CHAMPOLLION_PROFILE can turn profiling on).
        mode: value of --profile-mode (None: keep CHAMPOLLION_PROFILE_MODE, default cpu).
        stage: stage name of the process's first segment.

    Returns:
        True when this process is profiled. Profiling problems log one WARNING
        and return False; they never raise.
    """
    if profile_dir is None and not os.environ.get(PROFILE_DIR_ENV):
        return False
    boot = sys.modules.get(PROFILING_BOOT_MODULE)
    if boot is not None and boot.is_profiling_enabled():
        if profile_dir is not None:
            profiling_logger.debug(
                "profiling: --profile ignored, this process is already profiled by its parent"
            )
        return boot.ensure_profiling_from_env(stage=stage)
    pythonpath_before = os.environ.get("PYTHONPATH")
    if profile_dir is not None:
        path = Path(profile_dir).absolute()
        problem = _profile_dir_problem(path)
        if problem is not None:
            profiling_logger.warning(f"profiling disabled: {problem}: {path}")
            _drop_profiling_env(pythonpath_before)
            return False
        os.environ[PROFILE_DIR_ENV] = str(path)
        if mode is not None:
            os.environ[PROFILE_MODE_ENV] = mode
    from champollion_utils import profiling

    active = profiling.ensure_profiling_from_env(stage=stage)
    if not active:
        _drop_profiling_env(pythonpath_before)
    return active
