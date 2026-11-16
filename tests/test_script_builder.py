"""Characterization tests for champollion_utils.script_builder.ScriptBuilder.

REQ-QUALITY-BDRABCZUK-9AF13FF27808: when pytest runs the champollion_utils
test suite with pytest-cov, the reported line coverage of
src/champollion_utils/script_builder.py shall be at least 85 percent.

These tests pin the CURRENT behaviour of ScriptBuilder (they pass on
arrival); their purpose is to make the module's coverage measurable and to
guard its behaviour while its ruff findings are fixed. The update check and
init_process are stubbed so no test touches the network or the pytest
process's root logger and crash hook.

LOGGING convention (pipeline TASK-LOGGING-BDRABCZUK-0D3FE48FC54E): ScriptBuilder's
own messages are records of the champollion_utils.script_builder logger, asserted
with caplog, not capsys:
- REQ-LOGGING-BDRABCZUK-F43C03C63B8D: validate_paths, missing path -> ERROR
- REQ-LOGGING-BDRABCZUK-5A6B0B17B4DE: print_args -> LOG (22)
- REQ-LOGGING-BDRABCZUK-BF5F53D2E9D1: execute_command line, before running -> LOG (22)
- REQ-LOGGING-BDRABCZUK-4FD155594041: execute_command failure -> ERROR
- REQ-LOGGING-BDRABCZUK-9BF22EA0B3FD: sys.stdout only through logging handlers
"""

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from champollion_utils import script_builder
from champollion_utils.logger import LOG
from champollion_utils.script_builder import ScriptBuilder

SCRIPT_BUILDER_LOGGER = "champollion_utils.script_builder"


def _builder_records(caplog, level=None):
    """Records of the champollion_utils.script_builder logger, optionally at one level."""
    return [
        record
        for record in caplog.records
        if record.name == SCRIPT_BUILDER_LOGGER
        and (level is None or record.levelno == level)
    ]


def _messages(caplog, level):
    return [record.getMessage() for record in _builder_records(caplog, level)]


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


