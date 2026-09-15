"""Load a Qt Designer ``.ui`` file into an existing widget.

``QUiLoader`` normally builds a brand new widget tree; returning the window
itself for the top-level widget is what lets ``MainWindow`` and
``EngineWindow`` stay real ``QMainWindow`` subclasses while every widget,
dock, menu and action still comes from the ``.ui`` file.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QFile
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget


class _Loader(QUiLoader):
    def __init__(self, base: QWidget, custom: Sequence[type] = ()):
        super().__init__()
        self._base: QWidget | None = base
        for widget_class in custom:
            self.registerCustomWidget(widget_class)

    def createWidget(self, class_name, parent=None, name=""):  # noqa: N802 (Qt API)
        if parent is None and self._base is not None:
            widget = self._base
            self._base = None
            return widget
        return super().createWidget(class_name, parent, name)


def load_ui_into(widget: QWidget, ui_path: Path | str, custom: Sequence[type] = ()) -> None:
    """Populate ``widget`` from ``ui_path``.  Raises if the file cannot be read."""
    ui_file = QFile(str(ui_path))
    if not ui_file.open(QFile.ReadOnly):
        raise RuntimeError(f"cannot open {ui_path}: {ui_file.errorString()}")
    try:
        _Loader(widget, custom).load(ui_file)
    finally:
        ui_file.close()
