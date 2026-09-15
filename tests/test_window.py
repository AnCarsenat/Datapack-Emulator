"""Offscreen checks of the main window. Skipped when PySide6 is not installed."""

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from src.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    from src.window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
    window.close()


def test_panels_open_and_survive_hiding(app, window):
    assert all(dock.isVisible() for dock in window.docks)
    window.hide()
    app.processEvents()
    window.show()
    app.processEvents()
    assert all(dock.isVisible() for dock in window.docks)


def test_opening_a_project_keeps_its_version(window, make_pack):
    from src.project import Project

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    project = Project(name="p", datapack=pack, version="1.20.4")
    window._apply_project(project)
    assert window.version.id == "1.20.4"


def test_source_view_keeps_a_single_highlighter(window, make_pack):
    from PySide6.QtGui import QSyntaxHighlighter

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    for relative in ("data/test/function/tick.mcfunction", "pack.mcmeta", "data/test/function/tick.mcfunction"):
        window._show_source(pack / relative)
    document = window.source_edit.document()
    attached = [child for child in document.children() if isinstance(child, QSyntaxHighlighter)]
    assert len(attached) == 1
