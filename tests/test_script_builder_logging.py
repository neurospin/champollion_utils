"""Tests for ScriptBuilder.main() setting up process logging (init_process).

TASK-004. Requirement: REQ-LOGGER-36 (see .alm/REQUIREMENTS.md).

The script runs in a fresh interpreter so the root logger and crash hook that
main() configures never leak into the pytest process. The update check is
stubbed out in the driver so the test never touches the network.
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
READY_MARKER = "DRIVER-READY-c21d"
EXIT_MARKER = "MAIN-RETURNED"

DRIVER = textwrap.dedent(
    """
    import logging
    import sys

    import champollion_utils.logger  # noqa: F401 - registers logger.output
    from champollion_utils import script_builder
    from champollion_utils.script_builder import ScriptBuilder

    script_builder.check_for_updates = lambda *args, **kwargs: None


    class DemoScript(ScriptBuilder):
        def __init__(self):
            super().__init__("demo", "demo script for REQ-LOGGER-36")

        def run(self):
            logging.getLogger("demo.stage").output("run-marker-36")
            return 0


    sys.argv = ["demo"]
    print({ready!r}, flush=True)
    code = DemoScript().main()
    print({done!r} + "=" + str(code), flush=True)
    """
).format(ready=READY_MARKER, done=EXIT_MARKER)


# REQ-LOGGER-36
def test_script_builder_main_sets_up_logging_before_run(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [sys.executable, str(driver)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert READY_MARKER in result.stdout, result.stderr
    assert f"{EXIT_MARKER}=0" in result.stdout, result.stderr
    lines = [line for line in result.stdout.splitlines() if "run-marker-36" in line]
    assert len(lines) == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert re.match(r"^\d{2}:\d{2}:\d{2} OUTPUT\s+\S+ \| run-marker-36$", lines[0]), (
        lines[0]
    )
