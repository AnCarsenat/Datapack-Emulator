"""What the window remembers between sessions: recent projects and datapacks,
and where the window and its docks were."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QMenu

from datapack_emulator.settings import PATHS
from datapack_emulator.window.controllers.base import Controller

log = logging.getLogger(__name__)

#: entries kept in each recent list
MAX_RECENT = 10


def state_file() -> Path:
    return PATHS.CACHE / "window-state.json"


def _paths(value) -> list[str]:
    """A recent list from a hand-edited or damaged state file."""
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)][:MAX_RECENT]


class SessionController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self.recent_projects: list[str] = []
        self.recent_datapacks: list[str] = []
        self._default_state = QByteArray()

    def connect(self) -> None:
        window = self.window
        window.recent_projects_menu.aboutToShow.connect(self._fill_recent_projects)
        window.recent_datapacks_menu.aboutToShow.connect(self._fill_recent_datapacks)

    # -- saving and restoring -------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads(state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def restore(self) -> None:
        """At start-up, after the default layout is in place."""
        window = self.window
        self._default_state = window.saveState()
        data = self._read()
        self.recent_projects = _paths(data.get("recent_projects"))
        self.recent_datapacks = _paths(data.get("recent_datapacks"))
        geometry = data.get("geometry")
        docks = data.get("docks")
        if isinstance(geometry, str):
            window.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        if isinstance(docks, str):
            window.restoreState(QByteArray.fromBase64(docks.encode()))

    def save(self) -> None:
        window = self.window
        data = {
            "recent_projects": self.recent_projects,
            "recent_datapacks": self.recent_datapacks,
            "geometry": bytes(window.saveGeometry().toBase64()).decode(),
            "docks": bytes(window.saveState().toBase64()).decode(),
        }
        try:
            state_file().parent.mkdir(parents=True, exist_ok=True)
            state_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("cannot save the window state: %s", exc)

    def reset_layout(self) -> None:
        window = self.window
        if not self._default_state.isEmpty():
            window.restoreState(self._default_state)
        window.show_all_docks()
        self.status("layout reset")

    # -- recent lists -----------------------------------------------------------

    @staticmethod
    def _push(entries: list[str], path: Path) -> list[str]:
        text = str(Path(path).resolve())
        return [text, *(entry for entry in entries if entry != text)][:MAX_RECENT]

    def remember_project(self, path: Path) -> None:
        self.recent_projects = self._push(self.recent_projects, path)
        self.save()

    def remember_datapack(self, path: Path) -> None:
        self.recent_datapacks = self._push(self.recent_datapacks, path)
        self.save()

    def _fill(self, menu: QMenu, entries: list[str], open_entry) -> None:
        menu.clear()
        existing = [entry for entry in entries if Path(entry).exists()]
        if not existing:
            menu.addAction("nothing yet").setEnabled(False)
            return
        for entry in existing:
            path = Path(entry)
            action = menu.addAction(f"{path.name}  —  {path.parent}", lambda p=path: open_entry(p))
            action.setStatusTip(entry)

    def _fill_recent_projects(self) -> None:
        window = self.window

        def open_project(path: Path) -> None:
            if window.projects.confirm_close():
                window.projects.open_path(path)

        self._fill(window.recent_projects_menu, self.recent_projects, open_project)

    def _fill_recent_datapacks(self) -> None:
        window = self.window
        self._fill(window.recent_datapacks_menu, self.recent_datapacks, window.datapacks.load)

    # -- focus ------------------------------------------------------------------

    def focus_console(self) -> None:
        window = self.window
        window.dock_logs.show()
        window.dock_logs.raise_()
        window.edit_console.setFocus()
        window.edit_console.selectAll()
