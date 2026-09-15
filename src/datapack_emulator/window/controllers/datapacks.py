"""Importing, loading and showing a datapack; the explorer and the inspector."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QFileDialog, QTreeWidgetItem

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.datapack import Datapack
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
)

#: statuses the server shows as "made for an older/newer version"
INCOMPATIBLE_STATUSES = ("too_old", "too_new")
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

    # -- loading ----------------------------------------------------------

    def import_(self) -> None:
        start = PATHS.SAMPLES if PATHS.SAMPLES.is_dir() else Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self.window, "select a datapack folder (the one holding pack.mcmeta)", str(start)
        )
        if chosen:
            self.load(Path(chosen))

    def open_default(self) -> None:
        """Cold start: open the first pack in samples/ so there is something to run."""
        sample = default_sample()
        if sample is None:
            self.status("no datapack loaded — file > import datapack")
            return
        self.load(sample)
        self.window.output.app(
            f"opened the sample datapack {sample.name} (file > import to change)"
        )

    def load(self, path: Path, keep_project: bool = False, keep_version: bool = False) -> None:
        window = self.window
        window.log_view.clear()
        datapack = Datapack.load(path)
        window.datapack = datapack
        window.output.app(f"loaded {datapack.path}")
        for error in datapack.errors:
            window.output.app(error, level=LogLevel.ERROR)

        if not keep_version and (not keep_project or not window.project.version):
            # reloading keeps the version on screen and a project remembers its
            # own; a freshly imported pack gets its newest declared release
            self.select_pack_version(datapack)
        window.jars.autoload()
        self.rebuild_emulator()
        window.call_graph = None
        self.show(datapack)
        if not keep_project:
            if window.project.path is None:
                window.project.name = datapack.name
            window.project.datapack = datapack.path
        window.projects.refresh_title()
        self.status(
            f"{datapack.name}: {len(datapack.namespaces)} namespace(s), "
            f"{len(datapack.functions)} function(s), pack_format {datapack.pack_format} "
            f"({datapack.minecraft_version})"
        )

    def reload(self) -> None:
        if self.window.datapack is None:
            self.status("nothing to reload")
            return
        self.load(self.window.datapack.path, keep_project=True, keep_version=True)

    def select_pack_version(self, datapack: Datapack) -> None:
        """Default the version combo to the newest release the pack declares.

        A multi-version pack's pack_format is often its oldest target (hat_v2:
        5, but supported up to 121), so it is only the fallback. Pre-releases
        and versions whose server would mark the pack incompatible are skipped.
        """
        declared = datapack.declared_versions()
        usable = [
            version
            for version in declared
            if version.stable
            and datapack.compatibility(version).status not in INCOMPATIBLE_STATUSES
        ]
        if usable:
            target = usable[-1]
        elif declared:
            target = declared[-1]
        else:
            target = versions.closest_to_pack_format(datapack.pack_format)
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
        window.tick_label.setText("idle")

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
            window.pack_label.setText("no datapack loaded")
            self.fill_inspector([])
            return
        status = datapack.compatibility(window.version).status
        window.pack_label.setText(
            f"{datapack.name} — emulating {window.version.id}" + COMPATIBILITY_NOTES.get(status, "")
        )
        self.fill_inspector(describe_datapack(datapack, window.version))

    def on_tree_clicked(self, index: QModelIndex) -> None:
        window = self.window
        path_value = index.data(PATH_ROLE)
        resource_id = index.data(RESOURCE_ROLE)
        resource = self.find_resource(resource_id) if resource_id else None

        if resource is not None:
            self.fill_inspector(describe_resource(resource, window.version))
        elif window.datapack is not None:
            self.fill_inspector(describe_datapack(window.datapack, window.version))

        if not path_value:
            return
        path = Path(path_value)
        if path.is_file():
            window.navigation.show_source(path)

    def find_resource(self, resource_id: str) -> Resource | None:
        datapack = self.window.datapack
        if datapack is None:
            return None
        for layer in [datapack.base, *datapack.overlays]:
            for namespace in layer.namespaces.values():
                for resource in namespace.resources():
                    if resource.id == resource_id:
                        return resource
        return None

    def fill_inspector(self, rows: list[tuple[str, str]]) -> None:
        inspector = self.window.inspector
        inspector.clear()
        for key, value in rows:
            inspector.addTopLevelItem(QTreeWidgetItem([key, str(value)]))
        inspector.resizeColumnToContents(0)
