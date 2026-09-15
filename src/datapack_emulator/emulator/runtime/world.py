"""The emulated server state: entities, scoreboards, storage, selectors.

Deliberately small — no blocks, no chunks, no physics.  Enough to make
selectors, scores, tags and NBT behave, which is what datapack logic is built
out of.
"""

from __future__ import annotations

import copy
import random
import uuid as _uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.commands.parser import Selector
from datapack_emulator.emulator.common import (
    distance_squared,
    flatten_text_component,
    in_range,
    load_text_component,
    nbt_matches,
    normalise_id,
    parse_snbt,
    split_arguments,
)
from datapack_emulator.emulator.runtime.inventory import INVENTORY_KEYS, Inventory

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.context import ExecutionContext


#: NBT every entity reports besides what was summoned or set on it
ENTITY_DEFAULTS: dict[str, Any] = {
    "Motion": [0.0, 0.0, 0.0],
    "FallDistance": 0.0,
    "Fire": -1,
    "Air": 300,
    "OnGround": 0,
    "Invulnerable": 0,
    "PortalCooldown": 0,
}
#: what a player adds (abilities are not modelled; the inventory has its own model)
PLAYER_DEFAULTS: dict[str, Any] = {
    "Health": 20.0,
    "foodLevel": 20,
    "XpLevel": 0,
    "XpP": 0.0,
    "playerGameType": 0,
}
#: kept in the entity's own fields, not in ``nbt``
SYNCED_KEYS = ("Pos", "Rotation", "UUID", "Tags", *INVENTORY_KEYS)


def normalise_rotation(rotation: list[float]) -> list[float]:
    """Yaw wrapped to [-180, 180), pitch clamped to [-90, 90], as entities store them."""
    yaw = (float(rotation[0]) + 180.0) % 360.0 - 180.0
    pitch = max(-90.0, min(90.0, float(rotation[1])))
    return [yaw, pitch]


def uuid_to_ints(value: str) -> list[int]:
    """A UUID as NBT stores it: four signed 32-bit ints, most significant first."""
    number = _uuid.UUID(value).int
    parts = [(number >> shift) & 0xFFFFFFFF for shift in (96, 64, 32, 0)]
    return [part - 2**32 if part >= 2**31 else part for part in parts]


def ints_to_uuid(parts: list[int]) -> str | None:
    if not isinstance(parts, list) or len(parts) != 4:
        return None
    try:
        number = 0
        for part in parts:
            number = (number << 32) | (int(part) & 0xFFFFFFFF)
    except (TypeError, ValueError):
        return None
    return str(_uuid.UUID(int=number))


@dataclass
class Entity:
    """An entity: position, rotation, tags and the rest of its NBT.

    ``nbt`` holds everything that was summoned, merged or modified onto the
    entity except the keys the emulator keeps as fields (``Pos``, ``Rotation``,
    ``UUID``, ``Tags``); :meth:`data` puts them back together the way ``data
    get entity`` shows them.
    """

    type: str = "minecraft:marker"
    uuid: str = field(default_factory=lambda: str(_uuid.uuid4()))
    name: str = ""
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0])
    dimension: str = "minecraft:overworld"
    tags: set[str] = field(default_factory=set)
    nbt: dict[str, Any] = field(default_factory=dict)
    is_player: bool = False
    #: the game time it was summoned at
    born: int = 0
    inventory: Inventory = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.inventory is None:
            self.inventory = Inventory(player=self.is_player)

    @property
    def id(self) -> str:
        """The score holder: a player's name, otherwise the UUID."""
        return self.name or self.uuid

    @property
    def display(self) -> str:
        """How vanilla names the entity in feedback: player name, custom name, type."""
        if self.name:
            return self.name
        custom = self.nbt.get("CustomName")
        if custom:
            component = load_text_component(custom) if isinstance(custom, str) else custom
            text = flatten_text_component(component if component is not None else custom)
            if text:
                return text
        return self.type.split(":")[-1].replace("_", " ").title()

    def data(self, version: Any = None) -> dict[str, Any]:
        """The entity's full NBT, as ``data get entity`` shows it (a copy).

        ``version`` picks the item format (see :mod:`runtime.inventory`); without
        one the newest format is used.
        """
        data: dict[str, Any] = {
            "Pos": [float(value) for value in self.position],
            **copy.deepcopy(ENTITY_DEFAULTS),
            "Rotation": [float(value) for value in self.rotation],
            "UUID": uuid_to_ints(self.uuid),
        }
        if self.is_player:
            data.update(copy.deepcopy(PLAYER_DEFAULTS))
            data["Dimension"] = self.dimension
        else:
            data["id"] = self.type
        data.update(copy.deepcopy(self.nbt))
        data.update(self.inventory.to_nbt(version))
        if self.tags:
            data["Tags"] = sorted(self.tags)
        return data

    def apply_data(self, data: dict[str, Any]) -> None:
        """Load NBT back onto the entity, like ``Entity.load``: position,
        rotation and tags follow; the UUID never changes."""
        position = data.get("Pos")
        if _numbers(position, 3):
            self.position = [float(value) for value in position]
        rotation = data.get("Rotation")
        if _numbers(rotation, 2):
            self.rotation = normalise_rotation(rotation)
        tags = data.get("Tags")
        if isinstance(tags, list):  # without the key, Entity.load keeps the tags
            self.tags = {str(tag) for tag in tags}
        self.inventory.load_nbt(data)
        defaults = {**ENTITY_DEFAULTS, **(PLAYER_DEFAULTS if self.is_player else {})}
        self.nbt = {
            key: copy.deepcopy(value)
            for key, value in data.items()
            if key not in SYNCED_KEYS
            and key not in ("id", "Dimension")
            and not (key in defaults and defaults[key] == value)
        }

    def __repr__(self) -> str:
        return f"<Entity {self.type} {self.id} tags={sorted(self.tags)}>"


