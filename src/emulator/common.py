"""Parsing helpers shared by the model, the command parser and the runtime.

Nothing here knows about versions, Qt or the world state: it is pure text and
NBT plumbing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

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


def flatten_text_component(component: Any) -> str:
    """Turn a JSON text component (or a raw string) into plain text."""
    if isinstance(component, str):
        return component
    if isinstance(component, list):
        return "".join(flatten_text_component(part) for part in component)
    if isinstance(component, dict):
        text = str(component.get("text", ""))
        for key in ("selector", "score", "keybind", "translate"):
            if key in component and not text:
                value = component[key]
                text = value if isinstance(value, str) else str(value)
        extra = component.get("extra")
        if extra:
            text += flatten_text_component(extra)
        return text
    return ""


def parse_text_component(payload: str) -> Optional[str]:
    """Plain text of a ``tellraw``/``title`` component, or ``None`` if unreadable.

    Accepts JSON (every version) and SNBT (1.21.5 and later), e.g.
    ``{text:"hi",color:"red"}`` or ``["",{text:"a"},'b']``.
    """
    payload = payload.strip()
    try:
        return flatten_text_component(json.loads(payload))
    except json.JSONDecodeError:
        pass
    try:
        return flatten_text_component(json.loads(snbt_to_json(payload)))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# tokenising
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Split on spaces, but keep ``[...]``, ``{...}`` and quoted strings whole."""
    tokens: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    quote: Optional[str] = None
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
    quote: Optional[str] = None
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
        if low and float(value) < float(low):
            return False
        if high and float(value) > float(high):
            return False
        return True
    except ValueError:
        return False


def distance_squared(a: Iterable[float], b: Iterable[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


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
    return [part for part in re.split(r"[.\[\]]", path.strip()) if part]


def nbt_get(store: dict[str, Any], path: str) -> Any:
    node: Any = store
    for part in split_path(path):
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def nbt_set(store: dict[str, Any], path: str, value: Any) -> None:
    parts = split_path(path)
    if not parts:
        return
    node: Any = store
    for part in parts[:-1]:
        if isinstance(node, dict):
            node = node.setdefault(part, {})
        else:
            return
    if isinstance(node, dict):
        node[parts[-1]] = value


def nbt_remove(store: dict[str, Any], path: str) -> bool:
    parts = split_path(path)
    if not parts:
        return False
    node: Any = store
    for part in parts[:-1]:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
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


def as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, dict, str)):
        return len(value)
    return 0
