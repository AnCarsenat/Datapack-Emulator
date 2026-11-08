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
        text = (
            resolve("translate", component)
            if resolve
            else str(component.get("fallback", component["translate"]))
        )
    elif "nbt" in component:
        text = resolve("nbt", component) if resolve else ""
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


_NUMBER_RE = re.compile(r"-?(\d+\.?\d*|\.\d+)")


def parse_number(token: str) -> float | None:
    """A Brigadier number: digits with an optional point and sign — no
    ``inf``, ``nan``, exponents or underscores."""
    token = token.strip()
    return float(token) if _NUMBER_RE.fullmatch(token) else None


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


class NbtByte(int):
    """A number that remembers its SNBT type (``5b``). Typed numbers behave as
    plain ``int``/``float`` everywhere; only NBT comparisons (:func:`nbt_equal`,
    :func:`nbt_matches`) and :func:`to_snbt` look at the type. Numbers the
    emulator computes itself stay plain and compare by value alone."""

    __slots__ = ()


class NbtShort(int):
    __slots__ = ()


class NbtInt(int):
    __slots__ = ()


class NbtLong(int):
    __slots__ = ()


class NbtFloat(float):
    __slots__ = ()


class NbtDouble(float):
    __slots__ = ()


class NbtArray(list):
    """``[B;…]``, ``[I;…]`` or ``[L;…]``: compared whole, unlike a list."""

    __slots__ = ("kind",)

    def __init__(self, items: Iterable[Any] = (), kind: str = "I"):
        super().__init__(items)
        self.kind = kind

    def __deepcopy__(self, memo: dict[int, Any]) -> NbtArray:
        return NbtArray((copy.deepcopy(item, memo) for item in self), self.kind)

    def __reduce__(self):
        return (NbtArray, (list(self), self.kind))


_NUMBER_TYPES: dict[str, type] = {
    "b": NbtByte,
    "s": NbtShort,
    "i": NbtInt,
    "l": NbtLong,
    "f": NbtFloat,
    "d": NbtDouble,
}
_SUFFIXES = {cls: suffix for suffix, cls in _NUMBER_TYPES.items()}
#: the key snbt_to_json(typed=True) wraps a typed number or array in
_TYPED_KEY = "\x00nbt"


def typed_number(value: float, kind: str) -> Any:
    """``value`` as a typed NBT number; ``kind`` is a suffix (``b s i l f d``)
    or a ``store`` type name (``byte short int long float double``)."""
    kind = {"byte": "b", "short": "s", "int": "i", "long": "l", "float": "f", "double": "d"}.get(
        kind, kind.lower()
    )
    cls = _NUMBER_TYPES.get(kind)
    if cls is None:
        return value
    return cls(value) if kind in "fd" else cls(int(value))


def nbt_type(value: Any) -> str | None:
    """The NBT number type of ``value`` as a suffix letter, or None when the
    emulator does not know it (a plain number) or it is not a number."""
    if isinstance(value, bool):
        return "b"
    return _SUFFIXES.get(type(value))


