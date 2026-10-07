"""
Champollion Utils: Minimalist utility package for the Champollion Pipeline project.

This package provides the `ScriptBuilder` class for building flexible command-line scripts.
"""

from .process import init_process
from .script_builder import ScriptBuilder

# Define the public API
__all__ = ["ScriptBuilder", "init_process"]
