"""Cross-process profiling (opt-in through --profile DIR or CHAMPOLLION_PROFILE).

Re-exports the public names of ``core``. When the profiling boot file already
loaded ``core.py`` in this process, that module is reused, so the process keeps
a single profiling session.
"""

import sys

_BOOT_MODULE_NAME = "_champollion_profiling_core"

if _BOOT_MODULE_NAME in sys.modules:
    sys.modules[f"{__name__}.core"] = sys.modules[_BOOT_MODULE_NAME]

from champollion_utils.profiling.core import (
    ENV_PROFILE_DIR,
    ENV_PROFILE_GPU,
    ENV_PROFILE_INTERVAL,
    ENV_PROFILE_MODE,
    ENV_PROFILE_PARENT,
    ENV_PROFILE_RUN,
    ENV_PROFILE_SAMPLER,
    ENV_PROFILE_STAGE,
    ENV_PROFILE_TAGS,
    LOGGER_NAME,
    MODES,
    InvalidProfileMode,
    ensure_profiling_from_env,
    get_boot_dir,
    is_profiling_enabled,
    make_profiling_env,
    new_run_id,
    profile_segment,
    resolve_mode,
    write_boot_dir,
)

__all__ = [
    "ENV_PROFILE_DIR",
    "ENV_PROFILE_GPU",
    "ENV_PROFILE_INTERVAL",
    "ENV_PROFILE_MODE",
    "ENV_PROFILE_PARENT",
    "ENV_PROFILE_RUN",
    "ENV_PROFILE_SAMPLER",
    "ENV_PROFILE_STAGE",
    "ENV_PROFILE_TAGS",
    "LOGGER_NAME",
    "MODES",
    "InvalidProfileMode",
    "ensure_profiling_from_env",
    "get_boot_dir",
    "is_profiling_enabled",
    "make_profiling_env",
    "new_run_id",
    "profile_segment",
    "resolve_mode",
    "write_boot_dir",
]
