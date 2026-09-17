"""Log table: one row per :class:`LogRecord`, filterable by source and level.

The three sources are coloured differently on purpose — ``game`` rows are what
Minecraft itself would have printed, ``emulator`` rows are this engine's own
diagnostics, ``app`` rows are the program talking about itself.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource, trim_records

COLUMNS = ("tick", "source", "level", "where", "message")
COLUMN_HELP = (
    "the server tick the record was made in",
    "app: this program · emulator: the emulation engine · game: what Minecraft would print",
    "debug, info, warning or error; failures inside functions are debug: silent in game",
    "function:line the record came from; double-click to open it",
    "right-click to copy it, open its file, analyze its command or add it as a test",
)

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

#: a command that failed inside a function: the game shows nobody, so muted
SILENT_FAILURE_COLOUR = QColor("#b07a74")

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
        #: "" for every record, otherwise only the chat this player reads
        self._reader: str = ""

    # -- data -------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.ToolTipRole:
            return COLUMN_HELP[section]
        return COLUMNS[section] if role == Qt.DisplayRole else None

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
            if record.failure and record.level < LogLevel.ERROR:
                return QBrush(SILENT_FAILURE_COLOUR)
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

    #: records kept for the table; on long or endless runs the oldest go first,
    #: debug before info before warnings before errors
    MAX_RECORDS = 50_000

    def append(self, record: LogRecord) -> None:
        self.extend([record])

    def extend(self, records: list[LogRecord]) -> None:
        """Add many records with one model update (endless runs log a lot)."""
        if not records:
            return
        self._records.extend(records)
        if len(self._records) > self.MAX_RECORDS:
            self.beginResetModel()
            # trim well below the cap, so this happens rarely
            self._records = trim_records(self._records, self.MAX_RECORDS * 4 // 5)
            self._refilter()
            self.endResetModel()
            return
        shown = [record for record in records if self._passes(record)]
        if shown:
            start = len(self._visible)
            self.beginInsertRows(QModelIndex(), start, start + len(shown) - 1)
            self._visible.extend(shown)
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
        reader: str | None = None,
    ) -> None:
        if reader is not None:
            self._reader = reader
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
            and record.seen_by(self._reader)
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
