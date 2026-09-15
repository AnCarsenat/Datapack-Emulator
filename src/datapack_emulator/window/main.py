"""Main window: the layout comes from ``window.ui``, the behaviour from controllers.

Every widget is declared in ``window.ui`` and looked up here by object name —
add or move widgets in Qt Designer, not in code. This class owns the shared
state and wires menu actions to the controllers in ``window/controllers/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import shiboken6
from PySide6.QtGui import QAction
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QWidget,
)

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.datapack import Datapack
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.output import OutputBus
from datapack_emulator.emulator.vanilla import VanillaAssets, default_library
from datapack_emulator.project import Project
from datapack_emulator.settings import EMULATION, WINDOW
from datapack_emulator.window.controllers import (
    ConsoleController,
    DatapackController,
    EnvironmentController,
    JarController,
    LogController,
    NavigationController,
    NotesController,
    ProjectController,
    RunController,
    SessionController,
    WorldController,
)
from datapack_emulator.window.controllers.base import (
    TAB_ENVIRONMENT,
    TAB_GRAPH,
    TAB_PROFILER,
    TAB_SOURCE,
)
from datapack_emulator.window.engine_window import EngineWindow
from datapack_emulator.window.panels import FunctionGraphWidget, load_ui_into

log = logging.getLogger(__name__)

UI_FILE = Path(__file__).with_name("window.ui")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(parent=None)
        # shared state, read and written by the controllers
        self.datapack: Datapack | None = None
        self.emulator: Emulator | None = None
        self.call_graph: CallGraph | None = None
        self.output = OutputBus()
        self.library = default_library()
        self.vanilla: VanillaAssets | None = None
        self.engine_window: EngineWindow | None = None
        self.project = Project()

        load_ui_into(self, UI_FILE, custom=[QWebEngineView])
        self._bind_widgets()
        self._fill_versions()

        self.log_view = LogController(self)
        self.projects = ProjectController(self)
        self.jars = JarController(self)
        self.datapacks = DatapackController(self)
        self.runs = RunController(self)
        self.navigation = NavigationController(self)
        self.environment = EnvironmentController(self)
        self.world_view = WorldController(self)
        self.console = ConsoleController(self)
        self.notes = NotesController(self)
        self.session = SessionController(self)
        for controller in (
            self.projects,
            self.log_view,
            self.datapacks,
            self.runs,
            self.navigation,
            self.environment,
            self.world_view,
            self.console,
            self.notes,
            self.session,
        ):
            controller.connect()
        self._wire_actions()

        self.resize(WINDOW.WINDOW_WIDTH, WINDOW.WINDOW_HEIGHT)
        self.runs._set_running(False)
        self.show_all_docks()
        self.datapacks.show(None)
        self.session.restore()
        self.datapacks.open_default()
        self.projects.mark_saved()  # the default pack is not a change to save

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.projects.confirm_close():
            self.runs.stop(refresh=False)
            self.session.save()
            event.accept()
        else:
            event.ignore()

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
        self.version_note: QLabel = find(QLabel, "labelVersionNote")
        self.combo_level: QComboBox = find(QComboBox, "comboLevel")
        self.edit_filter: QLineEdit = find(QLineEdit, "editLogFilter")
        self.spin_ticks: QSpinBox = find(QSpinBox, "spinTicks")
        self.spin_players: QSpinBox = find(QSpinBox, "spinPlayers")
        self.spin_seed: QSpinBox = find(QSpinBox, "spinSeed")
        self.combo_speed: QComboBox = find(QComboBox, "comboSpeed")
        self.check_tests_during_runs: QCheckBox = find(QCheckBox, "checkTestsDuringRuns")
        self.tick_label: QLabel = find(QLabel, "labelTickStatus")
        self.edit_project_notes: QPlainTextEdit = find(QPlainTextEdit, "editProjectNotes")
        self.table_tests: QTableWidget = find(QTableWidget, "tableTests")
        self.add_test_button: QPushButton = find(QPushButton, "buttonAddTest")
        self.remove_test_button: QPushButton = find(QPushButton, "buttonRemoveTest")
        self.duplicate_test_button: QPushButton = find(QPushButton, "buttonDuplicateTest")
        self.move_test_up_button: QPushButton = find(QPushButton, "buttonMoveTestUp")
        self.move_test_down_button: QPushButton = find(QPushButton, "buttonMoveTestDown")
        self.run_selected_test_button: QPushButton = find(QPushButton, "buttonRunSelectedTest")
        self.console_add_test_button: QPushButton = find(QPushButton, "buttonConsoleAddTest")
        self.run_tests_button: QPushButton = find(QPushButton, "buttonRunTests")
        self.test_summary: QLabel = find(QLabel, "labelTestSummary")
        self.step_button: QPushButton = find(QPushButton, "buttonStep")
        self.stop_button: QPushButton = find(QPushButton, "buttonStop")
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
        self.dock_world: QDockWidget = find(QDockWidget, "dockWidgetWorld")
        self.recent_projects_menu: QMenu = find(QMenu, "menurecent_projects")
        self.recent_datapacks_menu: QMenu = find(QMenu, "menurecent_datapacks")
        self.world_label: QLabel = find(QLabel, "labelWorld")
        self.edit_world_filter: QLineEdit = find(QLineEdit, "editWorldFilter")
        self.edit_objective_filter: QLineEdit = find(QLineEdit, "editObjectiveFilter")
        self.world_summon_button: QPushButton = find(QPushButton, "buttonWorldSummon")
        self.world_objective_button: QPushButton = find(QPushButton, "buttonWorldObjective")
        self.env_summon_button: QPushButton = find(QPushButton, "buttonEnvSummon")
        self.env_score_button: QPushButton = find(QPushButton, "buttonEnvScore")
        self.env_world_button: QPushButton = find(QPushButton, "buttonEnvWorld")
        self.tabs_world: QTabWidget = find(QTabWidget, "tabsWorld")
        self.table_scores: QTableWidget = find(QTableWidget, "tableScores")
        self.tree_entities: QTreeWidget = find(QTreeWidget, "treeEntities")
        self.tree_storage: QTreeWidget = find(QTreeWidget, "treeStorage")
        self.edit_console: QLineEdit = find(QLineEdit, "editConsole")
        self.console_run_button: QPushButton = find(QPushButton, "buttonConsoleRun")

        # the only widget the .ui cannot describe: the pyqtgraph canvas
        container: QWidget = find(QWidget, "graphContainer")
        self.graph_widget = FunctionGraphWidget(container)
        container.layout().addWidget(self.graph_widget)

        self.spin_ticks.setValue(EMULATION.DEFAULT_TICKS)
        self.spin_players.setValue(EMULATION.DEFAULT_PLAYERS)
        self.spin_seed.setValue(EMULATION.DEFAULT_SEED)

    def _fill_versions(self) -> None:
        self.combo_version.blockSignals(True)
        self.combo_version.clear()
        for version in reversed(versions.VERSIONS):
            label = f"{version.id}  ({version.format_string})"
            if not version.stable:
                label += "  pre-release"
            self.combo_version.addItem(label, version.id)
        self.combo_version.setCurrentIndex(max(self.combo_version.findData(versions.LATEST.id), 0))
        self.combo_version.blockSignals(False)

    def tab_page(self, name: str) -> QWidget:
        """A tab page of window.ui by object name (see controllers.base)."""
        return self.findChild(QWidget, name)

    @property
    def version(self) -> versions.Version:
        return versions.parse(self.combo_version.currentData() or versions.LATEST)

    def _action(self, name: str) -> QAction | None:
        return self.findChild(QAction, name)

    def _wire_actions(self) -> None:
        navigation = self.navigation
        connections = {
            "actionimport_datapack": self.datapacks.import_,
            "actionreload_datapack": self.datapacks.reload,
            "actionnew_project": self.projects.new,
            "actionopen_project": self.projects.open,
            "actionsave_project": self.projects.save,
            "actionsave_project_as": self.projects.save_as,
            "actionload_vanilla": self.jars.load_by_hand,
            "actiondownload_vanilla": self.jars.download,
            "actionquit": self.close,
            "actionrun_all": self.runs.run_all,
            "actionrun_emulator": self.runs.run_emulator,
            "actionstep_tick": self.runs.step,
            "actionstop": lambda: self.runs.stop(),
            "actionrun_tests": self.environment.run,
            "actionenvironment": lambda: self.tabs.setCurrentWidget(self.tab_page(TAB_ENVIRONMENT)),
            "actionrun_profiler": self.runs.run_profiler,
            "actionrun_graphview": self.runs.run_graphview,
            "actionopen_engine": self.runs.open_engine,
            "actionexport_dot": self.runs.export_dot,
            "actionprofile": lambda: self.tabs.setCurrentWidget(self.tab_page(TAB_PROFILER)),
            "actiongraphview": lambda: self.tabs.setCurrentWidget(self.tab_page(TAB_GRAPH)),
            "actionsource": lambda: self.tabs.setCurrentWidget(self.tab_page(TAB_SOURCE)),
            "actionopen_file_in_editor": lambda: navigation.open_externally(
                navigation.selected_path()
            ),
            "actionopen_folder_in_explorer": lambda: navigation.open_in_file_manager(
                navigation.selected_path()
            ),
            "actionopen_in_source": lambda: navigation.open_in_source(navigation.selected_path()),
            "actioncopy_path": lambda: navigation.copy_path(navigation.selected_path()),
            "actionanalyze_line": navigation.analyze_cursor_line,
            "actionquick_open": lambda: navigation.search(0),
            "actionreset_layout": lambda: self.session.reset_layout(),
            "actionfocus_console": lambda: self.session.focus_console(),
            "actionadd_console_test": lambda: self.console.add_as_test(),
            "actionsearch_pack": lambda: navigation.search(1),
        }
        for name, slot in connections.items():
            action = self._action(name)
            if action is not None:
                action.triggered.connect(slot)

        for name, dock in (
            ("actionexplorer", self.dock_explorer),
            ("actioninspector", self.dock_inspector),
            ("actionlog", self.dock_logs),
            ("actionworld", self.dock_world),
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

    # -- docks ------------------------------------------------------------

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
        return (self.dock_explorer, self.dock_inspector, self.dock_logs, self.dock_world)

    def show_all_docks(self) -> None:
        """Every panel open and docked — the default layout."""
        for dock in self.docks:
            dock.setFloating(False)
            dock.show()
        for name in ("actionexplorer", "actioninspector", "actionlog", "actionworld"):
            action = self._action(name)
            if action is not None:
                action.setChecked(True)
