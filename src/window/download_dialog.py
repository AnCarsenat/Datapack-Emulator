"""The client-jar download popup: layout from ``download.ui``, work on a thread.

The dialog opens asking for confirmation, then shows a progress bar, the bytes
received, the transfer rate and a cancel button while
:meth:`VanillaLibrary.download` streams the jar on a worker thread.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton

from src.emulator.vanilla import DownloadCancelled, VanillaLibrary
from src.window.panels import load_ui_into

UI_FILE = Path(__file__).with_name("download.ui")

MIB = 1024 * 1024


class DownloadWorker(QObject):
    """Runs one download off the UI thread and reports through signals."""

    status = Signal(str)
    #: ``(bytes received, bytes expected)`` — object, so sizes past 2 GiB stay safe
    progressed = Signal(object, object)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, library: VanillaLibrary, version_id: str):
        super().__init__()
        self.library = library
        self.version_id = version_id
        self._cancel_requested = False

    def request_cancel(self) -> None:
        # read from the worker thread between chunks; a plain bool is enough
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            path = self.library.download(
                self.version_id,
                progress=self.status.emit,
                on_bytes=self.progressed.emit,
                cancelled=lambda: self._cancel_requested,
            )
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:  # network, disk, checksum: all shown to the user
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit(str(path))


class DownloadDialog(QDialog):
    """Confirm, download with progress, and hand back the jar path."""

    def __init__(self, library: VanillaLibrary, version_id: str, parent=None):
        super().__init__(parent)
        self.library = library
        self.version_id = version_id
        #: set when the download finished
        self.path: Path | None = None
        #: set when it failed
        self.error: str | None = None
        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._started_at = 0.0

        load_ui_into(self, UI_FILE)
        find = self.findChild
        self.label_title: QLabel = find(QLabel, "labelTitle")
        self.label_status: QLabel = find(QLabel, "labelStatus")
        self.label_bytes: QLabel = find(QLabel, "labelBytes")
        self.progress: QProgressBar = find(QProgressBar, "progressBar")
        self.button_download: QPushButton = find(QPushButton, "buttonDownload")
        self.button_cancel: QPushButton = find(QPushButton, "buttonCancel")

        self.setWindowTitle(f"Download Minecraft {version_id}")
        self.label_title.setText(f"Download the Minecraft {version_id} client jar")
        self.label_status.setText(
            f"Fetched from Mojang (about 25–35 MB) into {library.cache_dir}. "
            "Its SHA-1 is checked before it is used."
        )
        self.button_download.clicked.connect(self.start)
        self.button_cancel.clicked.connect(self.cancel)

    # -- flow -------------------------------------------------------------

    def run(self) -> Path | None:
        """Show the dialog; returns the jar path, or ``None`` if cancelled/failed."""
        self.exec()
        return self.path

    def start(self) -> None:
        self.button_download.setEnabled(False)
        self.button_download.hide()
        self.progress.setRange(0, 0)  # busy until the size is known
        self.label_status.setText("contacting Mojang…")
        self._started_at = time.monotonic()

        self._thread = QThread(self)
        self._worker = DownloadWorker(self.library, self.version_id)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.status.connect(self.label_status.setText)
        self._worker.progressed.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        for signal in (self._worker.succeeded, self._worker.failed, self._worker.cancelled):
            signal.connect(self._thread.quit)
        self._thread.start()

    def cancel(self) -> None:
        if self._worker is None:
            self.reject()  # nothing started yet
            return
        self._worker.request_cancel()
        self.button_cancel.setEnabled(False)
        self.label_status.setText("cancelling…")

    # -- worker callbacks -------------------------------------------------

    def _on_progress(self, received: int, total: int) -> None:
        elapsed = max(time.monotonic() - self._started_at, 1e-6)
        rate = received / elapsed / MIB
        if total:
            if self.progress.maximum() != total // 1024:
                self.progress.setRange(0, max(total // 1024, 1))
            self.progress.setValue(received // 1024)
            self.label_bytes.setText(
                f"{received / MIB:6.1f} / {total / MIB:.1f} MiB   {rate:4.1f} MiB/s"
            )
        else:
            self.label_bytes.setText(f"{received / MIB:6.1f} MiB   {rate:4.1f} MiB/s")

    def _on_succeeded(self, path: str) -> None:
        self.path = Path(path)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._finish()
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.label_status.setText(f"download failed — {message}")
        self.button_cancel.setText("close")
        self.button_cancel.setEnabled(True)
        self.button_cancel.clicked.disconnect()
        self.button_cancel.clicked.connect(self.reject)
        self._finish()

    def _on_cancelled(self) -> None:
        self._finish()
        self.reject()

    def _finish(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._worker = None

    # -- closing ----------------------------------------------------------

    def reject(self) -> None:
        """Escape and the window's close button cancel a running download first."""
        if self._worker is not None:
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._worker is not None:
            self.cancel()
            event.ignore()
            return
        super().closeEvent(event)
