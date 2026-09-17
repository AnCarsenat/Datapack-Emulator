"""What the window remembers between sessions: recent projects and datapacks,
and where the window and its docks were."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QSignalBlocker
from PySide6.QtWidgets import QMenu

from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.project import state_file
from datapack_emulator.window.controllers.base import Controller

log = logging.getLogger(__name__)

#: entries kept in each recent list
MAX_RECENT = 10


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
        #: start on the last project instead of the sample datapack
        self.open_last_on_launch = True
        self._default_state = QByteArray()

    def connect(self) -> None:
        window = self.window
        action = window._action("actionopen_last_on_launch")
        if action is not None:
            action.toggled.connect(self._set_open_last_on_launch)
        window.recent_projects_menu.aboutToShow.connect(self._fill_recent_projects)
        window.recent_datapacks_menu.aboutToShow.connect(self._fill_recent_datapacks)
        window.remove_datapack_menu.aboutToShow.connect(self._fill_remove_datapack)

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
        self.open_last_on_launch = bool(data.get("open_last_on_launch", True))
        action = window._action("actionopen_last_on_launch")
        if action is not None:
            # setChecked would answer its own toggled and save the layout
            # before it is restored, just below
            blocker = QSignalBlocker(action)
            action.setChecked(self.open_last_on_launch)
            del blocker
        geometry = data.get("geometry")
        docks = data.get("docks")
        if isinstance(geometry, str):
            window.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        if isinstance(docks, str):
            window.restoreState(QByteArray.fromBase64(docks.encode()))

    def save(self) -> bool:
        """Write the state file; False when it could not be written."""
        window = self.window
        # another window may have written its own keys since this one started
        data = self._read()
        data.update(
            {
                "recent_projects": self.recent_projects,
                "recent_datapacks": self.recent_datapacks,
                "open_last_on_launch": self.open_last_on_launch,
                "geometry": bytes(window.saveGeometry().toBase64()).decode(),
                "docks": bytes(window.saveState().toBase64()).decode(),
            }
        )
        try:
            state_file().parent.mkdir(parents=True, exist_ok=True)
            state_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
        except OSError as exc:
            log.warning("cannot save the window state: %s", exc)
            return False

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

    def _fill(self, menu: QMenu, entries: list[str], open_entry, clear=None) -> None:
        menu.clear()
        existing = [entry for entry in entries if Path(entry).exists()]
        if not existing:
            menu.addAction("nothing yet").setEnabled(False)
            return
        for number, entry in enumerate(existing, start=1):
            path = Path(entry)
            action = menu.addAction(
                f"&{number % 10}  {path.stem}  —  {path.parent}", lambda p=path: open_entry(p)
            )
            action.setStatusTip(entry)
        if clear is not None:
            menu.addSeparator()
            menu.addAction("clear the list", clear)

    def open_project(self, path: Path) -> bool:
        window = self.window
        if not path.exists():
            self.status(f"{path} no longer exists")
            self.recent_projects = [entry for entry in self.recent_projects if Path(entry) != path]
            self.save()
            return False
        return window.projects.confirm_close() and window.projects.open_path(path)

    def open_last_project(self) -> bool:
        """Ctrl+Alt+O: the most recent project that still exists."""
        existing = [Path(entry) for entry in self.recent_projects if Path(entry).exists()]
        if not existing:
            self.status("no recent project yet: open or save one first")
            return False
        return self.open_project(existing[0])

    def _set_open_last_on_launch(self, checked: bool) -> None:
        self.open_last_on_launch = checked
        if not self.save():
            self.status(f"cannot save the setting: {state_file()} is not writable")
            return
        openable = any(Path(entry).is_file() for entry in self.recent_projects)
        if not checked:
            self.status("the sample datapack will open on launch")
        elif openable:
            self.status("the last project will open on launch")
        else:
            self.status("the last project will open on launch (there is none yet)")

    def start(self, path: Path | None = None, open_last: bool | None = None) -> None:
        """What the window opens with: a path from the command line, else the
        project opened most recently that still opens, else the sample."""
        window = self.window
        failed = ""
        if path is not None:
            if path.suffix.lower() in (".dpemu", ".json") and path.is_file():
                if window.projects.open_path(path):
                    return
                failed = f"cannot open the project {path}"
            elif (path / "pack.mcmeta").is_file():
                window.datapacks.load(path)
                return
            else:
                failed = f"{path} is not a project or a datapack folder (no pack.mcmeta)"
        wanted = self.open_last_on_launch if open_last is None else open_last
        if wanted and not failed:
            for entry in list(self.recent_projects):
                if not Path(entry).is_file():
                    continue
                if window.projects.open_path(Path(entry), quiet=True):
                    return
                window.output.app(
                    f"cannot open the last project {entry}: it is off the list now",
                    level=LogLevel.ERROR,
                )
                # it is in the list but cannot be read: do not ask again
                self.recent_projects = [kept for kept in self.recent_projects if kept != entry]
                self.save()
        window.datapacks.open_default()
        if failed:
            window.output.app(failed, level=LogLevel.ERROR)
            self.status(failed)

    def clear_recent_projects(self) -> None:
        self.recent_projects = []
        self.save()
        self.status("recent projects cleared")

    def _fill_recent_projects(self) -> None:
        window = self.window
        self._fill(
            window.recent_projects_menu,
            self.recent_projects,
            self.open_project,
            clear=self.clear_recent_projects,
        )

    def _fill_recent_datapacks(self) -> None:
        window = self.window
        self._fill(window.recent_datapacks_menu, self.recent_datapacks, window.datapacks.add)

    def _fill_remove_datapack(self) -> None:
        window = self.window
        menu = window.remove_datapack_menu
        menu.clear()
        packs = list(window.datapack) if window.datapack is not None else []
        if not packs:
            menu.addAction("no datapack analyzed").setEnabled(False)
            return
        for index, pack in enumerate(packs):
            action = menu.addAction(
                f"{index + 1}. {pack.name}", lambda i=index: window.datapacks.remove(i)
            )
            action.setStatusTip(str(pack.path))

    # -- focus ------------------------------------------------------------------

    def focus_console(self) -> None:
        window = self.window
        window.dock_logs.show()
        window.dock_logs.raise_()
        window.edit_console.setFocus()
        window.edit_console.selectAll()
