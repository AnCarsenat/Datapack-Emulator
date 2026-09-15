"""The command line under the logs: type a command into the current world."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt

from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.testing import run_as
from datapack_emulator.window.controllers.base import Controller

#: the "run as" entry that means the server console
CONSOLE = "console"


class _HistoryKeys(QObject):
    """↑ / ↓ in the command line walk through earlier commands."""

    def __init__(self, controller: ConsoleController):
        super().__init__(controller.window)
        self.controller = controller

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.controller.browse(-1 if event.key() == Qt.Key_Up else 1)
            return True
        return False


class ConsoleController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self.history: list[str] = []
        self._position = 0
        self._keys = _HistoryKeys(self)

    def connect(self) -> None:
        window = self.window
        # clicked(bool) would pass `checked` as the text to run
        window.edit_console.returnPressed.connect(lambda: self.run())
        window.console_run_button.clicked.connect(lambda: self.run())
        window.edit_console.installEventFilter(self._keys)
        self.refresh_players()

    # -- who runs it --------------------------------------------------------

    def refresh_players(self) -> None:
        """Offer the console and every player of the current world (or the
        players setting when there is no world yet)."""
        combo = self.window.combo_console_as
        current = combo.currentText() or CONSOLE
        emulator = self.window.emulator
        if emulator is not None:
            names = [entity.name for entity in emulator.world.players]
        else:
            names = [f"Player{index + 1}" for index in range(self.window.spin_players.value())]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([CONSOLE, *names])
        combo.setCurrentText(current)
        combo.blockSignals(False)

    def use_executor(self, who: str) -> None:
        self.window.combo_console_as.setCurrentText(who)
        self.window.dock_logs.show()
        self.window.edit_console.setFocus()

    @property
    def executor(self) -> str:
        text = self.window.combo_console_as.currentText().strip()
        return "" if text in ("", CONSOLE) else text

    # -- running --------------------------------------------------------------

    def run(self, text: str | None = None) -> CommandResult | None:
        window = self.window
        # a leading slash, as typed in chat, is optional
        line = (window.edit_console.text() if text is None else text).strip().removeprefix("/")
        if not line:
            return None
        if not self.need_datapack():
            return None
        command = Command.parse(line, source="<console>")
        if command is None:
            self.status("nothing to run: the line is empty or a comment")
            return None
        if window.emulator is None:
            window.datapacks.rebuild_emulator()
        emulator = window.emulator
        assert emulator is not None
        if not emulator.started or emulator.world.tick == 0:
            # like a server that is up: load and the first tick have run
            emulator.start()
            emulator.run_tick()
            window.output.app("started the world (ran the first tick) to run the command")
        who = self.executor
        window.output.app(f"> {line}" + (f"   (as {who})" if who else ""))
        result = run_as(emulator, command, who)
        self._remember(line)
        if text is None:
            window.edit_console.clear()
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        self.refresh_players()
        outcome = "succeeded" if result.success else "failed"
        self.status(f"{line}: {outcome} (value {result.value}) at game time {emulator.world.tick}")
        return result

    def _remember(self, line: str) -> None:
        if not self.history or self.history[-1] != line:
            self.history.append(line)
        self._position = len(self.history)

    def browse(self, step: int) -> None:
        if not self.history:
            return
        self._position = max(0, min(len(self.history), self._position + step))
        edit = self.window.edit_console
        edit.setText(self.history[self._position] if self._position < len(self.history) else "")
