"""The emulated server state: entities, scoreboards, storage, selectors.

Deliberately small — no blocks, no chunks, no physics.  Enough to make
selectors, scores, tags and NBT behave, which is what datapack logic is built
out of.
"""

from __future__ import annotations

import random
import uuid as _uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.commands.parser import Selector
from datapack_emulator.emulator.common import (
    distance_squared,
    in_range,
    normalise_id,
    parse_snbt,
    split_arguments,
)

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.context import ExecutionContext


@dataclass
class Entity:
    """A minimal entity: enough for selectors, tags, scores and NBT probing."""

    type: str = "minecraft:marker"
    uuid: str = field(default_factory=lambda: str(_uuid.uuid4()))
    name: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0])
    dimension: str = "minecraft:overworld"
    tags: set[str] = field(default_factory=set)
    nbt: dict[str, Any] = field(default_factory=dict)
    is_player: bool = False

    @property
    def id(self) -> str:
        return self.name or self.uuid

    @property
    def display(self) -> str:
        """How vanilla names the entity in feedback."""
        return self.name or self.type.split(":")[-1].replace("_", " ").title()

    def __repr__(self) -> str:
        return f"<Entity {self.type} {self.id} tags={sorted(self.tags)}>"


class Scoreboard:
    def __init__(self) -> None:
        self.objectives: dict[str, str] = {}  # name -> criterion
        self.scores: dict[str, dict[str, int]] = {}  # holder -> objective -> value
        self.enabled_triggers: set[tuple[str, str]] = set()

    def add_objective(self, name: str, criterion: str = "dummy") -> bool:
        if name in self.objectives:
            return False
        self.objectives[name] = criterion
        return True

    def remove_objective(self, name: str) -> bool:
        existed = self.objectives.pop(name, None) is not None
        for holder in self.scores.values():
            holder.pop(name, None)
        return existed

    def get(self, holder: str, objective: str) -> int | None:
        return self.scores.get(holder, {}).get(objective)

    def set(self, holder: str, objective: str, value: int) -> None:
        # scores are Java ints: they wrap around at 32 bits
        wrapped = (int(value) + 2**31) % 2**32 - 2**31
        self.scores.setdefault(holder, {})[objective] = wrapped

    def tracked(self) -> list[str]:
        """Every holder with a score in any objective — what ``*`` means."""
        return [holder for holder, values in self.scores.items() if values]

    def add(self, holder: str, objective: str, delta: int) -> int:
        value = (self.get(holder, objective) or 0) + int(delta)
        self.set(holder, objective, value)
        return value

    def reset(self, holder: str, objective: str | None = None) -> None:
        if objective is None:
            self.scores.pop(holder, None)
        else:
            self.scores.get(holder, {}).pop(objective, None)


