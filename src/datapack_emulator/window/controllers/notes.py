"""The user's own notes: one for the project, one per function."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog

from datapack_emulator.window.controllers.base import Controller


class NotesController(Controller):
    def connect(self) -> None:
        window = self.window
        window.edit_project_notes.textChanged.connect(window.projects.mark_modified)

    # -- the project note ---------------------------------------------------

    @property
    def project_notes(self) -> str:
        return self.window.edit_project_notes.toPlainText()

    @project_notes.setter
    def project_notes(self, text: str) -> None:
        self.window.edit_project_notes.setPlainText(text)

    # -- function notes -----------------------------------------------------

    def function_note(self, function_id: str) -> str:
        return self.window.project.function_notes.get(function_id.lstrip("#"), "")

    def set_function_note(self, function_id: str, text: str) -> None:
        notes = self.window.project.function_notes
        key = function_id.lstrip("#")
        if text.strip():
            notes[key] = text.strip()
        else:
            notes.pop(key, None)
        self.window.projects.mark_modified()

    def edit_function_note(self, function_id: str) -> None:
        text, accepted = QInputDialog.getMultiLineText(
            self.window,
            "note",
            f"your note on {function_id} (saved with the project; empty removes it):",
            self.function_note(function_id),
        )
        if accepted:
            self.set_function_note(function_id, text)
            self.status(f"note on {function_id} {'saved' if text.strip() else 'removed'}")
            self.window.navigation.inspect_function(function_id)

    def rows_for(self, function_id: str) -> list[tuple[str, str]]:
        note = self.function_note(function_id)
        return [("your note", note)] if note else []
