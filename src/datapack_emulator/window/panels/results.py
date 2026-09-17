"""Table model for the version matrix in the engine window."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from datapack_emulator.emulator.engine import VersionRun

COLUMNS = (
    "version",
    "format",
    "status",
    "ticks",
    "commands",
    "total ms",
    "worst ms",
    "warnings",
    "errors",
    "tests",
    "unknown commands",
    "overlays",
)

COLUMN_HELP = {
    "version": "the Minecraft version this row ran",
    "format": "its pack format",
    "status": "cancelled (stopped before its last tick) › errors › tests failed › warnings › "
    "unsupported (the metadata does not claim this version; it still loads) › ok",
    "ticks": "ticks run; a cancelled run shows how far it got (7/20)",
    "commands": "commands run in all ticks",
    "total ms": "estimated time of every tick together (a cost model, not a measurement)",
    "worst ms": "estimated time of the slowest tick; 50 ms is a whole tick",
    "warnings": "warning records: functions or tags that failed to load, metadata problems, …",
    "errors": "error records: typed or test commands that failed, emulator crashes",
    "tests": "command tests passed out of those run, when run tests is ticked; tests a "
    "cancel stopped are counted as skipped",
    "unknown commands": "commands the version does not have, used by functions that failed to load",
    "overlays": "overlay folders active in this version",
}

STATUS_COLOURS = {
    "ok": QColor("#1e6f3d"),
    "warnings": QColor("#b9770e"),
    "errors": QColor("#c0392b"),
    "unsupported": QColor("#7f8c8d"),
    "cancelled": QColor("#555555"),
    "tests failed": QColor("#8e44ad"),
}


class ResultsTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._runs: list[VersionRun] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(self._runs)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.ToolTipRole:
            return COLUMN_HELP.get(COLUMNS[section])
        return COLUMNS[section] if role == Qt.DisplayRole else None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        run = self._runs[index.row()]
        if role == Qt.DisplayRole:
            return (
                run.version.id,
                run.version.format_string,
                run.status,
                run.ticks_summary,
                str(run.commands),
                f"{run.total_us / 1000:.2f}",
                f"{run.worst_tick_us / 1000:.2f}",
                str(run.warnings),
                str(run.errors),
                run.tests_summary,
                ", ".join(sorted(run.unknown_commands)) or "-",
                ", ".join(run.overlays) or "-",
            )[index.column()]
        if role == Qt.ForegroundRole and index.column() == 2:
            return QBrush(STATUS_COLOURS.get(run.status, QColor("#111111")))
        if role == Qt.FontRole and index.column() == 2:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.UserRole:
            return run
        return None

    # -- feeding ----------------------------------------------------------

    def set_runs(self, runs: list[VersionRun]) -> None:
        self.beginResetModel()
        self._runs = list(runs)
        self.endResetModel()

    def append(self, run: VersionRun) -> None:
        self.beginInsertRows(QModelIndex(), len(self._runs), len(self._runs))
        self._runs.append(run)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._runs.clear()
        self.endResetModel()

    def run_at(self, row: int) -> VersionRun | None:
        return self._runs[row] if 0 <= row < len(self._runs) else None

    @property
    def runs(self) -> list[VersionRun]:
        return self._runs
