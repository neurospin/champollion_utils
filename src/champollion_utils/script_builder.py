#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScriptBuilder: A base class for building flexible command-line scripts.
Implements the Builder pattern for script configuration.
"""

import os  # noqa: F401
import sys
import pprint
import argparse
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from os.path import dirname, join, exists  # noqa: F401
from subprocess import run, check_call


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
        self.args = None

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create the argument parser."""
        return argparse.ArgumentParser(
            prog=self.script_name,
            description=self.description,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )

    def add_argument(self, *args, **kwargs):
        """Add an argument to the parser. Returns self for chaining."""
        self.parser.add_argument(*args, **kwargs)
        return self

    def add_required_argument(
        self,
        name: str,
        help_text: str,
        type_: type = str
    ):
        """Add a required argument. Returns self for chaining."""
        self.parser.add_argument(
            name,
            type=type_,
            required=True,
            help=help_text
        )
        return self

    def add_optional_argument(
        self,
        name: str,
        help_text: str,
        default: Any = None,
        type_: type = str,
        action: Optional[str] = None
    ):
        """Add an optional argument. Returns self for chaining."""
        if action:
            self.parser.add_argument(name, action=action, help=help_text)
        else:
            self.parser.add_argument(
                name,
                type=type_,
                default=default,
                help=help_text
            )
        return self

    def add_flag(self, name: str, help_text: str):
        """Add a boolean flag. Returns self for chaining."""
        self.parser.add_argument(name, action="store_true", help=help_text)
        return self

    def parse_args(
        self,
        args: Optional[List[str]] = None
    ) -> argparse.Namespace:
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
        self.parse_args()
        return self

    def validate_paths(self, paths: List[str]) -> bool:
        """Validate that all paths exist."""
        for path in paths:
            if not exists(path):
                print(f"Error: Path does not exist: {path}")
                return False
        return True

    def print_args(self):
        """Print the parsed arguments. Returns self for chaining."""
        print(f"{self.script_name} arguments:")
        pprint.pprint(vars(self.args))
        return self

    def build_command(
        self,
        script_path: str,
        required_args: List[str],
        optional_args: Dict[str, Any] = None,
        defaults: Dict[str, Any] = None
    ) -> List[str]:
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

    def execute_command(self, cmd: List[str], shell: bool = False) -> int:
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
                    executable="/bin/bash"
                )
                return result.returncode
            else:
                return check_call(cmd)
        except Exception as e:
            print(f"Error running command: {e}")
            return 1

    @abstractmethod
    def run(self) -> int:
        """Run the script. Must be implemented by subclasses."""
        pass

    def main(self) -> int:
        """Main entry point for the script."""
        return self.build().print_args().run()
