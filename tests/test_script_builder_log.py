"""Tests for the ScriptBuilder ``--log [PATH]`` option (run log through RunLogCapture).

Task: TASK-RUNLOG-BDRABCZUK-69F1A5D6C5CF. Requirements:
REQ-RUNLOG-BDRABCZUK-EE680ACE5753 (no run log without --log),
REQ-RUNLOG-BDRABCZUK-DA480BA6D303 (--log PATH),
REQ-RUNLOG-BDRABCZUK-6D5CCBFB0BB8 (bare --log uses default_log_path()),
REQ-RUNLOG-BDRABCZUK-DD27802F0906 (default path ./.log/<prog>_<YYYYmmdd-HHMMSS>.log),
REQ-RUNLOG-BDRABCZUK-6C2C17856A41 (footer exit code is run()'s return value),
REQ-RUNLOG-BDRABCZUK-89188BE1BF06 (footer exit code 1 when run() raises).

main() redirects file descriptors 1 and 2 when --log is given, so each main()
scenario runs in a fresh interpreter: the capture and the logging that main()
sets up never touch the pytest process. The update check is stubbed out in the
driver so no test touches the network.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from champollion_utils.script_builder import ScriptBuilder

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
RUN_MARKER = "RUN-OUTPUT-runlog-7f3a"
END_OF_RUN = "==== end of run ===="

DRIVER = textwrap.dedent(
    """
    import json
    import sys

    from champollion_utils import script_builder
    from champollion_utils.script_builder import ScriptBuilder

    script_builder.check_for_updates = lambda *args, **kwargs: None

    CONFIG = json.loads(sys.argv[1])
    SCRIPT_ARGS = sys.argv[2:]


    class DemoScript(ScriptBuilder):
        def __init__(self):
            super().__init__("demo", "demo script for the --log option")

        def run(self):
            print({marker!r}, flush=True)
            if CONFIG["run"] == "raise":
                raise RuntimeError("boom-runlog")
            return int(CONFIG["run"])


    if CONFIG.get("override_after_run"):

        def _after_run(self, success):
            print("after-run-override success=" + str(success), flush=True)

        DemoScript.after_run = _after_run

    if CONFIG.get("log_path_override"):
        DemoScript.default_log_path = lambda self: CONFIG["log_path_override"]

    sys.argv = ["demo", *SCRIPT_ARGS]
    script = DemoScript()
    try:
        code = script.main()
    except RuntimeError as error:
        print("MAIN-RAISED=" + str(error), flush=True)
        sys.exit(0)
    print("ARGS-LOG=" + repr(getattr(script.args, "log", "<missing>")), flush=True)
    print("MAIN-RETURNED=" + str(code), flush=True)
    """
).format(marker=RUN_MARKER)


class DemoScript(ScriptBuilder):
    def __init__(self):
        super().__init__("demo", "demo script for default_log_path")

    def run(self):
        return 0


def _run_driver(tmp_path, workdir, script_args, **config):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    config.setdefault("run", 0)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, str(driver), json.dumps(config), *script_args],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _workdir(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    return workdir


def _describe(result):
    return (
        f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _footer(log_text):
    assert END_OF_RUN in log_text, log_text
    return log_text.split(END_OF_RUN, 1)[1]


# REQ-RUNLOG-BDRABCZUK-EE680ACE5753
def test_main_without_log_option_writes_no_run_log(tmp_path):
    workdir = _workdir(tmp_path)
    result = _run_driver(tmp_path, workdir, [])

    assert "MAIN-RETURNED=0" in result.stdout, _describe(result)
    assert "ARGS-LOG=None" in result.stdout, _describe(result)
    assert sorted(p.name for p in workdir.rglob("*")) == [], _describe(result)


# REQ-RUNLOG-BDRABCZUK-DA480BA6D303
def test_main_with_log_path_copies_run_output_to_that_file(tmp_path):
    workdir = _workdir(tmp_path)
    log_path = tmp_path / "logs" / "nested" / "run.log"
    result = _run_driver(tmp_path, workdir, ["--log", str(log_path)])

    assert "MAIN-RETURNED=0" in result.stdout, _describe(result)
    assert log_path.is_file(), _describe(result)
    assert RUN_MARKER in log_path.read_text().splitlines(), _describe(result)


# REQ-RUNLOG-BDRABCZUK-6D5CCBFB0BB8
def test_main_with_bare_log_uses_overridden_default_log_path(tmp_path):
    workdir = _workdir(tmp_path)
    log_path = tmp_path / "stage_derivatives" / "stage.log"
    result = _run_driver(tmp_path, workdir, ["--log"], log_path_override=str(log_path))

    assert "MAIN-RETURNED=0" in result.stdout, _describe(result)
    assert log_path.is_file(), _describe(result)
    assert RUN_MARKER in log_path.read_text().splitlines(), _describe(result)


# REQ-RUNLOG-BDRABCZUK-6D5CCBFB0BB8 (with the base default_log_path)
def test_main_with_bare_log_uses_base_default_log_path(tmp_path):
    workdir = _workdir(tmp_path)
    result = _run_driver(tmp_path, workdir, ["--log"])

    assert "MAIN-RETURNED=0" in result.stdout, _describe(result)
    logs = sorted((workdir / ".log").glob("demo_*.log"))
    assert len(logs) == 1, _describe(result)
    assert RUN_MARKER in logs[0].read_text().splitlines(), _describe(result)


# REQ-RUNLOG-BDRABCZUK-DD27802F0906
def test_default_log_path_is_dot_log_prog_timestamp_under_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = datetime.now().astimezone().replace(microsecond=0)
    path = DemoScript().default_log_path()
    after = datetime.now().astimezone()

    assert not os.path.isabs(path), path
    directory, name = os.path.split(os.path.normpath(path))
    assert directory == ".log", path
    match = re.fullmatch(r"demo_(\d{8}-\d{6})\.log", name)
    assert match, path
    stamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").astimezone()
    assert before <= stamp <= after, (path, before, after)


# REQ-RUNLOG-BDRABCZUK-6C2C17856A41
def test_main_with_log_footer_exit_code_is_run_return_value(tmp_path):
    workdir = _workdir(tmp_path)
    log_path = tmp_path / "run.log"
    result = _run_driver(
        tmp_path, workdir, ["--log", str(log_path)], run=3, override_after_run=True
    )

    assert "MAIN-RETURNED=3" in result.stdout, _describe(result)
    assert "after-run-override success=False" in result.stdout, _describe(result)
    assert log_path.is_file(), _describe(result)
    assert re.search(r"^exit code: 3$", _footer(log_path.read_text()), re.MULTILINE), (
        log_path.read_text()
    )


# REQ-RUNLOG-BDRABCZUK-89188BE1BF06
def test_main_with_log_footer_exit_code_is_one_when_run_raises(tmp_path):
    workdir = _workdir(tmp_path)
    log_path = tmp_path / "run.log"
    result = _run_driver(tmp_path, workdir, ["--log", str(log_path)], run="raise")

    assert "MAIN-RAISED=boom-runlog" in result.stdout, _describe(result)
    assert log_path.is_file(), _describe(result)
    assert re.search(r"^exit code: 1$", _footer(log_path.read_text()), re.MULTILINE), (
        log_path.read_text()
    )
