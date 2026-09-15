"""file > new / open / save project."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.project import Project, projects_dir
from datapack_emulator.window.controllers.base import Controller


class ProjectController(Controller):
    def apply(self, project: Project) -> None:
        """Put a loaded project into the widgets, then open its datapack."""
        window = self.window
        window.project = project
        if project.version:
            index = window.combo_version.findData(project.version)
            if index >= 0:
                window.combo_version.setCurrentIndex(index)
        window.spin_ticks.setValue(project.ticks)
        window.spin_players.setValue(project.players)
        if project.vanilla_jar and Path(project.vanilla_jar).is_file():
            try:
                window.jars.use(window.library.load_jar(Path(project.vanilla_jar)))
            except Exception as exc:
                window.output.app(f"cannot read {project.vanilla_jar}: {exc}", level=LogLevel.ERROR)
        if project.datapack and Path(project.datapack).is_dir():
            window.datapacks.load(Path(project.datapack), keep_project=True)
        self.refresh_title()

    def capture(self) -> Project:
        """Read the current window state back into the project."""
        window = self.window
        project = window.project
        project.datapack = window.datapack.path if window.datapack else None
        project.version = window.version.id
        project.ticks = window.spin_ticks.value()
        project.players = window.spin_players.value()
        project.vanilla_jar = str(window.vanilla.jar_path) if window.vanilla else ""
        if window.engine_window is not None:
            project.engine_versions = [
                version.id for version in window.engine_window.selected_versions()
            ]
        return project

    def refresh_title(self) -> None:
        project = self.window.project
        where = f" — {project.path}" if project.path else ""
        self.window.setWindowTitle(f"Datapack Emulator — {project.title}{where}")

    def new(self) -> None:
        name, accepted = QInputDialog.getText(self.window, "new project", "project name:")
        if not accepted or not name.strip():
            return
        self.window.project = Project(name=name.strip())
        self.capture()
        self.save()

    def open(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self.window, "open project", str(projects_dir()), "Projects (*.json)"
        )
        if not chosen:
            return
        try:
            project = Project.load(Path(chosen))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.window, "open project", f"cannot read {chosen}:\n{exc}")
            return
        self.window.output.app(f"opened project {chosen}")
        self.apply(project)

    def save(self) -> None:
        window = self.window
        project = self.capture()
        if project.path is None and project.name == "untitled" and window.datapack is not None:
            project.name = window.datapack.name
        target = project.save()
        self.refresh_title()
        window.output.app(f"saved project {target}")
        self.status(f"saved {target}")

    def save_as(self) -> None:
        suggestion = str(self.capture().default_path())
        chosen, _ = QFileDialog.getSaveFileName(
            self.window, "save project as", suggestion, "Projects (*.json)"
        )
        if not chosen:
            return
        self.window.project = self.window.project.renamed(Path(chosen).stem)
        self.capture().save(Path(chosen))
        self.refresh_title()
        self.status(f"saved {chosen}")
