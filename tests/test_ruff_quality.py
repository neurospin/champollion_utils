"""ruff check and ruff format must be clean on the ScriptBuilder modules.

REQ-QUALITY-BDRABCZUK-0BD8CD7A3144: the ruff check command of the
champollion_utils pixi test environment, run with --isolated, shall report
zero findings for src/champollion_utils/script_builder.py and
src/champollion_utils/update_check.py.

REQ-QUALITY-BDRABCZUK-F3112FAFD795: the ruff format --check command of the
champollion_utils pixi test environment, run with --isolated, shall exit with
status 0 for the same two files.

``--isolated`` makes ruff ignore every configuration file, so the rule set
and format style are those of the ruff version pinned in ``pixi.lock``; a
per-file ignore added to a config file cannot make these tests pass.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "src/champollion_utils/script_builder.py",
    "src/champollion_utils/update_check.py",
]


def _ruff() -> str:
    """The ruff executable of the running environment (pixi test env)."""
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("ruff")
    if found is None:
        pytest.skip("ruff executable not available (run via `pixi run -e test`)")
    return found


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ruff(), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("path", FILES, ids=[Path(f).name for f in FILES])
def test_ruff_check_reports_no_findings(path):
    """REQ-QUALITY-BDRABCZUK-0BD8CD7A3144: ruff check --isolated finds nothing."""
    result = _run(
        ["check", "--isolated", "--no-cache", "--output-format", "concise", path]
    )
    assert result.returncode == 0, (
        f"ruff check --isolated reports findings in {path} "
        f"(rc={result.returncode}):\n{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("path", FILES, ids=[Path(f).name for f in FILES])
def test_ruff_format_check_passes(path):
    """REQ-QUALITY-BDRABCZUK-F3112FAFD795: ruff format --check --isolated is clean."""
    result = _run(["format", "--check", "--isolated", "--no-cache", path])
    assert result.returncode == 0, (
        f"ruff format --check --isolated would reformat {path} "
        f"(rc={result.returncode}):\n{result.stdout}{result.stderr}"
    )
