"""The version test engine window: layout from ``engine.ui``, behaviour here.

Pick versions on the left (a range, the range the pack declares, one per pack
format, or hand-picked), run, and every version gets its own row with its own
log records underneath.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableView,
)

from src.emulator import versions
from src.emulator.datapack import Datapack
from src.emulator.engine import TestEngine, VersionRun
from src.emulator.runtime.output import LogLevel, LogSource, OutputBus
from src.settings import EMULATION, PATHS
from src.window.panels import (
    LEVELS,
    LogTableModel,
    ResultsTableModel,
    load_ui_into,
)

log = logging.getLogger(__name__)

UI_FILE = Path(__file__).with_name("engine.ui")


class EngineWindow(QMainWindow):
    def __init__(self, datapack: Datapack, parent=None):
        super().__init__(parent)
        self.datapack = datapack
        self.results = ResultsTableModel(self)
        self.records = LogTableModel(self)
        self._runs: list[VersionRun] = []

        load_ui_into(self, UI_FILE)
        self._bind_widgets()
        self._fill_versions()
        self.select_declared()
        self.setWindowTitle(f"Version test engine — {datapack.name}")

    # -- setup ------------------------------------------------------------

    def _bind_widgets(self) -> None:
        find = self.findChild
        self.combo_from: QComboBox = find(QComboBox, "comboFrom")
        self.combo_to: QComboBox = find(QComboBox, "comboTo")
        self.list_versions: QListWidget = find(QListWidget, "listVersions")
        self.spin_ticks: QSpinBox = find(QSpinBox, "spinTicks")
        self.spin_players: QSpinBox = find(QSpinBox, "spinPlayers")
        self.spin_seed: QSpinBox = find(QSpinBox, "spinSeed")
        self.progress: QProgressBar = find(QProgressBar, "progressBar")
        self.table_results: QTableView = find(QTableView, "tableResults")
        self.table_records: QTableView = find(QTableView, "tableRecords")
        self.label_results: QLabel = find(QLabel, "labelResults")
        self.label_detail: QLabel = find(QLabel, "labelDetail")
        self.combo_level: QComboBox = find(QComboBox, "comboLevel")
        self.edit_filter: QLineEdit = find(QLineEdit, "editFilter")
        self.check_app: QCheckBox = find(QCheckBox, "checkApp")
        self.check_emulator: QCheckBox = find(QCheckBox, "checkEmulator")
        self.check_game: QCheckBox = find(QCheckBox, "checkGame")

        self.spin_ticks.setValue(EMULATION.DEFAULT_TICKS)
        self.spin_players.setValue(EMULATION.DEFAULT_PLAYERS)

        self.table_results.setModel(self.results)
        self.table_results.verticalHeader().setVisible(False)
        self.table_records.setModel(self.records)
        self.table_records.verticalHeader().setVisible(False)
        self.table_records.horizontalHeader().setStretchLastSection(True)

        find(QPushButton, "buttonRun").clicked.connect(self.run_matrix)
        find(QPushButton, "buttonExport").clicked.connect(self.export_html)
        find(QPushButton, "buttonSelectRange").clicked.connect(self.select_range)
        find(QPushButton, "buttonSelectDeclared").clicked.connect(self.select_declared)
        find(QPushButton, "buttonSelectBoundaries").clicked.connect(self.select_boundaries)
        find(QPushButton, "buttonSelectAll").clicked.connect(lambda: self._set_all(True))
        find(QPushButton, "buttonSelectNone").clicked.connect(lambda: self._set_all(False))

        self.table_results.selectionModel().selectionChanged.connect(self._on_result_selected)
        self.combo_level.currentTextChanged.connect(self._apply_filter)
        self.edit_filter.textChanged.connect(self._apply_filter)
        for box in (self.check_app, self.check_emulator, self.check_game):
            box.toggled.connect(self._apply_filter)

    def _fill_versions(self) -> None:
        self.list_versions.clear()
        for version in versions.VERSIONS:
            item = QListWidgetItem(f"{version.id}  ({version.format_string})")
            item.setData(Qt.UserRole, version.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list_versions.addItem(item)
        for combo in (self.combo_from, self.combo_to):
            combo.clear()
            for version in versions.VERSIONS:
                combo.addItem(version.id, version.id)
        self.combo_from.setCurrentIndex(0)
        self.combo_to.setCurrentIndex(self.combo_to.count() - 1)

    def set_datapack(self, datapack: Datapack) -> None:
        self.datapack = datapack
        self.setWindowTitle(f"Version test engine — {datapack.name}")
        self.select_declared()

    # -- selection --------------------------------------------------------

    def _items(self):
        for row in range(self.list_versions.count()):
            yield self.list_versions.item(row)

    def _set_all(self, checked: bool) -> None:
        for item in self._items():
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._update_selection_label()

    def _check_only(self, chosen: list) -> None:
        wanted = {version.id for version in chosen}
        for item in self._items():
            state = Qt.Checked if item.data(Qt.UserRole) in wanted else Qt.Unchecked
            item.setCheckState(state)
        self._update_selection_label()

    def selected_versions(self) -> list:
        return [
            versions.parse(item.data(Qt.UserRole))
            for item in self._items()
            if item.checkState() == Qt.Checked
        ]

    def select_range(self) -> None:
        chosen = versions.version_range(
            self.combo_from.currentData(), self.combo_to.currentData()
        )
        self._check_only(chosen)

    def select_declared(self) -> None:
        declared = self.datapack.declared_versions()
        if not declared:
            declared = [versions.closest_to_pack_format(self.datapack.pack_format)]
            self.statusBar().showMessage(
                "pack.mcmeta declares no range; selected the version matching its pack_format"
            )
        self._check_only(declared)
        if declared:
            index = self.combo_from.findData(declared[0].id)
            if index >= 0:
                self.combo_from.setCurrentIndex(index)
            index = self.combo_to.findData(declared[-1].id)
            if index >= 0:
                self.combo_to.setCurrentIndex(index)

    def select_boundaries(self) -> None:
        current = self.selected_versions() or list(versions.VERSIONS)
        self._check_only(TestEngine.format_boundaries(current))

    def _update_selection_label(self) -> None:
        count = len(self.selected_versions())
        self.label_results.setText(
            f"results — {count} version(s) selected" if count else "results — nothing selected"
        )

    # -- running ----------------------------------------------------------

    def run_matrix(self) -> None:
        chosen = self.selected_versions()
        if not chosen:
            self.statusBar().showMessage("select at least one version")
            return

        engine = TestEngine(
            self.datapack,
            ticks=self.spin_ticks.value(),
            players=self.spin_players.value(),
            seed=self.spin_seed.value(),
        )
        self.results.clear()
        self.records.clear()
        self._runs = []
        self.progress.setMaximum(len(chosen))
        self.progress.setValue(0)

        for index, version in enumerate(chosen, start=1):
            self.statusBar().showMessage(f"[{index}/{len(chosen)}] {version.id}…")
            self.progress.setValue(index - 1)
            self.repaint()
            run = engine.run_version(version, output=OutputBus())
            self._runs.append(run)
            self.results.append(run)
            self.table_results.resizeColumnsToContents()
            self.repaint()

        self.progress.setValue(len(chosen))
        worst = [run for run in self._runs if run.status in ("errors", "unsupported")]
        self.label_results.setText(
            f"results — {len(self._runs)} version(s), {len(worst)} with problems"
        )
        self.statusBar().showMessage(
            f"done: {len(self._runs)} version(s), "
            f"{sum(run.errors for run in self._runs)} error(s), "
            f"{sum(run.warnings for run in self._runs)} warning(s)"
        )
        if self._runs:
            self.table_results.selectRow(0)

    def export_html(self) -> None:
        if not self._runs:
            self.statusBar().showMessage("run the matrix first")
            return
        target = PATHS.GENERATED / f"{self.datapack.name}-matrix.html"
        TestEngine.write_html(self._runs, target, self.datapack.name)
        self.statusBar().showMessage(f"wrote {target}")

    # -- detail -----------------------------------------------------------

    def _on_result_selected(self, *_args) -> None:
        run = self._current_run()
        if run is None:
            return
        self.records.set_records(run.records)
        self._apply_filter()
        self.label_detail.setText(
            f"{run.version.id} — {run.status}, {run.commands} command(s), "
            f"worst tick {run.worst_tick_us / 1000:.2f} ms"
            + (f", overlays: {', '.join(run.overlays)}" if run.overlays else "")
        )

    def _current_run(self) -> Optional[VersionRun]:
        indexes = self.table_results.selectionModel().selectedRows()
        if not indexes:
            return None
        return self.results.run_at(indexes[0].row())

    def _apply_filter(self) -> None:
        sources = set()
        if self.check_app.isChecked():
            sources.add(LogSource.APP)
        if self.check_emulator.isChecked():
            sources.add(LogSource.EMULATOR)
        if self.check_game.isChecked():
            sources.add(LogSource.GAME)
        self.records.set_filter(
            sources=sources,
            level=LEVELS.get(self.combo_level.currentText(), LogLevel.INFO),
            text=self.edit_filter.text(),
        )
        self.table_records.resizeColumnsToContents()
