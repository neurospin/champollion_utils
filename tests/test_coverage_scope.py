"""The pyproject coverage configuration measures champollion_utils itself.

REQ-QUALITY-BDRABCZUK-1FCB607A4766: when the champollion_utils toolchain
test command measures coverage, the coverage report shall list only source
files located under the src/champollion_utils directory of the repository.

REQ-QUALITY-BDRABCZUK-F40484D5E893: when a champollion_utils test starts a
child Python process running champollion_utils code from the src directory,
the coverage report shall count the lines executed by the child process.

The toolchain test command is ``pytest --cov=champollion_utils``. The
``--cov`` value overrides any ``[tool.coverage.run] source`` and makes coverage
ignore ``[tool.coverage.run] include``, and ``source = champollion_utils``
matches modules by name, so a copy of the package imported from tmp_path
(``test_profiling_hook.py`` makes one) is measured as well. These tests run
that situation in a nested ``coverage run --source=champollion_utils`` driven
by the repository's own ``pyproject.toml`` (cwd = repository root, as for
``alm verify``): one child imports the package from ``src/`` and installs the
crash hook, a second child imports a copy of the package from tmp_path. The
coverage JSON report is then read.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("coverage")

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
PACKAGE_DIR = SRC_DIR / "champollion_utils"
PYPROJECT = REPO_ROOT / "pyproject.toml"

CHILD_SCRIPT = """\
from champollion_utils import logger

logger.install_excepthook()
"""

PARENT_SCRIPT = """\
import os
import subprocess
import sys

child, src_path, copy_path = sys.argv[1:4]
for pythonpath in (src_path, copy_path):
    env = dict(os.environ, PYTHONPATH=pythonpath)
    subprocess.run([sys.executable, child], env=env, check=True)
"""


def _coverage_free_env() -> dict[str, str]:
    """The environment without the outer test run's coverage variables."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("COV", "PYTEST"))
    }


NO_DATA = "No data to report."


def _coverage(args: list[str], env: dict[str, str]) -> str:
    """Run one coverage command from the repository root; return its stdout."""
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "coverage", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode == 1 and NO_DATA in result.stdout:
        return result.stdout
    assert result.returncode == 0, (
        f"coverage {args[0]} failed (rc={result.returncode}): "
        f"{result.stderr.strip()[-2000:]}"
    )
    return result.stdout


@pytest.fixture(scope="module")
def coverage_report(tmp_path_factory) -> dict:
    """JSON coverage report of a parent and two child Python processes."""
    work = tmp_path_factory.mktemp("coverage_scope")
    copy_root = work / "copy"
    shutil.copytree(
        PACKAGE_DIR,
        copy_root / "champollion_utils",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    child = work / "child.py"
    child.write_text(CHILD_SCRIPT, encoding="utf-8")
    parent = work / "parent.py"
    parent.write_text(PARENT_SCRIPT, encoding="utf-8")
    data_dir = work / "data"
    data_dir.mkdir()

    env = _coverage_free_env()
    env["COVERAGE_FILE"] = str(data_dir / ".coverage")
    env.pop("PYTHONPATH", None)
    rcfile = f"--rcfile={PYPROJECT}"
    _coverage(
        [
            "run",
            "--source=champollion_utils",
            rcfile,
            str(parent),
            str(child),
            str(SRC_DIR),
            str(copy_root),
        ],
        env,
    )
    _coverage(["combine", rcfile, str(data_dir)], env)
    report = work / "coverage.json"
    if NO_DATA in _coverage(["json", rcfile, "-o", str(report)], env):
        return {"files": {}}
    return json.loads(report.read_text(encoding="utf-8"))


def _report_path(name: str) -> Path:
    path = Path(name)
    return (path if path.is_absolute() else REPO_ROOT / path).resolve()


def _install_excepthook_first_line() -> int:
    """Line number of the first statement in ``logger.install_excepthook``."""
    tree = ast.parse((PACKAGE_DIR / "logger.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "install_excepthook":
            body = node.body
            if isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ):
                body = body[1:]
            return body[0].lineno
    raise AssertionError("logger.py defines no install_excepthook")


def test_coverage_report_lists_only_src_package_files(coverage_report):
    """REQ-QUALITY-BDRABCZUK-1FCB607A4766: no file outside src/champollion_utils."""
    package = PACKAGE_DIR.resolve()
    outside = sorted(
        name
        for name in coverage_report["files"]
        if not _report_path(name).is_relative_to(package)
    )
    assert outside == [], (
        f"coverage report lists {len(outside)} file(s) outside "
        f"src/champollion_utils: {outside[:5]}"
    )


def test_coverage_report_counts_child_process_lines(coverage_report):
    """REQ-QUALITY-BDRABCZUK-F40484D5E893: child-process lines are counted."""
    logger = (PACKAGE_DIR / "logger.py").resolve()
    executed = {
        line
        for name, data in coverage_report["files"].items()
        if _report_path(name) == logger
        for line in data["executed_lines"]
    }
    first = _install_excepthook_first_line()
    assert first in executed, (
        "the crash-hook line run by the child process "
        f"(logger.py:{first}) is not counted"
    )
