"""
ScriptBuilder: A base class for building flexible command-line scripts.
Implements the Builder pattern for script configuration.
"""

import argparse
import logging
import os  # noqa: F401
import pprint
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from os.path import dirname, exists, join  # noqa: F401
from subprocess import check_call, run
from typing import Any

from champollion_utils.process import init_process
from champollion_utils.run_log import RunLogCapture
from champollion_utils.update_check import check_for_updates

logger = logging.getLogger(__name__)

RUN_LOG_OPTION = "--log"
RUN_LOG_DIR = ".log"
RUN_LOG_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def _footer_exit_code(value: Any) -> int:
    """Exit code written to the run-log footer: an int as is, None as 0, anything else as 1."""
    if isinstance(value, int):
        return int(value)
    return 0 if value is None else 1


class ScriptBuilder(ABC):
    """
    Base class for building flexible command-line scripts.

    Uses the Builder pattern.
    """

    def __init__(self, script_name: str, description: str):
        """
        Initialize the script builder.

        Args:
            script_name: Name of the script (used in help messages).
            description: Description of the script.
        """
        self.script_name = script_name
        self.description = description
        self.parser = self._create_parser()
        self._add_log_option()
        self.args = None

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create the argument parser."""
        return argparse.ArgumentParser(
            prog=self.script_name,
            description=self.description,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )

    def _add_log_option(self) -> None:
        """Add --log [PATH], given to every script: copy the run output into a run-log file."""
        self.parser.add_argument(
            RUN_LOG_OPTION,
            nargs="?",
            const="",
            default=None,
            metavar="PATH",
            help="Copy the run output (stdout, stderr, child processes) into a run-log file at PATH; "
            "without PATH, at default_log_path().",
        )

    def add_argument(self, *args, **kwargs):
        """Add an argument to the parser. Returns self for chaining."""
        self.parser.add_argument(*args, **kwargs)
        return self

    def add_required_argument(self, name: str, help_text: str, type_: type = str):
        """Add a required argument. Returns self for chaining."""
        self.parser.add_argument(name, type=type_, required=True, help=help_text)
        return self

    def add_optional_argument(
        self,
        name: str,
        help_text: str,
        default: Any = None,
        type_: type = str,
        action: str | None = None,
    ):
        """Add an optional argument. Returns self for chaining."""
        if action:
            self.parser.add_argument(name, action=action, help=help_text)
        else:
            self.parser.add_argument(name, type=type_, default=default, help=help_text)
        return self

    def add_flag(self, name: str, help_text: str):
        """Add a boolean flag. Returns self for chaining."""
        self.parser.add_argument(name, action="store_true", help=help_text)
        return self

    def parse_args(self, args: list[str] | None = None) -> argparse.Namespace:
        """Parse command-line arguments."""
        self.args = self.parser.parse_args(args)
        return self.args

    def build(self):
        """
        Build and finalize the script configuration.

        This method completes the builder pattern by parsing arguments
        and returning self for final method calls like run().

        Returns self for chaining to run() or other methods.
        """
        check_for_updates()
        self.parse_args()
        return self

    def validate_paths(self, paths: list[str]) -> bool:
        """Validate that all paths exist."""
        for path in paths:
            if not exists(path):
                print(f"Error: Path does not exist: {path}")
                return False
        return True

    def print_args(self):
        """Print the parsed arguments. Returns self for chaining."""
        print(f"{self.script_name} arguments:")
        shown = {
            key: value
            for key, value in vars(self.args).items()
            if not (key == "log" and value is None)
        }
        pprint.pprint(shown)
        return self

    def build_command(
        self,
        script_path: str,
        required_args: list[str],
        optional_args: dict[str, Any] | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Build a command to execute another script.

        Args:
            script_path: Path to the script to execute.
            required_args: List of required argument names.
            optional_args: Dictionary of optional argument names
                          (not used, kept for compatibility).
            defaults: Dictionary of default values for optional
                     arguments.

        Returns:
            List of command components.
        """
        if defaults is None:
            defaults = {}

        cmd = [sys.executable, script_path]

        # Add required arguments
        for arg in required_args:
            value = getattr(self.args, arg)
            cmd.append(f"--{arg}={value}")

        # Add optional arguments if they differ from defaults
        for arg, default in defaults.items():
            value = getattr(self.args, arg, None)
            if value is not None and value != default:
                if isinstance(value, bool) and value:
                    cmd.append(f"--{arg}")
                elif isinstance(value, list):
                    for item in value:
                        cmd.append(f"--{arg}={item}")
                else:
                    cmd.append(f"--{arg}={value}")

        return cmd

    def execute_command(self, cmd: list[str], shell: bool = False) -> int:
        """
        Execute a command.

        Args:
            cmd: Command to execute.
            shell: Whether to use shell execution.

        Returns:
            Return code of the command.
        """
        print("Running command:")
        print(" ".join(cmd))
        try:
            if shell:
                result = run(
                    " ".join(cmd),
                    shell=True,
                    executable="/bin/bash",
                    check=False,
                )
                return result.returncode
            else:
                return check_call(cmd)
        except Exception as e:
            print(f"Error running command: {e}")
            logger.debug("execute_command failed: %r", cmd, exc_info=True)
            return 1

    @abstractmethod
    def run(self) -> int:
        """Run the script. Must be implemented by subclasses."""

    def default_log_path(self) -> str:
        """Return the run-log path used by a bare --log. Override to choose another path."""
        stamp = datetime.now().astimezone().strftime(RUN_LOG_TIMESTAMP_FORMAT)
        return join(".", RUN_LOG_DIR, f"{self.script_name}_{stamp}.log")

    def _resolve_log_path(self) -> str | None:
        """Path of this run's log from args.log (None: no run log), written back to args.log."""
        value = getattr(self.args, "log", None)
        if not isinstance(value, str):
            return None
        path = value or self.default_log_path()
        self.args.log = path
        return path

    def before_run(self) -> None:
        """Called by main() after build/print_args, before run(). Override to add pre-run logic."""

    def after_run(self, success: bool) -> None:
        """Called by main() after run() completes. Override to add post-run logic."""

    def main(self) -> int:
        """Main entry point for the script.

        Sets up logging and the crash hook first, so the update check and
        argument parsing are covered too. With --log, print_args, before_run and run
        are copied into the run log; after_run stays outside it.
        """
        init_process()
        self.build()
        log_path = self._resolve_log_path()
        if log_path is None:
            self.print_args()
            self.before_run()
            exit_code = self.run()
        else:
            with RunLogCapture(log_path) as capture:
                self.print_args()
                self.before_run()
                exit_code = self.run()
                capture.exit_code = _footer_exit_code(exit_code)
        self.after_run(success=(exit_code == 0))
        return exit_code
