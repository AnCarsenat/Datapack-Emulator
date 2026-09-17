"""Importing, loading and showing a datapack; the explorer and the inspector."""

from __future__ import annotations

import textwrap
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QObject
from PySide6.QtWidgets import QFileDialog, QTreeWidgetItem

from datapack_emulator.emulator.analysis.inspector import version_note
from datapack_emulator.emulator.datapack import Datapack, DatapackSet, preferred_version
from datapack_emulator.emulator.resources import Resource
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.project import default_sample
from datapack_emulator.settings import PATHS
from datapack_emulator.window.controllers.base import Controller
from datapack_emulator.window.panels import (
    PATH_ROLE,
    RESOURCE_ROLE,
    build_explorer_model,
    describe_datapack,
    describe_resource,
    row_help,
)

#: what the pack label adds for each compatibility status
COMPATIBILITY_NOTES = {
    "too_old": "  (pack is marked incompatible: made for an older version)",
    "too_new": "  (pack is marked incompatible: made for a newer version)",
    "unknown": "  (pack.mcmeta is invalid for this version)",
}


class DatapackController(Controller):
    def connect(self) -> None:
        window = self.window
        window.tree.clicked.connect(self.on_tree_clicked)
        window.combo_version.currentTextChanged.connect(self.on_version_changed)
        self._inspector_rows: list[tuple[str, str]] = []
        self._resize_watch = _OnResize(window.inspector.viewport(), self._wrap_inspector)

    # -- loading ----------------------------------------------------------

    def import_(self) -> None:
        """file › add datapack: another pack analyzed alongside the others."""
        start = PATHS.SAMPLES if PATHS.SAMPLES.is_dir() else Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self.window, "select a datapack folder (the one holding pack.mcmeta)", str(start)
        )
        if chosen:
            self.add(Path(chosen))

    def open_default(self) -> None:
        """Cold start: open the first pack in samples/ so there is something to run."""
        sample = default_sample()
        if sample is None:
            self.status("no datapack loaded — file › add datapack")
            return
        self.load(sample)
        self.window.output.app(
            f"opened the sample datapack {sample.name} (file › add datapack for more, "
            "or open a project)"
        )

    def load(self, path: Path, keep_project: bool = False, keep_version: bool = False) -> None:
        """Analyze this one pack (replacing the ones loaded)."""
        self.load_many([path], keep_project=keep_project, keep_version=keep_version)

    def load_many(
        self, paths: list[Path], keep_project: bool = False, keep_version: bool = False
    ) -> None:
        """Analyze these packs together, in load order; an empty list unloads all."""
        packs = DatapackSet(Datapack.load(path) for path in paths)
        self._use(packs, keep_project=keep_project, keep_version=keep_version)

    def add(self, path: Path) -> None:
        """One more pack, loaded after the others (so it wins on shared ids)."""
        window = self.window
        current = window.datapack
        if current is None:
            self.load(path)
            return
        if current.index_of(path) is not None:
            self.status(f"{path.name} is already analyzed")
            return
        pack = DatapackSet(current.packs)
        pack.add(Datapack.load(path))
        self._use(pack, keep_version=True)

    def remove(self, index: int) -> None:
        window = self.window
        if window.datapack is None or not 0 <= index < len(window.datapack):
            return
        packs = DatapackSet(window.datapack.packs)
        removed = packs.remove(index)
        window.output.app(f"removed the datapack {removed.name} from the project")
        self._use(packs, keep_version=True)

    def move(self, index: int, step: int) -> None:
        window = self.window
        if window.datapack is None:
            return
        packs = DatapackSet(window.datapack.packs)
        if packs.move(index, step):
            self._use(packs, keep_version=True)

    def _use(
        self, packs: DatapackSet, keep_project: bool = False, keep_version: bool = False
    ) -> None:
        window = self.window
        window.log_view.clear()
        window.call_graph = None
        window.graph_widget.clear_graph()
        if not packs and window.project.path is None and not keep_project:
            # no project open: fall back to the default pack rather than nothing
            sample = default_sample()
            if sample is not None:
                window.output.app(
                    f"no datapack left and no project open: opened the default pack {sample.name}"
                )
                packs = DatapackSet([Datapack.load(sample)])
        if not packs:
            window.runs.stop(refresh=False)
            window.datapack = None
            window.emulator = None
            if window.engine_window is not None:
                window.engine_window.close()
            window.world_view.forget()
            self.show(None)
            if not keep_project:
                window.project.datapacks = []
                window.projects.mark_modified()
            window.projects.refresh_title()
            self.status("no datapack analyzed — file › add datapack")
            return
        window.datapack = packs
        if window.engine_window is not None:
            window.engine_window.set_datapack(packs)
        for pack in packs:
            window.output.app(f"loaded {pack.path}")
            window.session.remember_datapack(pack.path)
        for error in packs.errors:
            window.output.app(error, level=LogLevel.ERROR)

        if not keep_version and (not keep_project or not window.project.version):
            # reloading keeps the version on screen and a project remembers its
            # own; freshly loaded packs get their newest declared release
            self.select_pack_version(packs)
        window.jars.autoload()
        self.rebuild_emulator()
        self.show(packs)
        if not keep_project:
            if window.project.path is None:
                window.project.name = packs.name
            window.project.datapacks = packs.paths
            window.projects.mark_modified()
        window.projects.refresh_title()
        functions = len(packs.view_for(window.version).functions)
        self.status(
            f"{packs.name}: {len(packs.namespaces)} namespace(s), {functions} function(s) in "
            f"{window.version.id}"
            + (
                f", pack_format {packs.pack_format} ({packs.minecraft_version})"
                if len(packs) == 1
                else f", {len(packs)} datapacks"
            )
        )

    def reload(self) -> None:
        if self.window.datapack is None:
            self.status("nothing to reload")
            return
        self.load_many(self.window.datapack.paths, keep_project=True, keep_version=True)

    def select_pack_version(self, datapack: Datapack) -> None:
        """Default the version combo to the version the pack prefers."""
        target = preferred_version(datapack)
        index = self.window.combo_version.findData(target.id)
        if index >= 0:
            self.window.combo_version.setCurrentIndex(index)

    def rebuild_emulator(self) -> None:
        window = self.window
        if window.datapack is None:
            return
        window.runs.stop(refresh=False)  # the running world is replaced
        window.emulator = Emulator(
            window.datapack,
            version=window.version,
            players=window.spin_players.value(),
            output=window.output,
            seed=window.spin_seed.value(),
            vanilla=window.vanilla,
        )
        window.debug.attach(window.emulator)
        window.tick_label.setText("idle")
        window.world_view.forget()
        window.log_view.refresh_readers()

    def on_version_changed(self, _text: str) -> None:
        window = self.window
        if window.datapack is None:
            return
        window.jars.autoload()
        self.rebuild_emulator()
        window.output.app(f"version set to {window.version.id}")
        self.show(window.datapack)
        if window.call_graph is not None:
            window.runs.run_graphview()

    # -- explorer and inspector -------------------------------------------

    def show(self, datapack: Datapack | None) -> None:
        window = self.window
        window.tree.setModel(build_explorer_model(datapack))
        window.tree.expandToDepth(2)
        if datapack is None:
            window.version_note.setText("")
            window.pack_label.setText("no datapack loaded")
            self.fill_inspector([])
            window.problems.schedule_refresh()
            window.editor.check_lines()  # nothing is loaded: nothing is refused
            return
        compatibility = datapack.compatibility(window.version)
        window.pack_label.setText(
            f"{datapack.name} — emulating {window.version.id}"
            + COMPATIBILITY_NOTES.get(compatibility.status, "")
        )
        window.version_note.setText(version_note(datapack, window.version))
        self.fill_inspector(describe_datapack(datapack, window.version))
        window.problems.schedule_refresh()
        window.editor.check_lines()  # the version may have changed

    def on_tree_clicked(self, index: QModelIndex) -> None:
        window = self.window
        path_value = index.data(PATH_ROLE)
        resource_id = index.data(RESOURCE_ROLE)
        resource = self.find_resource(resource_id, path_value) if resource_id else None

        if resource is not None:
            rows = describe_resource(resource, window.version)
            self.fill_inspector(rows + window.notes.rows_for(resource.id))
        elif window.datapack is not None:
            self.fill_inspector(describe_datapack(window.datapack, window.version))

        if not path_value:
            return
        path = Path(path_value)
        if path.is_file():
            window.navigation.show_source(path, reveal=False)  # the row is right there

    def find_resource(self, resource_id: str, path: str | None = None) -> Resource | None:
        """The resource of an explorer row: the file clicked when ``path`` is
        given (two packs can share an id), otherwise the first with that id."""
        packs = self.window.datapack
        if packs is None:
            return None
        first = None
        wanted = Path(path) if path else None
        for pack in packs:
            for layer in [pack.base, *pack.overlays]:
                for namespace in layer.namespaces.values():
                    for resource in namespace.resources():
                        if resource.id != resource_id:
                            continue
                        if wanted is None or resource.path == wanted:
                            return resource
                        first = first or resource
        return first

    def fill_inspector(self, rows: list[tuple[str, str]]) -> None:
        """Rows of the inspector: long labels and values wrap to the dock's
        width (again when it is resized); tooltips hold the full value and
        what the property means."""
        self._inspector_rows = [(str(key), str(value)) for key, value in rows]
        inspector = self.window.inspector
        inspector.clear()
        for key, value in self._inspector_rows:
            item = QTreeWidgetItem([key, value])
            help_text = row_help(key)
            if help_text:
                item.setToolTip(0, f"{key}\n{help_text}")
            item.setToolTip(1, value)
            inspector.addTopLevelItem(item)
        self._wrap_inspector()

    #: the property column takes at most this share of the inspector's width
    LABEL_SHARE = 0.4

    def _wrap_inspector(self) -> None:
        inspector = self.window.inspector
        if inspector.topLevelItemCount() != len(self._inspector_rows):
            return
        width = max(120, inspector.viewport().width())
        character = max(1, inspector.fontMetrics().averageCharWidth())
        metrics = inspector.fontMetrics()
        # measured on the unwrapped labels: the items may hold wrapped ones
        natural = max(
            (metrics.horizontalAdvance(key) for key, _ in self._inspector_rows), default=0
        )
        label_width = min(natural + 24, int(width * self.LABEL_SHARE))
        inspector.setColumnWidth(0, label_width)
        label_chars = max(12, (label_width - 12) // character)
        value_chars = max(16, (width - label_width - 16) // character)
        for index, (key, value) in enumerate(self._inspector_rows):
            item = inspector.topLevelItem(index)
            item.setText(0, textwrap.fill(key, label_chars, break_long_words=True))
            item.setText(1, textwrap.fill(value, value_chars, break_long_words=True) or value)


class _OnResize(QObject):
    """Calls back when a widget is resized (the inspector re-wraps its text)."""

    def __init__(self, widget, callback):
        super().__init__(widget)
        self._callback = callback
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Resize:
            self._callback()
        return False
