"""Renaming a function across a pack: the file moves and every reference to
its id follows (function calls, function tags, advancement rewards, anything
else in the pack's ``data/``).

``datapack-emulator-cli rename`` and the source view's *rename function…* both
read this; nothing is written until ``apply=True``. References are found by a
textual search for the id inside each layer's ``data/`` folder, so an id
written in chat text or a comment follows the rename too; a ``#tag`` of the
same id does not, it is another resource.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import normalise_id

#: a resource location, as the game reads it
ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_.-]+(?:/[a-z0-9_.-]+)*$")
#: what may be read as text and searched for references
TEXT_SUFFIXES = (".mcfunction", ".json", ".mcmeta")
#: both spellings of the folder a function lives in (`function/` since 1.21)
REGISTRY_FOLDERS = ("function", "functions")


@dataclass(frozen=True)
class Edit:
    """One change a rename makes."""

    #: "move" (the file itself) or "line" (a reference inside a file)
    kind: str
    path: Path
    line: int = 0
    before: str = ""
    after: str = ""

    def format(self) -> str:
        if self.kind == "move":
            return f"move {self.path} -> {self.after}"
        return f"{self.path}:{self.line}: {self.before.strip()} -> {self.after.strip()}"


class RenameError(ValueError):
    """The rename cannot be done (unknown function, bad id, id taken, a file
    that cannot be written)."""


def _patterns(old_id: str, new_id: str) -> list[tuple[re.Pattern[str], str]]:
    """The id where it is used as an id: not inside a longer one, and not
    behind a ``#`` (that is the function tag of the same name)."""
    found = [
        (re.compile(rf"(?<![#A-Za-z0-9_.:/-]){re.escape(old_id)}(?![A-Za-z0-9_.:/-])"), new_id)
    ]
    namespace, _, rest = old_id.partition(":")
    if namespace == "minecraft":  # `function helper` means `minecraft:helper`
        escaped = re.escape(rest)
        found += [
            (re.compile(rf"(?<=\bfunction ){escaped}(?![A-Za-z0-9_.:/-])"), new_id),
            (re.compile(rf'(?<="){escaped}(?=")'), new_id),
        ]
    return found


def _check_id(new_id: str) -> None:
    if not ID_RE.match(new_id):
        raise RenameError(f"{new_id} is not a resource location (namespace:path, lowercase)")
    _, _, rest = new_id.partition(":")
    if ".." in rest.split("/") or rest.startswith("/") or rest.endswith("/"):
        raise RenameError(f"{new_id} is not a path inside the pack")


def _layers(datapack: Any) -> list[Path]:
    """Every ``data/`` folder the rename may touch: the base pack's, each
    overlay's, and the same for every pack of a set."""
    packs = getattr(datapack, "packs", None)
    if packs is not None:
        roots: list[Path] = []
        for pack in packs:
            roots += _layers(pack)
        return roots
    base = getattr(datapack, "base", None)
    if base is None:
        return [Path(datapack.path) / "data"]
    layers = [base, *getattr(datapack, "overlays", [])]
    return [Path(layer.path) for layer in layers]


def _files(roots: list[Path]) -> list[Path]:
    """Every text file under those folders, once."""
    seen: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                seen.setdefault(path.resolve(), None)
    return list(seen)


def _copies(roots: list[Path], function_id: str) -> list[tuple[Path, str, Path]]:
    """``(data folder, folder spelling, file)`` of every file serving that id:
    an overlay's copy and the other folder spelling are files of their own."""
    namespace, _, rest = function_id.partition(":")
    found = []
    for root in roots:
        for registry in REGISTRY_FOLDERS:
            path = root / namespace / registry / f"{rest}.mcfunction"
            if path.is_file():
                found.append((root, registry, path))
    return found


def _target(root: Path, registry: str, new_id: str) -> Path:
    namespace, _, rest = new_id.partition(":")
    target = root / namespace / registry / f"{rest}.mcfunction"
    inside = root.resolve()
    if not target.resolve().is_relative_to(inside):
        raise RenameError(f"{new_id} would put the file outside {inside}")
    return target


def _writable(path: Path) -> bool:
    """Whether the file (or, when it is to be created, its first existing
    parent) can be written."""
    if path.exists():
        return os.access(path, os.W_OK)
    for parent in path.parents:
        if parent.exists():
            return os.access(parent, os.W_OK)
    return False


def rename_function(
    datapack: Any,
    old_id: str,
    new_id: str,
    version: str | versions.Version = versions.LATEST,
    apply: bool = False,
) -> list[Edit]:
    """Move ``old_id``'s file to ``new_id`` and follow every reference to it.

    Returns what would change (or what changed, with ``apply``); raises
    :class:`RenameError` when the rename makes no sense, or when a file it
    would have to write cannot be written — in that case nothing is touched.
    """
    parsed = versions.parse(version)
    old_id, new_id = normalise_id(old_id), normalise_id(new_id)
    if old_id == new_id:
        raise RenameError("the new id is the old one")
    _check_id(new_id)
    view = datapack.view_for(parsed)
    function = view.functions.get(old_id)
    if function is None:
        raise RenameError(f"{old_id} is not a function of this pack in {parsed.id}")
    if new_id in view.functions:
        raise RenameError(f"{new_id} is already a function of this pack")

    roots = _layers(datapack)
    copies = _copies(roots, old_id)
    if not copies:
        raise RenameError(f"{Path(function.path)} is not where {old_id} should be: rename it")
    moves: list[tuple[Path, Path]] = []
    for root, registry, source in copies:
        target = _target(root, registry, new_id)
        if target.exists():
            raise RenameError(f"{target} already exists")
        moves.append((source, target))

    patterns = _patterns(old_id, new_id)
    edits: list[Edit] = []
    writes: dict[Path, str] = {}
    for path in _files(roots):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines(keepends=True)
        changed = False
        for number, line in enumerate(lines, start=1):
            replaced = line
            for pattern, replacement in patterns:
                replaced = pattern.sub(replacement, replaced)
            if replaced != line:
                edits.append(Edit("line", path, number, line.rstrip("\n"), replaced.rstrip("\n")))
                lines[number - 1] = replaced
                changed = True
        if changed:
            writes[path] = "".join(lines)
    for source, target in reversed(moves):
        edits.insert(0, Edit("move", source, after=str(target)))
    if not apply:
        return edits

    # nothing is written until every file that has to be written can be
    refused = [
        path
        for path in [*writes, *(source for source, _ in moves), *(target for _, target in moves)]
        if not _writable(path)
    ]
    if refused:
        raise RenameError("cannot write " + ", ".join(str(path) for path in sorted(set(refused))))
    written: list[tuple[Path, str]] = []
    moved: list[tuple[Path, Path]] = []
    try:
        for path, text in writes.items():
            written.append((path, path.read_text(encoding="utf-8")))
            path.write_text(text, encoding="utf-8")
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append((source, target))
    except OSError as exc:  # put the pack back as it was, then say so
        for source, target in reversed(moved):
            target.replace(source)
        for path, text in reversed(written):
            path.write_text(text, encoding="utf-8")
        raise RenameError(f"cannot rename {old_id}: {exc} (nothing was changed)") from exc
    return edits