class World:
    """Entities, scoreboard, command storage and gamerules."""

    def __init__(self, players: int = 1, seed: int = 0) -> None:
        self.entities: list[Entity] = []
        self.scoreboard = Scoreboard()
        self.storage: dict[str, dict[str, Any]] = {}
        self.gamerules: dict[str, str] = {
            "maxCommandChainLength": "65536",
            "sendCommandFeedback": "true",
        }
        self.tick: int = 0
        self.random = random.Random(seed)
        for index in range(players):
            self.spawn(Entity(type="minecraft:player", name=f"Player{index + 1}", is_player=True))

    # -- entities ---------------------------------------------------------

    def spawn(self, entity: Entity) -> Entity:
        self.entities.append(entity)
        return entity

    def kill(self, entity: Entity) -> None:
        if entity in self.entities:
            self.entities.remove(entity)

    @property
    def players(self) -> list[Entity]:
        return [entity for entity in self.entities if entity.is_player]

    # -- selector resolution ----------------------------------------------

    def select(self, selector: Selector, context: ExecutionContext) -> list[Entity]:
        if selector.kind == "literal":
            name = selector.raw
            return [
                entity for entity in self.entities if entity.name == name or entity.uuid == name
            ]
        if selector.kind == "@s":
            executor = context.executor
            if executor is None or not self._matches(executor, selector, context):
                return []
            return [executor]

        pool = self.players if selector.is_player_only else list(self.entities)
        pool = [entity for entity in pool if self._matches(entity, selector, context)]

        origin = _selector_origin(selector, context)
        sort = selector.first("sort") or {
            "@p": "nearest",
            "@n": "nearest",
            "@r": "random",
        }.get(selector.kind, "arbitrary")
        if sort == "nearest":
            pool.sort(key=lambda entity: distance_squared(entity.position, origin))
        elif sort == "furthest":
            pool.sort(key=lambda entity: -distance_squared(entity.position, origin))
        elif sort == "random":
            self.random.shuffle(pool)

        limit = selector.limit
        if limit is None and selector.kind in ("@p", "@r", "@n"):
            limit = 1
        return pool[:limit] if limit is not None else pool

    def _matches(self, entity: Entity, selector: Selector, context: ExecutionContext) -> bool:
        arguments = selector.arguments
        for raw in arguments.get("type", []):
            negated = raw.startswith("!")
            token = raw.lstrip("!")
            if token.startswith("#"):
                # an entity type tag: resolved from the client jar when one is
                # loaded, otherwise there is nothing to match against
                members = self._tag_members(context, token)
                if members is None:
                    continue
                if (entity.type in members) == negated:
                    return False
                continue
            if (entity.type == normalise_id(token)) == negated:
                return False
        for raw in arguments.get("tag", []):
            negated = raw.startswith("!")
            wanted = raw.lstrip("!").strip()
            if not wanted:  # `tag=` means "has no tags at all"
                if bool(entity.tags) != negated:
                    return False
                continue
            if (wanted in entity.tags) == negated:
                return False
        for raw in arguments.get("name", []):
            negated = raw.startswith("!")
            if (entity.name == raw.lstrip("!").strip('"')) == negated:
                return False
        for raw in arguments.get("scores", []):
            if not self._matches_scores(entity, raw):
                return False
        origin = _selector_origin(selector, context)
        for raw in arguments.get("distance", []):
            distance = distance_squared(entity.position, origin) ** 0.5
            if not in_range(distance, raw):
                return False
        if any(key in arguments for key in ("dx", "dy", "dz")) and not _inside_volume(
            entity, selector, origin
        ):
            return False
        for raw in arguments.get("nbt", []):
            negated = raw.startswith("!")
            pattern = parse_snbt(raw.lstrip("!"))
            data = _entity_data(entity)
            unmodelled = sorted(key for key in pattern if key not in data)
            if unmodelled:
                context.note_once(
                    f"selector nbt={{...}}: {', '.join(unmodelled)} not stored for "
                    f"{entity.type} by the emulator, so the check fails"
                )
            if nbt_matches(data, pattern) == negated:
                return False
        for key in selector.arguments:
            if key in UNMODELLED_SELECTOR_ARGUMENTS:
                context.note_once(
                    f"selector argument '{key}=' is not emulated and matches every entity"
                )
        return True

    @staticmethod
    def _tag_members(context: ExecutionContext, tag: str) -> set[str] | None:
        assets = getattr(context.emulator, "vanilla", None)
        if assets is None:
            return None
        return assets.resolve_tag("entity_type", tag)

    def _matches_scores(self, entity: Entity, raw: str) -> bool:
        body = raw.strip().lstrip("{").rstrip("}")
        for objective, condition in split_arguments(body):
            value = self.scoreboard.get(entity.id, objective)
            if value is None or not in_range(value, condition):
                return False
        return True


#: selector arguments the world model has nothing to check against
UNMODELLED_SELECTOR_ARGUMENTS = frozenset(
    {"team", "gamemode", "level", "advancements", "predicate", "x_rotation", "y_rotation"}
)


def _float_argument(selector: Selector, key: str) -> float | None:
    raw = selector.first(key)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _selector_origin(selector: Selector, context: ExecutionContext) -> list[float]:
    """``x=``/``y=``/``z=`` replace the matching coordinate of the command origin."""
    origin = list(context.position)
    for index, key in enumerate(("x", "y", "z")):
        value = _float_argument(selector, key)
        if value is not None:
            origin[index] = value
    return origin


def _inside_volume(entity: Entity, selector: Selector, origin: list[float]) -> bool:
    """``dx``/``dy``/``dz``: a box from the origin, one block larger than the deltas,
    tested against the entity's position (bounding boxes are not modelled)."""
    for index, key in enumerate(("dx", "dy", "dz")):
        delta = _float_argument(selector, key) or 0.0
        low, high = sorted((origin[index], origin[index] + delta))
        if not low <= entity.position[index] < high + 1:
            return False
    return True


def _entity_data(entity: Entity) -> dict[str, Any]:
    """The NBT a selector sees: what was summoned or set, plus the entity's tags
    (``tag`` writes into ``Tags`` in vanilla, but they are kept apart here)."""
    data = dict(entity.nbt)
    if entity.tags:
        data["Tags"] = sorted(set(data.get("Tags", [])) | entity.tags)
    return data


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
