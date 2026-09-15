"""``data/<namespace>/`` — its registries and its directory structure.

Folder names changed in 1.21 (pack_format 45): ``functions`` became
``function``, ``tags/items`` became ``tags/item``, and so on.  Both spellings
load; the registry key is always the modern singular one, and
:attr:`Namespace.plural_folders` records the old-style folders that were found
so the version checker can warn about them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from datapack_emulator.emulator.resources import Function, Resource, Tag, resource_class

log = logging.getLogger(__name__)

#: old plural folder -> modern singular folder
PLURAL_REGISTRIES: dict[str, str] = {
    "advancements": "advancement",
    "functions": "function",
    "item_modifiers": "item_modifier",
    "loot_tables": "loot_table",
    "predicates": "predicate",
    "recipes": "recipe",
    "structures": "structure",
    "tags/blocks": "tags/block",
    "tags/entity_types": "tags/entity_type",
    "tags/fluids": "tags/fluid",
    "tags/functions": "tags/function",
    "tags/game_events": "tags/game_event",
    "tags/items": "tags/item",
}

#: folders whose registry name is two path segments long
NESTED_ROOTS = ("tags", "worldgen")


def normalise_registry(registry: str) -> str:
    """``functions`` -> ``function``, ``tags/functions`` -> ``tags/function``."""
    registry = registry.replace("\\", "/")
    if registry in PLURAL_REGISTRIES:
        return PLURAL_REGISTRIES[registry]
    if registry.startswith("tags/"):
        inner = registry[len("tags/") :]
        modern = PLURAL_REGISTRIES.get(f"tags/{inner}", inner)
        return "tags/" + modern.removeprefix("tags/")
    return registry


def _spelling(raw_registry: str, registry: str) -> str:
    if raw_registry in PLURAL_REGISTRIES:
        return "plural"
    if registry in _SINGULAR_NAMES:
        return "singular"
    return "any"


_SINGULAR_NAMES = frozenset(PLURAL_REGISTRIES.values())


@dataclass
class DirectoryNode:
    """One node of the mirrored directory structure of a namespace."""

    name: str
    path: Path
    is_dir: bool
    children: list[DirectoryNode] = field(default_factory=list)
    resource: Resource | None = None

    def walk(self) -> Iterator[DirectoryNode]:
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, relative: str) -> DirectoryNode | None:
        node: DirectoryNode = self
        for part in Path(relative).parts:
            for child in node.children:
                if child.name == part:
                    node = child
                    break
            else:
                return None
        return node

    def __repr__(self) -> str:
        kind = "dir" if self.is_dir else "file"
        return f"<DirectoryNode {kind} {self.name} children={len(self.children)}>"


class Namespace:
    """One ``data/<name>/`` folder, with every subfolder loaded."""

    def __init__(self, name: str, path: Path | str, overlay: str = ""):
        self.name = name
        self.path = Path(path)
        #: "" for the base pack, else the overlay directory this came from
        self.overlay = overlay
        #: normalised registry -> resource id -> resource, preferring the modern
        #: folder when a pack ships both spellings (see registries_for)
        self.registries: dict[str, dict[str, Resource]] = {}
        #: the same, split by folder spelling: "plural", "singular" or "any"
        self.by_spelling: dict[str, dict[str, dict[str, Resource]]] = {}
        #: old-style folder names found while loading, e.g. {"functions"}
        self.plural_folders: set[str] = set()
        #: every registry folder as spelled on disk, e.g. {"function", "tags/function"}
        self.raw_folders: set[str] = set()
        self.tree = DirectoryNode(name=name, path=self.path, is_dir=True)

    # -- loading ----------------------------------------------------------

    def load(self) -> Namespace:
        self.registries.clear()
        self.by_spelling.clear()
        self.plural_folders.clear()
        self.raw_folders.clear()
        self.tree = self._build_tree(self.path)
        return self

    def _build_tree(self, directory: Path) -> DirectoryNode:
        node = DirectoryNode(name=directory.name, path=directory, is_dir=True)
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError as exc:
            log.error("cannot list %s: %s", directory, exc)
            return node
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                node.children.append(self._build_tree(entry))
            else:
                resource = self._make_resource(entry)
                node.children.append(
                    DirectoryNode(name=entry.name, path=entry, is_dir=False, resource=resource)
                )
        return node

    def _registry_of(self, file_path: Path) -> tuple[str, str, str]:
        """``(normalised registry, raw registry, resource path)``."""
        relative = file_path.relative_to(self.path)
        parts = list(relative.parts)
        depth = 2 if parts and parts[0] in NESTED_ROOTS else 1
        raw_registry = "/".join(parts[:depth])
        rest = parts[depth:]
        if not rest:  # a stray file directly in the namespace folder
            return ("", raw_registry, relative.as_posix())
        rest[-1] = Path(rest[-1]).stem
        return (normalise_registry(raw_registry), raw_registry, "/".join(rest))

    def _make_resource(self, file_path: Path) -> Resource | None:
        registry, raw_registry, resource_path = self._registry_of(file_path)
        if not registry:
            return None
        self.raw_folders.add(raw_registry)
        if raw_registry != registry:
            self.plural_folders.add(raw_registry)
        cls = resource_class(registry, file_path.suffix)
        resource = cls(file_path, self.name, registry, resource_path)
        resource.overlay = self.overlay
        resource.spelling = _spelling(raw_registry, registry)
        resource.load()
        self.by_spelling.setdefault(resource.spelling, {}).setdefault(registry, {})[resource.id] = (
            resource
        )
        bucket = self.registries.setdefault(registry, {})
        existing = bucket.get(resource.id)
        if existing is not None and raw_registry != registry:
            # the pack ships both `function/` and `functions/`: keep the modern one
            log.debug("duplicate %s (%s vs %s)", resource.id, existing.path, file_path)
            return resource
        bucket[resource.id] = resource
        return resource

    # -- accessors --------------------------------------------------------

    def registries_for(self, singular: bool) -> dict[str, dict[str, Resource]]:
        """What a version reads: unrenamed registries plus one folder spelling.

        1.21 renamed ``functions/`` to ``function/`` (and friends); a version
        ignores the spelling it does not use, even when only that one exists.
        """
        merged: dict[str, dict[str, Resource]] = {}
        for spelling in ("any", "singular" if singular else "plural"):
            for registry, bucket in self.by_spelling.get(spelling, {}).items():
                merged.setdefault(registry, {}).update(bucket)
        return merged

    @property
    def functions(self) -> dict[str, Function]:
        return {
            key: value
            for key, value in self.registries.get("function", {}).items()
            if isinstance(value, Function)
        }

    @property
    def function_tags(self) -> dict[str, Tag]:
        return {
            key: value
            for key, value in self.registries.get("tags/function", {}).items()
            if isinstance(value, Tag)
        }

    def resources(self) -> Iterator[Resource]:
        for bucket in self.registries.values():
            yield from bucket.values()

    def __repr__(self) -> str:
        counts = ", ".join(f"{key}={len(value)}" for key, value in sorted(self.registries.items()))
        return f"<Namespace {self.name} {counts}>"
