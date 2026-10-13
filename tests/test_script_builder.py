"""Characterization tests for champollion_utils.script_builder.ScriptBuilder.

REQ-QUALITY-BDRABCZUK-9AF13FF27808: when pytest runs the champollion_utils
test suite with pytest-cov, the reported line coverage of
src/champollion_utils/script_builder.py shall be at least 85 percent.

These tests pin the CURRENT behaviour of ScriptBuilder (they pass on
arrival); their purpose is to make the module's coverage measurable and to
guard its behaviour while its ruff findings are fixed. The update check and
init_process are stubbed so no test touches the network or the pytest
process's root logger and crash hook.
"""

import argparse
import shutil
import sys

import pytest

from champollion_utils import script_builder
from champollion_utils.script_builder import ScriptBuilder


class DemoScript(ScriptBuilder):
    """Minimal concrete ScriptBuilder; run() returns a configurable code."""

    def __init__(self, exit_code: int = 0):
        super().__init__("demo", "demo script description")
        self.exit_code = exit_code
        self.calls = []

    def run(self) -> int:
        self.calls.append("run")
        return self.exit_code


@pytest.fixture
def no_update_check(monkeypatch):
    """Replace the network update check with a call counter."""
    calls = []
    monkeypatch.setattr(
        script_builder, "check_for_updates", lambda *a, **k: calls.append(1)
    )
    return calls


# --- construction -----------------------------------------------------------


def test_init_stores_name_description_and_no_args():
    builder = DemoScript()
    assert builder.script_name == "demo"
    assert builder.description == "demo script description"
    assert builder.args is None
    assert isinstance(builder.parser, argparse.ArgumentParser)
    assert builder.parser.prog == "demo"
    assert builder.parser.description == "demo script description"
    assert builder.parser.formatter_class is argparse.ArgumentDefaultsHelpFormatter


def test_scriptbuilder_is_abstract():
    with pytest.raises(TypeError):
        ScriptBuilder("x", "y")


# --- argument builders --------------------------------------------------------


def test_add_argument_returns_self_and_registers_argument():
    builder = DemoScript()
    assert builder.add_argument("--count", type=int, default=3) is builder
    assert builder.parse_args(["--count", "7"]).count == 7
    assert builder.parse_args([]).count == 3


def test_add_required_argument_converts_type_and_is_required():
    builder = DemoScript()
    assert builder.add_required_argument("--size", "the size", type_=int) is builder
    assert builder.parse_args(["--size", "12"]).size == 12
    with pytest.raises(SystemExit) as exc:
        builder.parse_args([])
    assert exc.value.code == 2


def test_add_required_argument_defaults_to_str():
    builder = DemoScript().add_required_argument("--name", "a name")
    assert builder.parse_args(["--name", "42"]).name == "42"


def test_add_optional_argument_with_default_and_type():
    builder = DemoScript()
    assert (
        builder.add_optional_argument("--ratio", "a ratio", default=0.5, type_=float)
        is builder
    )
    assert builder.parse_args([]).ratio == 0.5
    assert builder.parse_args(["--ratio", "0.25"]).ratio == 0.25


def test_add_optional_argument_defaults_to_none():
    builder = DemoScript().add_optional_argument("--label", "a label")
    assert builder.parse_args([]).label is None


def test_add_optional_argument_with_action_ignores_default_and_type():
    builder = DemoScript().add_optional_argument(
        "--verbose", "be verbose", default="ignored", type_=int, action="store_true"
    )
    assert builder.parse_args([]).verbose is False
    assert builder.parse_args(["--verbose"]).verbose is True


def test_add_flag_is_store_true():
    builder = DemoScript()
    assert builder.add_flag("--dry-run", "no side effects") is builder
    assert builder.parse_args([]).dry_run is False
    assert builder.parse_args(["--dry-run"]).dry_run is True


def test_parse_args_stores_namespace_on_builder():
    builder = DemoScript().add_flag("--x", "x")
    ns = builder.parse_args(["--x"])
    assert isinstance(ns, argparse.Namespace)
    assert builder.args is ns


# --- build / print_args -------------------------------------------------------


def test_build_runs_update_check_parses_sys_argv_and_returns_self(
    monkeypatch, no_update_check
):
    monkeypatch.setattr(sys, "argv", ["demo", "--size", "5"])
    builder = DemoScript().add_required_argument("--size", "size", type_=int)
    assert builder.build() is builder
    assert no_update_check == [1]
    assert builder.args.size == 5


def test_print_args_prints_header_and_namespace(capsys):
    builder = DemoScript().add_optional_argument("--label", "label", default="abc")
    builder.parse_args([])
    assert builder.print_args() is builder
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "demo arguments:"
    assert "{'label': 'abc'}" in out


# --- validate_paths -----------------------------------------------------------


