"""The files inside ``data/<namespace>/``: functions, tags, recipes, …"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

from src.emulator.commands.parser import Command

log = logging.getLogger(__name__)


class Resource:
    """Any file inside ``data/<namespace>/``."""

    extension: Optional[str] = None

    def __init__(self, path: Path, namespace: str, registry: str, resource_path: str):
        self.path = Path(path)
        self.namespace = namespace
        self.registry = registry  # e.g. "function", "tags/function"
        self.resource_path = resource_path  # e.g. "sub/load"
        self.error: Optional[str] = None
        #: "" for the base pack, otherwise the overlay directory it came from
        self.overlay: str = ""

    @property
    def id(self) -> str:
        """The resource location, e.g. ``hat:load``."""
        return f"{self.namespace}:{self.resource_path}"

    def load(self) -> None:
        """Read the file.  Subclasses override; the base class reads nothing."""

    def __repr__(self) -> str:
        where = f" ({self.overlay})" if self.overlay else ""
        return f"<{type(self).__name__} {self.id}{where}>"


class JsonResource(Resource):
    extension = ".json"

    def __init__(self, path: Path, namespace: str, registry: str, resource_path: str):
        super().__init__(path, namespace, registry, resource_path)
        self.content: dict[str, Any] = {}

    def load(self) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                self.content = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.error = str(exc)
            log.error("cannot parse %s: %s", self.path, exc)


class Recipe(JsonResource):
    pass


class Advancement(JsonResource):
    pass


class LootTable(JsonResource):
    pass


class Predicate(JsonResource):
    pass


class ItemModifier(JsonResource):
    pass


class Tag(JsonResource):
    """A ``tags/<registry>/<name>.json`` list of entries."""

    @property
    def values(self) -> list[str]:
        out: list[str] = []
        for entry in self.content.get("values", []):
            if isinstance(entry, str):
                out.append(entry)
            elif isinstance(entry, dict) and "id" in entry:
                out.append(str(entry["id"]))
        return out

    @property
    def replace(self) -> bool:
        return bool(self.content.get("replace", False))

    @property
    def tag_id(self) -> str:
        """``#minecraft:tick`` style id (the registry part is stripped)."""
        return f"#{self.namespace}:{self.resource_path}"


class Function(Resource):
    """A ``.mcfunction`` file: an ordered list of :class:`Command`."""

    extension = ".mcfunction"

    def __init__(self, path: Path, namespace: str, registry: str, resource_path: str):
        super().__init__(path, namespace, registry, resource_path)
        self.lines: list[str] = []
        self.content: list[Command] = []
        self.is_macro = False

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            self.error = str(exc)
            log.error("cannot read %s: %s", self.path, exc)
            return
        self.lines = text.splitlines()
        self.content = []
        for number, line in enumerate(self.lines, start=1):
            command = Command.parse(line, source=self.id, line=number)
            if command is not None:
                self.content.append(command)
                self.is_macro = self.is_macro or command.is_macro

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Static call edges out of this function: ``(target_id, kind)``."""
        out: list[tuple[str, str]] = []
        for command in self.content:
            out.extend(command.calls())
        return out

    def features(self) -> set[str]:
        """Every feature key the body needs, for the version checker."""
        needed: set[str] = set()
        for command in self.content:
            needed |= command.features()
        return needed

    def estimate_cost(self, entity_count: int = 30) -> float:
        """Static cost estimate in us, *excluding* the bodies of called functions."""
        return sum(command.estimate_cost(entity_count) for command in self.content)

    def __iter__(self) -> Iterator[Command]:
        return iter(self.content)

    def __len__(self) -> int:
        return len(self.content)


#: registry (folder) -> Resource subclass
RESOURCE_CLASSES: dict[str, type[Resource]] = {
    "function": Function,
    "advancement": Advancement,
    "recipe": Recipe,
    "loot_table": LootTable,
    "predicate": Predicate,
    "item_modifier": ItemModifier,
}


def resource_class(registry: str, suffix: str) -> type[Resource]:
    if registry.startswith("tags/"):
        return Tag
    cls = RESOURCE_CLASSES.get(registry)
    if cls is not None:
        return cls
    return JsonResource if suffix == ".json" else Resource
