"""The logs dock: records from the output bus, filtered by source and level."""

from __future__ import annotations

from PySide6.QtCore import QTimer

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
        window.edit_filter.textChanged.connect(self.apply_filter)
        for box in (window.check_app, window.check_emulator, window.check_game):
            box.toggled.connect(self.apply_filter)
        window.output.listeners.append(self.on_record)
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
        )
        window.log_counts.setText(self.model.summary())
        window.log_table.resizeColumnsToContents()

    def clear(self) -> None:
        self._pending.clear()
        self.window.output.clear()
        self.model.clear()
        self.window.log_counts.setText(self.model.summary())