# REQ-LOGGING-BDRABCZUK-5A6B0B17B4DE
def test_print_args_logs_header_and_namespace_at_log_level(caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    builder = DemoScript().add_optional_argument("--label", "label", default="abc")
    builder.parse_args([])
    assert builder.print_args() is builder
    logged = "\n".join(_messages(caplog, LOG))
    assert logged, "no LOG-level record from print_args"
    assert logged.splitlines()[0] == "demo arguments:", logged
    assert "{'label': 'abc'}" in logged
    # the unset --log option stays hidden, as before
    assert "'log'" not in logged


# --- validate_paths -----------------------------------------------------------


def test_validate_paths_true_when_every_path_exists(tmp_path, capsys, caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    (tmp_path / "f.txt").write_text("")
    assert DemoScript().validate_paths([str(tmp_path), str(tmp_path / "f.txt")])
    assert capsys.readouterr().out == ""
    assert _builder_records(caplog) == []


def test_validate_paths_true_for_empty_list():
    assert DemoScript().validate_paths([]) is True


# REQ-LOGGING-BDRABCZUK-F43C03C63B8D
def test_validate_paths_false_and_logs_first_missing_path_at_error(tmp_path, caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    missing = tmp_path / "missing"
    other = tmp_path / "other-missing"
    assert (
        DemoScript().validate_paths([str(tmp_path), str(missing), str(other)]) is False
    )
    errors = _messages(caplog, logging.ERROR)
    assert errors == [f"Error: Path does not exist: {missing}"], errors


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


def test_execute_command_logs_and_returns_zero_on_success(caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    cmd = [sys.executable, "-c", "pass"]
    assert DemoScript().execute_command(cmd) == 0
    assert any(" ".join(cmd) in message for message in _messages(caplog, LOG))
    assert _messages(caplog, logging.ERROR) == []


# REQ-SCRIPTBUILDER-BDRABCZUK-84F9453A9485: execute_command returns the exit
# status of the child process, in shell mode as in non-shell mode (a real
# Python child; 3 tells the exact status apart from the old collapse to 1).


@pytest.mark.parametrize("status", [3, 0])
@pytest.mark.parametrize("shell", [False, True])
def test_execute_command_returns_child_exit_status(shell, status):
    if shell and shutil.which("bash") is None:
        pytest.skip("bash not available")
    code = f"import sys; sys.exit({status})"
    if shell:
        cmd = [shlex.quote(sys.executable), "-c", shlex.quote(code)]
    else:
        cmd = [sys.executable, "-c", code]
    assert DemoScript().execute_command(cmd, shell=shell) == status


# REQ-LOGGING-BDRABCZUK-BF5F53D2E9D1
def test_execute_command_logs_command_line_at_log_level_before_running(
    monkeypatch, caplog
):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    cmd = [sys.executable, "-c", "pass"]
    seen_before_run = []

    def fake_run(*_args, **_kwargs):
        seen_before_run.extend(_messages(caplog, LOG))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(script_builder, "run", fake_run)
    assert DemoScript().execute_command(cmd) == 0
    assert any(" ".join(cmd) in message for message in seen_before_run), seen_before_run


# REQ-SCRIPTBUILDER-BDRABCZUK-4D641FDE89D3: a non-shell command naming no
# existing executable returns 1.


# REQ-LOGGING-BDRABCZUK-4FD155594041
def test_execute_command_returns_one_and_logs_error_when_executable_missing(
    tmp_path, caplog
):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    cmd = [str(tmp_path / "no-such-executable")]
    assert DemoScript().execute_command(cmd) == 1
    errors = _messages(caplog, logging.ERROR)
    assert any("No such file or directory" in message for message in errors), errors


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_execute_command_shell_returns_exit_status(caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    assert DemoScript().execute_command(["exit", "3"], shell=True) == 3
    assert DemoScript().execute_command(["true"], shell=True) == 0
    assert any("exit 3" in message for message in _messages(caplog, LOG))


# REQ-LOGGING-BDRABCZUK-9BF22EA0B3FD
def test_script_builder_methods_write_nothing_to_stdout_without_handlers(
    monkeypatch, tmp_path, capsys
):
    # No handler sees the records: only a direct write could reach sys.stdout.
    monkeypatch.setattr(logging.getLogger(SCRIPT_BUILDER_LOGGER), "propagate", False)
    builder = DemoScript().add_optional_argument("--label", "label", default="abc")
    builder.parse_args([])
    builder.print_args()
    builder.validate_paths([str(tmp_path / "missing")])
    builder.execute_command([sys.executable, "-c", "pass"])
    builder.execute_command([str(tmp_path / "no-such-executable")])
    assert capsys.readouterr().out == ""


# REQ-SCRIPTBUILDER-BDRABCZUK-8A3DDA545A8E: execute_command runs the command in
# the given cwd, shell and non-shell, without changing the caller's cwd.
# REQ-SCRIPTBUILDER-BDRABCZUK-BAA24D71878F: a missing cwd directory returns 1.

_WRITE_CWD = "import os; open('cwd.txt', 'w').write(os.getcwd())"


def test_execute_command_runs_child_in_cwd(tmp_path):
    before = os.getcwd()
    cmd = [sys.executable, "-c", _WRITE_CWD]
    assert DemoScript().execute_command(cmd, cwd=tmp_path) == 0
    assert (tmp_path / "cwd.txt").read_text() == os.path.realpath(tmp_path)
    assert os.getcwd() == before


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_execute_command_shell_runs_child_in_cwd(tmp_path):
    before = os.getcwd()
    assert (
        DemoScript().execute_command(
            ["pwd -P > cwd.txt"], shell=True, cwd=str(tmp_path)
        )
        == 0
    )
    assert (tmp_path / "cwd.txt").read_text().strip() == os.path.realpath(tmp_path)
    assert os.getcwd() == before


@pytest.mark.parametrize("shell", [False, True])
def test_execute_command_with_cwd_leaves_parent_cwd_unchanged_on_failure(
    tmp_path, shell
):
    before = os.getcwd()
    cmd = ["exit 4"] if shell else [sys.executable, "-c", "raise SystemExit(4)"]
    expected = 4
    assert DemoScript().execute_command(cmd, shell=shell, cwd=tmp_path) == expected
    assert os.getcwd() == before


@pytest.mark.parametrize("shell", [False, True])
def test_execute_command_missing_cwd_returns_one(tmp_path, shell, caplog):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
    before = os.getcwd()
    cmd = ["true"] if shell else [sys.executable, "-c", "pass"]
    missing = tmp_path / "no-such-directory"
    assert DemoScript().execute_command(cmd, shell=shell, cwd=missing) == 1
    errors = _messages(caplog, logging.ERROR)
    assert any("Error running command:" in message for message in errors), errors
    assert os.getcwd() == before


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
    monkeypatch, no_update_check, caplog, exit_code, success
):
    caplog.set_level(1, logger=SCRIPT_BUILDER_LOGGER)
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
    assert any("demo arguments:" in message for message in _messages(caplog, LOG))