def untyped(value: Any) -> Any:
    """``value`` with every typed number made plain again, recursively.

    Entity and block-entity data goes through this when it is written: the
    game decodes it with codecs that give each field its own type (``Health``
    is a float whatever the SNBT said), and the emulator does not know those
    types, so it compares those numbers by value. Command storage keeps the
    exact SNBT types. Arrays keep their kind (``[I;…]``)."""
    if isinstance(value, dict):
        return {key: untyped(item) for key, item in value.items()}
    if isinstance(value, NbtArray):
        return NbtArray((untyped(item) for item in value), value.kind)
    if isinstance(value, list):
        return [untyped(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and type(value) is not int:
        return int(value)
    if isinstance(value, float) and type(value) is not float:
        return float(value)
    return value


def _typed_hook(obj: dict[str, Any]) -> Any:
    if len(obj) == 2 and _TYPED_KEY in obj and "v" in obj:
        kind, value = obj[_TYPED_KEY], obj["v"]
        if kind in "BIL" and isinstance(value, list):
            return NbtArray(value, kind)
        if isinstance(value, (int, float)):
            return typed_number(value, kind)
    return obj


def parse_snbt(text: str) -> dict[str, Any]:
    """Parse SNBT well enough for emulation: bare keys quoted, numbers typed
    (``5b`` is a :class:`NbtByte`, ``5`` a :class:`NbtInt`, ``5.0`` a
    :class:`NbtDouble`)."""
    text = text.strip()
    if not text.startswith("{"):
        return {}
    try:
        return json.loads(snbt_to_json(text, typed=True), object_hook=_typed_hook)
    except json.JSONDecodeError:
        log.debug("cannot parse SNBT %s", text[:80])
        return {}


def _typed(kind: str, value: str) -> str:
    return '{"\\u0000nbt":"' + kind + '","v":' + value + "}"


def snbt_to_json(text: str, typed: bool = False) -> str:
    """SNBT as JSON text. With ``typed``, numbers and arrays come wrapped so
    that ``json.loads(..., object_hook=_typed_hook)`` gives them their type."""
    out: list[str] = []
    arrays: list[bool] = []  # for each open "[": is it a typed array?
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
            kind = "d" if any(mark in word for mark in ".eE") else "i"
            if end < length and text[end] in "bslfdBSLFD":
                kind = text[end].lower()
                end += 1
            out.append(_typed(kind, word) if typed else word)
            index = end
            continue
        if char == "[":
            array = text[index : index + 3] in ("[I;", "[B;", "[L;")
            arrays.append(array and typed)
            if array and typed:
                out.append('{"\\u0000nbt":"' + text[index + 1] + '","v":[')
            else:
                out.append("[")
            index += 3 if array else 1
            continue
        if char == "]":
            out.append("]}" if arrays and arrays.pop() else "]")
            index += 1
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


def nbt_equal(a: Any, b: Any) -> bool:
    """Exact NBT equality (``Tag.equals``): numbers must agree in type as well
    as value when both types are known (``5`` is not ``5b``)."""
    if isinstance(a, dict) or isinstance(b, dict):
        return (
            isinstance(a, dict)
            and isinstance(b, dict)
            and a.keys() == b.keys()
            and all(nbt_equal(a[key], b[key]) for key in a)
        )
    if isinstance(a, list) or isinstance(b, list):
        return (
            isinstance(a, list)
            and isinstance(b, list)
            and len(a) == len(b)
            and all(nbt_equal(x, y) for x, y in zip(a, b, strict=True))
        )
    kind_a, kind_b = nbt_type(a), nbt_type(b)
    if kind_a is not None and kind_b is not None and kind_a != kind_b:
        return False
    return bool(a == b)


def nbt_matches(actual: Any, pattern: Any) -> bool:
    """Vanilla partial NBT matching (``NbtUtils.compareNbt(pattern, actual, true)``).

    Every key of a pattern compound must be in the actual compound and match
    (extra keys are fine); every element of a pattern list must match some
    element of the actual list, in any order, and an empty pattern list
    matches only an empty list; arrays (``[I;…]``) and everything else must be
    equal, numbers in type as well as value (see :func:`nbt_equal`)."""
    if isinstance(pattern, dict):
        return isinstance(actual, dict) and all(
            key in actual and nbt_matches(actual[key], value) for key, value in pattern.items()
        )
    if isinstance(pattern, NbtArray):
        return nbt_equal(pattern, actual)
    if isinstance(pattern, list):
        if not isinstance(actual, list):
            return False
        if not pattern:
            return not actual
        return all(any(nbt_matches(item, wanted) for item in actual) for wanted in pattern)
    return nbt_equal(pattern, actual)


class PathNode:
    """One node of an NBT path (vanilla ``NbtPathArgument``):

    * ``root``: ``{…}`` as the first node, the root if it matches ``pattern``
    * ``key``: ``name`` or ``"quoted name"``, with ``pattern`` set for
      ``name{…}`` (the child, only if it is a compound matching the pattern)
    * ``index``: ``[n]`` (negative counts from the end)
    * ``all``: ``[]``, every element
    * ``match``: ``[{…}]``, every compound element matching ``pattern``
    """

    __slots__ = ("kind", "key", "index", "pattern")

    def __init__(
        self, kind: str, key: str = "", index: int = 0, pattern: dict[str, Any] | None = None
    ):
        self.kind = kind
        self.key = key
        self.index = index
        self.pattern = pattern

    def __repr__(self) -> str:
        return f"PathNode({self.kind!r}, {self.key!r}, {self.index}, {self.pattern!r})"

    def selects(self, child: Any) -> bool:
        """Whether a ``key``/``match`` child passes this node's filter."""
        if self.pattern is None:
            return True
        return isinstance(child, dict) and nbt_matches(child, self.pattern)

    def new_parent(self) -> Any:
        """What a missing parent of this node is created as (vanilla
        ``createPreferredParentTag``)."""
        return [] if self.kind in ("index", "all", "match") else {}


_PATH_NAME_STOP = set(" \"'[]{}.")


def parse_path(text: str) -> list[PathNode] | None:
    """Parse an NBT path the way vanilla does; None when it is not valid."""
    text = text.strip()
    nodes: list[PathNode] = []
    index = 0
    length = len(text)
    while True:
        if index >= length:
            return None
        char = text[index]
        if char == "{":
            if nodes:
                return None  # a root compound only comes first
            end = _compound_end(text, index)
            pattern = _path_compound(text[index : end + 1])
            if pattern is None:
                return None
            nodes.append(PathNode("root", pattern=pattern))
            index = end + 1
        elif char == "[":
            index += 1
            if index < length and text[index] == "]":
                nodes.append(PathNode("all"))
                index += 1
            elif index < length and text[index] == "{":
                end = _compound_end(text, index)
                pattern = _path_compound(text[index : end + 1])
                if pattern is None or end + 1 >= length or text[end + 1] != "]":
                    return None
                nodes.append(PathNode("match", pattern=pattern))
                index = end + 2
            else:
                end = text.find("]", index)
                if end < 0:
                    return None
                try:
                    number = int(text[index:end])
                except ValueError:
                    return None
                nodes.append(PathNode("index", index=number))
                index = end + 1
        else:
            if char in "\"'":
                end = index + 1
                name: list[str] = []
                while end < length and text[end] != char:
                    if text[end] == "\\" and end + 1 < length:
                        end += 1
                    name.append(text[end])
                    end += 1
                if end >= length:
                    return None
                key = "".join(name)
                index = end + 1
            else:
                end = index
                while end < length and text[end] not in _PATH_NAME_STOP:
                    end += 1
                if end == index:
                    return None
                key = text[index:end]
                index = end
            pattern = None
            if index < length and text[index] == "{":
                end = _compound_end(text, index)
                pattern = _path_compound(text[index : end + 1])
                if pattern is None:
                    return None
                index = end + 1
            nodes.append(PathNode("key", key=key, pattern=pattern))
        if index >= length:
            return nodes
        if text[index] == "[":
            continue  # the dot before an element node may be left out
        if text[index] != ".":
            return None
        index += 1


def _path_compound(text: str) -> dict[str, Any] | None:
    if not text.endswith("}"):
        return None
    try:
        value = json.loads(snbt_to_json(text, typed=True), object_hook=_typed_hook)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _children(node: PathNode, tag: Any) -> list[Any]:
    """Vanilla ``getTag``: what ``node`` selects in ``tag``."""
    if node.kind == "root":
        return [tag] if node.selects(tag) else []
    if node.kind == "key":
        if isinstance(tag, dict) and node.key in tag and node.selects(tag[node.key]):
            return [tag[node.key]]
        return []
    if not isinstance(tag, list):
        return []
    if node.kind == "index":
        position = node.index + len(tag) if node.index < 0 else node.index
        return [tag[position]] if 0 <= position < len(tag) else []
    if node.kind == "all":
        return list(tag)
    return [item for item in tag if node.selects(item)]


def _children_or_create(node: PathNode, tag: Any, following: PathNode) -> list[Any]:
    """Vanilla ``getOrCreateTag``: like :func:`_children`, but a missing key,
    an empty list under ``[]`` or a list with no match under ``[{…}]`` gets
    what ``following`` needs (a filter node creates its pattern)."""
    if node.kind == "key" and isinstance(tag, dict):
        if node.key not in tag:
            tag[node.key] = (
                copy.deepcopy(node.pattern) if node.pattern is not None else following.new_parent()
            )
            return [tag[node.key]]
        return _children(node, tag)
    if node.kind == "all" and isinstance(tag, list) and not tag:
        tag.append(following.new_parent())
        return list(tag)
    if node.kind == "match" and isinstance(tag, list):
        found = _children(node, tag)
        if not found and node.pattern is not None:
            tag.append(copy.deepcopy(node.pattern))
            found = [tag[-1]]
        return found
    return _children(node, tag)


def nbt_get_all(store: Any, path: str) -> list[Any]:
    """Every tag ``path`` selects in ``store`` (empty for an invalid path)."""
    nodes = parse_path(path)
    if not nodes:
        return []
    tags = [store]
    for node in nodes:
        tags = [child for tag in tags for child in _children(node, tag)]
    return tags


def nbt_get(store: Any, path: str) -> Any:
    """The first tag ``path`` selects in ``store``, or None."""
    found = nbt_get_all(store, path)
    return found[0] if found else None


def nbt_set_count(store: Any, path: str, value: Any) -> int | None:
    """Vanilla ``NbtPath.set``: create the missing parents, then put ``value``
    at every tag the path selects. Returns how many tags changed, or None when
    the path is invalid or reaches no parent (``arguments.nbtpath.nothing_found``).

    The first target receives ``value`` itself, the others a copy.
    """
    nodes = parse_path(path)
    if not nodes:
        return None
    parents = [store]
    for node, following in zip(nodes, nodes[1:], strict=False):
        parents = [child for tag in parents for child in _children_or_create(node, tag, following)]
    if not parents:
        return None
    last = nodes[-1]
    changed = 0
    used = False
    if last.kind == "index" and not any(_children(last, parent) for parent in parents):
        return None  # no element at that index anywhere

    def give() -> Any:
        nonlocal used
        result = copy.deepcopy(value) if used else value
        used = True
        return result

    for parent in parents:
        if last.kind == "key" and isinstance(parent, dict):
            current = parent.get(last.key)
            if last.pattern is not None and not last.selects(current):
                continue
            if last.key in parent and nbt_equal(current, value):
                continue
            parent[last.key] = give()
            changed += 1
        elif last.kind in ("index", "all", "match") and isinstance(parent, list):
            if last.kind == "index":
                position = last.index + len(parent) if last.index < 0 else last.index
                positions = [position] if 0 <= position < len(parent) else []
            elif not parent:
                parent.append(give())
                changed += 1
                continue
            else:
                positions = [i for i, item in enumerate(parent) if last.selects(item)]
            for position in positions:
                if not nbt_equal(parent[position], value):
                    parent[position] = give()
                    changed += 1
    return changed


def nbt_set(store: Any, path: str, value: Any) -> bool:
    """Set ``path`` to ``value``, creating missing parents on the way (see
    :func:`nbt_set_count`). True when the path reached a place to set; a value
    that was already there counts."""
    nodes = parse_path(path)
    if not nodes:
        return False
    count = nbt_set_count(store, path, value)
    return count is not None and (count > 0 or bool(nbt_get_all(store, path)))


def nbt_remove_count(store: Any, path: str) -> int:
    """Vanilla ``NbtPath.remove``: how many tags were removed."""
    nodes = parse_path(path)
    if not nodes:
        return 0
    parents = [store]
    for node in nodes[:-1]:
        parents = [child for tag in parents for child in _children(node, tag)]
    last = nodes[-1]
    removed = 0
    for parent in parents:
        if last.kind == "key" and isinstance(parent, dict):
            if last.key in parent and last.selects(parent[last.key]):
                del parent[last.key]
                removed += 1
        elif last.kind in ("index", "all", "match") and isinstance(parent, list):
            if last.kind == "index":
                position = last.index + len(parent) if last.index < 0 else last.index
                if 0 <= position < len(parent):
                    del parent[position]
                    removed += 1
                continue
            kept = [item for item in parent if last.kind == "match" and not last.selects(item)]
            removed += len(parent) - len(kept)
            parent[:] = kept
    return removed


def nbt_remove(store: Any, path: str) -> bool:
    return nbt_remove_count(store, path) > 0


def parse_value(text: str) -> Any:
    text = text.strip()
    if text.startswith("{"):
        return parse_snbt(text)
    try:
        return json.loads(snbt_to_json(text, typed=True), object_hook=_typed_hook)
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

    Numbers read from SNBT keep their type and print with its suffix; numbers
    the emulator computed itself have none, so integers print bare and
    floating-point numbers as doubles.
    """
    if isinstance(value, dict):
        items = ", ".join(
            f"{key if _BARE_KEY_RE.match(key) else json.dumps(key)}: {to_snbt(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, NbtArray):
        return f"[{value.kind}; " + ", ".join(to_snbt(item) for item in value) + "]"
    if isinstance(value, list):
        return "[" + ", ".join(to_snbt(item) for item in value) + "]"
    if isinstance(value, bool):
        return "1b" if value else "0b"
    kind = nbt_type(value)
    if isinstance(value, int):
        suffix = {"b": "b", "s": "s", "l": "L"}.get(kind or "", "")
        return f"{int(value)}{suffix}"
    if isinstance(value, float):
        return f"{float(value)!r}{'f' if kind == 'f' else 'd'}"
    if value is None:
        return "{}"
    return json.dumps(str(value), ensure_ascii=False)
