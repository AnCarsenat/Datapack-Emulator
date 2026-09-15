"""Parsing helpers shared by the model, the command parser and the runtime.

Nothing here knows about versions, Qt or the world state: it is pure text and
NBT plumbing.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger(__name__)

_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {value: key for key, value in _OPEN.items()}


# ---------------------------------------------------------------------------
# identifiers and text components
# ---------------------------------------------------------------------------


def normalise_id(value: str) -> str:
    """``load`` -> ``minecraft:load``; already-qualified ids pass through."""
    value = value.strip()
    return value if ":" in value else f"minecraft:{value}"


def normalise_tagged_id(value: str) -> str:
    """Same, but keeps a leading ``#`` for tag references."""
    value = value.strip()
    if value.startswith("#"):
        return "#" + normalise_id(value[1:])
    return normalise_id(value)


#: resolves the dynamic parts of a text component: ``(kind, value) -> text``,
#: with kind "score" (value: {"name", "objective"}) or "selector" (value: str)
TextResolver = Callable[[str, Any], str]


def flatten_text_component(component: Any, resolve: TextResolver | None = None) -> str:
    """Turn a text component (or a raw string) into plain text.

    ``score`` and ``selector`` parts need the world to read; without a
    ``resolve`` callback they render as nothing rather than as raw data.
    """
    if isinstance(component, (str, int, float)):
        return str(component)
    if isinstance(component, list):
        return "".join(flatten_text_component(part, resolve) for part in component)
    if not isinstance(component, dict):
        return ""
    if "text" in component:
        text = str(component["text"])
    elif "score" in component:
        text = resolve("score", component["score"]) if resolve else ""
    elif "selector" in component:
        text = resolve("selector", component["selector"]) if resolve else ""
    elif "translate" in component:
        text = str(component.get("fallback", component["translate"]))
    elif "keybind" in component:
        text = str(component["keybind"])
    else:
        text = ""
    extra = component.get("extra")
    if extra:
        text += flatten_text_component(extra, resolve)
    return text


def load_text_component(payload: str) -> Any | None:
    """Parse a ``tellraw``/``title`` component from JSON or (1.21.5+) SNBT.

    Returns ``None`` when neither parses, e.g. ``{text:"hi",color:"red"}`` and
    ``["",{text:"a"},'b']`` are both accepted.
    """
    payload = payload.strip()
    for candidate in (payload, None):
        try:
            return json.loads(candidate if candidate is not None else snbt_to_json(payload))
        except json.JSONDecodeError:
            continue
    return None


def parse_text_component(payload: str, resolve: TextResolver | None = None) -> str | None:
    """Plain text of a component, or ``None`` if it cannot be parsed."""
    component = load_text_component(payload)
    return None if component is None else flatten_text_component(component, resolve)


