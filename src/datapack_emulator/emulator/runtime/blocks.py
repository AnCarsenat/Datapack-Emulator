"""Blocks: a sparse world of the positions commands touched.

Only positions a command sets are stored; everything else is air. A stored
block has an id, the block-state properties it was given and, for blocks with
a block entity, its NBT — containers keep their items as
:class:`~datapack_emulator.emulator.runtime.inventory.ItemStack` slots, written
in the emulated version's item format when read.

What is a model, not the game:

* default block states are not known: a block keeps the properties it was
  placed with (``stone_stairs`` has none until given ``facing=…``), so a
  predicate asking for a property the block was not given does not match;
* which blocks have a block entity, and how many slots a container has, come
  from the lists below, not from the game's registries;
* nothing ticks: no gravity, no redstone, no fluids, no block updates.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import nbt_matches, normalise_id, parse_snbt
from datapack_emulator.emulator.runtime.inventory import ItemStack
from datapack_emulator.emulator.versions import Version

AIR = "minecraft:air"
AIR_BLOCKS = frozenset({AIR, "minecraft:cave_air", "minecraft:void_air"})

#: vanilla's default for commandModificationBlockLimit (fill, clone, fillbiome)
MODIFICATION_LIMIT = 32_768
#: the gamerule that sets it, from 1.19.3 (spelled differently from 1.21.11)
LIMIT_RULES = ("commandModificationBlockLimit", "command_modification_block_limit")
#: the world got deeper in 1.18
DEEPER_WORLD_SINCE = "1.18"

Position = tuple[int, int, int]
Key = tuple[str, int, int, int]

#: container blocks and their slot count (what ``item … block`` can reach)
_CONTAINER_SIZES: dict[str, int] = {
    "chest": 27,
    "trapped_chest": 27,
    "barrel": 27,
    "shulker_box": 27,
    "dispenser": 9,
    "dropper": 9,
    "crafter": 9,
    "hopper": 5,
    "brewing_stand": 5,
    "furnace": 3,
    "blast_furnace": 3,
    "smoker": 3,
    "chiseled_bookshelf": 6,
    "decorated_pot": 1,
    "jukebox": 1,
    "shelf": 3,
}
#: containers that keep one item under their own key instead of ``Items``
_SINGLE_ITEM_KEYS = {"jukebox": "RecordItem", "decorated_pot": "item"}
#: block id suffix -> block entity type, where they differ
_ENTITY_TYPES: tuple[tuple[str, str], ...] = (
    ("_wall_hanging_sign", "hanging_sign"),
    ("_hanging_sign", "hanging_sign"),
    ("_wall_sign", "sign"),
    ("_sign", "sign"),
    ("_wall_banner", "banner"),
    ("_banner", "banner"),
    ("_wall_head", "skull"),
    ("_wall_skull", "skull"),
    ("_head", "skull"),
    ("_skull", "skull"),
    ("_bed", "bed"),
    ("shulker_box", "shulker_box"),
    ("soul_campfire", "campfire"),
    ("suspicious_sand", "brushable_block"),
    ("suspicious_gravel", "brushable_block"),
    ("command_block", "command_block"),
    ("_shelf", "shelf"),
    ("moving_piston", "piston"),
    ("copper_golem_statue", "copper_golem_statue"),
)
#: block entities that are not containers (and those whose id is the block id)
_OTHER_BLOCK_ENTITIES = frozenset({
    "sign", "hanging_sign", "banner", "skull", "command_block", "spawner", "beacon", "bed",
    "bell", "beehive", "bee_nest", "conduit", "comparator", "daylight_detector",
    "enchanting_table", "end_gateway", "end_portal", "ender_chest", "jigsaw",
    "structure_block", "piston", "sculk_sensor", "calibrated_sculk_sensor",
    "sculk_catalyst", "sculk_shrieker", "trial_spawner", "vault", "brushable_block",
    "creaking_heart", "test_block", "test_instance_block", "copper_golem_statue",
    "campfire", "lectern",
})  # fmt: skip


def entity_type(block_id: str) -> str | None:
    """The block entity type of a block (``oak_sign`` -> ``minecraft:sign``); None
    for blocks without one."""
    namespace, _, path = block_id.rpartition(":")
    if path == "piston_head":
        return None
    kind = path
    for suffix, name in _ENTITY_TYPES:
        if path.endswith(suffix):
            kind = name
            break
    if kind in _CONTAINER_SIZES or kind in _OTHER_BLOCK_ENTITIES:
        return f"{namespace or 'minecraft'}:{kind}"
    return None


def container_size(block_id: str) -> int:
    """Slots of a container block; 0 for any other block."""
    kind = entity_type(block_id)
    return _CONTAINER_SIZES.get(kind.split(":", 1)[1], 0) if kind else 0


def has_block_entity(block_id: str) -> bool:
    return entity_type(block_id) is not None


def is_shulker_box(block_id: str) -> bool:
    return block_id.endswith("shulker_box")


#: dimensions whose default type is 256 blocks high
_SHORT_DIMENSIONS = ("minecraft:the_nether", "minecraft:the_end")
#: blocks further out than this are outside the world
HORIZONTAL_LIMIT = 30_000_000


def world_height(dimension: str, version: Version | None) -> tuple[int, int]:
    """The lowest and highest block y a command may touch (default dimension
    types; a pack's own dimensions are taken as overworld-like)."""
    deeper = version is None or version >= versions.parse(DEEPER_WORLD_SINCE)
    if dimension not in _SHORT_DIMENSIONS and deeper:
        return (-64, 319)
    return (0, 255)


def in_world(position: Position, dimension: str, version: Version | None) -> bool:
    low, high = world_height(dimension, version)
    return low <= position[1] <= high and all(
        -HORIZONTAL_LIMIT <= position[axis] < HORIZONTAL_LIMIT for axis in (0, 2)
    )


def block_position(values: list[float]) -> Position:
    """The block a position is in (coordinates floored)."""
    return (math.floor(values[0]), math.floor(values[1]), math.floor(values[2]))


# ---------------------------------------------------------------------------
# block states and predicates
# ---------------------------------------------------------------------------


@dataclass
class Block:
    id: str = AIR
    properties: dict[str, str] = field(default_factory=dict)
    #: block entity data besides the items (and without id / x / y / z)
    nbt: dict[str, Any] = field(default_factory=dict)
    #: container slots: index -> stack
    items: dict[int, ItemStack] = field(default_factory=dict)

    @property
    def is_air(self) -> bool:
        return self.id in AIR_BLOCKS

    @property
    def is_container(self) -> bool:
        return container_size(self.id) > 0

    @property
    def has_entity(self) -> bool:
        return has_block_entity(self.id)

    @property
    def state(self) -> str:
        """``minecraft:oak_stairs[facing=east,half=top]``"""
        if not self.properties:
            return self.id
        inner = ",".join(f"{key}={value}" for key, value in sorted(self.properties.items()))
        return f"{self.id}[{inner}]"

    def copy(self) -> Block:
        return Block(
            self.id,
            dict(self.properties),
            copy.deepcopy(self.nbt),
            {slot: stack.copy() for slot, stack in self.items.items()},
        )

    def same_state(self, other: Block) -> bool:
        """The same block state (vanilla's setBlock compares only that)."""
        return self.id == other.id and self.properties == other.properties

    def same(self, other: Block) -> bool:
        return self.same_state(other) and self.data(None) == other.data(None)

    def clear(self) -> None:
        """Empty a container, as vanilla does before replacing a block."""
        self.items = {}

    # -- NBT --------------------------------------------------------------

    def data(self, version: Version | None, position: Position | None = None) -> dict[str, Any]:
        """The block entity NBT, as ``data get block`` shows it (a copy)."""
        if not self.has_entity:
            return {}
        data: dict[str, Any] = {}
        if position is not None:
            data.update({"x": position[0], "y": position[1], "z": position[2]})
        data["id"] = entity_type(self.id)
        data.update(copy.deepcopy(self.nbt))
        if self.is_container:
            single = _SINGLE_ITEM_KEYS.get(data["id"].split(":", 1)[1])
            if single is not None:
                if 0 in self.items:
                    data[single] = self.items[0].to_nbt(version)
            elif self.items or not is_shulker_box(self.id):
                # vanilla writes the list even when empty, but for shulker boxes
                data["Items"] = [
                    {"Slot": slot, **stack.to_nbt(version)}
                    for slot, stack in sorted(self.items.items())
                ]
        return data

    def apply_data(self, data: dict[str, Any]) -> None:
        """Load block entity NBT back; ``id`` and the position never change."""
        rest = {key: value for key, value in data.items() if key not in ("x", "y", "z", "id")}
        if self.is_container:
            items: dict[int, ItemStack] = {}
            size = container_size(self.id)
            single = _SINGLE_ITEM_KEYS.get(str(entity_type(self.id)).split(":", 1)[-1])
            if single is not None:
                stack = ItemStack.from_nbt(rest.pop(single, None))
                self.items = {0: stack} if stack is not None else {}
                self.nbt = copy.deepcopy(rest)
                return
            for entry in rest.pop("Items", None) or []:
                stack = ItemStack.from_nbt(entry)
                # like NBT's getByte, a missing Slot reads as 0
                slot = entry.get("Slot", 0) if isinstance(entry, dict) else None
                if stack is not None and isinstance(slot, int) and 0 <= slot < size:
                    items[slot] = stack
            self.items = items
        self.nbt = copy.deepcopy(rest)

    # -- container slots --------------------------------------------------

    def slot_keys(self, slot: str) -> list[int] | None:
        """``container.N`` (or ``container.*``) -> slot indexes; None if no such slot."""
        size = container_size(self.id)
        if not size:
            return None
        name, _, index = slot.strip().partition(".")
        if name != "container":
            return None
        if index == "*":
            return list(range(size))
        if not index.isdecimal() or int(index) >= size:
            return None
        return [int(index)]

    def get_item(self, slot: int) -> ItemStack | None:
        return self.items.get(slot)

    def set_item(self, slot: int, stack: ItemStack | None) -> None:
        if stack is None or stack.count <= 0 or stack.id == "minecraft:air":
            self.items.pop(slot, None)
        else:
            self.items[slot] = stack

    def insert(self, stack: ItemStack) -> bool:
        """``loot insert``: one pass in slot order, merging into matching stacks
        and stopping at the first empty slot (vanilla's distributeToContainer).
        Whether anything went in; what does not fit is lost."""
        stack = stack.copy()
        changed = False
        for index in range(container_size(self.id)):
            if stack.count <= 0:
                break
            current = self.items.get(index)
            if current is None:
                self.items[index] = stack
                return True
            if current.same_kind(stack):
                moved = min(stack.count, stack.max_count - current.count)
                if moved > 0:
                    current.count += moved
                    stack.count -= moved
                    changed = True
        return changed


def _split_properties(body: str) -> dict[str, str] | None:
    properties: dict[str, str] = {}
    for part in body.split(","):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        if not sep or not key.strip():
            return None
        properties[key.strip()] = value.strip()
    return properties


def _split_state(text: str) -> tuple[str, str, str] | None:
    """``id[props]{nbt}`` -> ``(id, props, nbt)``; None when brackets do not close."""
    text = text.strip()
    cut = min((text.index(mark) for mark in "[{" if mark in text), default=len(text))
    block_id, rest = text[:cut], text[cut:]
    properties = ""
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            return None
        properties, rest = rest[1:end], rest[end + 1 :]
    if rest and not (rest.startswith("{") and rest.endswith("}")):
        return None
    return (block_id, properties, rest)


def parse_block(text: str) -> Block | None:
    """A block state argument (``stone``, ``chest[facing=north]{Items:[…]}``)."""
    parts = _split_state(text)
    if parts is None or not parts[0] or parts[0].startswith("#"):
        return None
    block_id, raw_properties, raw_nbt = parts
    properties = _split_properties(raw_properties)
    if properties is None:
        return None
    block = Block(normalise_id(block_id), properties)
    if raw_nbt and block.has_entity:
        block.apply_data(parse_snbt(raw_nbt))
    return block


@dataclass
class BlockPredicate:
    """A block predicate argument: an id or ``#tag``, properties, NBT."""

    id: str
    properties: dict[str, str] = field(default_factory=dict)
    nbt: dict[str, Any] | None = None
    #: the ids a tag resolves to; None when the tag is unknown (then any id matches)
    tag_members: set[str] | None = None
    #: properties asked for that the stored block was never given (see the module note)
    unknown_properties: set[str] = field(default_factory=set)

    @property
    def is_tag(self) -> bool:
        return self.id.startswith("#")

    def matches(self, block: Block, version: Version | None = None) -> bool:
        if self.is_tag:
            if self.tag_members is not None and block.id not in self.tag_members:
                return False
        elif block.id != self.id:
            return False
        for key, value in self.properties.items():
            if key not in block.properties:
                self.unknown_properties.add(key)
                return False
            if block.properties[key] != value:
                return False
        return self.nbt is None or nbt_matches(block.data(version), self.nbt)


def parse_block_predicate(text: str) -> BlockPredicate | None:
    parts = _split_state(text)
    if parts is None or not parts[0]:
        return None
    block_id, raw_properties, raw_nbt = parts
    properties = _split_properties(raw_properties)
    if properties is None:
        return None
    wanted = (
        "#" + normalise_id(block_id[1:]) if block_id.startswith("#") else normalise_id(block_id)
    )
    return BlockPredicate(wanted, properties, parse_snbt(raw_nbt) if raw_nbt else None)


# ---------------------------------------------------------------------------
# the block world
# ---------------------------------------------------------------------------


class Blocks:
    """Every block a command set, by dimension and position."""

    def __init__(self) -> None:
        self.blocks: dict[Key, Block] = {}

    def get(self, dimension: str, position: Position) -> Block:
        """The block there (a shared air block when nothing was set)."""
        return self.blocks.get((dimension, *position)) or Block()

    def stored(self, dimension: str, position: Position) -> Block | None:
        return self.blocks.get((dimension, *position))

    def set(self, dimension: str, position: Position, block: Block) -> None:
        key = (dimension, *position)
        if block.is_air:
            self.blocks.pop(key, None)
        else:
            self.blocks[key] = block

    def items(self) -> Iterator[tuple[Key, Block]]:
        yield from sorted(self.blocks.items())

    def __len__(self) -> int:
        return len(self.blocks)


def box(start: Position, end: Position) -> tuple[Position, Position]:
    """The corners of the box two positions span, lowest first."""
    low = (min(start[0], end[0]), min(start[1], end[1]), min(start[2], end[2]))
    high = (max(start[0], end[0]), max(start[1], end[1]), max(start[2], end[2]))
    return (low, high)


def volume(low: Position, high: Position) -> int:
    return (high[0] - low[0] + 1) * (high[1] - low[1] + 1) * (high[2] - low[2] + 1)


def positions(low: Position, high: Position) -> Iterator[Position]:
    """Every position of a box, in vanilla's order (z, then y, then x)."""
    for z in range(low[2], high[2] + 1):
        for y in range(low[1], high[1] + 1):
            for x in range(low[0], high[0] + 1):
                yield (x, y, z)
