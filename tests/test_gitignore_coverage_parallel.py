"""The committed root .gitignore must ignore parallel coverage data files.

REQ-GITIGNORE-BDRABCZUK-E98716AD3D66: the champollion_utils root
``.gitignore`` file shall contain a pattern that makes git ignore files whose
names start with the ".coverage." prefix at the repository root.

``[tool.coverage.run] parallel = true`` (with ``patch = ["subprocess"]``)
makes coverage write one ``.coverage.<host>.<pid>.<random>`` data file per
process before combining them, so these files appear at the repository root
during and after a test run.

Same approach as ``test_gitignore.py``: ``git check-ignore`` honours
``$GIT_DIR/info/exclude`` and ``core.excludesFile`` even with
``--no-index``, so the root ``.gitignore`` alone is copied into a scratch
repository, global excludes are disabled, and git's real pattern matching
is run there.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PARALLEL_DATA_FILES = [
    ".coverage.myhost.12345.XkzQwErt",
    ".coverage.x",
]


@pytest.mark.parametrize("name", PARALLEL_DATA_FILES)
def test_root_gitignore_ignores_parallel_coverage_file(name, tmp_path):
    """REQ-GITIGNORE-BDRABCZUK-E98716AD3D66: root .coverage.* files are ignored."""
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), "champollion_utils has no root .gitignore"

    scratch = tmp_path / "repo"
    scratch.mkdir()
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    shutil.copyfile(gitignore, scratch / ".gitignore")
    (scratch / name).write_text("")

    result = subprocess.run(
        [
            "git",
            "-c",
            f"core.excludesFile={tmp_path / 'no-global-excludes'}",
            "-C",
            str(scratch),
            "check-ignore",
            "-q",
            "--no-index",
            name,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"champollion_utils/.gitignore has no pattern ignoring {name!r} "
        f"(git check-ignore rc={result.returncode}, "
        f"stderr={result.stderr.strip()!r})"
    )
