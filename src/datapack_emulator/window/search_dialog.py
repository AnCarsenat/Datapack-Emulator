"""Quick open and text search over the pack: layout from ``search.ui``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem

from datapack_emulator.emulator.analysis.search import Hit, open_candidates, text_hits
from datapack_emulator.emulator.datapack import PackView
from datapack_emulator.window.panels import load_ui_into

UI_FILE = Path(__file__).with_name("search.ui")
MODE_OPEN, MODE_TEXT = range(2)
#: results listed at most
MAX_RESULTS = 500
HIT_ROLE = Qt.UserRole


class SearchDialog(QDialog):
    def __init__(self, view: PackView, open_hit, parent=None, mode: int = MODE_OPEN):
        super().__init__(parent)
        load_ui_into(self, UI_FILE)
        self.view = view
        self.open_hit = open_hit
        find = self.findChild
        self.combo_mode: QComboBox = find(QComboBox, "comboSearchMode")
        self.edit: QLineEdit = find(QLineEdit, "editSearch")
        self.results: QListWidget = find(QListWidget, "listResults")
        self.summary: QLabel = find(QLabel, "labelSearchSummary")
        self.combo_mode.setCurrentIndex(mode)
        self.combo_mode.currentIndexChanged.connect(lambda _i: self.refresh())
        self.edit.textChanged.connect(lambda _t: self.refresh())
        self.edit.returnPressed.connect(self.activate_current)
        self.results.itemActivated.connect(self._activate)
        self.edit.installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        arrows = (Qt.Key_Up, Qt.Key_Down)
        if watched is self.edit and event.type() == QEvent.KeyPress and event.key() in arrows:
            row = self.results.currentRow() + (1 if event.key() == Qt.Key_Down else -1)
            self.results.setCurrentRow(max(0, min(self.results.count() - 1, row)))
            return True
        return super().eventFilter(watched, event)

    def refresh(self) -> None:
        text = self.edit.text().strip()
        if self.combo_mode.currentIndex() == MODE_TEXT:
            hits = text_hits(self.view, text) if len(text) >= 2 else []
            empty = "type at least two characters"
        else:
            wanted = text.lower()
            hits = [hit for hit in open_candidates(self.view) if wanted in hit.label.lower()]
            empty = "nothing matches"
        self.results.clear()
        for hit in hits[:MAX_RESULTS]:
            item = QListWidgetItem(hit.label)
            item.setData(HIT_ROLE, hit)
            item.setToolTip(f"{hit.path}" + (f":{hit.line}" if hit.line else ""))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        shown = min(len(hits), MAX_RESULTS)
        self.summary.setText(
            f"{len(hits)} result(s)" + (f", first {shown} shown" if len(hits) > shown else "")
            if hits
            else empty
        )

    def activate_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._activate(item)

    def _activate(self, item: QListWidgetItem) -> None:
        hit: Hit = item.data(HIT_ROLE)
        self.open_hit(hit.path, hit.line)
        self.accept()
