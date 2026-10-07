"""The repository's committed root .gitignore must ignore local-only artefacts.

REQ-GITIGNORE-01: the champollion_utils root ``.gitignore`` file shall
contain patterns that make git ignore the ``.alm/``, ``.codegraph/``,
``elm/`` and ``.pytest_cache/`` directories and the ``.coverage`` file at
the repository root.

``git check-ignore`` (even with ``--no-index``) honours
``$GIT_DIR/info/exclude`` and ``core.excludesFile``, so checking the live
repository would pass on local-only excludes (``.alm/`` is in this clone's
``info/exclude`` today). Instead the root ``.gitignore`` alone is copied
into a scratch repository, global excludes are disabled, and git's real
pattern matching is run there.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (path passed to git check-ignore, is_directory)
IGNORED_PATHS = [
    (".alm/", True),
    (".codegraph/", True),
    ("elm/", True),
    (".pytest_cache/", True),
    (".coverage", False),
]


@pytest.mark.parametrize(
    ("path", "is_dir"), IGNORED_PATHS, ids=[p for p, _ in IGNORED_PATHS]
)
def test_root_gitignore_ignores_local_artefact(path, is_dir, tmp_path):
    """REQ-GITIGNORE-01: root .gitignore alone makes git ignore ``path``."""
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), "champollion_utils has no root .gitignore"

    scratch = tmp_path / "repo"
    scratch.mkdir()
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    shutil.copyfile(gitignore, scratch / ".gitignore")

    name = path.rstrip("/")
    if is_dir:
        (scratch / name).mkdir()
        (scratch / name / "placeholder").write_text("")
    else:
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
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"champollion_utils/.gitignore has no pattern ignoring {path!r} "
        f"(git check-ignore rc={result.returncode}, "
        f"stderr={result.stderr.strip()!r})"
    )
