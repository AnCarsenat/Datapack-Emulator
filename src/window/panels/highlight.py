"""Syntax highlighting for the source view: ``.mcfunction`` and JSON."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)

PALETTE = {
    "comment": "#7a8b99",
    "command": "#0b5fa5",
    "subcommand": "#7b1fa2",
    "selector": "#c0392b",
    "resource": "#00796b",
    "number": "#b8860b",
    "string": "#2e7d32",
    "nbt": "#795548",
    "macro": "#e67e22",
    "key": "#0b5fa5",
    "literal": "#7b1fa2",
}


def _format(colour: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    text_format = QTextCharFormat()
    text_format.setForeground(QColor(colour))
    if bold:
        text_format.setFontWeight(QFont.Bold)
    text_format.setFontItalic(italic)
    return text_format


#: ``execute`` subcommands and other structural keywords
KEYWORDS = [
    "align",
    "anchored",
    "as",
    "at",
    "facing",
    "if",
    "in",
    "on",
    "positioned",
    "rotated",
    "run",
    "store",
    "summon",
    "unless",
    "block",
    "blocks",
    "biome",
    "data",
    "dimension",
    "entity",
    "function",
    "items",
    "loaded",
    "predicate",
    "score",
    "result",
    "success",
    "add",
    "remove",
    "set",
    "merge",
    "append",
    "prepend",
    "insert",
    "get",
    "enable",
    "operation",
    "players",
    "objectives",
    "with",
    "matches",
]


class McFunctionHighlighter(QSyntaxHighlighter):
    """Colours commands, selectors, resource locations, NBT, macros and comments."""

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.comment = _format(PALETTE["comment"], italic=True)
        self.command = _format(PALETTE["command"], bold=True)
        self.subcommand = _format(PALETTE["subcommand"])
        self.selector = _format(PALETTE["selector"])
        self.resource = _format(PALETTE["resource"])
        self.number = _format(PALETTE["number"])
        self.string = _format(PALETTE["string"])
        self.nbt = _format(PALETTE["nbt"])
        self.macro = _format(PALETTE["macro"], bold=True)

        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = [
            (QRegularExpression(r"\"(\\.|[^\"\\])*\""), self.string),
            (QRegularExpression(r"@[sparen](\[[^\]]*\])?"), self.selector),
            (QRegularExpression(r"\$\([A-Za-z0-9_.+-]+\)"), self.macro),
            (QRegularExpression(r"\b#?[a-z0-9_.-]+:[a-z0-9_./-]+"), self.resource),
            (QRegularExpression(r"(?<![\w.])[-~^]?\d+(\.\d+)?[bslfd]?\b"), self.number),
            (QRegularExpression(r"\{[^{}]*\}"), self.nbt),
            (
                QRegularExpression(r"\b(" + "|".join(KEYWORDS) + r")\b"),
                self.subcommand,
            ),
        ]
        self.command_re = QRegularExpression(r"^\s*\$?/?([a-z_]+)")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        stripped = text.strip()
        if stripped.startswith("#"):
            self.setFormat(0, len(text), self.comment)
            return
        if stripped.startswith("$"):
            self.setFormat(text.index("$"), 1, self.macro)

        for pattern, text_format in self.rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)

        match = self.command_re.match(text)
        if match.hasMatch():
            self.setFormat(match.capturedStart(1), match.capturedLength(1), self.command)


class JsonHighlighter(QSyntaxHighlighter):
    """Keys, strings, numbers and literals — enough for pack.mcmeta and tags."""

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self.key = _format(PALETTE["key"], bold=True)
        self.string = _format(PALETTE["string"])
        self.number = _format(PALETTE["number"])
        self.literal = _format(PALETTE["literal"])
        self.rules = [
            (QRegularExpression(r"\"(\\.|[^\"\\])*\"\s*(?=:)"), self.key),
            (QRegularExpression(r"\"(\\.|[^\"\\])*\"(?!\s*:)"), self.string),
            (QRegularExpression(r"\b-?\d+(\.\d+)?([eE][+-]?\d+)?\b"), self.number),
            (QRegularExpression(r"\b(true|false|null)\b"), self.literal),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        for pattern, text_format in self.rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


def highlighter_for(path: Path, document: QTextDocument) -> QSyntaxHighlighter | None:
    """Pick a highlighter from the file extension."""
    suffix = path.suffix.lower()
    if suffix == ".mcfunction":
        return McFunctionHighlighter(document)
    if suffix in (".json", ".mcmeta"):
        return JsonHighlighter(document)
    return None
