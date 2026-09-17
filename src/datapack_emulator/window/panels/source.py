"""The source view's text widget: line numbers, breakpoint dots and the line
the debugger stopped on.

``window.ui`` declares ``sourceEdit`` as this class (a custom widget), so the
gutter can use ``QPlainTextEdit``'s protected geometry methods.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QToolTip, QWidget

BREAKPOINT_COLOUR = QColor("#d32f2f")
DISABLED_COLOUR = QColor("#bdbdbd")
STOPPED_COLOUR = QColor("#fff59d")
GUTTER_BACKGROUND = QColor("#f3f3f3")
NUMBER_COLOUR = QColor("#9e9e9e")
#: a refused line, in the gutter: amber, so it is not read as a breakpoint
MARK_COLOUR = QColor("#ef8c00")


class _Gutter(QWidget):
    def __init__(self, editor: SourceEdit):
        super().__init__(editor)
        self.editor = editor
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("click to set or remove a breakpoint (F9)")

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.editor.paint_gutter(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt API)
        event.accept()  # the press already toggled; a second toggle would undo it

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.LeftButton:
            line = self.editor.line_at(int(event.position().y()))
            if line:
                self.editor.gutter_clicked.emit(line)


class SourceEdit(QPlainTextEdit):
    #: a line number (1-based) whose gutter was clicked
    gutter_clicked = Signal(int)
    #: the line so far, when completions are wanted for it (Ctrl+Space, typing)
    completion_wanted = Signal(str, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: line -> enabled, for the dots
        self.breakpoints: dict[int, bool] = {}
        #: the line the debugger stopped on (0: none)
        self.stopped_line = 0
        #: line -> why the version refuses it (underlined while editing)
        self.line_marks: dict[int, str] = {}
        self._gutter = _Gutter(self)
        #: fed by the editor controller (analysis.completion); None until then
        self.completer: QCompleter | None = None
        self.blockCountChanged.connect(self._update_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_width()

    # -- completion ----------------------------------------------------------

    def set_completer(self, completer: QCompleter) -> None:
        completer.setWidget(self)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.activated.connect(self.insert_completion)
        self.completer = completer

    def word_start(self) -> int:
        """Where the word under the cursor starts, in the line."""
        cursor = self.textCursor()
        line = cursor.block().text()[: cursor.positionInBlock()]
        start = len(line)
        while start > 0 and line[start - 1] not in " \t[]{},=\"'":
            start -= 1
        return start

    def insert_completion(self, text: str) -> None:
        cursor = self.textCursor()
        typed = cursor.positionInBlock() - self.word_start()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, typed)
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def ask_completions(self) -> None:
        """Ask for what could be typed here (the controller answers)."""
        cursor = self.textCursor()
        self.completion_wanted.emit(cursor.block().text(), cursor.positionInBlock())

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        popup = self.completer.popup() if self.completer is not None else None
        if (
            popup is not None
            and popup.isVisible()
            and event.key()
            in (
                Qt.Key_Enter,
                Qt.Key_Return,
                Qt.Key_Tab,
                Qt.Key_Escape,
            )
        ):
            event.ignore()  # the popup takes it (Escape closes it)
            return
        if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
            self.ask_completions()
            return
        super().keyPressEvent(event)
        if self.completer is not None and popup is not None and popup.isVisible():
            self.ask_completions()  # keep the list in step with what is typed

    # -- state ---------------------------------------------------------------

    def set_breakpoints(self, lines: dict[int, bool]) -> None:
        self.breakpoints = dict(lines)
        self._gutter.update()

    def set_stopped_line(self, line: int) -> None:
        self.stopped_line = line
        self._update_selections()

    def set_line_marks(self, marks: dict[int, str]) -> None:
        """Underline lines (1-based) with a reason shown when hovered."""
        self.line_marks = dict(marks)
        self._update_selections()

    def _update_selections(self) -> None:
        selections = []
        document = self.document()
        if self.stopped_line > 0:
            block = document.findBlockByNumber(self.stopped_line - 1)
            if block.isValid():
                selection = QTextEdit.ExtraSelection()
                selection.format.setBackground(STOPPED_COLOUR)
                selection.format.setProperty(QTextFormat.FullWidthSelection, True)
                selection.cursor = QTextCursor(block)
                selections.append(selection)
        for line in sorted(self.line_marks):
            block = document.findBlockByNumber(line - 1)
            if not block.isValid() or not block.text().strip():
                continue
            selection = QTextEdit.ExtraSelection()
            mark = QTextCharFormat()
            mark.setUnderlineStyle(QTextCharFormat.WaveUnderline)
            mark.setUnderlineColor(MARK_COLOUR)
            selection.format = mark
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selections.append(selection)
        self.setExtraSelections(selections)
        self._gutter.update()

    def viewportEvent(self, event) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.ToolTip and self.line_marks:
            line = self.cursorForPosition(event.pos()).blockNumber() + 1
            if line in self.line_marks:
                QToolTip.showText(event.globalPos(), self.line_marks[line], self)
            else:  # no blurb over every other line
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    # -- geometry ----------------------------------------------------------

    def gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits + 6

    def line_at(self, y: int) -> int:
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid():
            bottom = top + self.blockBoundingRect(block).height()
            if top <= y < bottom:
                return block.blockNumber() + 1
            block = block.next()
            top = bottom
        return 0

    def _update_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_width()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        area = self.contentsRect()
        self._gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.setFont(self.font())  # the editor's (monospace) font, as measured
        painter.fillRect(event.rect(), GUTTER_BACKGROUND)
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        height = self.fontMetrics().height()
        width = self._gutter.width()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + self.blockBoundingRect(block).height()
            if block.isVisible() and bottom >= event.rect().top():
                line = block.blockNumber() + 1
                if line == self.stopped_line:
                    painter.fillRect(QRect(0, int(top), width, height), STOPPED_COLOUR)
                elif line in self.line_marks:
                    # two pixels clear of the line number
                    painter.fillRect(QRect(width - 6, int(top), 3, height), MARK_COLOUR)
                if line in self.breakpoints:
                    painter.setRenderHint(QPainter.Antialiasing)
                    painter.setPen(Qt.NoPen)
                    enabled = self.breakpoints[line]
                    painter.setBrush(BREAKPOINT_COLOUR if enabled else DISABLED_COLOUR)
                    size = min(10, height - 2)
                    painter.drawEllipse(3, int(top) + (height - size) // 2, size, size)
                painter.setPen(NUMBER_COLOUR)
                painter.drawText(
                    0, int(top), width - 4, height, Qt.AlignRight | Qt.AlignVCenter, str(line)
                )
            block = block.next()
            top = bottom
        painter.end()
