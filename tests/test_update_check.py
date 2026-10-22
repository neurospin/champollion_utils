"""Characterization tests for champollion_utils.update_check.

REQ-QUALITY-BDRABCZUK-E4AF2B1D4509: when pytest runs the champollion_utils
test suite with pytest-cov, the reported line coverage of
src/champollion_utils/update_check.py shall be at least 85 percent.

These tests pin the CURRENT behaviour of the update check (they pass on
arrival). The network is never touched: ``urllib.request.urlopen`` is
replaced by a fake for every check_for_updates test.
"""

import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from champollion_utils import update_check


class _FakeResponse:
    def __init__(self, text: str):
        self._body = text.encode("utf-8")

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Install a recording fake urlopen; set ``.text`` or ``.error`` per test."""

    class Fake:
        def __init__(self):
            self.text = 'version = "0.0.0"\n'
            self.error = None
            self.calls = []

        def __call__(self, url, timeout=None):
            self.calls.append((url, timeout))
            if self.error is not None:
                raise self.error
            return _FakeResponse(self.text)

    fake = Fake()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


@pytest.fixture
def local_toml(tmp_path, monkeypatch):
    """A local pixi.toml at version 0.2.1, returned by _find_local_toml."""
    toml = tmp_path / "pixi.toml"
    toml.write_text('[workspace]\nname = "x"\nversion = "0.2.1"\n', encoding="utf-8")
    monkeypatch.setattr(update_check, "_find_local_toml", lambda: toml)
    return toml


# --- _parse_version -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('version = "0.2.1"', (0, 2, 1)),
        ('[project]\nname = "p"\nversion="1.10"\n', (1, 10)),
        ('version   =   "3"\n', (3,)),
        ('name = "p"\n', None),
        ('version = "1.2.beta"\n', None),
        ('  version = "1.2.3"\n', None),
        ("", None),
    ],
    ids=[
        "simple",
        "multiline-no-spaces",
        "single-part",
        "absent",
        "non-numeric",
        "indented",
        "empty",
    ],
)
def test_parse_version(text, expected):
    assert update_check._parse_version(text) == expected


def test_parse_version_uses_first_version_line():
    text = 'version = "1.0.0"\n[dep]\nversion = "9.9.9"\n'
    assert update_check._parse_version(text) == (1, 0, 0)


# --- _find_local_toml ---------------------------------------------------------


def _deep_dir(root: Path, depth: int) -> Path:
    d = root
    for i in range(depth):
        d = d / f"d{i}"
    d.mkdir(parents=True)
    return d


def test_find_local_toml_returns_nearest_ancestor_pixi_toml(tmp_path, monkeypatch):
    project = tmp_path / "project"
    script_dir = _deep_dir(project, 2)
    (project / "pixi.toml").write_text("")
    (project / "d0" / "pixi.toml").write_text("")
    monkeypatch.setattr(sys, "argv", [str(script_dir / "script.py")])
    assert update_check._find_local_toml() == (project / "d0" / "pixi.toml").resolve()


def test_find_local_toml_finds_toml_eight_levels_up(tmp_path, monkeypatch):
    root = tmp_path / "root"
    script_dir = _deep_dir(root, 7)  # script.py's 8th parent is root
    (root / "pixi.toml").write_text("")
    monkeypatch.setattr(sys, "argv", [str(script_dir / "script.py")])
    assert update_check._find_local_toml() == (root / "pixi.toml").resolve()


def test_find_local_toml_stops_after_eight_levels(tmp_path, monkeypatch):
    root = tmp_path / "root"
    script_dir = _deep_dir(root, 8)  # root is script.py's 9th parent
    (root / "pixi.toml").write_text("")
    monkeypatch.setattr(sys, "argv", [str(script_dir / "script.py")])
    assert update_check._find_local_toml() is None


def test_find_local_toml_falls_back_to_module_file_without_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", [])
    module_file = Path(update_check.__file__).resolve()
    expected = next(
        (
            p / "pixi.toml"
            for p in module_file.parents[:8]
            if (p / "pixi.toml").exists()
        ),
        None,
    )
    assert update_check._find_local_toml() == expected


# --- check_for_updates --------------------------------------------------------


def test_newer_remote_version_prints_update_notice(local_toml, fake_urlopen, capsys):
    fake_urlopen.text = 'version = "0.3.0"\n'
    assert update_check.check_for_updates() is None
    out = capsys.readouterr().out
    assert (
        "*** Update available: champollion_pipeline 0.3.0 (you have 0.2.1). ***" in out
    )
    assert "Run:  pixi run update" in out
    assert "git checkout pixi.lock && pixi run update" in out


def test_remote_url_and_timeout_are_passed_to_urlopen(local_toml, fake_urlopen):
    update_check.check_for_updates(timeout=1.5)
    assert fake_urlopen.calls == [(update_check._REMOTE_URL, 1.5)]


def test_default_timeout_is_three_seconds(local_toml, fake_urlopen):
    update_check.check_for_updates()
    assert fake_urlopen.calls == [(update_check._REMOTE_URL, 3.0)]


def test_remote_url_points_at_pipeline_main_pixi_toml():
    assert update_check._REMOTE_URL == (
        "https://raw.githubusercontent.com/neurospin/champollion_pipeline/main/pixi.toml"
    )


@pytest.mark.parametrize(
    "remote",
    [
        'version = "0.2.1"\n',
        'version = "0.2.0"\n',
        'version = "0.1.9"\n',
        'name = "no version"\n',
    ],
    ids=["same", "older", "older-minor", "unparsable"],
)
def test_no_notice_unless_remote_is_newer(local_toml, fake_urlopen, capsys, remote):
    fake_urlopen.text = remote
    update_check.check_for_updates()
    assert capsys.readouterr().out == ""


def test_versions_compare_numerically_not_lexically(local_toml, fake_urlopen, capsys):
    fake_urlopen.text = 'version = "0.10.0"\n'
    update_check.check_for_updates()
    assert "champollion_pipeline 0.10.0 (you have 0.2.1)" in capsys.readouterr().out


def test_no_local_toml_skips_network(monkeypatch, fake_urlopen, capsys):
    monkeypatch.setattr(update_check, "_find_local_toml", lambda: None)
    update_check.check_for_updates()
    assert fake_urlopen.calls == []
    assert capsys.readouterr().out == ""


def test_local_toml_without_version_skips_network(
    tmp_path, monkeypatch, fake_urlopen, capsys
):
    toml = tmp_path / "pixi.toml"
    toml.write_text('[workspace]\nname = "x"\n', encoding="utf-8")
    monkeypatch.setattr(update_check, "_find_local_toml", lambda: toml)
    update_check.check_for_updates()
    assert fake_urlopen.calls == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("offline"), TimeoutError("slow"), OSError("reset")],
    ids=["urlerror", "timeout", "oserror"],
)
def test_network_errors_are_silent(local_toml, fake_urlopen, capsys, error):
    fake_urlopen.error = error
    assert update_check.check_for_updates() is None
    assert capsys.readouterr().out == ""


def test_undecodable_remote_body_is_silent(local_toml, monkeypatch, capsys):
    class BadResponse:
        def read(self):
            return b"\xff\xfe\xfa"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: BadResponse()
    )
    assert update_check.check_for_updates() is None
    assert capsys.readouterr().out == ""
