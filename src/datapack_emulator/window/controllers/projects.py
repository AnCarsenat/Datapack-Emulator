"""file > new / open / save project."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.emulator.testing import CommandTest
from datapack_emulator.project import LEGACY_SUFFIX, SUFFIX, Project, projects_dir
from datapack_emulator.window.controllers.base import Controller

SAVE_FILTER = f"Datapack Emulator project (*{SUFFIX})"
OPEN_FILTER = f"Datapack Emulator projects (*{SUFFIX} *{LEGACY_SUFFIX})"


class ProjectController(Controller):
    def __init__(self, window):
        super().__init__(window)
        #: the widgets differ from what was last saved or opened
        self.modified = False
        self._applying = False

    def connect(self) -> None:
        window = self.window
        for spin in (window.spin_ticks, window.spin_players, window.spin_seed):
            spin.valueChanged.connect(self.mark_modified)
        for combo in (window.combo_speed, window.combo_version):
            combo.currentIndexChanged.connect(self.mark_modified)
        window.check_tests_during_runs.toggled.connect(self.mark_modified)
        window.check_step_on_command.toggled.connect(self.mark_modified)
        table = window.table_tests
        table.itemChanged.connect(self._on_test_item_changed)
        table.model().rowsInserted.connect(self.mark_modified)
        table.model().rowsRemoved.connect(self.mark_modified)

    # -- unsaved changes ------------------------------------------------------

    def mark_modified(self, *_args) -> None:
        if not self._applying and not self.modified:
            self.modified = True
            self.refresh_title()

    def mark_saved(self) -> None:
        self.modified = False
        self.refresh_title()

    def _on_test_item_changed(self, item) -> None:
        from datapack_emulator.window.controllers.environment import COLUMN_RESULT

        if item.column() != COLUMN_RESULT:  # results are not part of the project
            self.mark_modified()

    def confirm_close(self) -> bool:
        """Before the window closes, or another project opens: save, discard or
        stay. True to go on."""
        if not self.window.editor.maybe_discard():
            return False
        if not self.modified:
            return True
        answer = self.ask_save_changes()
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def ask_save_changes(self) -> QMessageBox.StandardButton:
        return QMessageBox.question(
            self.window,
            "unsaved changes",
            f"Save the changes to {self.window.project.name} (settings and tests)?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )

    # -- applying and capturing ----------------------------------------------

    def apply(self, project: Project) -> None:
        """Put a loaded project into the widgets, then open its datapack."""
        self._applying = True
        try:
            self._apply(project)
        finally:
            self._applying = False
        self.mark_saved()

    def _apply(self, project: Project) -> None:
        window = self.window
        window.project = project
        if project.version:
            index = window.combo_version.findData(project.version)
            if index >= 0:
                window.combo_version.setCurrentIndex(index)
        window.spin_ticks.setValue(project.ticks)
        window.spin_players.setValue(project.players)
        window.spin_seed.setValue(project.seed)
        window.runs.speed = project.speed
        window.check_tests_during_runs.setChecked(project.tests_during_runs)
        window.check_step_on_command.setChecked(project.step_on_command)
        window.notes.project_notes = project.notes
        window.environment.set_tests([CommandTest.from_dict(test) for test in project.tests])
        window.debug.load(project.breakpoints, project.watches)
        if project.vanilla_jar and Path(project.vanilla_jar).is_file():
            try:
                window.jars.use(window.library.load_jar(Path(project.vanilla_jar)))
            except Exception as exc:
                window.output.app(f"cannot read {project.vanilla_jar}: {exc}", level=LogLevel.ERROR)
        # the project's own packs replace whatever was open (the sample, other packs)
        wanted = [Path(path) for path in project.datapacks]
        missing = [path for path in wanted if not path.is_dir()]
        if missing:
            said = ", ".join(str(path) for path in missing)
            window.output.app(
                f"{project.name}: {len(missing)} datapack(s) of the project are gone: {said}",
                level=LogLevel.ERROR,
            )
            self.status(f"{len(missing)} datapack(s) of {project.name} are gone: {said}")
        window.datapacks.load_many([path for path in wanted if path.is_dir()], keep_project=True)
        if window.engine_window is not None and window.datapack is not None:
            window.engine_window.set_datapack(window.datapack)
            if project.engine_versions:
                window.engine_window.select_ids(project.engine_versions)

    def capture(self) -> Project:
        """Read the current window state back into the project."""
        window = self.window
        project = window.project
        project.datapacks = window.datapack.paths if window.datapack else []
        project.version = window.version.id
        project.ticks = window.spin_ticks.value()
        project.players = window.spin_players.value()
        project.seed = window.spin_seed.value()
        project.speed = window.runs.speed
        project.tests_during_runs = window.check_tests_during_runs.isChecked()
        project.step_on_command = window.check_step_on_command.isChecked()
        project.notes = window.notes.project_notes
        window.environment.commit_edits()  # a cell still being typed in counts
        project.tests = [test.to_dict() for test in window.environment.tests()]
        window.debug.capture(project)
        project.vanilla_jar = str(window.vanilla.jar_path) if window.vanilla else ""
        if window.engine_window is not None:
            project.engine_versions = [
                version.id for version in window.engine_window.selected_versions()
            ]
        return project

    def refresh_title(self) -> None:
        project = self.window.project
        where = f" — {project.path}" if project.path else ""
        star = "*" if self.modified else ""
        self.window.setWindowTitle(f"Datapack Emulator — {star}{project.title}{where}")

    def new(self) -> None:
        name, accepted = QInputDialog.getText(self.window, "new project", "project name:")
        if not accepted or not name.strip():
            return
        project = Project(name=name.strip())
        if project.default_path().exists() and not self.confirm_overwrite(project.default_path()):
            return
        self.window.project = project
        self._write(self.capture(), project.default_path())

    def open(self) -> None:
        if not self.confirm_close():
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self.window, "open project", str(projects_dir()), OPEN_FILTER
        )
        if chosen:
            self.open_path(Path(chosen))

    def open_path(self, path: Path, quiet: bool = False) -> bool:
        """``quiet``: the window is opening it by itself (on launch), so a
        broken file goes to the log instead of a box in front of no window."""
        try:
            project = Project.load(path)
        except (OSError, ValueError) as exc:
            if quiet:
                self.window.output.app(f"cannot read {path}: {exc}", level=LogLevel.ERROR)
            else:
                QMessageBox.warning(self.window, "open project", f"cannot read {path}:\n{exc}")
            return False
        self.window.output.app(f"opened project {path}")
        self.apply(project)
        self.window.session.remember_project(project.path or path)
        return True

    def save(self) -> bool:
        """Ctrl+S: write the project's .dpemu (a legacy .json saves beside itself)."""
        window = self.window
        project = self.capture()
        if project.path is None and project.name == "untitled" and window.datapack is not None:
            project.name = window.datapack.name
        if project.path is None and project.default_path().exists():
            # never replace another project just because it has the same name
            return self.save_as()
        return self._write(project, None)

    def save_as(self) -> bool:
        project = self.capture()
        chosen, _ = QFileDialog.getSaveFileName(
            self.window, "save project as", str(project.default_path()), SAVE_FILTER
        )
        return self.save_to(Path(chosen)) if chosen else False

    def save_to(self, path: Path) -> bool:
        """Save under a new name (the suffix is always .dpemu).

        The file dialog already asked before replacing an existing file.
        """
        name = path.stem if path.suffix in (SUFFIX, LEGACY_SUFFIX) else path.name
        # the window keeps its current project until the new file is written
        return self._write(self.capture().renamed(name), path)

    def confirm_overwrite(self, path: Path) -> bool:
        answer = QMessageBox.question(
            self.window,
            "replace project",
            f"{path.name} already exists. Replace it?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _write(self, project: Project, path: Path | None) -> bool:
        try:
            target = project.save(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.window, "save project", f"cannot write:\n{exc}")
            return False
        self.window.project = project
        self.mark_saved()
        self.window.session.remember_project(target)
        self.window.output.app(f"saved project {target}")
        self.status(f"saved {target}")
        return True
