"""Editing in the source view: unsaved changes, saving (and reloading the
packs), reverting, and the lines the emulated version would refuse,
underlined while you type."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QInputDialog, QMessageBox

from datapack_emulator.emulator.analysis.completion import complete
from datapack_emulator.emulator.analysis.problems import text_problems
from datapack_emulator.emulator.analysis.rename import RenameError, rename_function
from datapack_emulator.window.controllers.base import Controller

#: how long typing must pause before the lines are checked again
CHECK_DELAY_MS = 300


class EditorController(Controller):
    def __init__(self, window):
        super().__init__(window)
        self._check_timer = QTimer(window)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self.check_lines)
        #: the file as it was when it was opened or last saved, to notice
        #: another program writing it
        self._seen: tuple[int, int] | None = None
        #: the line endings the file was read with, kept when it is written
        self._newline = "\n"
        #: the line the file was opened at, for the label
        self._line = 0
        #: the text as it was written or read, to move the breakpoints of an edit
        self._saved_text = ""
        #: the completion popup's model (analysis.completion fills it)
        self._completions = QStringListModel(window)

    def connect(self) -> None:
        window = self.window
        edit = window.source_edit
        edit.document().modificationChanged.connect(self._on_modified)
        edit.textChanged.connect(self._check_timer.start)
        # the marks are anchored to lines: a new or removed line moves them
        edit.document().blockCountChanged.connect(lambda _: self.check_lines())
        completer = QCompleter(self._completions, window)
        edit.set_completer(completer)
        edit.completion_wanted.connect(self.show_completions)
        for name, slot in (
            ("actionrename_function", self.rename_function),
            ("actioncomplete", lambda: edit.ask_completions()),
        ):
            action = window._action(name)
            if action is not None:
                action.triggered.connect(slot)
                # the menu entries stay; their shortcuts belong to the source
                # view (F2 renames a row in a tree, Ctrl+Space is a text key)
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                edit.addAction(action)
        for name, slot in (
            ("actionsave_file", self.save),
            ("actionrevert_file", self.revert),
        ):
            action = window._action(name)
            if action is not None:
                action.triggered.connect(slot)
        self._on_modified(False)  # nothing is open yet: both actions are off

    # -- state ---------------------------------------------------------------

    @property
    def path(self):
        return self.window.navigation.source_path

    @property
    def modified(self) -> bool:
        return self.window.source_edit.document().isModified()

    def opened(self, path, text_file: bool, line: int = 0) -> None:
        """The source view shows a new file (called once its text is set)."""
        edit = self.window.source_edit
        edit.setReadOnly(not text_file)
        edit.document().setModified(False)
        self._seen = self._stat(path)
        self._saved_text = edit.toPlainText()
        self._line = line
        self._on_modified(False)
        self.check_lines()

    @staticmethod
    def _stat(path: Path | None) -> tuple[int, int] | None:
        """What the file on disk looks like, to notice it changing under us."""
        try:
            status = path.stat() if path is not None else None
        except OSError:
            return None
        return None if status is None else (status.st_mtime_ns, status.st_size)

    def read(self, path: Path) -> str:
        """The file's text, remembering its line endings for the next save."""
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        self._newline = "\r\n" if "\r\n" in text else "\n"
        return text.replace("\r\n", "\n")

    def _on_modified(self, modified: bool) -> None:
        window = self.window
        path = self.path
        base = "" if path is None else f"{path}:{self._line}" if self._line else str(path)
        if path is not None:
            window.source_label.setText(f"● {base} (unsaved)" if modified else base)
        for name in ("actionsave_file", "actionrevert_file"):
            action = window._action(name)
            if action is not None:
                action.setEnabled(modified and path is not None)
        window.source_tab_marker(modified and path is not None)

    # -- saving ----------------------------------------------------------------

    def save_or_project(self) -> None:
        """Ctrl+S: the open file while it has unsaved edits (wherever the focus
        is), else the project."""
        if self.modified and self.path is not None:
            self.save()
        else:
            self.window.projects.save()

    def save(self) -> bool:
        """Write the file, then reload the packs so everything sees it."""
        window = self.window
        path = self.path
        edit = window.source_edit
        if path is None or not self.modified or edit.isReadOnly():
            self.status("nothing to save")
            return False
        if window.debug.busy():  # saving reloads the packs under the stopped run
            return False
        if not self._still_ours(path):
            return False
        text = edit.toPlainText()
        if text and not text.endswith("\n"):
            text += "\n"
            edit.appendPlainText("")  # the view and the file say the same thing
        try:
            with open(path, "w", encoding="utf-8", newline=self._newline) as handle:
                handle.write(text)
        except OSError as exc:
            QMessageBox.warning(window, "save file", f"Cannot save {path}:\n{exc}")
            return False
        edit.document().setModified(False)
        self._seen = self._stat(path)
        window.output.app(f"saved {path}")
        window.debug.shift_breakpoints(path, self._saved_text, text)
        self._saved_text = text
        inside = window.datapack is not None and any(
            path.is_relative_to(root) for root in window.datapack.paths
        )
        if inside:
            # a project keeps its packs in its archive: saving the project keeps the edit
            window.projects.mark_modified()
            running = window.emulator is not None and window.emulator.world.tick > 0
            window.datapacks.reload()
            self.status(
                f"saved {path.name} and reloaded the datapacks"
                + (" (the run was stopped)" if running else "")
            )
        else:
            self.status(f"saved {path.name} (outside the loaded packs)")
        return True

    def _still_ours(self, path: Path) -> bool:
        """Ask before writing over what another program wrote meanwhile."""
        now = self._stat(path)
        if self._seen is None or now is None or now == self._seen:
            return True
        answer = QMessageBox.question(
            self.window,
            "the file changed on disk",
            f"{path.name} changed on disk since it was opened.\n"
            "Overwrite it with what is in the view, or reload it and lose the edits?",
            QMessageBox.Save | QMessageBox.Reset | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Reset:
            self.window.source_edit.document().setModified(False)
            self.window.navigation.show_source(path, reveal=False, ask=False)
            self.status(f"reloaded {path.name} from disk")
        return answer == QMessageBox.Save

    def revert(self) -> None:
        path = self.path
        if path is None:
            self.status("nothing to revert")
            return
        self.window.source_edit.document().setModified(False)
        self.window.navigation.show_source(path, reveal=False, ask=False)
        self.status(f"reverted {path.name}")

    def maybe_discard(self) -> bool:
        """Before the view shows something else: save, discard or stay.
        True to go on."""
        if not self.modified or self.path is None:
            return True
        answer = QMessageBox.question(
            self.window,
            "unsaved file",
            f"Save the changes to {self.path.name}?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self.save()
        if answer == QMessageBox.Discard:
            self.window.source_edit.document().setModified(False)
            return True
        return False

    # -- completing ------------------------------------------------------------

    def candidates(self, line: str, cursor: int) -> list:
        """What could be typed at ``cursor`` in ``line``, for this pack."""
        window = self.window
        path = self.path
        if path is None or path.suffix.lower() != ".mcfunction":
            return []
        view = window.datapack.view_for(window.version) if window.datapack is not None else None
        return complete(line, cursor, window.version, view=view, vanilla=window.vanilla)

    def show_completions(self, line: str, cursor: int) -> None:
        """Fill and show the popup under the cursor (Ctrl+Space, or typing)."""
        edit = self.window.source_edit
        completer = edit.completer
        if completer is None:
            return
        found = self.candidates(line, cursor)
        if not found:
            completer.popup().hide()
            return
        self._completions.setStringList([candidate.text for candidate in found])
        # complete() already kept what fits (including its contains fallback):
        # a prefix here would filter those candidates straight back out
        completer.setCompletionPrefix("")
        rectangle = edit.cursorRect()
        rectangle.setWidth(
            completer.popup().sizeHintForColumn(0)
            + completer.popup().verticalScrollBar().sizeHint().width()
            + 20
        )
        completer.complete(rectangle)

    # -- renaming ---------------------------------------------------------------

    def rename_function(self) -> bool:
        """Rename the function in the view, and every reference to its id."""
        window = self.window
        function_id = window.navigation.function_at(self.path)
        if function_id is None or window.datapack is None:
            self.status("open a function of a loaded pack first")
            return False
        if window.debug.busy():  # the stopped run reads the file where it is
            return False
        new_id, chosen = QInputDialog.getText(
            window, "rename function", "The function's new id:", text=function_id
        )
        if not chosen or not new_id.strip() or new_id.strip() == function_id:
            return False
        try:
            edits = rename_function(window.datapack, function_id, new_id.strip(), window.version)
        except RenameError as exc:
            QMessageBox.warning(window, "rename function", str(exc))
            return False
        lines = sum(edit.kind == "line" for edit in edits)
        answer = QMessageBox.question(
            window,
            "rename function",
            f"Rename {function_id} to {new_id.strip()}?\n"
            f"Its file moves and {lines} line(s) in the pack follow it.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False
        if not self.maybe_discard():  # the file is about to move
            return False
        try:
            edits = rename_function(
                window.datapack, function_id, new_id.strip(), window.version, apply=True
            )
        except RenameError as exc:  # a file that cannot be written: nothing moved
            QMessageBox.warning(window, "rename function", str(exc))
            return False
        moved = next(Path(edit.after) for edit in edits if edit.kind == "move")
        window.output.app(f"renamed {function_id} to {new_id.strip()} ({lines} line(s))")
        window.datapacks.reload()
        window.navigation.show_source(moved, ask=False)
        self.status(f"renamed {function_id} to {new_id.strip()}: {lines} line(s) followed")
        return True

    # -- checking as you type --------------------------------------------------

    def check_lines(self) -> None:
        """Underline what the emulated version would refuse in the text."""
        window = self.window
        path = self.path
        edit = window.source_edit
        marks: dict[int, str] = {}
        if path is not None and not edit.isReadOnly():
            marks = text_problems(edit.toPlainText(), path.suffix, window.version)
        edit.set_line_marks(marks)
