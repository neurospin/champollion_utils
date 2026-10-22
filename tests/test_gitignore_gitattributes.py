"""The repository's committed root .gitignore must ignore the root .gitattributes.

REQ-GITIGNORE-BDRABCZUK-0962420F4123: the champollion_utils root
``.gitignore`` file shall contain a pattern that makes git ignore the
``.gitattributes`` file at the repository root.

alm 6 writes ``.alm/*.jsonl merge=union`` to a root ``.gitattributes`` when
it expects an in-repo ``.alm/``. Here ``.alm/`` is its own git repository,
where that rule lives, so the root file must never be tracked by the code
repository.

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


def test_root_gitignore_ignores_root_gitattributes(tmp_path):
    """REQ-GITIGNORE-BDRABCZUK-0962420F4123: root .gitattributes is ignored."""
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), "champollion_utils has no root .gitignore"

    scratch = tmp_path / "repo"
    scratch.mkdir()
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    shutil.copyfile(gitignore, scratch / ".gitignore")
    (scratch / ".gitattributes").write_text(".alm/*.jsonl merge=union\n")

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
            ".gitattributes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "champollion_utils/.gitignore has no pattern ignoring the root "
        f"'.gitattributes' (git check-ignore rc={result.returncode}, "
        f"stderr={result.stderr.strip()!r})"
    )
