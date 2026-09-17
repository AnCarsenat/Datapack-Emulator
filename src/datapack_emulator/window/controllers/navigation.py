"""Opening things: the source view, the desktop's editor and file manager,
and the right-click menus of the explorer, the call graph and the profiler."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractItemModel, QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu

from datapack_emulator.window.controllers.base import TAB_GRAPH, TAB_SOURCE, Controller
from datapack_emulator.window.panels import (
    PACK_INDEX_ROLE,
    PATH_ROLE,
    describe_resource,
    highlighter_for,
)


class NavigationController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self._highlighter = None
        #: the file shown in the source view
        self.source_path: Path | None = None

    def connect(self) -> None:
        window = self.window
        window.tree.customContextMenuRequested.connect(self.explorer_menu)
        window.graph_widget.node_menu_requested.connect(self.graph_menu)
        window.web_view.setContextMenuPolicy(Qt.CustomContextMenu)
        window.web_view.customContextMenuRequested.connect(self.profiler_menu)
        window.source_edit.customContextMenuRequested.connect(self.source_menu)
        window.tree_profile.customContextMenuRequested.connect(self.profile_tree_menu)
        window.tree_profile.itemDoubleClicked.connect(self._on_profile_double_click)

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

    def resource_of(self, resource_id: str):
        """The function (``ns:id``) or function tag (``#ns:id``) the version
        reads; a plain id that names no function falls back to the tag."""
        window = self.window
        if window.datapack is None:
            return None
        view = window.datapack.view_for(window.version)
        if resource_id.startswith("#"):
            return view.function_tags.get(resource_id)
        return view.function(resource_id) or view.function_tags.get(f"#{resource_id}")

    def function_path(self, function_id: str) -> Path | None:
        """Where a function or tag id lives in the version being emulated."""
        resource = self.resource_of(function_id)
        return resource.path if resource is not None else None

    def open_function(self, function_id: str, line: int = 0, ask: bool = True) -> None:
        """Right-click target from the graph, the profiler and the logs."""
        path = self.function_path(function_id)
        if path is None:
            self.status(f"{function_id} has no file in {self.window.version.id}")
            return
        self.show_source(path, line, ask=ask)

    # -- the source view ----------------------------------------------------

    def _detach_highlighter(self) -> None:
        """Only one highlighter may colour the source document at a time."""
        if self._highlighter is not None:
            self._highlighter.setDocument(None)
            self._highlighter.setParent(None)
            self._highlighter = None

    def show_source(self, path: Path, line: int = 0, reveal: bool = True, ask: bool = True) -> None:
        """Open a file in the source view; ``reveal`` selects it in the
        explorer too (not when the click came from the explorer). ``ask`` is
        false when the view follows something else (the debugger stopping,
        reverting): unsaved edits are then kept, not asked about."""
        window = self.window
        same = path == self.source_path
        if same and window.editor.modified and ask:
            # clicking the file already open must not offer to throw the edits away
            self._go_to_line(line)
            return
        if ask and not window.editor.maybe_discard():
            return
        if same and window.editor.modified:
            self._go_to_line(line)  # the view keeps the edits and only moves
            return
        self.source_path = path
        self._detach_highlighter()
        if path.suffix.lower() == ".png":
            window.source_edit.setPlainText(f"{path.name}: binary image")
            window.editor.opened(path, text_file=False)
            window.source_label.setText(str(path))
            window.debug.refresh_gutter()
            return
        readable = True
        try:
            text = window.editor.read(path)
        except (OSError, UnicodeDecodeError) as exc:
            text = f"cannot read {path}: {exc}"
            readable = False
        self._highlighter = highlighter_for(path, window.source_edit.document())
        window.source_edit.setPlainText(text)
        window.editor.opened(path, text_file=readable, line=line)
        self._go_to_line(line)
        window.debug.refresh_gutter()
        if reveal:
            self.reveal_in_explorer(path)
        window.tabs.setCurrentWidget(window.tab_page(TAB_SOURCE))

    def _go_to_line(self, line: int) -> None:
        window = self.window
        if line > 0:
            block = window.source_edit.document().findBlockByNumber(line - 1)
            if block.isValid():
                cursor = window.source_edit.textCursor()
                cursor.setPosition(block.position())
                window.source_edit.setTextCursor(cursor)
                window.source_edit.centerCursor()
        window.tabs.setCurrentWidget(window.tab_page(TAB_SOURCE))

    # -- the explorer ---------------------------------------------------------

    def reveal_in_explorer(self, path: Path | None) -> bool:
        """Select a file's row in the explorer, expanding its folders and
        scrolling to it; whether the explorer has it."""
        tree = self.window.tree
        model: QAbstractItemModel | None = tree.model()
        if path is None or model is None or model.rowCount() == 0:
            return False
        matches = model.match(
            model.index(0, 0),
            PATH_ROLE,
            str(path),
            1,
            Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive,
        )
        if not matches:
            return False
        index = matches[0]
        if tree.currentIndex() == index:
            return True  # already there: do not move the row under the mouse
        parent = index.parent()
        while parent.isValid():
            tree.expand(parent)
            parent = parent.parent()
        tree.setCurrentIndex(index)
        tree.scrollTo(index, tree.ScrollHint.EnsureVisible)
        return True

    def show_panels(self, path: Path | None) -> None:
        """The inspector in front, and the explorer showing ``path``'s row."""
        window = self.window
        window.dock_inspector.show()
        window.dock_inspector.raise_()
        window.dock_explorer.show()
        if window.dock_explorer not in window.tabifiedDockWidgets(window.dock_inspector):
            window.dock_explorer.raise_()
        self.reveal_in_explorer(path)

    # -- the call graph ---------------------------------------------------------

    def show_in_graph(self, function_id: str | None = None) -> None:
        """The call graph tab, centred on a function (default: the one in the
        source view) with its callers and calls ringed."""
        window = self.window
        if function_id is None:
            function_id = self.resource_id_at(self.source_path)
        if function_id is None:
            self.status("open a function or function tag in the source view first")
            return
        if window.call_graph is None or window.graph_widget.graph is not window.call_graph:
            window.runs.run_graphview()
        window.tabs.setCurrentWidget(window.tab_page(TAB_GRAPH))
        if window.graph_widget.focus(function_id):
            graph = window.call_graph
            self.status(
                f"{function_id}: called by {len(set(graph.predecessors(function_id)))}, "
                f"calls {len(set(graph.successors(function_id)))}"
            )
        else:
            self.status(f"{function_id} is not in the call graph of {window.version.id}")

    # -- analysing lines ------------------------------------------------------

    def function_at(self, path: Path | None) -> str | None:
        """The id of the function a file holds in the version being emulated."""
        window = self.window
        if path is None or window.datapack is None:
            return None
        view = window.datapack.view_for(window.version)
        for function_id, function in view.functions.items():
            if function.path == path:
                return function_id
        return None

    def resource_id_at(self, path: Path | None) -> str | None:
        """The function or function tag (``#id``) a file is, in the version
        being emulated; for a file that version does not use (another
        overlay, another pack's copy), the id its folders give it."""
        window = self.window
        if path is None or window.datapack is None:
            return None
        function_id = self.function_at(path)
        if function_id is not None:
            return function_id
        view = window.datapack.view_for(window.version)
        for tag_id, tag in view.function_tags.items():
            if tag.path == path:
                return tag_id
        parts = path.with_suffix("").parts
        for index in range(len(parts) - 1, -1, -1):
            if parts[index] != "data" or index + 3 > len(parts):
                continue
            namespace, rest = parts[index + 1], parts[index + 2 :]
            if rest[0] in ("function", "functions") and len(rest) > 1:
                return f"{namespace}:{'/'.join(rest[1:])}"
            if rest[0] == "tags" and len(rest) > 2 and rest[1] in ("function", "functions"):
                return f"#{namespace}:{'/'.join(rest[2:])}"
            break
        return None

    def line_text(self, function_id: str, line: int) -> str | None:
        """The text of line ``line`` (1-based) of a function's file."""
        path = self.function_path(function_id)
        if path is None or line <= 0 or not path.is_file():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        return lines[line - 1] if line <= len(lines) else None

    def cursor_line(self) -> tuple[str, int]:
        """The source view's current line and its number (1-based)."""
        cursor = self.window.source_edit.textCursor()
        return cursor.block().text(), cursor.blockNumber() + 1

    def analyze(self, text: str, where: str = "", function_id: str | None = None) -> None:
        """Explain one command line in the inspector (``function_id``: the
        function it comes from, shown in the explorer)."""
        from datapack_emulator.emulator.analysis.explain import explain_line

        window = self.window
        view = window.datapack.view_for(window.version) if window.datapack else None
        rows = explain_line(text, window.version, view=view, vanilla=window.vanilla)
        if where:
            rows.insert(0, ("from", where))
        window.datapacks.fill_inspector(rows)
        if function_id:
            self.show_panels(self.function_path(function_id))
        else:
            window.dock_inspector.show()
            window.dock_inspector.raise_()
        self.status(f"analyzed {where or 'the line'} for {window.version.id}")

    def search(self, mode: int = 0) -> None:
        """Quick open (mode 0) or text search (mode 1) in the emulated version."""
        from datapack_emulator.window.search_dialog import SearchDialog

        window = self.window
        if not self.need_datapack():
            return
        view = window.datapack.view_for(window.version)
        dialog = SearchDialog(view, self.show_source, parent=window, mode=mode)
        dialog.exec()

    def calls_and_callers(self, function_id: str) -> None:
        """What a function calls and what calls it, in the inspector."""
        from datapack_emulator.emulator.analysis.graph import CallGraph

        window = self.window
        if window.datapack is None:
            return
        graph = window.call_graph or CallGraph.from_pack(window.datapack.view_for(window.version))
        rows = graph.relations(function_id)
        window.datapacks.fill_inspector(rows + window.notes.rows_for(function_id))
        self.show_panels(self.function_path(function_id))

    def analyze_cursor_line(self) -> None:
        if self.source_path is None:
            self.status("open a function in the source view first")
            return
        text, number = self.cursor_line()
        function_id = self.function_at(self.source_path)
        self.analyze(
            text, f"{function_id or self.source_path.name}:{number}", function_id=function_id
        )

    # -- menus ------------------------------------------------------------

    def path_menu(self, path: Path | None, title: str = "", in_explorer: bool = False) -> QMenu:
        """The menu every file and folder gets (``in_explorer``: the explorer's
        own, which needs no *show in explorer*)."""
        menu = QMenu(self.window)
        if title:
            menu.addAction(title).setEnabled(False)
            menu.addSeparator()
        if path is None:
            menu.addAction("nothing to open").setEnabled(False)
            return menu
        if path.is_file():
            menu.addAction("open in source view", lambda: self.open_in_source(path))
            if not in_explorer:
                menu.addAction("show in explorer", lambda: self.reveal_in_explorer(path))
            resource_id = self.resource_id_at(path)
            if resource_id is not None:
                menu.addAction("show in call graph", lambda: self.show_in_graph(resource_id))
            function_id = self.function_at(path)
            if function_id is not None:
                menu.addAction(
                    "edit note…", lambda: self.window.notes.edit_function_note(function_id)
                )
        menu.addAction("open in external editor", lambda: self.open_externally(path))
        menu.addAction("open in external file manager", lambda: self.open_in_file_manager(path))
        menu.addAction("copy path", lambda: self.copy_path(path))
        return menu

    def source_menu(self, point: QPoint) -> None:
        """The text editor's own menu, plus actions on the line under the cursor."""
        edit = self.window.source_edit
        cursor = edit.cursorForPosition(point)
        if not edit.textCursor().hasSelection():
            edit.setTextCursor(cursor)
        menu = edit.createStandardContextMenu()
        menu.addSeparator()
        window = self.window
        save = menu.addAction("save file (Ctrl+S)", window.editor.save)
        save.setEnabled(window.editor.modified)
        revert = menu.addAction("revert file", window.editor.revert)
        revert.setEnabled(window.editor.modified)
        menu.addSeparator()
        function_id = self.function_at(self.source_path)
        text = edit.textCursor().block().text().strip()
        runnable = function_id is not None and bool(text) and not text.startswith(("#", "$"))
        analyze = menu.addAction("analyze this line", self.analyze_cursor_line)
        analyze.setEnabled(self.source_path is not None)
        run_line = menu.addAction("run this line (console)", lambda: window.console.run(text))
        run_line.setEnabled(runnable)
        add_test = menu.addAction(
            "add this line as a test", lambda: window.environment.add_from_command(text)
        )
        add_test.setEnabled(runnable)
        breakpoint = menu.addAction("toggle breakpoint (F9)", window.debug.toggle_at_cursor)
        breakpoint.setEnabled(function_id is not None)
        menu.addSeparator()
        run_function = menu.addAction(
            "run this function", lambda: window.console.run(f"function {function_id}")
        )
        run_function.setEnabled(function_id is not None)
        graph = menu.addAction(
            "show callers and calls", lambda: self.calls_and_callers(function_id)
        )
        graph.setEnabled(function_id is not None)
        resource_id = self.resource_id_at(self.source_path)
        in_graph = window._action("actionshow_in_graph")
        if in_graph is not None:
            in_graph.setEnabled(resource_id is not None)
            menu.addAction(in_graph)
        inspect = menu.addAction("show in inspector", lambda: self.inspect_function(resource_id))
        inspect.setEnabled(resource_id is not None and self.resource_of(resource_id) is not None)
        note = menu.addAction(
            "edit note on this function…",
            lambda: self.window.notes.edit_function_note(function_id),
        )
        note.setEnabled(function_id is not None)
        menu.exec(edit.viewport().mapToGlobal(point))

    def explorer_menu(self, point: QPoint) -> None:
        window = self.window
        tree = window.tree
        index = tree.indexAt(point)
        value = index.data(PATH_ROLE) if index.isValid() else None
        path = Path(value) if value else None
        menu = self.path_menu(path, path.name if path else "", in_explorer=True)
        pack_index = index.data(PACK_INDEX_ROLE) if index.isValid() else None
        menu.addSeparator()
        if pack_index is not None and window.datapack is not None:
            count = len(window.datapack)
            menu.addAction("remove from the project", lambda: window.datapacks.remove(pack_index))
            up = menu.addAction(
                "load earlier (move up)", lambda: window.datapacks.move(pack_index, -1)
            )
            up.setEnabled(pack_index > 0)
            down = menu.addAction(
                "load later (move down)", lambda: window.datapacks.move(pack_index, 1)
            )
            down.setEnabled(pack_index < count - 1)
        menu.addAction("add datapack…", window.datapacks.import_)
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
        resource = self.resource_of(function_id)
        if resource is not None:
            rows = describe_resource(resource, window.version)
            window.datapacks.fill_inspector(rows + window.notes.rows_for(function_id))
            self.show_panels(resource.path)

    def _on_profile_double_click(self, item, _column: int) -> None:
        from datapack_emulator.window.panels.profile import FUNCTION_ROLE

        function_id = item.data(0, FUNCTION_ROLE)
        if function_id:
            self.open_function(function_id)

    def profile_tree_menu(self, point: QPoint) -> None:
        from datapack_emulator.window.panels.profile import FUNCTION_ROLE

        tree = self.window.tree_profile
        item = tree.itemAt(point)
        function_id = item.data(0, FUNCTION_ROLE) if item is not None else ""
        if not function_id:
            menu = QMenu(self.window)
            menu.addAction("right-click a function").setEnabled(False)
            menu.addAction("refresh", self.window.runs.run_profiler)
            menu.exec(tree.viewport().mapToGlobal(point))
            return
        menu = self.path_menu(self.function_path(function_id), function_id)
        menu.addSeparator()
        menu.addAction("show in inspector", lambda: self.inspect_function(function_id))
        menu.addAction("show callers and calls", lambda: self.calls_and_callers(function_id))
        menu.exec(tree.viewport().mapToGlobal(point))

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
