"""The logs dock: records from the output bus, filtered by source and level."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QTimer
from PySide6.QtWidgets import QMenu

from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource
from datapack_emulator.window.controllers.base import Controller
from datapack_emulator.window.panels import LEVELS, LogTableModel


class LogController(Controller):
    #: how often buffered records reach the table, in milliseconds
    FLUSH_INTERVAL_MS = 100

    def __init__(self, window):
        super().__init__(window)
        self.model = LogTableModel(window)
        self._pending: list[LogRecord] = []
        self._flush_timer = QTimer(window)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self.flush)

    def connect(self) -> None:
        window = self.window
        window.log_table.setModel(self.model)
        window.log_table.verticalHeader().setVisible(False)
        window.log_table.horizontalHeader().setStretchLastSection(True)
        window.clear_logs_button.clicked.connect(self.clear)
        window.combo_level.currentTextChanged.connect(self.apply_filter)
        window.combo_seen_by.currentTextChanged.connect(self.apply_filter)
        window.edit_filter.textChanged.connect(self.apply_filter)
        for box in (window.check_app, window.check_emulator, window.check_game):
            box.toggled.connect(self.apply_filter)
        window.output.listeners.append(self.on_record)
        window.log_table.customContextMenuRequested.connect(self.context_menu)
        window.log_table.doubleClicked.connect(self.on_double_click)
        self.apply_filter()

    def on_record(self, record: LogRecord) -> None:
        # records arrive thousands per second on long runs: buffer them and
        # update the table a few times a second instead of once per record
        self._pending.append(record)
        if not self._flush_timer.isActive():
            self._flush_timer.start(self.FLUSH_INTERVAL_MS)

    def flush(self) -> None:
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        self.model.extend(pending)
        self.window.log_counts.setText(self.model.summary())
        self.window.log_table.scrollToBottom()

    def apply_filter(self) -> None:
        window = self.window
        sources = set()
        if window.check_app.isChecked():
            sources.add(LogSource.APP)
        if window.check_emulator.isChecked():
            sources.add(LogSource.EMULATOR)
        if window.check_game.isChecked():
            sources.add(LogSource.GAME)
        self.model.set_filter(
            sources=sources,
            level=LEVELS.get(window.combo_level.currentText(), LogLevel.INFO),
            text=window.edit_filter.text(),
            reader=self.reader,
        )
        window.log_counts.setText(self.model.summary())
        window.log_table.resizeColumnsToContents()

    #: the "seen by" entry that shows every record
    EVERYONE = "everyone"

    @property
    def reader(self) -> str:
        text = self.window.combo_seen_by.currentText()
        return "" if text in ("", self.EVERYONE) else text

    def refresh_readers(self) -> None:
        """Offer every player of the current world (or of the players setting)."""
        window = self.window
        combo = window.combo_seen_by
        current = combo.currentText() or self.EVERYONE
        if window.emulator is not None:
            names = [entity.name for entity in window.emulator.world.players]
        else:
            names = [f"Player{index + 1}" for index in range(window.spin_players.value())]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([self.EVERYONE, *names])
        combo.setCurrentText(current if current in (self.EVERYONE, *names) else self.EVERYONE)
        combo.blockSignals(False)
        self.apply_filter()

    # -- acting on a record -----------------------------------------------

    def record_at(self, index: QModelIndex) -> LogRecord | None:
        return self.model.record_at(index.row()) if index.isValid() else None

    def source_of(self, record: LogRecord) -> tuple[str, int] | None:
        """``(function id, line)`` a record came from, if it names a real file."""
        if not record.function or record.function.startswith("<"):
            return None  # <server>, <test>: typed commands have no file
        if self.window.navigation.function_path(record.function) is None:
            return None
        return (record.function, record.line)

    def copy_message(self, record: LogRecord) -> None:
        self.window.navigation.copy_text(record.message, what="the message")

    def copy_details(self, record: LogRecord) -> None:
        lines = [record.format()]
        if record.command:
            lines.append(f"command: {record.command}")
        if record.key:
            lines.append(f"key: {record.key}")
        if record.version:
            lines.append(f"version: {record.version}")
        if record.recipient:
            lines.append(
                "seen by: " + ("everyone" if record.recipient == "*" else record.recipient)
            )
        self.window.navigation.copy_text("\n".join(lines), what="the record")

    def command_of(self, record: LogRecord) -> str | None:
        """The command a record is about: typed on the command line, or the
        line of the function it came from."""
        if record.source is LogSource.APP and record.message.startswith("> "):
            return record.message[2:]
        if record.command:
            return record.command
        source = self.source_of(record)
        if source is not None and source[1]:
            return self.window.navigation.line_text(*source)
        return None

    def analyze(self, record: LogRecord) -> None:
        command = self.command_of(record)
        if command is None:
            self.status("this record is not about a command")
            return
        source = self.source_of(record)
        where = f"{source[0]}:{source[1]}" if source else "the log"
        self.window.navigation.analyze(command, where)

    def reveal(self, record: LogRecord) -> None:
        """Show, select and scroll to a record, loosening the filters if needed."""
        window = self.window
        self.flush()
        boxes = {
            LogSource.APP: window.check_app,
            LogSource.EMULATOR: window.check_emulator,
            LogSource.GAME: window.check_game,
        }
        boxes[record.source].setChecked(True)
        if LEVELS.get(window.combo_level.currentText(), LogLevel.INFO) > record.level:
            window.combo_level.setCurrentText("debug")
        window.edit_filter.clear()
        for row in range(self.model.rowCount()):
            if self.model.record_at(row) is record:
                window.log_table.selectRow(row)
                window.log_table.scrollTo(self.model.index(row, 0))
                window.dock_logs.show()
                window.dock_logs.raise_()
                return
        self.status("that record is no longer in the log")

    def open_source(self, record: LogRecord) -> None:
        source = self.source_of(record)
        if source is None:
            self.status("this record does not come from a file of the pack")
            return
        self.window.navigation.open_function(*source)

    def on_double_click(self, index: QModelIndex) -> None:
        record = self.record_at(index)
        if record is not None and self.source_of(record) is not None:
            self.open_source(record)

    def context_menu(self, point: QPoint) -> None:
        table = self.window.log_table
        record = self.record_at(table.indexAt(point))
        menu = self.menu_for(record)
        menu.exec(table.viewport().mapToGlobal(point))

    def menu_for(self, record: LogRecord | None) -> QMenu:
        menu = QMenu(self.window)
        if record is None:
            menu.addAction("right-click a record").setEnabled(False)
            return menu
        is_error = record.failure or record.level >= LogLevel.WARNING
        menu.addAction(
            "copy error message" if is_error else "copy message",
            lambda: self.copy_message(record),
        )
        menu.addAction("copy with details", lambda: self.copy_details(record))
        menu.addSeparator()
        source = self.source_of(record)
        label = "open file in source view"
        if source is not None:
            function_id, line = source
            label += f" ({function_id}:{line})" if line else f" ({function_id})"
        open_action = menu.addAction(label, lambda: self.open_source(record))
        open_action.setEnabled(source is not None)
        command = self.command_of(record)
        analyze = menu.addAction("analyze the command", lambda: self.analyze(record))
        analyze.setEnabled(command is not None)
        add = menu.addAction(
            "add the command as a test",
            lambda: self.window.environment.add_from_command(command or "", record.tick),
        )
        add.setEnabled(command is not None and not command.lstrip().startswith("$"))
        return menu

    def clear(self) -> None:
        self._pending.clear()
        self.window.output.clear()
        self.model.clear()
        self.window.log_counts.setText(self.model.summary())
