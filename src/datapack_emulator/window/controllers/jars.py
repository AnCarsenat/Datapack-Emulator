"""Base-game client jars: auto-pick, load by hand, download."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from datapack_emulator.emulator.runtime.output import LogLevel
from datapack_emulator.emulator.vanilla import VanillaAssets
from datapack_emulator.window.controllers.base import Controller
from datapack_emulator.window.download_dialog import DownloadDialog


class JarController(Controller):
    def use(self, assets: VanillaAssets | None) -> None:
        """Adopt base-game assets: registries to check ids, strings to quote."""
        window = self.window
        previous = window.vanilla.jar_path if window.vanilla else None
        window.vanilla = assets
        if (assets.jar_path if assets else None) != previous:
            window.projects.mark_modified()  # the project remembers the jar
        if assets is None:
            window.vanilla_label.setText("no client jar")
            window.vanilla_label.setToolTip(
                "file > load client jar to check ids against the base game"
            )
        else:
            window.vanilla_label.setText(f"client.jar {assets.version_id}")
            window.vanilla_label.setToolTip(assets.summary)
            window.output.app(f"vanilla assets: {assets.summary}")
        window.datapacks.rebuild_emulator()

    def autoload(self) -> None:
        """Pick up a client jar for the current version if one is installed."""
        window = self.window
        try:
            assets = window.library.load(window.version.id, allow_download=False)
        except Exception as exc:  # a broken jar must not stop the app
            window.output.app(f"cannot read client jar: {exc}", level=LogLevel.ERROR)
            return
        if assets is not None:
            self.use(assets)
        elif window.vanilla is not None and window.vanilla.version_id != window.version.id:
            window.output.app(
                f"client jar loaded is {window.vanilla.version_id}, emulating {window.version.id}",
                level=LogLevel.WARNING,
            )

    def load_by_hand(self) -> None:
        library = self.window.library
        start = library.cache_dir if library.cache_dir.is_dir() else Path.home()
        chosen, _ = QFileDialog.getOpenFileName(
            self.window, "select a Minecraft client.jar", str(start), "Minecraft client (*.jar)"
        )
        if not chosen:
            return
        try:
            self.use(library.load_jar(Path(chosen)))
        except Exception as exc:
            QMessageBox.warning(self.window, "client jar", f"cannot read {chosen}:\n{exc}")

    def download(self) -> None:
        """Popup: confirm, then download with a progress bar and a cancel button."""
        window = self.window
        version = window.version
        existing = window.library.find(version.id)
        if existing is not None:
            self.status(f"{version.id} client jar already installed: {existing}")
            self.use(window.library.load_jar(existing))
            return
        dialog = DownloadDialog(window.library, version.id, parent=window)
        path = dialog.run()
        if path is not None:
            window.output.app(f"downloaded {path}")
            self.use(window.library.load_jar(path))
            self.status(f"client jar ready: {path}")
        elif dialog.error:
            window.output.app(f"client jar download failed: {dialog.error}", level=LogLevel.ERROR)
            self.status("client jar download failed")
        else:
            self.status("client jar download cancelled")
