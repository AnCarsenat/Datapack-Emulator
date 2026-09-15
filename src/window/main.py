"""Main window: the layout comes from ``window.ui``, the behaviour from here.

Every widget is declared in ``src/window/window.ui`` and looked up by object
name — add or move widgets in Qt Designer, not in this file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QModelIndex, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableView,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from src.emulator import versions
from src.emulator.analysis.graph import CallGraph
from src.emulator.datapack import Datapack
from src.emulator.resources import Resource
from src.emulator.runtime.emulator import Emulator
from src.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus
from src.settings import EMULATION, PATHS, WINDOW
from src.window.engine_window import EngineWindow
from src.window.panels import (
    LEVELS,
    PATH_ROLE,
    RESOURCE_ROLE,
    FunctionGraphWidget,
    LogTableModel,
    build_explorer_model,
    describe_datapack,
    describe_resource,
    highlighter_for,
    load_ui_into,
)

log = logging.getLogger(__name__)

UI_FILE = Path(__file__).with_name("window.ui")

TAB_PROFILER, TAB_GRAPH, TAB_SOURCE, TAB_CHAT = 0, 1, 2, 3


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(parent=None)
        self.datapack: Optional[Datapack] = None
        self.emulator: Optional[Emulator] = None
        self.call_graph: Optional[CallGraph] = None
        self.output = OutputBus()
        self.logs = LogTableModel(self)
        self.engine_window: Optional[EngineWindow] = None
        self._highlighter = None

        load_ui_into(self, UI_FILE, custom=[QWebEngineView])
        self._bind_widgets()
        self._fill_versions()
        self._wire_actions()

        self.output.listeners.append(self._on_record)
        self.resize(WINDOW.WINDOW_WIDTH, WINDOW.WINDOW_HEIGHT)
        self.dock_explorer.show()
        self.dock_logs.show()
        self.statusBar().showMessage("no datapack loaded — file > import datapack")
        self.show_datapack(None)

    # -- setup ------------------------------------------------------------

    def _bind_widgets(self) -> None:
        find = self.findChild
        self.tabs: QTabWidget = find(QTabWidget, "tabWidget")
        self.tree: QTreeView = find(QTreeView, "treeView")
        self.inspector: QTreeWidget = find(QTreeWidget, "inspectorTree")
        self.log_table: QTableView = find(QTableView, "logTable")
        self.source_edit: QPlainTextEdit = find(QPlainTextEdit, "sourceEdit")
        self.chat_view: QPlainTextEdit = find(QPlainTextEdit, "chatView")
        self.web_view: QWebEngineView = find(QWebEngineView, "webEngineView")
        self.pack_label: QLabel = find(QLabel, "labelPack")
        self.graph_label: QLabel = find(QLabel, "labelGraph")
        self.source_label: QLabel = find(QLabel, "labelSource")
        self.log_counts: QLabel = find(QLabel, "labelLogCounts")
        self.combo_version: QComboBox = find(QComboBox, "comboVersion")
        self.combo_level: QComboBox = find(QComboBox, "comboLevel")
        self.edit_filter: QLineEdit = find(QLineEdit, "editLogFilter")
        self.spin_ticks: QSpinBox = find(QSpinBox, "spinTicks")
        self.spin_players: QSpinBox = find(QSpinBox, "spinPlayers")
        self.run_button: QPushButton = find(QPushButton, "buttonRun")
        self.engine_button: QPushButton = find(QPushButton, "buttonEngine")
        self.clear_logs_button: QPushButton = find(QPushButton, "buttonClearLogs")
        self.check_app: QCheckBox = find(QCheckBox, "checkApp")
        self.check_emulator: QCheckBox = find(QCheckBox, "checkEmulator")
        self.check_game: QCheckBox = find(QCheckBox, "checkGame")
        self.dock_explorer: QDockWidget = find(QDockWidget, "dockWidgetExplorer")
        self.dock_inspector: QDockWidget = find(QDockWidget, "dockWidgetInspector")
        self.dock_logs: QDockWidget = find(QDockWidget, "dockWidgetLogs")

        # the only widget the .ui cannot describe: the pyqtgraph canvas
        container: QWidget = find(QWidget, "graphContainer")
        self.graph_widget = FunctionGraphWidget(container)
        container.layout().addWidget(self.graph_widget)

        self.log_table.setModel(self.logs)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setStretchLastSection(True)

        self.spin_ticks.setValue(EMULATION.DEFAULT_TICKS)
        self.spin_players.setValue(EMULATION.DEFAULT_PLAYERS)

        self.tree.clicked.connect(self._on_tree_clicked)
        self.run_button.clicked.connect(self.run_emulator)
        self.engine_button.clicked.connect(self.open_engine)
        self.clear_logs_button.clicked.connect(self.clear_logs)
        self.combo_level.currentTextChanged.connect(self._apply_log_filter)
        self.edit_filter.textChanged.connect(self._apply_log_filter)
        for box in (self.check_app, self.check_emulator, self.check_game):
            box.toggled.connect(self._apply_log_filter)
        self.combo_version.currentTextChanged.connect(self._on_version_changed)
        self._apply_log_filter()

    def _fill_versions(self) -> None:
        self.combo_version.blockSignals(True)
        self.combo_version.clear()
        for version in reversed(versions.VERSIONS):
            self.combo_version.addItem(f"{version.id}  ({version.format_string})", version.id)
        self.combo_version.setCurrentIndex(0)
        self.combo_version.blockSignals(False)

    def _action(self, name: str) -> Optional[QAction]:
        return self.findChild(QAction, name)

    def _wire_actions(self) -> None:
        connections = {
            "actionimport_datapack": self.import_datapack,
            "actionopen_project": self.import_datapack,
            "actionreload_datapack": self.reload_datapack,
            "actionquit": self.close,
            "actionrun_emulator": self.run_emulator,
            "actionrun_profiler": self.run_profiler,
            "actionrun_graphview": self.run_graphview,
            "actionopen_engine": self.open_engine,
            "actionexport_dot": self.export_dot,
            "actionprofile": lambda: self.tabs.setCurrentIndex(TAB_PROFILER),
            "actiongraphview": lambda: self.tabs.setCurrentIndex(TAB_GRAPH),
            "actionsource": lambda: self.tabs.setCurrentIndex(TAB_SOURCE),
            "actionchat": lambda: self.tabs.setCurrentIndex(TAB_CHAT),
            "actionopen_file_in_editor": self.open_selected_externally,
            "actionopen_folder_in_explorer": self.open_selected_folder,
        }
        for name, slot in connections.items():
            action = self._action(name)
            if action is not None:
                action.triggered.connect(slot)

        for name, dock in (
            ("actionexplorer", self.dock_explorer),
            ("actioninspector", self.dock_inspector),
            ("actionlog", self.dock_logs),
        ):
            action = self._action(name)
            if action is None or dock is None:
                continue
            action.toggled.connect(dock.setVisible)
            dock.visibilityChanged.connect(action.setChecked)

    # -- datapack ---------------------------------------------------------

    @property
    def version(self):
        return versions.parse(self.combo_version.currentData() or versions.LATEST)

    def import_datapack(self) -> None:
        start = PATHS.SAMPLES if PATHS.SAMPLES.is_dir() else Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self, "select a datapack folder (the one holding pack.mcmeta)", str(start)
        )
        if chosen:
            self.load_datapack(Path(chosen))

    def load_datapack(self, path: Path) -> None:
        self.clear_logs()
        datapack = Datapack.load(path)
        self.datapack = datapack
        self.output.app(f"loaded {datapack.path}")
        for error in datapack.errors:
            self.output.app(error, level=LogLevel.ERROR)

        self._select_pack_version(datapack)
        self._rebuild_emulator()
        self.call_graph = None
        self.show_datapack(datapack)
        self.statusBar().showMessage(
            f"{datapack.name}: {len(datapack.namespaces)} namespace(s), "
            f"{len(datapack.functions)} function(s), pack_format {datapack.pack_format} "
            f"({datapack.minecraft_version})"
        )

    def _select_pack_version(self, datapack: Datapack) -> None:
        """Default the version combo to what the pack declares."""
        target = versions.closest_to_pack_format(datapack.pack_format)
        index = self.combo_version.findData(target.id)
        if index >= 0:
            self.combo_version.setCurrentIndex(index)

    def _rebuild_emulator(self) -> None:
        if self.datapack is None:
            return
        self.emulator = Emulator(
            self.datapack,
            version=self.version,
            players=self.spin_players.value(),
            output=self.output,
        )

    def _on_version_changed(self, _text: str) -> None:
        if self.datapack is None:
            return
        self._rebuild_emulator()
        self.output.app(f"version set to {self.version.id}")
        self.show_datapack(self.datapack)
        if self.call_graph is not None:
            self.run_graphview()

    def reload_datapack(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("nothing to reload")
            return
        self.load_datapack(self.datapack.path)

    def show_datapack(self, datapack: Optional[Datapack]) -> None:
        self.tree.setModel(build_explorer_model(datapack))
        self.tree.expandToDepth(2)
        if datapack is None:
            self.pack_label.setText("no datapack loaded")
            self._fill_inspector([])
            return
        supported = datapack.supports(self.version)
        self.pack_label.setText(
            f"{datapack.name} — emulating {self.version.id}"
            + ("" if supported else "  (pack does not declare support)")
        )
        self._fill_inspector(describe_datapack(datapack, self.version))

    # -- running ----------------------------------------------------------

    def run_emulator(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("load a datapack first")
            return
        self._rebuild_emulator()
        assert self.emulator is not None
        ticks = self.spin_ticks.value()
        self.clear_logs()
        self.statusBar().showMessage(f"running {ticks} tick(s) on {self.version.id}…")
        profiler = self.emulator.run(ticks=ticks)
        self.statusBar().showMessage(
            f"{self.version.id}: {ticks} tick(s), {profiler.total_us / 1000:.2f} ms estimated, "
            f"worst tick {profiler.worst_tick_us / 1000:.2f} ms"
        )
        self._refresh_chat()
        self.run_profiler()

    def run_profiler(self) -> None:
        """Regenerate the HTML report and show it in the profiler tab."""
        if self.emulator is None:
            self.statusBar().showMessage("load a datapack first")
            return
        name = self.datapack.name if self.datapack else "datapack"
        report = self.emulator.profiler.write_html(
            PATHS.GENERATED / "index.html",
            f"Function profiler — {name}",
            f"{self.version.id} (pack_format {self.version.format_string})",
        )
        self.output.app(f"wrote {report}")
        self.web_view.setUrl(QUrl.fromLocalFile(str(report)))
        self.web_view.reload()
        self.tabs.setCurrentIndex(TAB_PROFILER)

    def run_graphview(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("load a datapack first")
            return
        view = self.datapack.view_for(self.version)
        self.call_graph = CallGraph.from_pack(view)
        self.graph_widget.set_graph(self.call_graph)
        cycles = self.call_graph.cycles()
        missing = self.call_graph.missing()
        unreachable = self.call_graph.unreachable()
        self.graph_label.setText(
            f"{self.version.id}: {len(self.call_graph.nodes)} node(s), "
            f"{len(self.call_graph.edges)} edge(s) — "
            + ("DAG" if not cycles else f"{len(cycles)} cycle(s)")
            + (f", {len(missing)} missing" if missing else "")
            + (f", {len(unreachable)} unreachable" if unreachable else "")
        )
        for cycle in cycles:
            self.output.emulator("recursion: " + " -> ".join(cycle), level=LogLevel.WARNING)
        for name in missing:
            self.output.emulator(f"calls missing function {name}", level=LogLevel.ERROR)
        for name in unreachable:
            self.output.emulator(
                f"{name} is never called from #minecraft:load/tick", level=LogLevel.WARNING
            )
        self.tabs.setCurrentIndex(TAB_GRAPH)

    def export_dot(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("load a datapack first")
            return
        graph = self.call_graph or CallGraph.from_pack(self.datapack.view_for(self.version))
        target = PATHS.GENERATED / f"{self.datapack.name}-{self.version.id}.dot"
        graph.write_dot(target)
        self.statusBar().showMessage(f"wrote {target}")
        self.output.app(f"call graph exported to {target}")

    def open_engine(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("load a datapack first")
            return
        if self.engine_window is None:
            self.engine_window = EngineWindow(self.datapack, parent=self)
        else:
            self.engine_window.set_datapack(self.datapack)
        self.engine_window.show()
        self.engine_window.raise_()
        self.engine_window.activateWindow()

    # -- explorer / inspector ---------------------------------------------

    def _selected_path(self) -> Optional[Path]:
        indexes = self.tree.selectedIndexes()
        if not indexes:
            return None
        value = indexes[0].data(PATH_ROLE)
        return Path(value) if value else None

    def open_selected_externally(self) -> None:
        from PySide6.QtGui import QDesktopServices

        path = self._selected_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_selected_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices

        path = self._selected_path()
        if path is not None:
            folder = path if path.is_dir() else path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        path_value = index.data(PATH_ROLE)
        resource_id = index.data(RESOURCE_ROLE)
        resource = self._find_resource(resource_id) if resource_id else None

        if resource is not None:
            self._fill_inspector(describe_resource(resource, self.version))
        elif self.datapack is not None:
            self._fill_inspector(describe_datapack(self.datapack, self.version))

        if not path_value:
            return
        path = Path(path_value)
        if path.is_file():
            self._show_source(path)

    def _find_resource(self, resource_id: str) -> Optional[Resource]:
        if self.datapack is None:
            return None
        for layer in [self.datapack.base, *self.datapack.overlays]:
            for namespace in layer.namespaces.values():
                for resource in namespace.resources():
                    if resource.id == resource_id:
                        return resource
        return None

    def _show_source(self, path: Path) -> None:
        self.source_label.setText(str(path))
        if path.suffix.lower() == ".png":
            self.source_edit.setPlainText(f"{path.name}: binary image")
            self._highlighter = None
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            text = f"cannot read {path}: {exc}"
        self._highlighter = highlighter_for(path, self.source_edit.document())
        self.source_edit.setPlainText(text)
        self.tabs.setCurrentIndex(TAB_SOURCE)

    def _fill_inspector(self, rows: list[tuple[str, str]]) -> None:
        self.inspector.clear()
        for key, value in rows:
            self.inspector.addTopLevelItem(QTreeWidgetItem([key, str(value)]))
        self.inspector.resizeColumnToContents(0)

    # -- logs -------------------------------------------------------------

    def _on_record(self, record: LogRecord) -> None:
        self.logs.append(record)
        self.log_counts.setText(self.logs.summary())
        self.log_table.scrollToBottom()

    def _apply_log_filter(self) -> None:
        sources = set()
        if self.check_app.isChecked():
            sources.add(LogSource.APP)
        if self.check_emulator.isChecked():
            sources.add(LogSource.EMULATOR)
        if self.check_game.isChecked():
            sources.add(LogSource.GAME)
        self.logs.set_filter(
            sources=sources,
            level=LEVELS.get(self.combo_level.currentText(), LogLevel.INFO),
            text=self.edit_filter.text(),
        )
        self.log_counts.setText(self.logs.summary())
        self.log_table.resizeColumnsToContents()

    def clear_logs(self) -> None:
        self.output.clear()
        self.logs.clear()
        self.chat_view.clear()
        self.log_counts.setText(self.logs.summary())

    def _refresh_chat(self) -> None:
        """The game tab: only what a player would have seen."""
        lines = []
        for record in self.output.records:
            if record.source is not LogSource.GAME:
                continue
            if record.level >= LogLevel.ERROR:
                lines.append(f"[{record.tick}] § {record.message}")
            elif record.level > LogLevel.DEBUG:
                lines.append(f"[{record.tick}] {record.message}")
        self.chat_view.setPlainText("\n".join(lines))
