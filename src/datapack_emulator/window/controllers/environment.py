"""The environment tab: world settings and command tests."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem

from datapack_emulator.emulator.testing import CommandTest, TestResult, run_tests
from datapack_emulator.window.controllers.base import TAB_ENVIRONMENT, Controller

COLUMN_TICK, COLUMN_COMMAND, COLUMN_EXPECT, COLUMN_RESULT = range(4)
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
        table.setItem(row, COLUMN_COMMAND, QTableWidgetItem(test.command))
        table.setItem(row, COLUMN_EXPECT, QTableWidgetItem(test.expect))
        result = QTableWidgetItem("")
        result.setFlags(result.flags() & ~Qt.ItemIsEditable)
        table.setItem(row, COLUMN_RESULT, result)
        self._fit_columns()

    def _fit_columns(self) -> None:
        table = self.window.table_tests
        for column in (COLUMN_TICK, COLUMN_COMMAND, COLUMN_EXPECT):
            table.resizeColumnToContents(column)
            table.setColumnWidth(column, min(max(table.columnWidth(column), 80), 360))

    def remove_selected(self) -> None:
        table = self.window.table_tests
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

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
        results = run_tests(
            window.datapack,
            tests,
            version=window.version,
            players=window.spin_players.value(),
            seed=window.spin_seed.value(),
            vanilla=window.vanilla,
            output=window.output,
        )
        window.log_view.flush()
        self._show_results(tests, results)
        window.tabs.setCurrentWidget(window.tab_page(TAB_ENVIRONMENT))
        return results

    def _show_results(self, tests: list[CommandTest], results: list[TestResult]) -> None:
        table = self.window.table_tests
        # run_tests returns one result per enabled test, in table order
        enabled_rows = [row for row, test in enumerate(tests) if test.enabled]
        by_row = dict(zip(enabled_rows, results, strict=True))
        passed = 0
        for row in range(table.rowCount()):
            item = table.item(row, COLUMN_RESULT)
            result = by_row.get(row)
            if result is None:
                item.setText("skipped")
                item.setForeground(QBrush(QColor("#7f8c8d")))
                continue
            passed += result.passed
            item.setText(("✔ " if result.passed else "✘ ") + result.reason)
            item.setToolTip("\n".join(record.format() for record in result.records))
            item.setForeground(QBrush(PASS_COLOUR if result.passed else FAIL_COLOUR))
        self._fit_columns()
        self.window.test_summary.setText(
            f"{passed}/{len(results)} passed on {self.window.version.id}"
        )
        self.status(f"tests: {passed}/{len(results)} passed on {self.window.version.id}")


def _text(item: QTableWidgetItem | None) -> str:
    return item.text().strip() if item is not None else ""
