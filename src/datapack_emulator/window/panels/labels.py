"""A one-line label that elides its text instead of widening the window.

A plain ``QLabel`` is never narrower than its text, so a long file path held
the whole central area (and the window) wider than the screen and squeezed
the docks. ``window.ui`` declares the long status lines as this class (a
custom widget): it shows as much as fits, with ``…`` where text was cut, and
the whole text as its tooltip when it is cut. ``text()`` is still the whole
text.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

#: the narrowest the label asks for, by default: a few characters and the ellipsis
MINIMUM_WIDTH = 40


class ElidedLabel(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: the whole text; None until set from Python (the .ui sets it in C++)
        self._full: str | None = None
        self._mode = Qt.TextElideMode.ElideRight
        self._minimum = MINIMUM_WIDTH
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        """``ElideMiddle`` keeps both ends of a path: its root and its file name."""
        self._mode = mode
        self._elide()

    def set_minimum_text_width(self, width: int) -> None:
        """Shorter text than this is never cut; longer text is cut down to it."""
        self._minimum = width
        self.updateGeometry()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = text
        self._elide()
        self.updateGeometry()

    def text(self) -> str:
        return self._full if self._full is not None else super().text()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        width = min(self.sizeHint().width(), self._minimum)
        return QSize(width, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        width = self.fontMetrics().horizontalAdvance(self.text())
        margins = self.contentsMargins()
        width += margins.left() + margins.right() + 2 * self.margin()
        return QSize(width, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        full = self.text()
        self._full = full
        width = self.contentsRect().width() - 2 * self.margin()
        shown = self.fontMetrics().elidedText(full, self._mode, max(width, 0))
        super().setText(shown)
        self.setToolTip(full if shown != full else "")
