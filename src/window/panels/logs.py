"""Log table: one row per :class:`LogRecord`, filterable by source and level.

The three sources are coloured differently on purpose — ``game`` rows are what
Minecraft itself would have printed, ``emulator`` rows are this engine's own
diagnostics, ``app`` rows are the program talking about itself.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from src.emulator.runtime.output import LogLevel, LogRecord, LogSource

COLUMNS = ("tick", "source", "level", "where", "message")

SOURCE_COLOURS = {
    LogSource.APP: QColor("#2c3e50"),
    LogSource.EMULATOR: QColor("#8e44ad"),
    LogSource.GAME: QColor("#1e6f3d"),
}

LEVEL_COLOURS = {
    LogLevel.DEBUG: QColor("#8a8a8a"),
    LogLevel.INFO: None,  # keep the source colour
    LogLevel.WARNING: QColor("#b9770e"),
    LogLevel.ERROR: QColor("#c0392b"),
}

LEVELS = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warning": LogLevel.WARNING,
    "error": LogLevel.ERROR,
}


class LogTableModel(QAbstractTableModel):
    """Holds every record, shows the ones that pass the filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: list[LogRecord] = []
        self._visible: list[LogRecord] = []
        self._sources: set[LogSource] = set(LogSource)
        self._level: LogLevel = LogLevel.INFO
        self._text: str = ""

    # -- data -------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return COLUMNS[section]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._visible[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            return (
                "" if record.tick is None else str(record.tick),
                str(record.source),
                record.level.label,
                record.origin,
                record.message.replace("\n", " ⏎ "),
            )[column]
        if role == Qt.ToolTipRole:
            parts = [record.message]
            if record.command:
                parts.append(f"command: {record.command}")
            if record.key:
                parts.append(f"key: {record.key}")
            if record.version:
                parts.append(f"version: {record.version}")
            return "\n".join(parts)
        if role == Qt.ForegroundRole:
            colour = LEVEL_COLOURS.get(record.level) or SOURCE_COLOURS.get(record.source)
            return QBrush(colour) if colour else None
        if role == Qt.FontRole and record.level >= LogLevel.ERROR:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.UserRole:
            return record
        return None

    def record_at(self, row: int) -> LogRecord | None:
        return self._visible[row] if 0 <= row < len(self._visible) else None

    # -- feeding ----------------------------------------------------------

    def append(self, record: LogRecord) -> None:
        self._records.append(record)
        if self._passes(record):
            self.beginInsertRows(QModelIndex(), len(self._visible), len(self._visible))
            self._visible.append(record)
            self.endInsertRows()

    def set_records(self, records: list[LogRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self._refilter()
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._visible.clear()
        self.endResetModel()

    # -- filtering --------------------------------------------------------

    def set_filter(
        self,
        sources: set[LogSource] | None = None,
        level: LogLevel | None = None,
        text: str | None = None,
    ) -> None:
        if sources is not None:
            self._sources = sources
        if level is not None:
            self._level = level
        if text is not None:
            self._text = text.lower()
        self.beginResetModel()
        self._refilter()
        self.endResetModel()

    def _refilter(self) -> None:
        self._visible = [record for record in self._records if self._passes(record)]

    def _passes(self, record: LogRecord) -> bool:
        return (
            record.source in self._sources
            and record.level >= self._level
            and (not self._text or self._text in record.message.lower())
        )

    # -- summary ----------------------------------------------------------

    def counts(self) -> dict[LogSource, int]:
        out = {source: 0 for source in LogSource}
        for record in self._records:
            out[record.source] += 1
        return out

    def summary(self) -> str:
        counts = self.counts()
        errors = sum(1 for record in self._records if record.level >= LogLevel.ERROR)
        warnings = sum(1 for record in self._records if record.level == LogLevel.WARNING)
        return (
            f"{len(self._visible)}/{len(self._records)} shown — "
            f"app {counts[LogSource.APP]}, emulator {counts[LogSource.EMULATOR]}, "
            f"game {counts[LogSource.GAME]} — {warnings} warning(s), {errors} error(s)"
        )
