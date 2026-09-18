"""The problems dock: static diagnostics of the pack for the emulated version
(``analysis/problems.py``; ``datapack-emulator-cli check`` prints the same)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from datapack_emulator.emulator.analysis.problems import SEVERITIES, Problem, count, find_problems
from datapack_emulator.emulator.analysis.schema import Schema, schema_for
from datapack_emulator.settings.main import PATHS
from datapack_emulator.window.controllers.base import Controller

log = logging.getLogger(__name__)

PROBLEM_ROLE = Qt.UserRole + 1
COLOURS = {
    "error": QColor("#c0392b"),
    "warning": QColor("#b9770e"),
    "info": QColor("#7f8c8d"),
}


class ProblemsController(Controller):
    def __init__(self, window):
        super().__init__(window)
        find = window.findChild
        self.tree: QTreeWidget = find(QTreeWidget, "treeProblems")
        self.label: QLabel = find(QLabel, "labelProblems")
        self.combo_severity: QComboBox = find(QComboBox, "comboProblemsSeverity")
        self.edit_filter: QLineEdit = find(QLineEdit, "editProblemsFilter")
        self.refresh_button: QPushButton = find(QPushButton, "buttonProblemsRefresh")
        #: every problem of the last check, before filtering
        self.problems: list[Problem] = []
        #: the pack, version or jar changed since the last check
        self.stale = False
        #: the jar the fields were learned from (path, when, how big), and
        #: what was learned from it
        self._schema_jar: tuple[str, int, int] | None = None
        self._schema: Schema | None = None
        self._refresh_timer = QTimer(window)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(lambda: self.refresh())
        self._filter_timer = QTimer(window)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self.fill)

    def connect(self) -> None:
        window = self.window
        self.refresh_button.clicked.connect(lambda: self.refresh(show=True))
        action = window._action("actioncheck_pack")
        if action is not None:
            action.triggered.connect(lambda: self.refresh(show=True))
        self.combo_severity.currentIndexChanged.connect(lambda _index: self.fill())
        self.edit_filter.textChanged.connect(lambda _text: self._filter_timer.start())
        window.dock_problems.visibilityChanged.connect(self._on_visibility)
        fields = window._action("actioncheck_fields")
        if fields is not None:
            fields.triggered.connect(lambda _checked: self.refresh())
        self.tree.itemDoubleClicked.connect(lambda item, _column: self.open(item))
        self.tree.customContextMenuRequested.connect(self.menu)

    # -- checking ------------------------------------------------------------

    def schedule_refresh(self) -> None:
        """The pack, version or jar changed: check again once the current
        action is over (several changes make one check), or when the dock
        is next shown."""
        self.stale = True
        if not self.window.dock_problems.isHidden():
            self._refresh_timer.start(0)

    def _on_visibility(self, visible: bool) -> None:
        if visible and self.stale:
            self._refresh_timer.start(0)

    def refresh(self, show: bool = False) -> None:
        """Check the pack again for the emulated version."""
        window = self.window
        self.stale = False
        self._refresh_timer.stop()
        if window.datapack is None:
            self.problems = []
            self.label.setText("no pack loaded")
            self.fill()
            return
        try:
            self.problems = find_problems(
                window.datapack, window.version, window.vanilla, self.schema()
            )
        except Exception as exc:  # a bug in a check must not break loading
            log.exception("checking the pack failed")
            self.problems = []
            self.label.setText(f"the check failed: {type(exc).__name__}: {exc}")
            self.fill()
            return
        totals = count(self.problems)
        jar = "" if window.vanilla else " (no client jar: ids are not checked)"
        self.label.setText(
            f"{window.version.id}: {totals['error']} error(s), {totals['warning']} "
            f"warning(s), {totals['info']} note(s){jar}"
        )
        self.fill()
        if show:
            window.dock_problems.show()
            window.dock_problems.raise_()
            self.status(self.label.text())

    def schema(self) -> Schema | None:
        """The fields of the loaded jar's own files, learned once per jar
        (reading a jar's data folder takes a moment; it is kept in the cache).
        ``datapack-emulator-cli schema`` prints the same thing, and its
        ``check --no-schema`` is this dock's *check JSON fields* entry."""
        window = self.window
        if not self.checks_fields():
            return None
        jar = getattr(window.vanilla, "jar_path", None)
        if jar is None:
            return None
        try:  # a jar written again in place is another jar
            stat = jar.stat()
            key = (str(jar), stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = (str(jar), 0, 0)
        if self._schema_jar != key:
            self._schema_jar = key
            try:
                self._schema = schema_for(window.vanilla, PATHS.CACHE)
            except Exception:  # a schema is a nicety: never break the check
                log.exception("cannot read the fields of %s", jar)
                self._schema = None
        return self._schema

    def checks_fields(self) -> bool:
        """Whether *run › check JSON fields against the client jar* is on."""
        action = self.window._action("actioncheck_fields")
        return action is None or action.isChecked()

    def visible(self) -> list[Problem]:
        lowest = SEVERITIES.index(self.combo_severity.currentText() or "info")
        text = self.edit_filter.text().strip().lower()
        return [
            problem
            for problem in self.problems
            if SEVERITIES.index(problem.severity) <= lowest
            and (
                not text
                or text in problem.where.lower()
                or text in problem.message.lower()
                or text in problem.code
            )
        ]

    def fill(self) -> None:
        self.tree.clear()
        for problem in self.visible():
            item = QTreeWidgetItem([problem.severity, problem.where, problem.message, problem.code])
            item.setForeground(0, QBrush(COLOURS[problem.severity]))
            item.setToolTip(2, problem.message)
            if problem.path is not None:
                item.setToolTip(1, str(problem.path))
            item.setData(0, PROBLEM_ROLE, problem)
            self.tree.addTopLevelItem(item)
        for column in (0, 1):
            self.tree.resizeColumnToContents(column)

    # -- acting ----------------------------------------------------------------

    def open(self, item: QTreeWidgetItem) -> None:
        problem: Problem | None = item.data(0, PROBLEM_ROLE)
        if problem is None:
            return
        if problem.path is None:
            self.status(f"{problem.where or 'this problem'} has no file to open")
            return
        self.window.navigation.show_source(problem.path, problem.line)

    def menu(self, point: QPoint) -> None:
        window = self.window
        item = self.tree.itemAt(point)
        menu = QMenu(window)
        problem: Problem | None = item.data(0, PROBLEM_ROLE) if item is not None else None
        if problem is not None:
            opened = menu.addAction("open in source view", lambda: self.open(item))
            opened.setEnabled(problem.path is not None)
            text = (
                window.navigation.line_text(problem.resource, problem.line)
                if problem.line and not problem.resource.startswith("#")
                else None
            )
            if text is not None:
                menu.addAction(
                    "analyze the line",
                    lambda: window.navigation.analyze(
                        text, problem.where, function_id=problem.resource
                    ),
                )
            menu.addAction(
                "copy", lambda: window.navigation.copy_text(problem.format(), "the problem")
            )
            menu.addSeparator()
        menu.addAction("check again", lambda: self.refresh(show=True))
        menu.exec(self.tree.viewport().mapToGlobal(point))
