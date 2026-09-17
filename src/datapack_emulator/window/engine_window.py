"""The version test engine window: layout from ``engine.ui``, behaviour here.

Pick versions on the left (a range, the range the pack declares, one per pack
format, or hand-picked), run, and every version gets its own row with its own
log records underneath.

The versions run on a worker thread (``EngineWorker``), so the window stays
responsive; each finished version arrives through a signal, and *cancel*
stops the run after its current tick. The worker only reads the datapack and
builds its own emulators and output buses.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
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

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.datapack import Datapack
from datapack_emulator.emulator.engine import TestEngine, VersionRun
from datapack_emulator.emulator.runtime.output import LogLevel, LogSource, OutputBus
from datapack_emulator.emulator.vanilla import default_library
from datapack_emulator.settings import EMULATION, PATHS
from datapack_emulator.window.panels import (
    LEVELS,
    LogTableModel,
    ResultsTableModel,
    load_ui_into,
)

log = logging.getLogger(__name__)

UI_FILE = Path(__file__).with_name("engine.ui")


class EngineWorker(QObject):
    """Runs the chosen versions off the UI thread."""

    #: (1-based index, count, version id) before each version
    started_version = Signal(int, int, str)
    #: a finished (or cancelled) VersionRun
    finished_version = Signal(object)
    #: every version ran, or the run was cancelled (True)
    done = Signal(bool)
    failed = Signal(str)

    def __init__(self, engine: TestEngine, chosen: list):
        super().__init__()
        self.engine = engine
        self.chosen = chosen
        self._cancel_requested = False

    def request_cancel(self) -> None:
        # read between ticks from the worker thread; a plain bool is enough
        self._cancel_requested = True

    def cancelled(self) -> bool:
        return self._cancel_requested

    @Slot()
    def run(self) -> None:
        try:
            for index, version in enumerate(self.chosen, start=1):
                if self._cancel_requested:
                    break
                self.started_version.emit(index, len(self.chosen), version.id)
                run = self.engine.run_version(version, output=OutputBus(), cancelled=self.cancelled)
                self.finished_version.emit(run)
                if run.cancelled:
                    break
        except Exception as exc:  # a crash must end the run, not the app
            log.exception("engine run failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        self.done.emit(self._cancel_requested)


class EngineWindow(QMainWindow):
    def __init__(self, datapack: Datapack, parent=None, library=None, tests_provider=None):
        super().__init__(parent)
        self.datapack = datapack
        #: returns the tests to run when "run tests" is ticked (the main window's)
        self.tests_provider = tests_provider or list
        self.library = library or default_library()
        self.results = ResultsTableModel(self)
        self.records = LogTableModel(self)
        self._runs: list[VersionRun] = []
        self._thread: QThread | None = None
        self._worker: EngineWorker | None = None
        self._total = 0

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
        self.check_run_tests: QCheckBox = find(QCheckBox, "checkRunTests")

        self.spin_ticks.setValue(EMULATION.DEFAULT_TICKS)
        self.spin_players.setValue(EMULATION.DEFAULT_PLAYERS)

        self.table_results.setModel(self.results)
        self.table_results.verticalHeader().setVisible(False)
        self.table_records.setModel(self.records)
        self.table_records.verticalHeader().setVisible(False)
        self.table_records.horizontalHeader().setStretchLastSection(True)

        self.button_run: QPushButton = find(QPushButton, "buttonRun")
        self.button_cancel: QPushButton = find(QPushButton, "buttonCancel")
        self.button_run.clicked.connect(self.run_matrix)
        self.button_cancel.clicked.connect(self.cancel)
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
        self.cancel(wait=True)  # a run of the old pack would report into the new one
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

    def select_ids(self, ids: list[str]) -> None:
        """Tick exactly these versions (a project's saved selection); unknown ids are ignored."""
        known = {version.id for version in versions.VERSIONS}
        self._check_only([versions.parse(i) for i in ids if i in known])

    def selected_versions(self) -> list:
        return [
            versions.parse(item.data(Qt.UserRole))
            for item in self._items()
            if item.checkState() == Qt.Checked
        ]

    def select_range(self) -> None:
        chosen = versions.version_range(self.combo_from.currentData(), self.combo_to.currentData())
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

    @property
    def running(self) -> bool:
        return self._thread is not None

    def run_matrix(self) -> None:
        if self.running:
            self.statusBar().showMessage("a run is going: cancel it first")
            return
        chosen = self.selected_versions()
        if not chosen:
            self.statusBar().showMessage("select at least one version")
            return

        tests = self.tests_provider() if self.check_run_tests.isChecked() else []
        if self.check_run_tests.isChecked() and not any(test.enabled for test in tests):
            self.statusBar().showMessage("no enabled tests: add some in the environment tab")
            return
        engine = TestEngine(
            self.datapack,
            ticks=self.spin_ticks.value(),
            players=self.spin_players.value(),
            seed=self.spin_seed.value(),
            library=self.library,
            tests=tests,
        )
        self.results.clear()
        self.records.clear()
        self._runs = []
        self.progress.setMaximum(len(chosen))
        self.progress.setValue(0)
        self._total = len(chosen)

        self._thread = QThread(self)
        self._worker = EngineWorker(engine, chosen)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.started_version.connect(self._on_started_version)
        self._worker.finished_version.connect(self._on_finished_version)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._on_done)
        self._set_running(True)
        self._thread.start()

    def cancel(self, wait: bool = False) -> None:
        """Stop after the current tick (``wait``: block until the worker ended)."""
        if self._worker is None:
            return
        self._worker.request_cancel()
        self.button_cancel.setEnabled(False)
        self.statusBar().showMessage("cancelling after this tick…")
        if wait:
            self.wait()

    def wait(self) -> None:
        """Block until the run ended and its results were shown (tests, closing)."""
        while self._thread is not None:
            self._thread.wait(10)
            QApplication.processEvents()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.cancel(wait=True)
        super().closeEvent(event)

    def _set_running(self, running: bool) -> None:
        self.button_run.setEnabled(not running)
        self.button_cancel.setEnabled(running)
        for widget in (self.list_versions, self.spin_ticks, self.spin_players, self.spin_seed,
                       self.check_run_tests):  # fmt: skip
            widget.setEnabled(not running)

    def _on_started_version(self, index: int, total: int, version_id: str) -> None:
        self.statusBar().showMessage(f"[{index}/{total}] {version_id}…")
        self.progress.setValue(index - 1)

    def _on_finished_version(self, run: VersionRun) -> None:
        self._runs.append(run)
        self.results.append(run)
        self.progress.setValue(len(self._runs))
        self.table_results.resizeColumnsToContents()
        if len(self._runs) == 1:
            self.table_results.selectRow(0)

    def _on_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"the run failed: {message}")

    def _on_done(self, cancelled: bool) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        self._set_running(False)
        worst = [
            run for run in self._runs if run.status in ("errors", "tests failed", "unsupported")
        ]
        self.label_results.setText(
            f"results — {len(self._runs)} version(s), {len(worst)} with problems"
        )
        if self.statusBar().currentMessage().startswith("the run failed"):
            return
        summary = (
            f"{len(self._runs)} version(s), "
            f"{sum(run.errors for run in self._runs)} error(s), "
            f"{sum(run.warnings for run in self._runs)} warning(s)"
        )
        if cancelled:
            skipped = self._total - len(self._runs)
            self.statusBar().showMessage(f"cancelled: {summary}; {skipped} version(s) not run")
        else:
            self.progress.setValue(self._total)
            self.statusBar().showMessage(f"done: {summary}")

    def export_html(self) -> None:
        if self.running:
            self.statusBar().showMessage("wait for the run to end (or cancel it)")
            return
        if not self._runs:
            self.statusBar().showMessage("run the matrix first")
            return
        target = PATHS.GENERATED / f"{self.datapack.name.replace(' + ', '+')}-matrix.html"
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
            + (f", tests {run.tests_summary}" if run.tests else "")
            + (f", overlays: {', '.join(run.overlays)}" if run.overlays else "")
        )

    def _current_run(self) -> VersionRun | None:
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
