"""Shared plumbing for the main window's controllers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.window.main import MainWindow

#: tab indexes in window.ui
TAB_PROFILER, TAB_GRAPH, TAB_SOURCE = 0, 1, 2


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
