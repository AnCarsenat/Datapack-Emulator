"""The function debugger in the window: breakpoints in the source view's
gutter, the debugger dock (call stack, context, watches, breakpoints) and the
stop itself.

A stop happens inside ``Emulator.run_tick`` (or a typed command, or a test),
deep in the call stack. The window keeps working meanwhile by running a nested
event loop from the debugger's pause handler; the buttons end that loop with
an answer. Everything stays on the UI thread, so the docks can read the world
while it is stopped. Actions that would run the emulator again are disabled
until the answer is given, and replacing the world answers ``stop`` first.
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from datapack_emulator.emulator.runtime.debugger import (
    Action,
    Debugger,
    DebugStopped,
    Pause,
    command_line_at,
    condition_problem,
    split_breakpoint,
    watch_value,
)
from datapack_emulator.window.controllers.base import Controller

LOCATION_ROLE = Qt.UserRole + 1

#: buttons and actions that run the emulator: off while stopped
RUNNING_BUTTONS = (
    "buttonRun",
    "buttonRunAll",
    "buttonStep",
    "buttonRunTests",
    "buttonRunSelectedTest",
    "buttonEngine",
)
RUNNING_ACTIONS = (
    "actionrun_all",
    "actionrun_emulator",
    "actionstep_tick",
    "actionrun_tests",
    "actionrun_profiler",
    "actionopen_engine",
)
ANSWERS = {
    "buttonDebugContinue": Action.CONTINUE,
    "buttonDebugStepInto": Action.STEP_INTO,
    "buttonDebugStepOver": Action.STEP_OVER,
    "buttonDebugStepOut": Action.STEP_OUT,
    "buttonDebugStop": Action.STOP,
}
ACTION_ANSWERS = {
    "actiondebug_continue": Action.CONTINUE,
    "actiondebug_step_into": Action.STEP_INTO,
    "actiondebug_step_over": Action.STEP_OVER,
    "actiondebug_step_out": Action.STEP_OUT,
}


class DebugController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self.debugger = Debugger(self.on_pause)
        #: the stop being answered: its nested loop and its answer
        self._loop: QEventLoop | None = None
        self._answers: dict[int, Action] = {}
        self._filling = False
        #: breakpoint problems already reported (see check_breakpoints)
        self._reported: set[tuple] = set()
        find = window.findChild
        self.status_label: QLabel = find(QLabel, "labelDebugStatus")
        self.tabs: QTabWidget = find(QTabWidget, "tabsDebug")
        self.tree_stack: QTreeWidget = find(QTreeWidget, "treeDebugStack")
        self.tree_context: QTreeWidget = find(QTreeWidget, "treeDebugContext")
        self.tree_watches: QTreeWidget = find(QTreeWidget, "treeDebugWatches")
        self.tree_breakpoints: QTreeWidget = find(QTreeWidget, "treeDebugBreakpoints")
        self.edit_watch: QLineEdit = find(QLineEdit, "editDebugWatch")
        self.buttons = {name: find(QPushButton, name) for name in ANSWERS}
        self.pause_button: QPushButton = find(QPushButton, "buttonDebugPause")

    def connect(self) -> None:
        window = self.window
        find = window.findChild
        for name, action in ANSWERS.items():
            self.buttons[name].clicked.connect(lambda _=False, action=action: self.answer(action))
        for name, action in ACTION_ANSWERS.items():
            qaction = window._action(name)
            if qaction is not None:
                qaction.triggered.connect(lambda _=False, action=action: self.answer(action))
        self.pause_button.clicked.connect(self.pause)
        for name, slot in (
            ("actiondebug_pause", self.pause),
            ("actiontoggle_breakpoint", self.toggle_at_cursor),
            ("actionclear_breakpoints", self.clear_breakpoints),
        ):
            qaction = window._action(name)
            if qaction is not None:
                qaction.triggered.connect(slot)
        window.source_edit.gutter_clicked.connect(self.toggle_source_line)
        find(QPushButton, "buttonDebugAddWatch").clicked.connect(self.add_watch)
        self.edit_watch.returnPressed.connect(self.add_watch)
        find(QPushButton, "buttonDebugRemoveWatch").clicked.connect(self.remove_watches)
        find(QPushButton, "buttonDebugRemoveBreakpoint").clicked.connect(
            self.remove_selected_breakpoints
        )
        find(QPushButton, "buttonDebugClearBreakpoints").clicked.connect(self.clear_breakpoints)
        for tree in (self.tree_watches, self.tree_breakpoints):
            tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_watches.itemDoubleClicked.connect(
            lambda item, _column: self.tree_watches.editItem(item, 0)
        )
        self.tree_watches.itemChanged.connect(self._watch_edited)
        self.tree_breakpoints.itemDoubleClicked.connect(self._breakpoint_double_clicked)
        self.tree_breakpoints.itemChanged.connect(self._breakpoint_edited)
        self.tree_stack.itemDoubleClicked.connect(self._open_item)
        self._set_paused(False)
        self.fill_breakpoints()

    # -- the emulator ------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._loop is not None

    def current(self) -> Pause | None:
        """The stop being shown (None once it was answered)."""
        return self.debugger.paused if self._loop is not None else None

    def attach(self, emulator) -> None:
        """A new world: it asks the same debugger (breakpoints are kept)."""
        if self.paused:
            self.answer(Action.STOP)
        self.debugger.reset()
        emulator.debugger = self.debugger
        self.check_breakpoints(quiet_if_seen=True)

    def on_pause(self, pause: Pause) -> Action:
        window = self.window
        if self._loop is not None:
            # only reachable through a path that runs the world while stopped;
            # never stack a second stop on the first
            return Action.CONTINUE
        runs = window.runs
        resume = runs.timer.isActive()
        runs.timer.stop()  # the nested loop must not tick the world again
        loop = self._loop = QEventLoop()
        self._answers[id(loop)] = Action.CONTINUE
        try:
            self._set_paused(True)
            self.show_pause(pause)
            window.log_view.flush()
            runs.show_tick()
            window.world_view.refresh()
            loop.exec()
        finally:
            answer = self._answers.pop(id(loop), Action.STOP)
            self._loop = None
            self._set_paused(False)
            self.show_pause(None)
        if resume and runs.running and answer is not Action.STOP:
            runs.timer.start(runs.interval_ms)
        return answer

    def answer(self, action: Action) -> None:
        loop = self._loop
        if loop is None:
            self.status("the debugger is not stopped")
            return
        self._answers[id(loop)] = action
        loop.quit()

    def busy(self) -> bool:
        """Stopped: say that the world has to go on first (for actions that
        would run it again)."""
        if self.paused:
            self.status("stopped in the debugger: continue or stop first")
            return True
        return False

    def pause(self) -> None:
        if self.paused:
            return
        if not self.window.runs.running:
            self.status("nothing is running: set a breakpoint, or step (F7)")
            return
        self.debugger.request_pause()
        self.status("pausing at the next function line…")

    def stopped(self, stop: DebugStopped) -> None:
        """A tick was abandoned: say so (the world keeps what already ran)."""
        self.debugger.reset()
        self.window.output.app(f"debugger: stopped at {stop}; the rest of that tick did not run")
        self.status("stopped from the debugger")

    def run_here(self, line: str):
        """A console command while stopped: it runs as the stopped line would."""
        from datapack_emulator.emulator.commands.parser import Command

        paused = self.current()
        command = Command.parse(line, source="<console>")
        if paused is None or command is None:
            return None
        with self.debugger.aside():
            result = paused.context.emulator.run_command(command, paused.context.branch(depth=0))
        self.refresh_values()
        return result

    def _set_paused(self, paused: bool) -> None:
        window = self.window
        find = window.findChild
        for button in self.buttons.values():
            button.setEnabled(paused)
        self.pause_button.setEnabled(not paused)
        for name, _ in ACTION_ANSWERS.items():
            action = window._action(name)
            if action is not None:
                action.setEnabled(paused)
        for name in RUNNING_BUTTONS:
            button = find(QPushButton, name)
            if button is not None and paused:
                button.setEnabled(False)
        for name in RUNNING_ACTIONS:
            action = window._action(name)
            if action is not None and paused:
                action.setEnabled(False)
        if paused:  # the toolbar's stop abandons the stopped tick too
            window.stop_button.setEnabled(True)
            stop = window._action("actionstop")
            if stop is not None:
                stop.setEnabled(True)
        if not paused:
            window.runs._set_running(window.runs.running)
            for name in ("buttonRunTests", "buttonRunSelectedTest", "buttonEngine"):
                button = find(QPushButton, name)
                if button is not None:
                    button.setEnabled(True)
            for name in ("actionrun_tests", "actionrun_profiler", "actionopen_engine"):
                action = window._action(name)
                if action is not None:
                    action.setEnabled(True)
        window.combo_version.setEnabled(not paused)

    # -- the dock ----------------------------------------------------------

    def show_pause(self, pause: Pause | None) -> None:
        window = self.window
        self.tree_stack.clear()
        self.tree_context.clear()
        if pause is None:
            self.status_label.setText("not stopped — F9 in the source view sets a breakpoint")
            window.source_edit.set_stopped_line(0)
            self.refresh_values()
            self.fill_breakpoints()
            return
        why = f"breakpoint {pause.breakpoint}" if pause.breakpoint else pause.reason
        self.status_label.setText(
            f"stopped at {pause.function_id}:{pause.line} ({why}), tick {pause.tick}\n"
            f"{pause.command.raw}"
        )
        for frame in reversed(pause.stack):
            executor = frame.context.executor
            item = QTreeWidgetItem(
                [frame.function_id, str(frame.line), executor.display if executor else "server"]
            )
            item.setData(0, LOCATION_ROLE, (frame.function_id, frame.line))
            self.tree_stack.addTopLevelItem(item)
        context = pause.context
        for name, value in (
            ("function", f"{pause.function_id}:{pause.line}"),
            ("command", pause.command.raw),
            ("tick", str(pause.tick)),
            ("executor", watch_value("executor", context)),
            ("position", watch_value("position", context)),
            ("rotation", watch_value("rotation", context)),
            ("dimension", context.dimension),
            ("depth", str(len(pause.stack))),
        ):
            self.tree_context.addTopLevelItem(QTreeWidgetItem([name, value]))
        for tree in (self.tree_stack, self.tree_context):
            tree.resizeColumnToContents(0)
        window.dock_debug.show()
        window.dock_debug.raise_()
        window.navigation.open_function(pause.function_id, pause.line)
        window.source_edit.set_stopped_line(pause.line)
        self.refresh_values()
        self.fill_breakpoints()
        self.status(f"stopped at {pause.function_id}:{pause.line}")

    def _context(self):
        paused = self.current()
        if paused is not None:
            return paused.context
        emulator = self.window.emulator
        return emulator.root_context() if emulator is not None else None

    def refresh_values(self) -> None:
        """Re-evaluate the watches where the world is (or stopped)."""
        context = self._context()
        self._filling = True
        try:
            self.tree_watches.clear()
            for text in self.debugger.watches:
                value = watch_value(text, context, self.debugger) if context else "no world yet"
                item = QTreeWidgetItem([text, value])
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                self.tree_watches.addTopLevelItem(item)
            self.tree_watches.resizeColumnToContents(0)
        finally:
            self._filling = False

    def add_watch(self) -> None:
        text = self.edit_watch.text().strip()
        if not text:
            self.status("type a watch expression first")
            return
        if text.split(None, 1)[0] in ("if", "unless") and condition_problem(text):
            self.status(condition_problem(text))
            return
        self.debugger.watches.append(text)
        self.edit_watch.clear()
        self.refresh_values()
        self._changed()

    def remove_watches(self) -> None:
        rows = sorted(
            {
                self.tree_watches.indexOfTopLevelItem(item)
                for item in self.tree_watches.selectedItems()
            },
            reverse=True,
        )
        for row in rows:
            del self.debugger.watches[row]
        if rows:
            self.refresh_values()
            self._changed()

    def _watch_edited(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        row = self.tree_watches.indexOfTopLevelItem(item)
        text = item.text(0).strip()
        if text and text.split(None, 1)[0] in ("if", "unless") and condition_problem(text):
            self.status(condition_problem(text))
        elif text:
            self.debugger.watches[row] = text
        else:
            del self.debugger.watches[row]
        # never rebuild a view from inside its own itemChanged
        QTimer.singleShot(0, self.refresh_values)
        self._changed()

    # -- breakpoints -------------------------------------------------------

    def fill_breakpoints(self) -> None:
        self._filling = True
        try:
            self.tree_breakpoints.clear()
            for point in sorted(self.debugger.breakpoints.values(), key=lambda p: p.key):
                item = QTreeWidgetItem(
                    [f"{point.function_id}:{point.line}", point.condition, str(point.hits)]
                )
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
                item.setCheckState(0, Qt.Checked if point.enabled else Qt.Unchecked)
                item.setData(0, LOCATION_ROLE, point.key)
                self.tree_breakpoints.addTopLevelItem(item)
            self.tree_breakpoints.resizeColumnToContents(0)
        finally:
            self._filling = False
        self.refresh_gutter()

    def refresh_gutter(self) -> None:
        window = self.window
        function_id = window.navigation.function_at(window.navigation.source_path)
        lines = (
            {
                point.line: point.enabled
                for point in self.debugger.breakpoints.values()
                if point.function_id == function_id
            }
            if function_id
            else {}
        )
        window.source_edit.set_breakpoints(lines)
        paused = self.current()
        stopped = paused.line if paused is not None and paused.function_id == function_id else 0
        window.source_edit.set_stopped_line(stopped)

    def toggle_at_cursor(self) -> None:
        _, line = self.window.navigation.cursor_line()
        self.toggle_source_line(line)

    def toggle_source_line(self, line: int) -> None:
        """Set or remove a breakpoint on a line of the function in the source view."""
        window = self.window
        navigation = window.navigation
        function_id = navigation.function_at(navigation.source_path)
        if function_id is None:
            self.status("open a function in the source view to set a breakpoint")
            return
        if line in self.debugger.lines_of(function_id):
            self.debugger.remove(function_id, line)
            self.status(f"removed the breakpoint at {function_id}:{line}")
        else:
            function = window.datapack.view_for(window.version).function(function_id)
            stop_line = command_line_at(function, line) if function is not None else None
            if stop_line is None:
                self.status(f"{function_id} has no command on or after line {line}")
                return
            if stop_line in self.debugger.lines_of(function_id):
                self.debugger.remove(function_id, stop_line)
                self.status(f"removed the breakpoint at {function_id}:{stop_line}")
            else:
                self.debugger.add(function_id, stop_line)
                self.status(f"breakpoint at {function_id}:{stop_line}")
        self.fill_breakpoints()
        self._changed()

    def remove_selected_breakpoints(self) -> None:
        items = self.tree_breakpoints.selectedItems()
        for item in items:
            function_id, line = item.data(0, LOCATION_ROLE)
            self.debugger.remove(function_id, line)
        if items:
            self.fill_breakpoints()
            self._changed()

    def clear_breakpoints(self) -> None:
        if not self.debugger.breakpoints:
            return
        self.debugger.clear()
        self.fill_breakpoints()
        self._changed()

    def _breakpoint_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 1:
            self.tree_breakpoints.editItem(item, 1)
        else:
            self._open_item(item, column)

    def _breakpoint_edited(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling:
            return
        point = self.debugger.breakpoints.get(tuple(item.data(0, LOCATION_ROLE)))
        if point is None:
            return
        if column == 0:
            point.enabled = item.checkState(0) == Qt.Checked
        elif column == 1:
            _, condition = split_breakpoint("_ " + item.text(1))
            problem = condition_problem(condition) if condition else ""
            if problem:
                self.status(problem)
            else:
                point.condition = condition
        # never rebuild a view from inside its own itemChanged
        QTimer.singleShot(0, self.fill_breakpoints)
        self._changed()

    def _open_item(self, item: QTreeWidgetItem, _column: int) -> None:
        location = item.data(0, LOCATION_ROLE)
        if location:
            function_id, line = location
            self.window.navigation.open_function(function_id, line)

    # -- the project -------------------------------------------------------

    def load(self, breakpoints: list[str], watches: list[str]) -> None:
        from datapack_emulator.emulator.runtime.output import LogLevel

        output = self.window.output
        for entry in self.debugger.load_strings(breakpoints):
            output.app(f"cannot read the project's breakpoint {entry!r}", level=LogLevel.WARNING)
        self.debugger.watches = list(watches)
        self.check_breakpoints()
        self.fill_breakpoints()
        self.refresh_values()

    def check_breakpoints(self, quiet_if_seen: bool = False) -> None:
        """Move breakpoints to command lines of the emulated version; say
        which ones it does not have (once per pack, version and problem when
        ``quiet_if_seen``)."""
        from datapack_emulator.emulator.runtime.output import LogLevel

        window = self.window
        if window.datapack is None:
            return
        view = window.datapack.view_for(window.version)
        for problem in self.debugger.resolve(view.function):
            key = (id(window.datapack), window.version.id, problem)
            if quiet_if_seen and key in self._reported:
                continue
            self._reported.add(key)
            window.output.app(problem, level=LogLevel.WARNING)
        self.fill_breakpoints()

    def capture(self, project) -> None:
        project.breakpoints = self.debugger.to_strings()
        project.watches = list(self.debugger.watches)

    def _changed(self) -> None:
        self.window.projects.mark_modified()
