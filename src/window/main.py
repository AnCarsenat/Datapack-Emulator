"""Main window: the layout comes from ``window.ui``, the behaviour from here.

Every widget is declared in ``src/window/window.ui`` and looked up by object
name — add or move widgets in Qt Designer, not in this file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import shiboken6
from PySide6.QtCore import QModelIndex, QPoint, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QTabWidget,
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
from src.emulator.vanilla import VanillaAssets, default_library
from src.project import Project, default_sample, projects_dir
from src.settings import EMULATION, PATHS, WINDOW
from src.window.download_dialog import DownloadDialog
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

TAB_PROFILER, TAB_GRAPH, TAB_SOURCE = 0, 1, 2


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(parent=None)
        self.datapack: Datapack | None = None
        self.emulator: Emulator | None = None
        self.call_graph: CallGraph | None = None
        self.output = OutputBus()
        self.logs = LogTableModel(self)
        self.library = default_library()
        self.vanilla: VanillaAssets | None = None
        self.engine_window: EngineWindow | None = None
        self.project = Project()
        self._highlighter = None

        load_ui_into(self, UI_FILE, custom=[QWebEngineView])
        self._bind_widgets()
        self._fill_versions()
        self._wire_actions()

        self.output.listeners.append(self._on_record)
        self.resize(WINDOW.WINDOW_WIDTH, WINDOW.WINDOW_HEIGHT)
        self.show_all_docks()
        self.show_datapack(None)
        self.open_default_datapack()

    # -- setup ------------------------------------------------------------

    def _bind_widgets(self) -> None:
        find = self.findChild
        self.tabs: QTabWidget = find(QTabWidget, "tabWidget")
        self.tree: QTreeView = find(QTreeView, "treeView")
        self.inspector: QTreeWidget = find(QTreeWidget, "inspectorTree")
        self.log_table: QTableView = find(QTableView, "logTable")
        self.source_edit: QPlainTextEdit = find(QPlainTextEdit, "sourceEdit")
        self.web_view: QWebEngineView = find(QWebEngineView, "webEngineView")
        self.pack_label: QLabel = find(QLabel, "labelPack")
        self.vanilla_label: QLabel = find(QLabel, "labelVanilla")
        self.graph_label: QLabel = find(QLabel, "labelGraph")
        self.source_label: QLabel = find(QLabel, "labelSource")
        self.log_counts: QLabel = find(QLabel, "labelLogCounts")
        self.combo_version: QComboBox = find(QComboBox, "comboVersion")
        self.combo_level: QComboBox = find(QComboBox, "comboLevel")
        self.edit_filter: QLineEdit = find(QLineEdit, "editLogFilter")
        self.spin_ticks: QSpinBox = find(QSpinBox, "spinTicks")
        self.spin_players: QSpinBox = find(QSpinBox, "spinPlayers")
        self.run_button: QPushButton = find(QPushButton, "buttonRun")
        self.run_all_button: QPushButton = find(QPushButton, "buttonRunAll")
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
        self.tree.customContextMenuRequested.connect(self._explorer_menu)
        self.graph_widget.node_menu_requested.connect(self._graph_menu)
        self.web_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.web_view.customContextMenuRequested.connect(self._profiler_menu)
        self.run_button.clicked.connect(self.run_emulator)
        self.run_all_button.clicked.connect(self.run_all)
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

    def _action(self, name: str) -> QAction | None:
        return self.findChild(QAction, name)

    def _wire_actions(self) -> None:
        connections = {
            "actionimport_datapack": self.import_datapack,
            "actionnew_project": self.new_project,
            "actionopen_project": self.open_project,
            "actionsave_project": self.save_project,
            "actionsave_project_as": self.save_project_as,
            "actionreload_datapack": self.reload_datapack,
            "actionquit": self.close,
            "actionrun_all": self.run_all,
            "actionrun_emulator": self.run_emulator,
            "actionrun_profiler": self.run_profiler,
            "actionrun_graphview": self.run_graphview,
            "actionopen_engine": self.open_engine,
            "actionload_vanilla": self.load_vanilla_jar,
            "actiondownload_vanilla": self.download_vanilla_jar,
            "actionexport_dot": self.export_dot,
            "actionprofile": lambda: self.tabs.setCurrentIndex(TAB_PROFILER),
            "actiongraphview": lambda: self.tabs.setCurrentIndex(TAB_GRAPH),
            "actionsource": lambda: self.tabs.setCurrentIndex(TAB_SOURCE),
            "actionopen_file_in_editor": self.open_selected_externally,
            "actionopen_folder_in_explorer": self.open_selected_in_file_manager,
            "actionopen_in_source": lambda: self.open_in_source(self._selected_path()),
            "actioncopy_path": lambda: self.copy_path(self._selected_path()),
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
            # `triggered` fires only when the user clicks the menu entry, never
            # when the checkmark is updated from code, so hiding the window
            # (minimise, workspace switch, the window manager's first map)
            # can no longer switch a dock off for good.
            action.triggered.connect(
                lambda checked, dock=dock: self._set_dock_visible(dock, checked)
            )
            # `isHidden()` is the dock's own flag; `visibilityChanged(False)` also
            # fires when only the main window is hidden.
            dock.visibilityChanged.connect(
                lambda _visible, dock=dock, action=action: self._sync_dock_action(dock, action)
            )

    @staticmethod
    def _set_dock_visible(dock: QDockWidget, visible: bool) -> None:
        if shiboken6.isValid(dock):
            dock.setVisible(visible)

    @staticmethod
    def _sync_dock_action(dock: QDockWidget, action: QAction) -> None:
        # also runs while Qt tears the window down, after the C++ objects are gone
        if shiboken6.isValid(dock) and shiboken6.isValid(action):
            action.setChecked(not dock.isHidden())

    @property
    def docks(self) -> tuple[QDockWidget, ...]:
        return (self.dock_explorer, self.dock_inspector, self.dock_logs)

    def show_all_docks(self) -> None:
        """Every panel open and docked — the default layout."""
        for dock in self.docks:
            dock.setFloating(False)
            dock.show()
        for name in ("actionexplorer", "actioninspector", "actionlog"):
            action = self._action(name)
            if action is not None:
                action.setChecked(True)

    # -- projects ---------------------------------------------------------

    def _apply_project(self, project: Project) -> None:
        """Put a loaded project into the widgets, then open its datapack."""
        self.project = project
        if project.version:
            index = self.combo_version.findData(project.version)
            if index >= 0:
                self.combo_version.setCurrentIndex(index)
        self.spin_ticks.setValue(project.ticks)
        self.spin_players.setValue(project.players)
        if project.vanilla_jar and Path(project.vanilla_jar).is_file():
            try:
                self.use_vanilla(self.library.load_jar(Path(project.vanilla_jar)))
            except Exception as exc:
                self.output.app(f"cannot read {project.vanilla_jar}: {exc}", level=LogLevel.ERROR)
        if project.datapack and Path(project.datapack).is_dir():
            self.load_datapack(Path(project.datapack), keep_project=True)
        self._refresh_title()

    def _capture_project(self) -> Project:
        """Read the current window state back into the project."""
        self.project.datapack = self.datapack.path if self.datapack else None
        self.project.version = self.version.id
        self.project.ticks = self.spin_ticks.value()
        self.project.players = self.spin_players.value()
        self.project.vanilla_jar = str(self.vanilla.jar_path) if self.vanilla else ""
        if self.engine_window is not None:
            self.project.engine_versions = [
                version.id for version in self.engine_window.selected_versions()
            ]
        return self.project

    def _refresh_title(self) -> None:
        where = f" — {self.project.path}" if self.project.path else ""
        self.setWindowTitle(f"Datapack Emulator — {self.project.title}{where}")

    def new_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "new project", "project name:")
        if not accepted or not name.strip():
            return
        self.project = Project(name=name.strip())
        self._capture_project()
        self.save_project()

    def open_project(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "open project", str(projects_dir()), "Projects (*.json)"
        )
        if not chosen:
            return
        try:
            project = Project.load(Path(chosen))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "open project", f"cannot read {chosen}:\n{exc}")
            return
        self.output.app(f"opened project {chosen}")
        self._apply_project(project)

    def save_project(self) -> None:
        project = self._capture_project()
        if project.path is None and project.name == "untitled" and self.datapack is not None:
            project.name = self.datapack.name
        target = project.save()
        self._refresh_title()
        self.output.app(f"saved project {target}")
        self.statusBar().showMessage(f"saved {target}")

    def save_project_as(self) -> None:
        suggestion = str(self._capture_project().default_path())
        chosen, _ = QFileDialog.getSaveFileName(
            self, "save project as", suggestion, "Projects (*.json)"
        )
        if not chosen:
            return
        self.project = self.project.renamed(Path(chosen).stem)
        self._capture_project().save(Path(chosen))
        self._refresh_title()
        self.statusBar().showMessage(f"saved {chosen}")

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

    def open_default_datapack(self) -> None:
        """Cold start: open the first pack in samples/ so there is something to run."""
        sample = default_sample()
        if sample is None:
            self.statusBar().showMessage("no datapack loaded — file > import datapack")
            return
        self.load_datapack(sample)
        self.output.app(f"opened the sample datapack {sample.name} (file > import to change)")

    def load_datapack(
        self, path: Path, keep_project: bool = False, keep_version: bool = False
    ) -> None:
        self.clear_logs()
        datapack = Datapack.load(path)
        self.datapack = datapack
        self.output.app(f"loaded {datapack.path}")
        for error in datapack.errors:
            self.output.app(error, level=LogLevel.ERROR)

        if not keep_version and (not keep_project or not self.project.version):
            # reloading keeps the version on screen and a project remembers its
            # own; a freshly imported pack gets the release matching its format
            self._select_pack_version(datapack)
        self.autoload_vanilla()
        self._rebuild_emulator()
        self.call_graph = None
        self.show_datapack(datapack)
        if not keep_project:
            if self.project.path is None:
                self.project.name = datapack.name
            self.project.datapack = datapack.path
        self._refresh_title()
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
            vanilla=self.vanilla,
        )

    # -- base game --------------------------------------------------------

    def use_vanilla(self, assets: VanillaAssets | None) -> None:
        """Adopt base-game assets: registries to check ids, strings to quote."""
        self.vanilla = assets
        if assets is None:
            self.vanilla_label.setText("no client jar")
            self.vanilla_label.setToolTip(
                "file > load client jar to check ids against the base game"
            )
        else:
            self.vanilla_label.setText(f"client.jar {assets.version_id}")
            self.vanilla_label.setToolTip(assets.summary)
            self.output.app(f"vanilla assets: {assets.summary}")
        self._rebuild_emulator()

    def autoload_vanilla(self) -> None:
        """Pick up a client jar for the current version if one is installed."""
        try:
            assets = self.library.load(self.version.id, allow_download=False)
        except Exception as exc:  # a broken jar must not stop the app
            self.output.app(f"cannot read client jar: {exc}", level=LogLevel.ERROR)
            return
        if assets is not None:
            self.use_vanilla(assets)
        elif self.vanilla is not None and self.vanilla.version_id != self.version.id:
            self.output.app(
                f"client jar loaded is {self.vanilla.version_id}, emulating {self.version.id}",
                level=LogLevel.WARNING,
            )

    def load_vanilla_jar(self) -> None:
        start = self.library.cache_dir if self.library.cache_dir.is_dir() else Path.home()
        chosen, _ = QFileDialog.getOpenFileName(
            self, "select a Minecraft client.jar", str(start), "Minecraft client (*.jar)"
        )
        if not chosen:
            return
        try:
            self.use_vanilla(self.library.load_jar(Path(chosen)))
        except Exception as exc:
            QMessageBox.warning(self, "client jar", f"cannot read {chosen}:\n{exc}")

    def download_vanilla_jar(self) -> None:
        """Popup: confirm, then download with a progress bar and a cancel button."""
        version = self.version
        existing = self.library.find(version.id)
        if existing is not None:
            self.statusBar().showMessage(f"{version.id} client jar already installed: {existing}")
            self.use_vanilla(self.library.load_jar(existing))
            return
        dialog = DownloadDialog(self.library, version.id, parent=self)
        path = dialog.run()
        if path is not None:
            self.output.app(f"downloaded {path}")
            self.use_vanilla(self.library.load_jar(path))
            self.statusBar().showMessage(f"client jar ready: {path}")
        elif dialog.error:
            self.output.app(f"client jar download failed: {dialog.error}", level=LogLevel.ERROR)
            self.statusBar().showMessage("client jar download failed")
        else:
            self.statusBar().showMessage("client jar download cancelled")

    def _on_version_changed(self, _text: str) -> None:
        if self.datapack is None:
            return
        self.autoload_vanilla()
        self._rebuild_emulator()
        self.output.app(f"version set to {self.version.id}")
        self.show_datapack(self.datapack)
        if self.call_graph is not None:
            self.run_graphview()

    def reload_datapack(self) -> None:
        if self.datapack is None:
            self.statusBar().showMessage("nothing to reload")
            return
        self.load_datapack(self.datapack.path, keep_project=True, keep_version=True)

    def show_datapack(self, datapack: Datapack | None) -> None:
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
        self.run_profiler()

    def run_all(self) -> None:
        """F5: emulate, refresh the profiler, rebuild the graph."""
        if self.datapack is None:
            self.statusBar().showMessage("load a datapack first")
            return
        self.run_emulator()
        self.run_graphview()
        self.tabs.setCurrentIndex(TAB_PROFILER)
        assert self.emulator is not None
        profiler = self.emulator.profiler
        self.statusBar().showMessage(
            f"run all on {self.version.id}: {len(profiler.tick_times)} tick(s), "
            f"{profiler.total_us / 1000:.2f} ms estimated, "
            f"worst tick {profiler.worst_tick_us / 1000:.2f} ms, "
            f"{len(self.call_graph.nodes) if self.call_graph else 0} graph node(s)"
        )

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
            self.engine_window = EngineWindow(self.datapack, parent=self, library=self.library)
        else:
            self.engine_window.set_datapack(self.datapack)
        self.engine_window.show()
        self.engine_window.raise_()
        self.engine_window.activateWindow()

    # -- explorer / inspector ---------------------------------------------

    def _selected_path(self) -> Path | None:
        indexes = self.tree.selectedIndexes()
        if not indexes:
            return None
        value = indexes[0].data(PATH_ROLE)
        return Path(value) if value else None

    def open_selected_externally(self) -> None:
        self.open_externally(self._selected_path())

    def open_selected_in_file_manager(self) -> None:
        self.open_in_file_manager(self._selected_path())

    # -- opening things ---------------------------------------------------

    def open_externally(self, path: Path | None) -> None:
        """Hand a file or folder to whatever the desktop uses for it."""
        if path is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.statusBar().showMessage(f"no application is registered for {path}")
        else:
            self.output.app(f"opened {path} externally")

    def open_in_file_manager(self, path: Path | None) -> None:
        """A folder opens as itself; a file opens the folder that holds it."""
        if path is None:
            return
        self.open_externally(path if path.is_dir() else path.parent)

    def copy_path(self, path: Path | None) -> None:
        if path is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(str(path))
        self.statusBar().showMessage(f"copied {path}")

    def open_in_source(self, path: Path | None) -> None:
        if path is not None and path.is_file():
            self._show_source(path)

    def function_path(self, function_id: str) -> Path | None:
        """Where a function id lives in the version currently being emulated."""
        if self.datapack is None:
            return None
        view = self.datapack.view_for(self.version)
        function = view.function(function_id.lstrip("#"))
        if function is not None:
            return function.path
        tag = view.function_tags.get(
            function_id if function_id.startswith("#") else f"#{function_id}"
        )
        return tag.path if tag is not None else None

    def open_function(self, function_id: str) -> None:
        """Right-click target from the graph and the profiler."""
        path = self.function_path(function_id)
        if path is None:
            self.statusBar().showMessage(f"{function_id} has no file in {self.version.id}")
            return
        self._show_source(path)

    def _path_menu(self, path: Path | None, title: str = "") -> QMenu:
        """The menu every file and folder gets."""
        menu = QMenu(self)
        if title:
            menu.addAction(title).setEnabled(False)
            menu.addSeparator()
        if path is None:
            menu.addAction("nothing to open").setEnabled(False)
            return menu
        if path.is_file():
            menu.addAction("open in source view", lambda: self.open_in_source(path))
        menu.addAction("open in external editor", lambda: self.open_externally(path))
        menu.addAction("open in external file manager", lambda: self.open_in_file_manager(path))
        menu.addAction("copy path", lambda: self.copy_path(path))
        return menu

    def _explorer_menu(self, point: QPoint) -> None:
        index = self.tree.indexAt(point)
        value = index.data(PATH_ROLE) if index.isValid() else None
        path = Path(value) if value else None
        menu = self._path_menu(path, path.name if path else "")
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _graph_menu(self, node_id: str, global_point: QPoint) -> None:
        path = self.function_path(node_id)
        menu = self._path_menu(path, node_id)
        if path is not None:
            menu.addSeparator()
        menu.addAction("show in inspector", lambda: self._inspect_function(node_id))
        menu.exec(global_point)

    def _inspect_function(self, function_id: str) -> None:
        if self.datapack is None:
            return
        view = self.datapack.view_for(self.version)
        resource = view.function(function_id.lstrip("#")) or view.function_tags.get(
            function_id if function_id.startswith("#") else f"#{function_id}"
        )
        if resource is not None:
            self._fill_inspector(describe_resource(resource, self.version))
            self.dock_inspector.show()

    def _profiler_menu(self, point: QPoint) -> None:
        """Right-click a profiler row: ask the page which function it is."""
        global_point = self.web_view.mapToGlobal(point)
        script = (
            f"(function(){{var e=document.elementFromPoint({point.x()},{point.y()});"
            "while(e&&!e.dataset.function){e=e.parentElement;}"
            "return e?e.dataset.function:'';})()"
        )
        self.web_view.page().runJavaScript(
            script, lambda result: self._show_profiler_menu(result or "", global_point)
        )

    def _show_profiler_menu(self, function_id: str, global_point: QPoint) -> None:
        if not function_id:
            menu = QMenu(self)
            menu.addAction("right-click a row to open its function").setEnabled(False)
            menu.addAction("refresh report", self.run_profiler)
            menu.exec(global_point)
            return
        self._graph_menu(function_id, global_point)

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

    def _find_resource(self, resource_id: str) -> Resource | None:
        if self.datapack is None:
            return None
        for layer in [self.datapack.base, *self.datapack.overlays]:
            for namespace in layer.namespaces.values():
                for resource in namespace.resources():
                    if resource.id == resource_id:
                        return resource
        return None

    def _detach_highlighter(self) -> None:
        """Only one highlighter may colour the source document at a time."""
        if self._highlighter is not None:
            self._highlighter.setDocument(None)
            self._highlighter.setParent(None)
            self._highlighter = None

    def _show_source(self, path: Path) -> None:
        self.source_label.setText(str(path))
        self._detach_highlighter()
        if path.suffix.lower() == ".png":
            self.source_edit.setPlainText(f"{path.name}: binary image")
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
        self.log_counts.setText(self.logs.summary())