# ---------------------------------------------------------------------------
# tokenising
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Split on spaces, but keep ``[...]``, ``{...}`` and quoted strings whole."""
    tokens: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for char in text:
        if quote is not None:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char in _OPEN:
            stack.append(char)
            current.append(char)
            continue
        if char in _CLOSE:
            if stack and stack[-1] == _CLOSE[char]:
                stack.pop()
            current.append(char)
            continue
        if char.isspace() and not stack:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def split_arguments(body: str) -> list[tuple[str, str]]:
    """Split ``type=x,tag=!y,scores={a=1}`` into ``(key, value)`` pairs."""
    out: list[tuple[str, str]] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for char in body:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth -= 1
        if char == "," and depth == 0:
            out.append(split_pair("".join(current)))
            current = []
            continue
        current.append(char)
    if "".join(current).strip():
        out.append(split_pair("".join(current)))
    return [pair for pair in out if pair[0]]


def split_pair(text: str) -> tuple[str, str]:
    key, _, value = text.partition("=")
    return (key.strip(), value.strip())


# ---------------------------------------------------------------------------
# numbers, ranges, positions
# ---------------------------------------------------------------------------


def in_range(value: float, expression: str) -> bool:
    """Match a value against a vanilla range: ``1``, ``1..``, ``..5``, ``1..5``."""
    expression = expression.strip()
    try:
        if ".." not in expression:
            return float(value) == float(expression)
        low, _, high = expression.partition("..")
        above_low = not low or float(value) >= float(low)
        below_high = not high or float(value) <= float(high)
        return above_low and below_high
    except ValueError:
        return False


def distance_squared(a: Iterable[float], b: Iterable[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))


def volume_of(arguments: list[str]) -> float:
    """Rough block count for ``fill``/``clone`` from two absolute coordinates."""
    numbers: list[float] = []
    for argument in arguments[:6]:
        try:
            numbers.append(float(argument.lstrip("~^") or 0))
        except ValueError:
            return 1.0
    if len(numbers) < 6:
        return 1.0
    dx = abs(numbers[3] - numbers[0]) + 1
    dy = abs(numbers[4] - numbers[1]) + 1
    dz = abs(numbers[5] - numbers[2]) + 1
    return min(dx * dy * dz, 32768.0)


# ---------------------------------------------------------------------------
# SNBT and NBT paths
# ---------------------------------------------------------------------------

_SNBT_SUFFIX_RE = re.compile(r"^(-?\d+(?:\.\d+)?)[bslfdBSLFD]$")


def parse_snbt(text: str) -> dict[str, Any]:
    """Parse SNBT well enough for emulation: strip suffixes, quote bare keys."""
    text = text.strip()
    if not text.startswith("{"):
        return {}
    try:
        return json.loads(snbt_to_json(text))
    except json.JSONDecodeError:
        log.debug("cannot parse SNBT %s", text[:80])
        return {}


def snbt_to_json(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in "\"'":
            # SNBT allows both quote styles; JSON only double quotes
            end = index + 1
            while end < length and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            inner = text[index + 1 : end]
            if char == "'":
                inner = inner.replace("\\'", "'")
                out.append(json.dumps(inner))
            else:
                out.append(f'"{inner}"')
            index = end + 1
            continue
        if char.isalpha() or char == "_":
            end = index
            while end < length and (text[end].isalnum() or text[end] in "_.-+"):
                end += 1
            word = text[index:end]
            match = _SNBT_SUFFIX_RE.match(word)
            if word in ("true", "false", "null"):
                out.append(word)
            elif match:
                out.append(match.group(1))
            else:
                out.append(json.dumps(word))
            index = end
            continue
        if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
            end = index + 1
            while end < length and (text[end].isdigit() or text[end] in ".eE+-"):
                end += 1
            word = text[index:end]
            if end < length and text[end] in "bslfdBSLFD":
                end += 1
            out.append(word)
            index = end
            continue
        if char == "[" and text[index : index + 3] in ("[I;", "[B;", "[L;"):
            out.append("[")
            index += 3
            continue
        out.append(char)
        index += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(out))


def split_path(path: str) -> list[str]:
    """``a.b[0]."minecraft:x"`` -> ``["a", "b", "0", "minecraft:x"]``.

    Keys may be quoted (with ``"`` or ``'``) to hold dots, colons or spaces;
    list indexes are the numbers in brackets, and ``[{Slot:103b}]`` keeps the
    compound whole (``"{Slot:103b}"``) to pick the elements that match it.
    """
    parts: list[str] = []
    text = path.strip()
    index = 0
    current = ""
    while index < len(text):
        char = text[index]
        if char == "{" and not current:
            end = _compound_end(text, index)
            parts.append(text[index : end + 1])
            index = end + 1
            continue
        if char in "\"'":
            end = index + 1
            quoted = []
            while end < len(text) and text[end] != char:
                if text[end] == "\\" and end + 1 < len(text):
                    end += 1
                quoted.append(text[end])
                end += 1
            current += "".join(quoted)
            index = end + 1
            continue
        if char in ".[]":
            if current:
                parts.append(current)
            current = ""
        else:
            current += char
        index += 1
    if current:
        parts.append(current)
    return parts


def _compound_end(text: str, start: int) -> int:
    depth = 0
    quote: str | None = None
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == "\\":
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def nbt_matches(actual: Any, pattern: Any) -> bool:
    """Vanilla NBT matching: compounds match by subset, every pattern list
    element must match some actual element, other tags must be equal."""
    if isinstance(pattern, dict):
        return isinstance(actual, dict) and all(
            key in actual and nbt_matches(actual[key], value) for key, value in pattern.items()
        )
    if isinstance(pattern, list):
        return isinstance(actual, list) and all(
            any(nbt_matches(item, wanted) for item in actual) for wanted in pattern
        )
    return actual == pattern


def nbt_get(store: dict[str, Any], path: str) -> Any:
    return _get_parts(store, split_path(path))


def _get_parts(store: Any, parts: list[str]) -> Any:
    node: Any = store
    for part in parts:
        if isinstance(node, dict):
            if part.startswith("{"):  # a compound filter on the node itself
                if not nbt_matches(node, parse_snbt(part)):
                    return None
                continue
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            index = _list_index(node, part)
            if index is None:
                return None
            node = node[index]
        else:
            return None
    return node


def _list_index(node: list[Any], part: str) -> int | None:
    """A list index in a path (negative counts from the end, like vanilla), or
    the first element matching a compound filter such as ``{Slot:103b}``."""
    if part.startswith("{"):
        pattern = parse_snbt(part)
        return next((index for index, item in enumerate(node) if nbt_matches(item, pattern)), None)
    try:
        index = int(part)
    except ValueError:
        return None
    if index < 0:
        index += len(node)
    return index if 0 <= index < len(node) else None


def nbt_set(store: dict[str, Any], path: str, value: Any) -> bool:
    """Set ``path`` to ``value``, creating missing compounds on the way.

    List elements are addressed by index (``Pos[1]``, ``list[-1]``) or by a
    compound filter (``Items[{Slot:0b}]``), which reaches every matching
    element and, when none matches, adds the filter as a new element. An index
    that does not exist, or a path through something that is neither a
    compound nor a list, sets nothing and returns False.
    """
    parts = split_path(path)
    return bool(parts) and _set_parts(store, parts, value)


def _set_parts(node: Any, parts: list[str], value: Any) -> bool:
    part, rest = parts[0], parts[1:]
    if isinstance(node, dict):
        if part.startswith("{"):  # a filter on the compound itself
            return nbt_matches(node, parse_snbt(part)) and (
                _set_parts(node, rest, value) if rest else False
            )
        if not rest:
            node[part] = value
            return True
        child = node.get(part)
        if not isinstance(child, (dict, list)):
            child = node[part] = [] if rest[0].startswith("{") else {}
        return _set_parts(child, rest, value)
    if isinstance(node, list):
        if part.startswith("{"):
            pattern = parse_snbt(part)
            matches = [index for index, item in enumerate(node) if nbt_matches(item, pattern)]
            if not matches:
                node.append(copy.deepcopy(pattern))
                matches = [len(node) - 1]
            changed = False
            for index in matches:
                if rest:
                    changed = _set_parts(node[index], rest, value) or changed
                else:
                    node[index] = copy.deepcopy(value)
                    changed = True
            return changed
        index = _list_index(node, part)
        if index is None:
            return False
        if not rest:
            node[index] = value
            return True
        return _set_parts(node[index], rest, value)
    return False


def nbt_remove(store: dict[str, Any], path: str) -> bool:
    parts = split_path(path)
    if not parts:
        return False
    parent = _get_parts(store, parts[:-1])
    last = parts[-1]
    if isinstance(parent, dict) and last in parent:
        del parent[last]
        return True
    if isinstance(parent, list):
        if last.startswith("{"):  # every matching element goes
            pattern = parse_snbt(last)
            kept = [item for item in parent if not nbt_matches(item, pattern)]
            removed = len(kept) != len(parent)
            parent[:] = kept
            return removed
        index = _list_index(parent, last)
        if index is not None:
            del parent[index]
            return True
    return False


def parse_value(text: str) -> Any:
    text = text.strip()
    if text.startswith("{"):
        return parse_snbt(text)
    try:
        return json.loads(snbt_to_json(text))
    except json.JSONDecodeError:
        return text.strip('"')


def as_int(value: Any, scale: float | None = None) -> int:
    """What ``data get`` returns: numbers floored (after scaling), lengths otherwise."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        scaled = math.floor(value * (scale if scale is not None else 1))
        return max(-(2**31), min(2**31 - 1, scaled))  # Java's (int) cast saturates
    if isinstance(value, (list, dict, str)):
        return len(value)
    return 0


def merge_compound(target: dict[str, Any], source: dict[str, Any]) -> bool:
    """Vanilla CompoundTag.merge: nested compounds merge, anything else is replaced.

    Returns whether anything changed.
    """
    changed = False
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            changed = merge_compound(current, value) or changed
        elif current != value:
            target[key] = copy.deepcopy(value)
            changed = True
    return changed


_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def to_snbt(value: Any) -> str:
    """SNBT the way vanilla prints it in feedback: ``{Pos: [0.0d, 1.0d], Tags: ["a"]}``.

    Numeric types are not tracked by the emulator, so integers print bare and
    floating-point numbers as doubles.
    """
    if isinstance(value, dict):
        items = ", ".join(
            f"{key if _BARE_KEY_RE.match(key) else json.dumps(key)}: {to_snbt(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, list):
        return "[" + ", ".join(to_snbt(item) for item in value) + "]"
    if isinstance(value, bool):
        return "1b" if value else "0b"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value!r}d"
    if value is None:
        return "{}"
    return json.dumps(str(value), ensure_ascii=False)
