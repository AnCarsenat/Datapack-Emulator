"""Renaming a function across a pack: the file moves and every reference to
its id follows (function calls, function tags, advancement rewards, anything
else in the pack's JSON).

``datapack-emulator-cli rename`` and the source view's *rename function…* both
read this; nothing is written until ``apply=True``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import normalise_id

#: a resource location, as the game reads it
ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
#: what may be read as text and searched for references
TEXT_SUFFIXES = (".mcfunction", ".json", ".mcmeta")


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
    """The rename cannot be done (unknown function, bad id, id taken)."""


def _reference(old_id: str) -> re.Pattern[str]:
    """The id where it is used as an id, not inside a longer one."""
    return re.compile(rf"(?<![A-Za-z0-9_.:/-]){re.escape(old_id)}(?![A-Za-z0-9_.:/-])")


def _new_path(path: Path, old_id: str, new_id: str) -> Path:
    """Where the file goes: the same pack, under the new namespace and path."""
    old_namespace, _, old_rest = old_id.partition(":")
    new_namespace, _, new_rest = new_id.partition(":")
    parts = path.as_posix()
    # …/data/<namespace>/<function|functions>/<rest>.mcfunction (either spelling)
    match = re.search(
        rf"/data/{re.escape(old_namespace)}/(functions?)/{re.escape(old_rest)}\b", parts
    )
    if match is None:
        raise RenameError(f"{path} is not where {old_id} should be: rename it by hand")
    head, registry = parts[: match.start()], match.group(1)
    return Path(f"{head}/data/{new_namespace}/{registry}/{new_rest}{path.suffix}")


def _files(datapack: Any) -> list[Path]:
    """Every text file of the pack (or of each pack of a set), once."""
    roots = getattr(datapack, "paths", None) or [datapack.path]
    seen: dict[Path, None] = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                seen.setdefault(path.resolve(), None)
    return list(seen)


def rename_function(
    datapack: Any,
    old_id: str,
    new_id: str,
    version: str | versions.Version = versions.LATEST,
    apply: bool = False,
) -> list[Edit]:
    """Move ``old_id``'s file to ``new_id`` and follow every reference to it.

    Returns what would change (or what changed, with ``apply``); raises
    :class:`RenameError` when the rename makes no sense.
    """
    parsed = versions.parse(version)
    old_id, new_id = normalise_id(old_id), normalise_id(new_id)
    if old_id == new_id:
        raise RenameError("the new id is the old one")
    if not ID_RE.match(new_id):
        raise RenameError(f"{new_id} is not a resource location (namespace:path, lowercase)")
    view = datapack.view_for(parsed)
    function = view.functions.get(old_id)
    if function is None:
        raise RenameError(f"{old_id} is not a function of this pack in {parsed.id}")
    if new_id in view.functions:
        raise RenameError(f"{new_id} is already a function of this pack")

    source = Path(function.path)
    target = _new_path(source, old_id, new_id)
    if target.exists():
        raise RenameError(f"{target} already exists")

    pattern = _reference(old_id)
    edits: list[Edit] = []
    for path in _files(datapack):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if old_id not in text:
            continue
        lines = text.splitlines(keepends=True)
        changed = False
        for number, line in enumerate(lines, start=1):
            replaced = pattern.sub(new_id, line)
            if replaced != line:
                edits.append(Edit("line", path, number, line.rstrip("\n"), replaced.rstrip("\n")))
                lines[number - 1] = replaced
                changed = True
        if changed and apply:
            path.write_text("".join(lines), encoding="utf-8")
    edits.insert(0, Edit("move", source, after=str(target)))
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
    return edits
