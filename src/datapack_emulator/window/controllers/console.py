"""The command line under the logs: type a command into the current world."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt

from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.window.controllers.base import Controller


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
        window.console_add_test_button.clicked.connect(self.add_as_test)
        window.edit_console.installEventFilter(self._keys)

    def prefill(self, text: str) -> None:
        """Put ``text`` in the command line, ready to be completed."""
        window = self.window
        window.dock_logs.show()
        window.edit_console.setText(text)
        window.edit_console.setFocus()

    # -- running --------------------------------------------------------------

    def run(self, text: str | None = None) -> CommandResult | None:
        window = self.window
        # a leading slash, as typed in chat, is optional
        line = (window.edit_console.text() if text is None else text).strip().removeprefix("/")
        if not line:
            return None
        if not self.need_datapack():
            return None
        if Command.parse(line, source="<console>") is None:
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
        window.output.app(f"> {line}")
        result, _ = emulator.run_typed(line)
        if result is None:  # checked above; kept for safety
            return None
        self._remember(line)
        if text is None:
            window.edit_console.clear()
        outcome = "succeeded" if result.success else "failed"
        if window.check_step_on_command.isChecked():
            window.runs.step()  # one more tick, as step (F7) runs it
            self.status(
                f"{line}: {outcome} (value {result.value}), then stepped to game time "
                f"{window.emulator.world.tick}"
            )
            return result
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        self.status(f"{line}: {outcome} (value {result.value}) at game time {emulator.world.tick}")
        return result

    def add_as_test(self) -> None:
        """The line being typed, or the last command run, as a test at the
        current server tick."""
        window = self.window
        line = window.edit_console.text().strip() or (self.history[-1] if self.history else "")
        if not line:
            self.status("type a command first")
            return
        emulator = window.emulator
        tick = emulator.world.tick - 1 if emulator is not None and emulator.started else 0
        window.environment.add_from_command(line, tick)

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
