"""The environment tab: world settings and command tests."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QHeaderView,
    QInputDialog,
    QMenu,
    QTableWidgetItem,
    QWidget,
)

from datapack_emulator.emulator.runtime.debugger import DebugStopped
from datapack_emulator.emulator.testing import (
    CHECK_HELP,
    CommandTest,
    TestResult,
    run_tests,
    valid_check,
)
from datapack_emulator.window.controllers.base import TAB_ENVIRONMENT, Controller

COLUMN_TICK, COLUMN_COMMAND, COLUMN_EXPECT, COLUMN_VALUE, COLUMN_CHECKS, COLUMN_RESULT = range(6)
EDITABLE_COLUMNS = (COLUMN_TICK, COLUMN_COMMAND, COLUMN_EXPECT, COLUMN_VALUE)
FITTED_COLUMNS = (*EDITABLE_COLUMNS, COLUMN_CHECKS)
CHECKS_ROLE = Qt.UserRole + 1
PASS_COLOUR = QColor("#1e6f3d")
FAIL_COLOUR = QColor("#c0392b")


class EnvironmentController(Controller):
    def __init__(self, window):
        super().__init__(window)
        #: the last result of each row, to reveal its records
        self.results: dict[int, TestResult] = {}
        #: the table is being filled from code (not edited by hand)
        self._filling = False

    def connect(self) -> None:
        window = self.window
        table = window.table_tests
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(COLUMN_COMMAND, QHeaderView.Interactive)
        table.verticalHeader().setVisible(False)
        table.customContextMenuRequested.connect(self.test_menu)
        table.cellDoubleClicked.connect(self._on_double_click)
        table.itemChanged.connect(self._on_item_changed)
        window.add_test_button.clicked.connect(lambda: self.add_test())
        window.remove_test_button.clicked.connect(self.remove_selected)
        window.duplicate_test_button.clicked.connect(self.duplicate_selected)
        window.move_test_up_button.clicked.connect(lambda: self.move_selected(-1))
        window.move_test_down_button.clicked.connect(lambda: self.move_selected(1))
        window.run_selected_test_button.clicked.connect(lambda: self.run(self.selected_rows()))
        # clicked(bool) would pass `checked` as the rows to run
        window.run_tests_button.clicked.connect(lambda: self.run())

    # -- the table ----------------------------------------------------------

    def add_test(self, test: CommandTest | None = None, row: int | None = None) -> int:
        self._filling = True
        try:
            return self._add_test(test, row)
        finally:
            self._filling = False

    def _add_test(self, test: CommandTest | None, row: int | None) -> int:
        test = test or CommandTest("say hello")
        table = self.window.table_tests
        row = table.rowCount() if row is None else row
        table.insertRow(row)
        tick = QTableWidgetItem(str(test.at_tick))
        tick.setFlags(tick.flags() | Qt.ItemIsUserCheckable)
        tick.setCheckState(Qt.Checked if test.enabled else Qt.Unchecked)
        tick.setToolTip("uncheck to skip this test")
        table.setItem(row, COLUMN_TICK, tick)
        table.setItem(row, COLUMN_COMMAND, QTableWidgetItem(test.command))
        table.setItem(row, COLUMN_EXPECT, QTableWidgetItem(test.expect))
        value = QTableWidgetItem(test.expect_value)
        value.setToolTip("the command's result must be in this range: 5, 1.., ..3 or 1..4")
        table.setItem(row, COLUMN_VALUE, value)
        checks = QTableWidgetItem()
        checks.setFlags(checks.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, COLUMN_CHECKS, checks)
        self._show_checks(row, test.checks)
        result = QTableWidgetItem("")
        result.setFlags(result.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, COLUMN_RESULT, result)
        self._fit_columns()
        return row

    def _fit_columns(self) -> None:
        table = self.window.table_tests
        for column in FITTED_COLUMNS:
            table.resizeColumnToContents(column)
            table.setColumnWidth(column, min(max(table.columnWidth(column), 80), 360))

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.window.table_tests.selectedIndexes()})

    def remove_selected(self) -> None:
        table = self.window.table_tests
        for row in reversed(self.selected_rows()):
            table.removeRow(row)
        self.results = {}

    def duplicate_selected(self) -> None:
        tests = self.tests()
        rows = self.selected_rows()
        for offset, row in enumerate(rows):
            self.add_test(replace(tests[row]), row=row + offset + 1)
        self.results = {}

    def move_selected(self, step: int) -> None:
        rows = self.selected_rows()
        tests = self.tests()
        if len(rows) != 1 or not 0 <= rows[0] + step < len(tests):
            return
        row = rows[0]
        tests[row], tests[row + step] = tests[row + step], tests[row]
        self.set_tests(tests)
        self.window.table_tests.selectRow(row + step)

    def add_from_command(self, command: str, at_tick: int = 0) -> None:
        """Add a test for a command seen elsewhere (command line, log record)."""
        row = self.add_test(CommandTest(command.strip().removeprefix("/"), at_tick=max(0, at_tick)))
        window = self.window
        window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
        window.table_tests.selectRow(row)
        self.status(f"added a test at tick {max(0, at_tick)}: {command.strip()}")

    def commit_edits(self) -> None:
        """Keep what is typed in a cell that is still open (e.g. Ctrl+S mid-edit)."""
        table = self.window.table_tests
        for editor in table.viewport().findChildren(QWidget):
            if editor.isVisible() and editor.parent() is table.viewport():
                table.commitData(editor)
                table.closeEditor(editor, QAbstractItemDelegate.NoHint)

    def tests(self) -> list[CommandTest]:
        table = self.window.table_tests
        tests = []
        for row in range(table.rowCount()):
            tick_item = table.item(row, COLUMN_TICK)
            try:
                at_tick = max(0, int(tick_item.text())) if tick_item else 0
            except ValueError:
                at_tick = 0
            tests.append(
                CommandTest(
                    command=_text(table.item(row, COLUMN_COMMAND)),
                    at_tick=at_tick,
                    expect=_text(table.item(row, COLUMN_EXPECT)),
                    enabled=tick_item is None or tick_item.checkState() == Qt.Checked,
                    expect_value=_text(table.item(row, COLUMN_VALUE)),
                    checks=list(self._checks(row)),
                )
            )
        return tests

    def set_tests(self, tests: list[CommandTest]) -> None:
        self.window.table_tests.setRowCount(0)
        for test in tests:
            self.add_test(test)
        self.results = {}
        self.window.test_summary.setText("no tests run")

    # -- running ------------------------------------------------------------

    def run(self, rows: list[int] | None = None) -> list[TestResult]:
        """Run the enabled tests, or only ``rows`` of them, in a fresh world."""
        window = self.window
        if not self.need_datapack() or window.debug.busy():
            return []
        tests = self.tests()
        if rows is not None:
            if not rows:
                self.status("select a test first")
                return []
            tests = [
                test if index in rows else replace(test, enabled=False)
                for index, test in enumerate(tests)
            ]
        if not any(test.enabled for test in tests):
            if rows:
                message = "the selected tests are disabled: tick their box first"
            elif tests:
                message = "enable at least one test (environment tab)"
            else:
                message = "add a test first (environment tab)"
            self.status(message)
            window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
            return []
        window.runs.stop(refresh=False)
        window.log_view.clear()
        # the tests run in the window's world, so it can be inspected afterwards
        window.datapacks.rebuild_emulator()
        try:
            results = run_tests(window.datapack, tests, emulator=window.emulator)
        except DebugStopped as stop:
            window.debug.stopped(stop)
            window.log_view.flush()
            window.runs.show_tick()
            window.world_view.refresh()
            return []
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        enabled_rows = [row for row, test in enumerate(tests) if test.enabled]
        self.show_results(
            dict(zip(enabled_rows, results, strict=True)),
            pending="skipped" if rows is None else "not run",
        )
        window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
        return results

    def show_results(self, by_row: dict[int, TestResult], pending: str = "skipped") -> None:
        """Fill the result column; rows without a result show ``pending``."""
        table = self.window.table_tests
        self.results = dict(by_row)
        passed = 0
        for row in range(table.rowCount()):
            item = table.item(row, COLUMN_RESULT)
            result = by_row.get(row)
            if result is None:
                item.setText(pending)
                item.setToolTip("")
                item.setForeground(QBrush(QColor("#7f8c8d")))
                continue
            passed += result.passed
            item.setText(("✔ " if result.passed else "✘ ") + result.reason)
            item.setToolTip(
                "\n".join(record.format() for record in result.records)
                + "\n\ndouble-click to show these records in the logs"
            )
            item.setForeground(QBrush(PASS_COLOUR if result.passed else FAIL_COLOUR))
        self._fit_columns()
        summary = f"{passed}/{len(by_row)} passed on {self.window.version.id}"
        self.window.test_summary.setText(summary)
        self.status(f"tests: {summary}")

    # -- a test's menu ----------------------------------------------------------

    def _on_double_click(self, row: int, column: int) -> None:
        if column == COLUMN_RESULT:
            self.reveal_records(row)
        elif column == COLUMN_CHECKS:
            self.edit_checks(row)

    # -- checks ---------------------------------------------------------------

    def _checks(self, row: int) -> list[str]:
        item = self.window.table_tests.item(row, COLUMN_CHECKS)
        value = item.data(CHECKS_ROLE) if item is not None else None
        return list(value) if value else []

    def _show_checks(self, row: int, checks: list[str]) -> None:
        item = self.window.table_tests.item(row, COLUMN_CHECKS)
        item.setData(CHECKS_ROLE, list(checks))
        item.setText("; ".join(checks))
        item.setToolTip(
            ("\n".join(checks) + "\n\n" if checks else "")
            + "double-click to edit, one check per line:\n"
            + CHECK_HELP
        )

    def edit_checks(self, row: int, text: str | None = None) -> bool:
        """Edit a test's checks (one per line); ``text`` skips the dialog. A
        line that cannot be read reopens the dialog with what was typed."""
        interactive = text is None
        typed = "\n".join(self._checks(row))
        problem = ""
        while True:
            if interactive:
                prompt = "What must hold after the command, one per line:\n" + CHECK_HELP
                if problem:
                    prompt = f"{problem}\n\n{prompt}"
                typed, accepted = QInputDialog.getMultiLineText(
                    self.window, "checks", prompt, typed
                )
                if not accepted:
                    return False
            else:
                typed = text
            lines = [line.strip() for line in typed.splitlines() if line.strip()]
            problem = next((p for p in map(valid_check, lines) if p), "")
            if not problem:
                break
            if not interactive:
                self.status(f"not saved: {problem}")
                return False
        if lines == self._checks(row):
            return True
        self._show_checks(row, lines)
        self._fit_columns()
        self.clear_result(row)
        self.window.projects.mark_modified()
        return True

    def clear_result(self, row: int) -> None:
        """A test was edited: its last result no longer says anything."""
        table = self.window.table_tests
        self.results.pop(row, None)
        item = table.item(row, COLUMN_RESULT)
        if item is not None and item.text():
            self._filling = True
            try:
                item.setText("edited: run it again")
                item.setToolTip("")
                item.setForeground(QBrush(QColor("#7f8c8d")))
            finally:
                self._filling = False
            self.window.test_summary.setText("tests edited since the last run")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() == COLUMN_RESULT:
            return
        self.clear_result(item.row())

    def reveal_records(self, row: int) -> None:
        result = self.results.get(row)
        if result is None:
            self.status("run this test first")
            return
        if not result.records:
            self.status("this test produced no records")
            return
        self.window.log_view.reveal(result.records[0])

    def test_menu(self, point: QPoint) -> None:
        window = self.window
        table = window.table_tests
        index = table.indexAt(point)
        menu = QMenu(window)
        if index.isValid():
            row = index.row()
            if row not in self.selected_rows():
                table.selectRow(row)
            command = _text(table.item(row, COLUMN_COMMAND))
            menu.addAction("run this test", lambda: self.run([row]))
            records = menu.addAction(
                "show its records in the logs", lambda: self.reveal_records(row)
            )
            records.setEnabled(row in self.results)
            menu.addAction(
                "analyze the command", lambda: window.navigation.analyze(command, "test")
            )
            menu.addAction("edit checks…", lambda: self.edit_checks(row))
            menu.addSeparator()
            menu.addAction("duplicate", self.duplicate_selected)
            menu.addAction("move up", lambda: self.move_selected(-1))
            menu.addAction("move down", lambda: self.move_selected(1))
            menu.addAction("remove", self.remove_selected)
            menu.addSeparator()
        menu.addAction("add test", lambda: self.add_test())
        menu.exec(table.viewport().mapToGlobal(point))


def _text(item: QTableWidgetItem | None) -> str:
    return item.text().strip() if item is not None else ""