def test_validate_paths_true_when_every_path_exists(tmp_path, capsys):
    (tmp_path / "f.txt").write_text("")
    assert DemoScript().validate_paths([str(tmp_path), str(tmp_path / "f.txt")])
    assert capsys.readouterr().out == ""


def test_validate_paths_true_for_empty_list():
    assert DemoScript().validate_paths([]) is True


def test_validate_paths_false_and_reports_first_missing_path(tmp_path, capsys):
    missing = tmp_path / "missing"
    other = tmp_path / "other-missing"
    assert (
        DemoScript().validate_paths([str(tmp_path), str(missing), str(other)]) is False
    )
    out = capsys.readouterr().out
    assert out == f"Error: Path does not exist: {missing}\n"


# --- build_command ------------------------------------------------------------


def _builder_with_args(**values):
    builder = DemoScript()
    builder.args = argparse.Namespace(**values)
    return builder


def test_build_command_starts_with_interpreter_and_script():
    builder = _builder_with_args()
    assert builder.build_command("child.py", []) == [sys.executable, "child.py"]


def test_build_command_renders_required_args_as_key_value():
    builder = _builder_with_args(src="/in", n=3)
    assert builder.build_command("child.py", ["src", "n"]) == [
        sys.executable,
        "child.py",
        "--src=/in",
        "--n=3",
    ]


def test_build_command_required_arg_missing_from_namespace_raises():
    with pytest.raises(AttributeError):
        _builder_with_args().build_command("child.py", ["absent"])


def test_build_command_optional_args_rendering():
    builder = _builder_with_args(
        flag=True, items=["a", "b"], level=2, same="x", unset=None
    )
    defaults = {
        "flag": False,
        "items": [],
        "level": 1,
        "same": "x",
        "unset": "d",
        "not_in_namespace": "d",
    }
    cmd = builder.build_command(
        "child.py", [], optional_args={"ignored": 1}, defaults=defaults
    )
    assert cmd == [
        sys.executable,
        "child.py",
        "--flag",
        "--items=a",
        "--items=b",
        "--level=2",
    ]


def test_build_command_false_bool_differing_from_default_is_rendered_as_value():
    # Current behaviour: a False flag whose default is True is not dropped,
    # it falls through to the generic "--name=value" form.
    builder = _builder_with_args(flag=False)
    cmd = builder.build_command("child.py", [], defaults={"flag": True})
    assert cmd == [sys.executable, "child.py", "--flag=False"]


def test_build_command_without_defaults_ignores_optional_values():
    builder = _builder_with_args(flag=True, level=9)
    assert builder.build_command("child.py", []) == [sys.executable, "child.py"]


# --- execute_command ----------------------------------------------------------


def test_execute_command_prints_and_returns_zero_on_success(capsys):
    cmd = [sys.executable, "-c", "pass"]
    assert DemoScript().execute_command(cmd) == 0
    out = capsys.readouterr().out
    assert out.startswith("Running command:\n" + " ".join(cmd) + "\n")


def test_execute_command_returns_one_on_nonzero_exit(capsys):
    cmd = [sys.executable, "-c", "raise SystemExit(4)"]
    assert DemoScript().execute_command(cmd) == 1
    assert "Error running command:" in capsys.readouterr().out


def test_execute_command_returns_one_when_executable_missing(tmp_path, capsys):
    cmd = [str(tmp_path / "no-such-executable")]
    assert DemoScript().execute_command(cmd) == 1
    assert "Error running command:" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_execute_command_shell_returns_exit_status(capsys):
    assert DemoScript().execute_command(["exit", "3"], shell=True) == 3
    assert DemoScript().execute_command(["true"], shell=True) == 0
    assert "Running command:\nexit 3\n" in capsys.readouterr().out


# --- hooks and main -----------------------------------------------------------


def test_default_hooks_do_nothing():
    builder = DemoScript()
    assert builder.before_run() is None
    assert builder.after_run(success=True) is None
    assert builder.after_run(success=False) is None


class HookedScript(DemoScript):
    """Records the order in which main() calls the builder's steps."""

    def build(self):
        self.calls.append("build")
        return super().build()

    def print_args(self):
        self.calls.append("print_args")
        return super().print_args()

    def before_run(self):
        self.calls.append("before_run")

    def after_run(self, success):
        self.calls.append(("after_run", success))


@pytest.mark.parametrize(("exit_code", "success"), [(0, True), (5, False)])
def test_main_runs_steps_in_order_and_returns_exit_code(
    monkeypatch, no_update_check, capsys, exit_code, success
):
    monkeypatch.setattr(sys, "argv", ["demo"])
    script = HookedScript(exit_code=exit_code)
    monkeypatch.setattr(
        script_builder, "init_process", lambda: script.calls.append("init_process")
    )
    assert script.main() == exit_code
    assert script.calls == [
        "init_process",
        "build",
        "print_args",
        "before_run",
        "run",
        ("after_run", success),
    ]
    assert no_update_check == [1]
    assert "demo arguments:" in capsys.readouterr().out
