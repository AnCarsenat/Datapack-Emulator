"""Widgets, models and highlighters that fill the containers from the .ui files.

The window layout lives in ``src/window/window.ui`` and ``src/window/engine.ui``;
nothing here creates docks or menus.
"""

from src.window.panels.explorer import (
    OVERLAY_ROLE,
    PATH_ROLE,
    RESOURCE_ROLE,
    build_explorer_model,
)
from src.window.panels.graph import FunctionGraphWidget
from src.window.panels.highlight import (
    JsonHighlighter,
    McFunctionHighlighter,
    highlighter_for,
)
from src.window.panels.inspector import (
    describe_datapack,
    describe_resource,
    describe_run,
    describe_version,
)
from src.window.panels.logs import LEVELS, LogTableModel
from src.window.panels.results import ResultsTableModel
from src.window.panels.uiloader import load_ui_into

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
    "load_ui_into",
]
