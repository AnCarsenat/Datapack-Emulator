"""Shared plumbing for the main window's controllers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.window.main import MainWindow

#: tab pages in window.ui, by object name (indexes change when tabs are added)
TAB_ENVIRONMENT = "environment"
TAB_PROFILER = "profiler"
TAB_GRAPH = "graph_view"
TAB_SOURCE = "source_view"


class Controller:
    def __init__(self, window: MainWindow):
        self.window = window

    def status(self, text: str) -> None:
        self.window.statusBar().showMessage(text)

    def need_datapack(self) -> bool:
        """True when a pack is loaded; otherwise says so in the status bar."""
        if self.window.datapack is None:
            self.status("load a datapack first")
            return False
        return True
