"""Opening things: the source view, the desktop's editor and file manager,
and the right-click menus of the explorer, the call graph and the profiler."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu

from datapack_emulator.window.controllers.base import TAB_SOURCE, Controller
from datapack_emulator.window.panels import PATH_ROLE, describe_resource, highlighter_for


class NavigationController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self._highlighter = None

    def connect(self) -> None:
        window = self.window
        window.tree.customContextMenuRequested.connect(self.explorer_menu)
        window.graph_widget.node_menu_requested.connect(self.graph_menu)
        window.web_view.setContextMenuPolicy(Qt.CustomContextMenu)
        window.web_view.customContextMenuRequested.connect(self.profiler_menu)

    # -- the explorer selection ---------------------------------------------

    def selected_path(self) -> Path | None:
        indexes = self.window.tree.selectedIndexes()
        if not indexes:
            return None
        value = indexes[0].data(PATH_ROLE)
        return Path(value) if value else None

    # -- opening ----------------------------------------------------------

    def open_externally(self, path: Path | None) -> None:
        """Hand a file or folder to whatever the desktop uses for it."""
        if path is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status(f"no application is registered for {path}")
        else:
            self.window.output.app(f"opened {path} externally")

    def open_in_file_manager(self, path: Path | None) -> None:
        """A folder opens as itself; a file opens the folder that holds it."""
        if path is None:
            return
        self.open_externally(path if path.is_dir() else path.parent)

    def copy_text(self, text: str, what: str = "") -> None:
        QApplication.clipboard().setText(text)
        self.status(f"copied {what or text}")

    def copy_path(self, path: Path | None) -> None:
        if path is not None:
            self.copy_text(str(path))

    def open_in_source(self, path: Path | None, line: int = 0) -> None:
        if path is not None and path.is_file():
            self.show_source(path, line)

    def function_path(self, function_id: str) -> Path | None:
        """Where a function or tag id lives in the version being emulated."""
        window = self.window
        if window.datapack is None:
            return None
        view = window.datapack.view_for(window.version)
        function = view.function(function_id.lstrip("#"))
        if function is not None:
            return function.path
        tag = view.function_tags.get(
            function_id if function_id.startswith("#") else f"#{function_id}"
        )
        return tag.path if tag is not None else None

    def open_function(self, function_id: str, line: int = 0) -> None:
        """Right-click target from the graph, the profiler and the logs."""
        path = self.function_path(function_id)
        if path is None:
            self.status(f"{function_id} has no file in {self.window.version.id}")
            return
        self.show_source(path, line)

    # -- the source view ----------------------------------------------------

    def _detach_highlighter(self) -> None:
        """Only one highlighter may colour the source document at a time."""
        if self._highlighter is not None:
            self._highlighter.setDocument(None)
            self._highlighter.setParent(None)
            self._highlighter = None

    def show_source(self, path: Path, line: int = 0) -> None:
        window = self.window
        window.source_label.setText(f"{path}:{line}" if line else str(path))
        self._detach_highlighter()
        if path.suffix.lower() == ".png":
            window.source_edit.setPlainText(f"{path.name}: binary image")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            text = f"cannot read {path}: {exc}"
        self._highlighter = highlighter_for(path, window.source_edit.document())
        window.source_edit.setPlainText(text)
        if line > 0:
            block = window.source_edit.document().findBlockByLineNumber(line - 1)
            if block.isValid():
                cursor = window.source_edit.textCursor()
                cursor.setPosition(block.position())
                window.source_edit.setTextCursor(cursor)
                window.source_edit.centerCursor()
        window.tabs.setCurrentIndex(TAB_SOURCE)

    # -- menus ------------------------------------------------------------

    def path_menu(self, path: Path | None, title: str = "") -> QMenu:
        """The menu every file and folder gets."""
        menu = QMenu(self.window)
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

    def explorer_menu(self, point: QPoint) -> None:
        tree = self.window.tree
        index = tree.indexAt(point)
        value = index.data(PATH_ROLE) if index.isValid() else None
        path = Path(value) if value else None
        menu = self.path_menu(path, path.name if path else "")
        menu.exec(tree.viewport().mapToGlobal(point))

    def graph_menu(self, node_id: str, global_point: QPoint) -> None:
        path = self.function_path(node_id)
        menu = self.path_menu(path, node_id)
        if path is not None:
            menu.addSeparator()
        menu.addAction("show in inspector", lambda: self.inspect_function(node_id))
        menu.exec(global_point)

    def inspect_function(self, function_id: str) -> None:
        window = self.window
        if window.datapack is None:
            return
        view = window.datapack.view_for(window.version)
        resource = view.function(function_id.lstrip("#")) or view.function_tags.get(
            function_id if function_id.startswith("#") else f"#{function_id}"
        )
        if resource is not None:
            window.datapacks.fill_inspector(describe_resource(resource, window.version))
            window.dock_inspector.show()

    def profiler_menu(self, point: QPoint) -> None:
        """Right-click a profiler row: ask the page which function it is."""
        web_view = self.window.web_view
        global_point = web_view.mapToGlobal(point)
        script = (
            f"(function(){{var e=document.elementFromPoint({point.x()},{point.y()});"
            "while(e&&!e.dataset.function){e=e.parentElement;}"
            "return e?e.dataset.function:'';})()"
        )
        web_view.page().runJavaScript(
            script, lambda result: self._show_profiler_menu(result or "", global_point)
        )

    def _show_profiler_menu(self, function_id: str, global_point: QPoint) -> None:
        if not function_id:
            menu = QMenu(self.window)
            menu.addAction("right-click a row to open its function").setEnabled(False)
            menu.addAction("refresh report", self.window.runs.run_profiler)
            menu.exec(global_point)
            return
        self.graph_menu(function_id, global_point)
