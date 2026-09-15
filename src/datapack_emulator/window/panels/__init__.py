"""Widgets, models and highlighters that fill the containers from the .ui files.

The window layout lives in the ``.ui`` files next to ``datapack_emulator.window``;
nothing here creates docks or menus.
"""

from datapack_emulator.window.panels.explorer import (
    OVERLAY_ROLE,
    PATH_ROLE,
    RESOURCE_ROLE,
    build_explorer_model,
)
from datapack_emulator.window.panels.graph import FunctionGraphWidget
from datapack_emulator.window.panels.highlight import (
    JsonHighlighter,
    McFunctionHighlighter,
    highlighter_for,
)
from datapack_emulator.window.panels.inspector import (
    describe_datapack,
    describe_resource,
    describe_run,
    describe_version,
    row_help,
)
from datapack_emulator.window.panels.logs import LEVELS, LogTableModel
from datapack_emulator.window.panels.results import ResultsTableModel
from datapack_emulator.window.panels.uiloader import load_ui_into

__all__ = [
    "LEVELS",
    "FunctionGraphWidget",
    "JsonHighlighter",
    "LogTableModel",
    "McFunctionHighlighter",
    "OVERLAY_ROLE",
    "PATH_ROLE",
    "RESOURCE_ROLE",
    "ResultsTableModel",
    "build_explorer_model",
    "describe_datapack",
    "describe_resource",
    "describe_run",
    "describe_version",
    "highlighter_for",
    "row_help",
    "load_ui_into",
]
