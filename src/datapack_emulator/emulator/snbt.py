"""A strict SNBT reader, in the two grammars the game has had.

``read_snbt(text, modern=True)`` reads one SNBT value the way the game's own
parser does and raises :class:`SnbtError` where the game would refuse it.

* ``modern`` (1.21.5 and later, ``net.minecraft.nbt.SnbtGrammar``): lists may
  mix types; numbers take ``_`` separators, ``0x``/``0b`` prefixes, a
  signedness (``5ub``, ``-1sb``) and may start with ``.`` (``.5``); a decimal
  integer cannot have a leading zero; quoted strings take ``\\n``, ``\\t``,
  ``\\xHH``, ``\\uHHHH``, ``\\UHHHHHHHH``, ``\\N{name}`` and the other
  escapes; an unquoted value cannot start with ``0-9 + - .``; ``true`` and
  ``false`` are bytes; ``bool(…)`` and ``uuid("…")`` are operations; a key
  cannot be empty; trailing commas are allowed.
* legacy (``TagParser`` before 1.21.5): a list holds one type; an unquoted
  token is a number only when it fits the number patterns and a string
  otherwise; strings escape only ``\\`` and the quote.

The common SNBT path (:func:`datapack_emulator.emulator.common.parse_snbt`)
falls back to this reader for what its JSON translation cannot handle.
"""

from __future__ import annotations

import re
import unicodedata
import uuid as uuid_module
from typing import Any

from datapack_emulator.emulator.common import NbtArray, nbt_type, typed_number

_UNQUOTED = re.compile(r"[0-9A-Za-z_\-.+]+")
_WHITESPACE = " \t\n\r"

# legacy TagParser patterns (case-insensitive)
_LEGACY_NUMBERS = (
    (re.compile(r"[-+]?(?:[0-9]+[.]|[0-9]*[.][0-9]+)(?:e[-+]?[0-9]+)?d", re.I), "d"),
    (re.compile(r"[-+]?(?:[0-9]+[.]?|[0-9]*[.][0-9]+)(?:e[-+]?[0-9]+)?f", re.I), "f"),
    (re.compile(r"[-+]?(?:0|[1-9][0-9]*)b", re.I), "b"),
    (re.compile(r"[-+]?(?:0|[1-9][0-9]*)l", re.I), "l"),
    (re.compile(r"[-+]?(?:0|[1-9][0-9]*)s", re.I), "s"),
    (re.compile(r"[-+]?(?:0|[1-9][0-9]*)"), "i"),
    (re.compile(r"[-+]?(?:[0-9]+[.]|[0-9]*[.][0-9]+)(?:e[-+]?[0-9]+)?", re.I), "d"),
)
_BITS = {"b": 8, "s": 16, "i": 32, "l": 64}
#: unsigned and signed ranges of each integer type
_RANGES = {kind: (0, 2**bits - 1) for kind, bits in _BITS.items()}
_SIGNED_RANGES = {kind: (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) for kind, bits in _BITS.items()}

# modern number pieces
_DIGITS = r"[0-9](?:[0-9_]*[0-9])?"
_MODERN_FLOAT = re.compile(
    rf"[-+]?(?:{_DIGITS}\.(?:{_DIGITS})?|\.{_DIGITS}|{_DIGITS})"
    rf"(?:[eE][-+]?{_DIGITS})?([fFdD])?"
)
_MODERN_INTEGER = re.compile(
    r"([-+]?)(?:0[xX]([0-9a-fA-F](?:[0-9a-fA-F_]*[0-9a-fA-F])?)"
    r"|0[bB]([01](?:[01_]*[01])?)"
    rf"|({_DIGITS}))"
    r"([sSuU]?[bBsSiIlL])?"
)


class SnbtError(ValueError):
    """The text is not SNBT the game reads."""


