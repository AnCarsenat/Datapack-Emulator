"""The environment tab: world settings and command tests."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemDelegate, QTableWidgetItem, QWidget

from datapack_emulator.emulator.testing import CommandTest, TestResult, run_tests
from datapack_emulator.window.controllers.base import TAB_ENVIRONMENT, Controller

COLUMN_TICK, COLUMN_AS, COLUMN_COMMAND, COLUMN_EXPECT, COLUMN_RESULT = range(5)
PASS_COLOUR = QColor("#1e6f3d")
FAIL_COLOUR = QColor("#c0392b")


class EnvironmentController(Controller):
    def connect(self) -> None:
        window = self.window
        table = window.table_tests
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        window.add_test_button.clicked.connect(lambda: self.add_test())
        window.remove_test_button.clicked.connect(self.remove_selected)
        window.run_tests_button.clicked.connect(self.run)

    # -- the table ----------------------------------------------------------

    def add_test(self, test: CommandTest | None = None) -> None:
        test = test or CommandTest("say hello")
        table = self.window.table_tests
        row = table.rowCount()
        table.insertRow(row)
        tick = QTableWidgetItem(str(test.at_tick))
        tick.setFlags(tick.flags() | Qt.ItemIsUserCheckable)
        tick.setCheckState(Qt.Checked if test.enabled else Qt.Unchecked)
        tick.setToolTip("uncheck to skip this test")
        table.setItem(row, COLUMN_TICK, tick)
        table.setItem(row, COLUMN_AS, QTableWidgetItem(test.run_as))
        table.setItem(row, COLUMN_COMMAND, QTableWidgetItem(test.command))
        table.setItem(row, COLUMN_EXPECT, QTableWidgetItem(test.expect))
        result = QTableWidgetItem("")
        result.setFlags(result.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, COLUMN_RESULT, result)
        self._fit_columns()

    def _fit_columns(self) -> None:
        table = self.window.table_tests
        for column in (COLUMN_TICK, COLUMN_AS, COLUMN_COMMAND, COLUMN_EXPECT):
            table.resizeColumnToContents(column)
            table.setColumnWidth(column, min(max(table.columnWidth(column), 80), 360))

    def remove_selected(self) -> None:
        table = self.window.table_tests
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

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
                    run_as=_text(table.item(row, COLUMN_AS)),
                )
            )
        return tests

    def set_tests(self, tests: list[CommandTest]) -> None:
        self.window.table_tests.setRowCount(0)
        for test in tests:
            self.add_test(test)
        self.window.test_summary.setText("no tests run")

    # -- running ------------------------------------------------------------

    def run(self) -> list[TestResult]:
        window = self.window
        if not self.need_datapack():
            return []
        tests = self.tests()
        if not any(test.enabled for test in tests):
            self.status(
                "enable at least one test (environment tab)"
                if tests
                else "add a test first (environment tab)"
            )
            window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
            return []
        window.runs.stop(refresh=False)
        window.log_view.clear()
        # the tests run in the window's world, so it can be inspected afterwards
        window.datapacks.rebuild_emulator()
        results = run_tests(window.datapack, tests, emulator=window.emulator)
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        enabled_rows = [row for row, test in enumerate(tests) if test.enabled]
        self.show_results(dict(zip(enabled_rows, results, strict=True)))
        window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
        return results

    def show_results(self, by_row: dict[int, TestResult], pending: str = "skipped") -> None:
        """Fill the result column; rows without a result show ``pending``."""
        table = self.window.table_tests
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
            item.setToolTip("\n".join(record.format() for record in result.records))
            item.setForeground(QBrush(PASS_COLOUR if result.passed else FAIL_COLOUR))
        self._fit_columns()
        summary = f"{passed}/{len(by_row)} passed on {self.window.version.id}"
        self.window.test_summary.setText(summary)
        self.status(f"tests: {summary}")


def _text(item: QTableWidgetItem | None) -> str:
    return item.text().strip() if item is not None else ""
