"""The champollion_utils manifests declare the 0.4.2 release version.

REQ-SCRIPTBUILDER-BDRABCZUK-C95059A5DADD: the [project] table of the
champollion_utils pyproject.toml shall declare version 0.4.2.

REQ-SCRIPTBUILDER-BDRABCZUK-0DB35B0589D7: the [project] table of the
champollion_utils pixi.toml shall declare version 0.4.2.

The ``[project]`` table is read with a small parser rather than ``tomllib``
because ``requires-python`` still admits Python 3.10, which has no
``tomllib``.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TABLE_HEADER = re.compile(r"^\s*\[\[?[^\]]+\]\]?\s*(#.*)?$")
_VERSION_LINE = re.compile(r"""^\s*version\s*=\s*["']([^"']+)["']\s*(#.*)?$""")


def _project_version(manifest: Path) -> str | None:
    """Return the ``version`` value of the ``[project]`` table, if any."""
    in_project = False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if _TABLE_HEADER.match(line):
            in_project = line.strip().split("#")[0].strip() == "[project]"
            continue
        if in_project:
            match = _VERSION_LINE.match(line)
            if match:
                return match.group(1)
    return None


def test_pyproject_declares_version_0_4_2():
    assert _project_version(REPO_ROOT / "pyproject.toml") == "0.4.2"


def test_pixi_toml_declares_version_0_4_2():
    assert _project_version(REPO_ROOT / "pixi.toml") == "0.4.2"
