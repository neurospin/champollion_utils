"""Check whether a newer version of champollion_pipeline is available on GitHub."""

import re
import sys
import urllib.request
from pathlib import Path

_REMOTE_URL = (
    "https://raw.githubusercontent.com/neurospin/champollion_pipeline"
    "/main/pixi.toml"
)
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _find_local_toml() -> Path | None:
    """Walk up from the running script (sys.argv[0]) to find pixi.toml.

    Using sys.argv[0] rather than __file__ means we find the *pipeline*
    root regardless of where champollion_utils is installed.
    """
    start = Path(sys.argv[0]).resolve() if sys.argv else Path(__file__).resolve()
    candidate = start
    for _ in range(8):
        candidate = candidate.parent
        toml = candidate / "pixi.toml"
        if toml.exists():
            return toml
    return None


def _parse_version(text: str):
    """Return a tuple of ints from a semver string, e.g. (0, 2, 1)."""
    m = _VERSION_RE.search(text)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def check_for_updates(timeout: float = 3.0) -> None:
    """Print a one-line notice if a newer version exists on the main branch.

    Silently returns on any network or parse error so it never blocks a script
    from running.
    """
    try:
        local_toml = _find_local_toml()
        if local_toml is None:
            return
        local_ver = _parse_version(local_toml.read_text(encoding="utf-8"))
        if local_ver is None:
            return

        req = urllib.request.urlopen(_REMOTE_URL, timeout=timeout)
        remote_text = req.read().decode("utf-8")
        remote_ver = _parse_version(remote_text)
        if remote_ver is None:
            return

        local_str = ".".join(str(x) for x in local_ver)
        remote_str = ".".join(str(x) for x in remote_ver)

        if remote_ver > local_ver:
            print(
                f"\n*** Update available: champollion_pipeline {remote_str} "
                f"(you have {local_str}). ***\n"
                f"    Run:  pixi run update\n"
                f"    (If git pull fails due to a pixi.lock conflict, run:\n"
                f"     git checkout pixi.lock && pixi run update)\n"
            )
    except Exception:
        # No network, private repo, timeout — do not disrupt the script
        pass