#: criteria whose scores the game computes; commands cannot change them
READ_ONLY_CRITERIA = frozenset({"health", "food", "air", "armor", "xp", "level"})


def wrap_int(value: int) -> int:
    """Scores are Java ints: they wrap around at 32 bits."""
    return (int(value) + 2**31) % 2**32 - 2**31


class Scoreboard:
    #: changes remembered per score, for the scoreboard grid's history
    HISTORY = 64

    def __init__(self, clock: Callable[[], int] = lambda: 0) -> None:
        self.objectives: dict[str, str] = {}  # name -> criterion
        self.display_names: dict[str, str] = {}  # name -> display name (text)
        self.display_slots: dict[str, str] = {}  # slot -> objective
        self.scores: dict[str, dict[str, int]] = {}  # holder -> objective -> value
        self.enabled_triggers: set[tuple[str, str]] = set()
        #: (holder, objective) -> (tick, value or None when reset), oldest first
        self.history: dict[tuple[str, str], deque[tuple[int, int | None]]] = {}
        self._clock = clock

    def add_objective(self, name: str, criterion: str = "dummy", display: str = "") -> bool:
        if name in self.objectives:
            return False
        self.objectives[name] = criterion
        self.display_names[name] = display or name
        return True

    def remove_objective(self, name: str) -> bool:
        existed = self.objectives.pop(name, None) is not None
        self.display_names.pop(name, None)
        for holder, values in self.scores.items():
            if values.pop(name, None) is not None:
                self._remember(holder, name, None)
        self.enabled_triggers = {pair for pair in self.enabled_triggers if pair[1] != name}
        self.display_slots = {s: o for s, o in self.display_slots.items() if o != name}
        return existed

    def is_read_only(self, objective: str) -> bool:
        return self.objectives.get(objective) in READ_ONLY_CRITERIA

    def get(self, holder: str, objective: str) -> int | None:
        return self.scores.get(holder, {}).get(objective)

    def set(self, holder: str, objective: str, value: int) -> None:
        wrapped = wrap_int(value)
        values = self.scores.setdefault(holder, {})
        if values.get(objective) != wrapped or objective not in values:
            self._remember(holder, objective, wrapped)
        values[objective] = wrapped

    def tracked(self) -> list[str]:
        """Every holder with a score in any objective — what ``*`` means."""
        return [holder for holder, values in self.scores.items() if values]

    def add(self, holder: str, objective: str, delta: int) -> int:
        value = wrap_int((self.get(holder, objective) or 0) + int(delta))
        self.set(holder, objective, value)
        return value

    def reset(self, holder: str, objective: str | None = None) -> bool:
        """Remove one score, or all of a holder's; whether anything was there."""
        values = self.scores.get(holder, {})
        removed = [objective] if objective is not None else list(values)
        removed = [name for name in removed if name in values]
        for name in removed:
            del values[name]
            self._remember(holder, name, None)
            # the lock lives on the score: a reset score is no longer enabled
            self.enabled_triggers.discard((holder, name))
        if not values:
            self.scores.pop(holder, None)
        return bool(removed)

    def forget_holder(self, holder: str) -> None:
        """A removed entity: its scores, trigger locks and history go with it."""
        self.scores.pop(holder, None)
        self.enabled_triggers = {pair for pair in self.enabled_triggers if pair[0] != holder}
        for key in [key for key in self.history if key[0] == holder]:
            del self.history[key]

    def _remember(self, holder: str, objective: str, value: int | None) -> None:
        changes = self.history.setdefault((holder, objective), deque(maxlen=self.HISTORY))
        changes.append((self._clock(), value))


class World:
    """Entities, scoreboard, command storage and gamerules."""

    def __init__(self, players: int = 1, seed: int = 0) -> None:
        self.entities: list[Entity] = []
        self.scoreboard = Scoreboard(clock=lambda: self.tick)
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
        entity.born = self.tick
        self.entities.append(entity)
        return entity

    def entity_by_id(self, holder: str) -> Entity | None:
        """The entity a score holder or literal name refers to, if any."""
        for entity in self.entities:
            if entity.id == holder or entity.uuid == holder:
                return entity
        return None

    def kill(self, entity: Entity) -> None:
        """Killed players respawn (nothing about them is modelled to reset);
        any other entity is removed."""
        if entity.is_player:
            return
        if entity in self.entities:
            self.entities.remove(entity)
            # vanilla drops the scores of an entity that is removed for good
            self.scoreboard.forget_holder(entity.id)

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
            data = _entity_data(entity, context)
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


def _entity_data(entity: Entity, context: ExecutionContext) -> dict[str, Any]:
    """The NBT a selector's ``nbt=`` sees, in the emulated version's format."""
    return entity.data(context.emulator.version)


def _numbers(value: Any, count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )
