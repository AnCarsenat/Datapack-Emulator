"""Editing in the source view: unsaved changes, saving (and reloading the
packs), reverting, and the lines the emulated version would refuse,
underlined while you type."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from datapack_emulator.emulator.analysis.problems import text_problems
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

    def connect(self) -> None:
        window = self.window
        edit = window.source_edit
        edit.document().modificationChanged.connect(self._on_modified)
        edit.textChanged.connect(self._check_timer.start)
        for name, slot in (
            ("actionsave_file", self.save),
            ("actionrevert_file", self.revert),
        ):
            action = window._action(name)
            if action is not None:
                action.triggered.connect(slot)

    # -- state ---------------------------------------------------------------

    @property
    def path(self):
        return self.window.navigation.source_path

    @property
    def modified(self) -> bool:
        return self.window.source_edit.document().isModified()

    def opened(self, path, text_file: bool) -> None:
        """The source view shows a new file (called once its text is set)."""
        edit = self.window.source_edit
        edit.setReadOnly(not text_file)
        edit.document().setModified(False)
        self._on_modified(False)
        self.check_lines()

    def _on_modified(self, modified: bool) -> None:
        window = self.window
        if self.path is None:
            return
        base = str(self.path)
        window.source_label.setText(f"● {base} (unsaved)" if modified else base)
        for name in ("actionsave_file", "actionrevert_file"):
            action = window._action(name)
            if action is not None:
                action.setEnabled(modified)

    # -- saving ----------------------------------------------------------------

    def save_or_project(self) -> None:
        """Ctrl+S: the file when the source view has the focus and unsaved
        edits, else the project."""
        if self.window.source_edit.hasFocus() and self.modified:
            self.save()
        else:
            self.window.projects.save()

    def save(self) -> bool:
        """Write the file, then reload the packs so everything sees it."""
        window = self.window
        path = self.path
        if path is None or not self.modified:
            self.status("nothing to save")
            return False
        text = window.source_edit.toPlainText()
        if text and not text.endswith("\n"):
            text += "\n"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(window, "save file", f"Cannot save {path}:\n{exc}")
            return False
        window.source_edit.document().setModified(False)
        window.output.app(f"saved {path}")
        # a project keeps its packs in its archive: saving the project keeps the edit
        window.projects.mark_modified()
        edit = window.source_edit
        position = edit.textCursor().position()
        scroll = edit.verticalScrollBar().value()
        if window.datapack is not None and any(
            path.is_relative_to(root) for root in window.datapack.paths
        ):
            window.datapacks.reload()
        # the reload keeps the view on this file; put the cursor back
        cursor = edit.textCursor()
        cursor.setPosition(min(position, len(edit.toPlainText())))
        edit.setTextCursor(cursor)
        edit.verticalScrollBar().setValue(scroll)
        self.status(f"saved {path.name} and reloaded the datapacks")
        return True

    def revert(self) -> None:
        path = self.path
        if path is None:
            return
        self.window.source_edit.document().setModified(False)
        self.window.navigation.show_source(path, reveal=False)
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