class _Reader:
    def __init__(self, text: str, modern: bool):
        self.text = text
        self.index = 0
        self.modern = modern

    # -- low level ---------------------------------------------------------

    def fail(self, message: str) -> SnbtError:
        return SnbtError(f"{message} at {self.index}: {self.text[self.index : self.index + 20]!r}")

    def skip(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in _WHITESPACE:
            self.index += 1

    def peek(self) -> str:
        self.skip()
        return self.text[self.index] if self.index < len(self.text) else ""

    def expect(self, char: str) -> None:
        if self.peek() != char:
            raise self.fail(f"expected {char!r}")
        self.index += 1

    def unquoted(self) -> str:
        self.skip()
        match = _UNQUOTED.match(self.text, self.index)
        if not match:
            return ""
        self.index = match.end()
        return match.group()

    # -- values ------------------------------------------------------------

    def value(self) -> Any:
        char = self.peek()
        if not char:
            raise self.fail("expected a value")
        if char == "{":
            return self.compound()
        if char == "[":
            return self.list_or_array()
        if char in "\"'":
            return self.quoted()
        return self.modern_scalar() if self.modern else self.legacy_scalar()

    def compound(self) -> dict[str, Any]:
        self.expect("{")
        result: dict[str, Any] = {}
        while self.peek() != "}":
            if self.peek() and self.peek() in "\"'":
                key = self.quoted()
            else:
                key = self.unquoted()
                if not key:
                    raise self.fail("expected a key")
            if self.modern and not key:
                raise self.fail("empty key")
            self.expect(":")
            result[key] = self.value()
            if self.peek() != ",":
                break
            self.index += 1
        self.expect("}")
        return result

    def list_or_array(self) -> Any:
        self.expect("[")
        head = self.text[self.index : self.index + 2]
        if len(head) == 2 and head[0] in "BIL" and head[1] == ";":
            self.index += 2
            kind = head[0]
            items = self.entries()
            for item in items:
                if isinstance(item, bool):
                    continue
                if not isinstance(item, int) or (
                    not self.modern and nbt_type(item) != kind.lower()
                ):
                    raise self.fail(f"invalid element in a [{kind};] array")
            return NbtArray((int(item) for item in items), kind)
        items = self.entries()
        if not self.modern and len({_list_kind(item) for item in items}) > 1:
            raise self.fail("a list holds one type before 1.21.5")
        return items

    def entries(self) -> list[Any]:
        items: list[Any] = []
        while self.peek() != "]":
            items.append(self.value())
            if self.peek() != ",":
                break
            self.index += 1
        self.expect("]")
        return items

    def quoted(self) -> str:
        self.skip()
        quote = self.text[self.index]
        self.index += 1
        out: list[str] = []
        while True:
            if self.index >= len(self.text):
                raise self.fail("unclosed string")
            char = self.text[self.index]
            self.index += 1
            if char == quote:
                return "".join(out)
            if char != "\\":
                out.append(char)
                continue
            if self.index >= len(self.text):
                raise self.fail("unclosed string")
            escape = self.text[self.index]
            self.index += 1
            if escape in "\\\"'" and (self.modern or escape in ("\\", quote)):
                out.append(escape)
            elif not self.modern:
                raise self.fail("invalid escape")
            elif escape in "bstnfr":
                out.append(
                    {"b": "\b", "s": " ", "t": "\t", "n": "\n", "f": "\f", "r": "\r"}[escape]
                )
            elif escape in "xuU":
                size = {"x": 2, "u": 4, "U": 8}[escape]
                digits = self.text[self.index : self.index + size]
                if len(digits) != size or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise self.fail("expected a hex escape")
                self.index += size
                try:
                    out.append(chr(int(digits, 16)))
                except (ValueError, OverflowError):
                    raise self.fail("invalid code point") from None
            elif escape == "N":
                match = re.compile(r"\{([-a-zA-Z0-9 ]+)\}").match(self.text, self.index)
                if not match:
                    raise self.fail("invalid character name")
                try:
                    out.append(unicodedata.lookup(match.group(1)))
                except KeyError:
                    raise self.fail("invalid character name") from None
                self.index = match.end()
            else:
                raise self.fail("invalid escape")

    def legacy_scalar(self) -> Any:
        token = self.unquoted()
        if not token:
            raise self.fail("expected a value")
        for pattern, kind in _LEGACY_NUMBERS:
            if pattern.fullmatch(token):
                body = token[:-1] if token[-1].lower() in "bslfd" and kind != "i" else token
                if kind in "fd":
                    return typed_number(float(body), kind)
                number = int(body)
                low, high = _SIGNED_RANGES[kind]
                if low <= number <= high:
                    return typed_number(number, kind)
                break  # out of range: the game reads it as a string
        if token.lower() == "true":
            return True
        if token.lower() == "false":
            return False
        return token

    def modern_scalar(self) -> Any:
        start = self.index
        char = self.text[start]
        if char in "+-.0123456789":
            return self.modern_number()
        name = self.unquoted()
        if not name:
            raise self.fail("expected a value")
        if self.peek() == "(":
            return self.operation(name, start)
        if name == "true":
            return True
        if name == "false":
            return False
        return name

    def modern_number(self) -> Any:
        start = self.index
        integer = _MODERN_INTEGER.match(self.text, start)
        floating = _MODERN_FLOAT.match(self.text, start)
        # the longer reading wins: 1.5 is a float, 0x1F an integer
        if floating and (not integer or floating.end() > integer.end()):
            body = floating.group().replace("_", "")
            suffix = floating.group(1)
            is_float = suffix or any(mark in body for mark in ".eE")
            if is_float:
                value = float(body.rstrip("fFdD"))
                if value in (float("inf"), float("-inf")):
                    raise self.fail("infinity is not allowed")
                self.index = floating.end()
                self.end_of_token()
                return typed_number(value, (suffix or "d").lower())
        if not integer:
            raise self.fail("expected a number")
        sign, hexa, binary, decimal, suffix = integer.groups()
        if decimal is not None and len(decimal) > 1 and decimal[0] == "0":
            raise self.fail("leading zero not allowed")
        base, digits = (16, hexa) if hexa else (2, binary) if binary else (10, decimal)
        number = int(digits.replace("_", ""), base)
        if sign == "-":
            number = -number
        suffix = (suffix or "").lower()
        kind = suffix[-1] if suffix else "i"
        signedness = suffix[0] if len(suffix) == 2 else ""
        if signedness == "u" and number < 0:
            raise self.fail("expected a non-negative number")
        # decimal numbers are signed unless marked u; hex and binary unsigned unless marked s
        signed = signedness == "s" or (signedness == "" and base == 10)
        low, high = _SIGNED_RANGES[kind] if signed else _RANGES[kind]
        if not low <= number <= high:
            raise self.fail("number out of range")
        if number > _SIGNED_RANGES[kind][1]:  # stored as the two's complement
            number -= _RANGES[kind][1] + 1
        self.index = integer.end()
        self.end_of_token()
        return typed_number(number, kind)

    def end_of_token(self) -> None:
        if self.index < len(self.text) and _UNQUOTED.match(self.text[self.index]):
            raise self.fail("unexpected character after a number")

    def operation(self, name: str, start: int) -> Any:
        self.expect("(")
        arguments = []
        while self.peek() != ")":
            arguments.append(self.value())
            if self.peek() != ",":
                break
            self.index += 1
        self.expect(")")
        if name == "bool" and len(arguments) == 1:
            value = arguments[0]
            if isinstance(value, (bool, int, float)):
                return bool(value)
            raise self.fail("bool() takes a number or a boolean")
        if name == "uuid" and len(arguments) == 1 and isinstance(arguments[0], str):
            try:
                parsed = uuid_module.UUID(arguments[0])
            except ValueError:
                raise self.fail("uuid() takes a UUID string") from None
            words = [(parsed.int >> shift) & 0xFFFFFFFF for shift in (96, 64, 32, 0)]
            return NbtArray(word - (1 << 32) if word >= 1 << 31 else word for word in words)
        self.index = start
        raise self.fail(f"no such operation {name}")


def _list_kind(value: Any) -> str:
    if isinstance(value, dict):
        return "compound"
    if isinstance(value, NbtArray):
        return "array" + value.kind
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    return nbt_type(value) or "number"


def read_snbt(text: str, modern: bool = True) -> Any:
    """One SNBT value (the whole of ``text``); :class:`SnbtError` when the
    game's parser (``modern``: 1.21.5+, else the older one) refuses it."""
    reader = _Reader(text, modern)
    value = reader.value()
    if reader.peek():
        raise reader.fail("trailing text")
    return value


def is_valid_snbt(text: str, modern: bool = True) -> bool:
    try:
        read_snbt(text, modern)
    except SnbtError:
        return False
    return True
