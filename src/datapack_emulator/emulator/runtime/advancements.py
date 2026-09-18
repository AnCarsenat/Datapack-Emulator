"""Advancements: the tree a pack (and the client jar) defines, and each
player's progress.

Criteria are granted by the ``advancement`` command and by two triggers the
emulator can check: ``minecraft:tick`` (every tick) and ``minecraft:location``
(every 20 ticks, like vanilla), with their ``player`` conditions. Every other
trigger needs gameplay and never fires. A completed advancement gives its
rewards: ``function`` (run as the player), ``experience`` and ``loot``;
recipes are not modelled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator.common import flatten_text_component, normalise_id

#: triggers the emulator fires by itself
TICK_TRIGGER = "minecraft:tick"
LOCATION_TRIGGER = "minecraft:location"
LOCATION_INTERVAL = 20
#: before this, location criteria had their own ``location`` predicate
PLAYER_LOCATION_SINCE = "1.19"


@dataclass
class Advancement:
    id: str
    parent: str | None = None
    criteria: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: groups of criteria: every group needs one of its criteria
    requirements: list[list[str]] = field(default_factory=list)
    rewards: dict[str, Any] = field(default_factory=dict)
    title: str = ""

    @classmethod
    def from_json(cls, advancement_id: str, data: dict[str, Any]) -> Advancement:
        criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
        requirements = data.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            requirements = [[name] for name in criteria]
        # 1.20.x "strategy"-less form: a flat list means each group of one
        groups = [
            [str(entry) for entry in group] if isinstance(group, list) else [str(group)]
            for group in requirements
        ]
        display = data.get("display") if isinstance(data.get("display"), dict) else {}
        title = flatten_text_component(display.get("title")) if display.get("title") else ""
        parent = data.get("parent")
        return cls(
            advancement_id,
            normalise_id(parent) if isinstance(parent, str) else None,
            {str(k): v for k, v in criteria.items() if isinstance(v, dict)},
            groups,
            data.get("rewards") if isinstance(data.get("rewards"), dict) else {},
            title,
        )

    @property
    def shown(self) -> str:
        """How feedback names it: the title in brackets, or the bare id."""
        return f"[{self.title}]" if self.title else self.id

    def done(self, criteria: set[str]) -> bool:
        return bool(self.requirements) and all(
            any(name in criteria for name in group) for group in self.requirements
        )


class AdvancementTree:
    """Every advancement a server of this version loads: the pack's, then the
    jar's (read when first needed)."""

    def __init__(
        self,
        pack: dict[str, dict[str, Any]] | None = None,
        vanilla: Callable[[], dict[str, dict[str, Any]]] | None = None,
    ):
        self._pack = pack or {}
        self._vanilla_source = vanilla
        self._advancements: dict[str, Advancement] | None = None
        self._pack_cache = {
            key: Advancement.from_json(key, value) for key, value in self._pack.items()
        }

    def _load(self) -> dict[str, Advancement]:
        if self._advancements is None:
            raw: dict[str, dict[str, Any]] = {}
            if self._vanilla_source is not None:
                raw.update(self._vanilla_source())
            raw.update(self._pack)
            self._advancements = {
                key: Advancement.from_json(key, value) for key, value in raw.items()
            }
        return self._advancements

    def pack_advancements(self) -> Iterator[Advancement]:
        """The pack's own advancements (the ones whose triggers are checked)."""
        loaded = self._pack_only()
        yield from loaded.values()

    def _pack_only(self) -> dict[str, Advancement]:
        return self._pack_cache

    def get(self, advancement_id: str) -> Advancement | None:
        pack = self._pack_only()
        if advancement_id in pack:
            return pack[advancement_id]
        return self._load().get(advancement_id)

    def all(self) -> list[Advancement]:
        return list(self._load().values())

    def children(self, advancement_id: str) -> list[Advancement]:
        return [adv for adv in self._load().values() if adv.parent == advancement_id]

    def descendants(self, advancement_id: str) -> list[Advancement]:
        """Children depth first, each before its own children (vanilla's order)."""
        out: list[Advancement] = []
        seen = {advancement_id}

        def visit(parent: str) -> None:
            for child in self.children(parent):
                if child.id not in seen:
                    seen.add(child.id)
                    out.append(child)
                    visit(child.id)

        visit(advancement_id)
        return out

    def ancestors(self, advancement_id: str) -> list[Advancement]:
        """The parent first, then its parent, up to the root."""
        out: list[Advancement] = []
        current = self.get(advancement_id)
        seen = {advancement_id}
        while current is not None and current.parent and current.parent not in seen:
            seen.add(current.parent)
            current = self.get(current.parent)
            if current is not None:
                out.append(current)
        return out


class Progress:
    """Criteria each player has, by advancement."""

    def __init__(self) -> None:
        self.tree = AdvancementTree()
        #: holder -> advancement id -> granted criteria
        self.granted: dict[str, dict[str, set[str]]] = {}
        #: called with (holder, advancement) when one is completed
        self.on_complete: Callable[[str, Advancement], None] | None = None

    def criteria(self, holder: str, advancement_id: str) -> set[str]:
        return self.granted.setdefault(holder, {}).setdefault(advancement_id, set())

    def done(self, holder: str, advancement: Advancement) -> bool:
        return advancement.done(self.granted.get(holder, {}).get(advancement.id, set()))

    def grant(self, holder: str, advancement: Advancement, criterion: str | None = None) -> bool:
        """Grant one criterion (or all); whether anything changed. Granting a
        whole advancement that is already done changes nothing, like vanilla."""
        was_done = self.done(holder, advancement)
        if criterion is None and was_done:
            return False
        have = self.criteria(holder, advancement.id)
        names = [criterion] if criterion is not None else list(advancement.criteria)
        added = [name for name in names if name not in have]
        have.update(added)
        if added and not was_done and self.done(holder, advancement) and self.on_complete:
            self.on_complete(holder, advancement)
        return bool(added)

    def revoke(self, holder: str, advancement: Advancement, criterion: str | None = None) -> bool:
        have = self.criteria(holder, advancement.id)
        names = [criterion] if criterion is not None else list(have)
        removed = [name for name in names if name in have]
        have.difference_update(removed)
        return bool(removed)

    def matches(self, holder: str, advancement_id: str, wanted: Any) -> bool:
        """The ``advancements=`` selector / player predicate: ``true``/``false``,
        or ``{criterion: true|false}``."""
        advancement = self.tree.get(advancement_id)
        if advancement is None:
            return False
        if isinstance(wanted, dict):
            have = self.granted.get(holder, {}).get(advancement_id, set())
            return all(
                name in advancement.criteria and (name in have) == bool(value)
                for name, value in wanted.items()
            )
        return self.done(holder, advancement) == bool(wanted)

    def completed(self, holder: str) -> list[str]:
        return sorted(
            advancement_id
            for advancement_id in self.granted.get(holder, {})
            if (advancement := self.tree.get(advancement_id)) is not None
            and self.done(holder, advancement)
        )
