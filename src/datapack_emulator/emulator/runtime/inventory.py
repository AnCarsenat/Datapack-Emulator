"""Items and inventories: stacks, slots, and the NBT each version stores them as.

What is modelled:

* **item stacks** — id, count, components (1.20.5+) or the ``tag`` compound
  (before), and the stack size limit
* **players** — the 36 container slots (hotbar 0–8, inventory 9–35), armour,
  offhand and ender chest; the selected hotbar slot is slot 0
* **other entities** — mainhand, offhand, armour, body armour and saddle

``Inventory.to_nbt`` writes what ``data get entity`` shows in a given version:

============  ==========================================================
before 1.20.5 ``{id, Count, tag}``; players ``Inventory`` with armour in
              slots 100–103 and offhand in -106; mobs ``HandItems`` and
              ``ArmorItems``
1.20.5        ``{id, count, components}``
1.21.5        armour, hands, body and saddle move to ``equipment``
============  ==========================================================
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import (
    nbt_matches,
    normalise_id,
    parse_snbt,
    parse_value,
    split_arguments,
)
from datapack_emulator.emulator.versions import Version

ITEM_COMPONENTS_SINCE = "1.20.5"
EQUIPMENT_SINCE = "1.21.5"

PLAYER_CONTAINER = 36
HOTBAR = 9
ENDER_CHEST = 27
ARMOR = ("head", "chest", "legs", "feet")
#: player Inventory slot numbers before 1.21.5
ARMOR_SLOT_NUMBERS = {"feet": 100, "legs": 101, "chest": 102, "head": 103}
OFFHAND_SLOT_NUMBER = -106

#: items that do not stack; the rest stack to 64 unless listed in SMALL_STACKS
_UNSTACKABLE_SUFFIXES = (
    "_sword", "_pickaxe", "_axe", "_shovel", "_hoe", "_helmet", "_chestplate", "_leggings",
    "_boots", "_horse_armor", "_boat", "_raft", "_minecart", "_bucket", "shulker_box",
    "_bed", "potion", "_banner_pattern", "music_disc_",
)  # fmt: skip
_UNSTACKABLE = frozenset(
    {
        "minecraft:bow", "minecraft:crossbow", "minecraft:trident", "minecraft:shield",
        "minecraft:elytra", "minecraft:fishing_rod", "minecraft:flint_and_steel",
        "minecraft:shears", "minecraft:saddle", "minecraft:cake", "minecraft:totem_of_undying",
        "minecraft:enchanted_book", "minecraft:written_book", "minecraft:writable_book",
        "minecraft:mace", "minecraft:brush", "minecraft:spyglass", "minecraft:carrot_on_a_stick",
        "minecraft:warped_fungus_on_a_stick", "minecraft:turtle_helmet", "minecraft:minecart",
        "minecraft:beef_stew", "minecraft:mushroom_stew", "minecraft:rabbit_stew",
        "minecraft:beetroot_soup", "minecraft:suspicious_stew", "minecraft:milk_bucket",
        "minecraft:bundle", "minecraft:debug_stick", "minecraft:knowledge_book",
    }
)  # fmt: skip
_SIXTEEN = frozenset(
    {
        "minecraft:ender_pearl", "minecraft:snowball", "minecraft:egg", "minecraft:bucket",
        "minecraft:honey_bottle", "minecraft:armor_stand", "minecraft:written_book",
        "minecraft:blue_egg", "minecraft:brown_egg", "minecraft:wind_charge",
    }
)  # fmt: skip


def max_stack_size(item_id: str, components: dict[str, Any] | None = None) -> int:
    if components:
        size = components.get("minecraft:max_stack_size")
        if isinstance(size, int) and size > 0:
            return size
    if item_id in _UNSTACKABLE or any(part in item_id for part in _UNSTACKABLE_SUFFIXES):
        return 1
    if item_id in _SIXTEEN or item_id.endswith(("_sign", "_hanging_sign", "_banner")):
        return 16
    return 64


def uses_components(version: Version | None) -> bool:
    return version is None or version >= versions.parse(ITEM_COMPONENTS_SINCE)


def uses_equipment(version: Version | None) -> bool:
    return version is None or version >= versions.parse(EQUIPMENT_SINCE)


# ---------------------------------------------------------------------------
# stacks
# ---------------------------------------------------------------------------


@dataclass
class ItemStack:
    id: str
    count: int = 1
    #: data components, keys with their namespace (1.20.5+)
    components: dict[str, Any] = field(default_factory=dict)
    #: the ``tag`` compound of versions before 1.20.5
    tag: dict[str, Any] = field(default_factory=dict)

    @property
    def max_count(self) -> int:
        return max_stack_size(self.id, self.components)

    def same_kind(self, other: ItemStack) -> bool:
        """Whether the two stacks can merge: same id and the same data."""
        return self.id == other.id and self.components == other.components and self.tag == other.tag

    def copy(self, count: int | None = None) -> ItemStack:
        return ItemStack(
            self.id,
            self.count if count is None else count,
            copy.deepcopy(self.components),
            copy.deepcopy(self.tag),
        )

    def to_nbt(self, version: Version | None) -> dict[str, Any]:
        if uses_components(version):
            data: dict[str, Any] = {"id": self.id, "count": self.count}
            if self.components:
                data["components"] = copy.deepcopy(self.components)
            return data
        data = {"id": self.id, "Count": self.count}
        if self.tag:
            data["tag"] = copy.deepcopy(self.tag)
        return data

    @classmethod
    def from_nbt(cls, data: Any) -> ItemStack | None:
        """Either format; ``None`` for an empty slot (``{}``, air or count 0)."""
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            return None
        item_id = normalise_id(data["id"])
        count = data.get("count", data.get("Count", 1))
        count = int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) else 1
        if item_id == "minecraft:air" or count <= 0:
            return None
        components = data.get("components")
        tag = data.get("tag")
        return cls(
            item_id,
            count,
            dict(components) if isinstance(components, dict) else {},
            dict(tag) if isinstance(tag, dict) else {},
        )


def _qualify_component(key: str) -> str:
    key = key.strip()
    return key if ":" in key else f"minecraft:{key}"


def parse_item(text: str) -> ItemStack | None:
    """``minecraft:diamond_sword[enchantments={…}]{Damage:5}`` -> a stack of 1.

    Components (``[k=v,…]``, 1.20.5+) and the old NBT tag (``{…}``) are both
    read, whatever the version: the command parser of each version rejects
    the other form, which is outside what this checks.
    """
    text = text.strip()
    if not text:
        return None
    cut = min((text.index(mark) for mark in "[{" if mark in text), default=len(text))
    item_id = normalise_id(text[:cut])
    rest = text[cut:]
    components: dict[str, Any] = {}
    tag: dict[str, Any] = {}
    if rest.startswith("["):
        end = _matching(rest, 0)
        for key, value in split_arguments(rest[1:end]):
            if key.startswith("!"):
                continue  # removing a default component: nothing to store
            components[_qualify_component(key)] = parse_value(value) if value else {}
        rest = rest[end + 1 :]
    if rest.startswith("{"):
        tag = parse_snbt(rest)
    if item_id in ("minecraft:", "minecraft:air") or not text[:cut]:
        return None
    return ItemStack(item_id, 1, components, tag)


def _matching(text: str, start: int) -> int:
    """Index of the bracket closing the one at ``start``."""
    pairs = {"[": "]", "{": "}", "(": ")"}
    depth = 0
    quote: str | None = None
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in pairs:
            depth += 1
        elif char in pairs.values():
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


@dataclass
class ComponentCheck:
    """One test on a stack's components: ``key`` present, ``key=value``, or negated."""

    key: str
    value: Any = None
    has_value: bool = False
    negated: bool = False

    def passes(self, stack: ItemStack) -> bool:
        if self.has_value:
            result = stack.components.get(self.key) == self.value
        else:
            result = self.key in stack.components
        return result != self.negated


@dataclass
class ItemPredicate:
    """What ``clear`` and ``execute if items`` match: an id, ``*`` or ``#tag``;
    then groups of component checks, all groups needed and any check of a group
    enough (``[a|b,c]`` is (a or b) and c); or, before 1.20.5, an NBT tag subset."""

    id: str = "*"
    groups: list[list[ComponentCheck]] = field(default_factory=list)
    tag: dict[str, Any] = field(default_factory=dict)
    #: ids of a #tag, resolved by the caller (None when it could not be resolved)
    tag_members: set[str] | None = None
    #: parts of the predicate the emulator does not check (they pass)
    unchecked: list[str] = field(default_factory=list)

    def matches(self, stack: ItemStack) -> bool:
        if self.id.startswith("#"):
            if self.tag_members is not None and stack.id not in self.tag_members:
                return False
        elif self.id != "*" and stack.id != self.id:
            return False
        for group in self.groups:
            if group and not any(check.passes(stack) for check in group):
                return False
        return nbt_matches(stack.tag, self.tag) if self.tag else True


def parse_item_predicate(text: str) -> ItemPredicate:
    text = text.strip()
    cut = min((text.index(mark) for mark in "[{" if mark in text), default=len(text))
    head = text[:cut]
    predicate = ItemPredicate(id="*" if head == "*" else normalise_tagged(head))
    rest = text[cut:]
    if rest.startswith("["):
        end = _matching(rest, 0)
        for alternatives in _split_top(rest[1:end], ","):
            group: list[ComponentCheck] = []
            for part in _split_top(alternatives, "|"):
                negated = part.startswith("!")
                body = part[1:].strip() if negated else part
                name = body.split("=", 1)[0]
                if "~" in name or any(mark in name for mark in "<>") or name.strip() == "count":
                    predicate.unchecked.append(part)
                    group = []  # an unchecked alternative lets the whole group pass
                    break
                if "=" in body:
                    key, value = body.split("=", 1)
                    check = ComponentCheck(
                        _qualify_component(key), parse_value(value), True, negated
                    )
                else:
                    check = ComponentCheck(_qualify_component(body), negated=negated)
                group.append(check)
            if group:
                predicate.groups.append(group)
        rest = rest[end + 1 :]
    if rest.startswith("{"):
        predicate.tag = parse_snbt(rest)
    return predicate


def normalise_tagged(value: str) -> str:
    return "#" + normalise_id(value[1:]) if value.startswith("#") else normalise_id(value)


def _split_top(body: str, separator: str) -> list[str]:
    """Split on ``separator`` outside brackets and quotes."""
    parts, depth, current, quote = [], 0, [], None
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


# ---------------------------------------------------------------------------
# inventories
# ---------------------------------------------------------------------------

#: slot keys other entities have
MOB_SLOTS = ("mainhand", "offhand", "head", "chest", "legs", "feet", "body", "saddle")

#: numeric slot ids (SlotArgument), used by loot replace to fill a range of slots
SLOT_NUMBERS: dict[str, int] = {
    "weapon.mainhand": 98,
    "weapon.offhand": 99,
    "armor.feet": 100,
    "armor.legs": 101,
    "armor.chest": 102,
    "armor.head": 103,
    "armor.body": 105,
    "horse.saddle": 400,
    **{f"container.{i}": i for i in range(54)},
    **{f"enderchest.{i}": 200 + i for i in range(ENDER_CHEST)},
}
SLOT_NAMES = {number: name for name, number in SLOT_NUMBERS.items()}


def slot_number(slot: str) -> int | None:
    """``hotbar.2`` -> 2, ``inventory.0`` -> 9, ``weapon`` -> 98 …"""
    name, _, index = slot.partition(".")
    if slot == "weapon":
        return 98
    if name == "hotbar" and index.isdigit():
        return int(index)
    if name == "inventory" and index.isdigit():
        return HOTBAR + int(index)
    return SLOT_NUMBERS.get(slot)


#: entity types without equipment (not living); the rest have hands and armour
NON_LIVING = frozenset(
    f"minecraft:{name}"
    for name in [
        "marker",
        "item",
        "experience_orb",
        "area_effect_cloud",
        "arrow",
        "spectral_arrow",
        "trident",
        "snowball",
        "egg",
        "ender_pearl",
        "fireball",
        "small_fireball",
        "dragon_fireball",
        "wither_skull",
        "falling_block",
        "tnt",
        "firework_rocket",
        "minecart",
        "chest_minecart",
        "furnace_minecart",
        "hopper_minecart",
        "tnt_minecart",
        "spawner_minecart",
        "command_block_minecart",
        "painting",
        "item_frame",
        "glow_item_frame",
        "leash_knot",
        "end_crystal",
        "evoker_fangs",
        "eye_of_ender",
        "fishing_bobber",
        "llama_spit",
        "shulker_bullet",
        "lightning_bolt",
        "interaction",
        "block_display",
        "item_display",
        "text_display",
        "wind_charge",
        "breeze_wind_charge",
        "ominous_item_spawner",
        "potion",
        "splash_potion",
        "lingering_potion",
        "experience_bottle",
    ]
)


def has_equipment(entity_type: str) -> bool:
    return entity_type not in NON_LIVING and not entity_type.endswith(("_boat", "_raft"))


class Inventory:
    """The slots of one entity, keyed ``container.N``, ``enderchest.N``,
    ``mainhand``, ``offhand``, ``head``, ``chest``, ``legs``, ``feet``, ``body``
    and ``saddle`` (a player's mainhand is their selected container slot)."""

    def __init__(self, player: bool = False, equipment: bool = True):
        self.player = player
        #: whether it has hands and armour at all (not markers, items, arrows…)
        self.equipment = equipment or player
        self.selected = 0
        self.slots: dict[str, ItemStack] = {}

    # -- slot names -----------------------------------------------------------

    def keys_for(self, slot: str) -> list[str] | None:
        """The storage keys a command slot name refers to; ``None`` when this
        entity has no such slot. Wildcards (``container.*``) give several."""
        slot = slot.strip()
        if not self.equipment:
            return None
        if slot.endswith(".*"):
            group = slot[:-2]
            names = {
                "container": [f"container.{i}" for i in range(PLAYER_CONTAINER)],
                "hotbar": [f"container.{i}" for i in range(HOTBAR)],
                "inventory": [f"container.{i}" for i in range(HOTBAR, PLAYER_CONTAINER)],
                "enderchest": [f"enderchest.{i}" for i in range(ENDER_CHEST)],
                "armor": [*ARMOR, "body"] if not self.player else list(ARMOR),
                "weapon": [self._mainhand(), "offhand"],
            }.get(group)
            if names is None or (
                not self.player and group in ("container", "hotbar", "inventory", "enderchest")
            ):
                return None
            return names
        key = self._single(slot)
        return None if key is None else [key]

    def _mainhand(self) -> str:
        return f"container.{self.selected}" if self.player else "mainhand"

    def _single(self, slot: str) -> str | None:
        name, _, number = slot.partition(".")
        if slot in ("weapon", "weapon.mainhand"):
            return self._mainhand()
        if slot == "weapon.offhand":
            return "offhand"
        if name == "armor" and number in (*ARMOR, "body"):
            return None if (self.player and number == "body") else number
        if slot in ("horse.saddle", "saddle"):
            return None if self.player else "saddle"
        if not number.lstrip("-").isdigit():
            return None
        index = int(number)
        limits = {
            "container": (0, PLAYER_CONTAINER, 0),
            "hotbar": (0, HOTBAR, 0),
            "inventory": (0, PLAYER_CONTAINER - HOTBAR, HOTBAR),
            "enderchest": (0, ENDER_CHEST, 0),
        }
        if name in limits and self.player:
            low, high, offset = limits[name]
            if not low <= index < high:
                return None
            prefix = "enderchest" if name == "enderchest" else "container"
            return f"{prefix}.{index + offset}"
        return None

    # -- contents ---------------------------------------------------------------

    def get(self, key: str) -> ItemStack | None:
        return self.slots.get(key)

    def set(self, key: str, stack: ItemStack | None) -> None:
        if stack is None or stack.count <= 0 or stack.id == "minecraft:air":
            self.slots.pop(key, None)
        else:
            self.slots[key] = stack

    def items(self) -> Iterator[tuple[str, ItemStack]]:
        yield from self.slots.items()

    def clear_all(self) -> list[ItemStack]:
        """Empty every slot but the ender chest; what was there."""
        dropped = [stack for key, stack in self.slots.items() if not key.startswith("enderchest.")]
        self.slots = {k: v for k, v in self.slots.items() if k.startswith("enderchest.")}
        return dropped

    def add(self, stack: ItemStack) -> int:
        """Put a stack into a player's inventory like picking it up: first onto
        matching stacks (selected slot, offhand, then container order), then
        into empty container slots. Returns the count that did not fit."""
        remaining = stack.count
        order = [self._mainhand(), "offhand", *(f"container.{i}" for i in range(PLAYER_CONTAINER))]
        for key in dict.fromkeys(order):
            current = self.slots.get(key)
            if (
                current is not None
                and current.same_kind(stack)
                and current.count < current.max_count
            ):
                moved = min(remaining, current.max_count - current.count)
                current.count += moved
                remaining -= moved
                if remaining == 0:
                    return 0
        for index in range(PLAYER_CONTAINER):
            key = f"container.{index}"
            if key not in self.slots:
                moved = min(remaining, stack.max_count)
                self.slots[key] = stack.copy(moved)
                remaining -= moved
                if remaining == 0:
                    return 0
        return remaining

    def count(self, predicate: ItemPredicate) -> int:
        return sum(stack.count for _, stack in self._clearable() if predicate.matches(stack))

    def remove(self, predicate: ItemPredicate, max_count: int = -1) -> int:
        """Remove up to ``max_count`` matching items (-1: all); how many went.

        Slots are looked through in vanilla's order: container 0–35, armour, offhand."""
        removed = 0
        for key, stack in list(self._clearable()):
            if not predicate.matches(stack):
                continue
            take = stack.count if max_count < 0 else min(stack.count, max_count - removed)
            stack.count -= take
            removed += take
            if stack.count <= 0:
                del self.slots[key]
            if 0 <= max_count <= removed:
                break
        return removed

    def _clearable(self) -> Iterator[tuple[str, ItemStack]]:
        """What ``clear`` looks through, in order: everything but the ender chest."""
        order = [
            *(f"container.{i}" for i in range(PLAYER_CONTAINER)),
            "feet", "legs", "chest", "head", "offhand", "mainhand", "body", "saddle",
        ]  # fmt: skip
        return ((key, self.slots[key]) for key in order if key in self.slots)

    # -- NBT --------------------------------------------------------------------

    def to_nbt(self, version: Version | None) -> dict[str, Any]:
        if not self.equipment:
            return {}
        return self._player_nbt(version) if self.player else self._mob_nbt(version)

    def _player_nbt(self, version: Version | None) -> dict[str, Any]:
        inventory = []
        for index in range(PLAYER_CONTAINER):
            stack = self.slots.get(f"container.{index}")
            if stack is not None:
                inventory.append({"Slot": index, **stack.to_nbt(version)})
        data: dict[str, Any] = {}
        if uses_equipment(version):
            equipment = {
                key: self.slots[key].to_nbt(version)
                for key in (*ARMOR, "offhand")
                if key in self.slots
            }
            if equipment:
                data["equipment"] = equipment
        else:
            for key, number in ARMOR_SLOT_NUMBERS.items():
                if key in self.slots:
                    inventory.append({"Slot": number, **self.slots[key].to_nbt(version)})
            if "offhand" in self.slots:
                inventory.append(
                    {"Slot": OFFHAND_SLOT_NUMBER, **self.slots["offhand"].to_nbt(version)}
                )
        data["Inventory"] = inventory
        data["EnderItems"] = [
            {"Slot": index, **self.slots[f"enderchest.{index}"].to_nbt(version)}
            for index in range(ENDER_CHEST)
            if f"enderchest.{index}" in self.slots
        ]
        data["SelectedItemSlot"] = self.selected
        held = self.slots.get(self._mainhand())
        if held is not None:
            data["SelectedItem"] = held.to_nbt(version)
        return data

    def _mob_nbt(self, version: Version | None) -> dict[str, Any]:
        def item(key: str) -> dict[str, Any]:
            stack = self.slots.get(key)
            return stack.to_nbt(version) if stack is not None else {}

        if uses_equipment(version):
            equipment = {key: item(key) for key in MOB_SLOTS if key in self.slots}
            return {"equipment": equipment} if equipment else {}
        data: dict[str, Any] = {
            "HandItems": [item("mainhand"), item("offhand")],
            "ArmorItems": [item("feet"), item("legs"), item("chest"), item("head")],
        }
        if "body" in self.slots:
            data["body_armor_item"] = item("body")
        if "saddle" in self.slots:
            data["SaddleItem"] = item("saddle")
        return data

    def load_nbt(self, data: dict[str, Any]) -> None:
        """Read slots back from entity NBT in any version's format.

        ``data`` is the entity's whole NBT: slots it does not mention are empty
        (so ``data remove entity @s equipment`` clears the equipment)."""
        if not self.equipment:
            return
        slots: dict[str, ItemStack] = {}
        for entry in data.get("Inventory") or []:
            stack = ItemStack.from_nbt(entry)
            slot = entry.get("Slot") if isinstance(entry, dict) else None
            if stack is None or not isinstance(slot, int):
                continue
            if 0 <= slot < PLAYER_CONTAINER:
                slots[f"container.{slot}"] = stack
            elif slot == OFFHAND_SLOT_NUMBER:
                slots["offhand"] = stack
            else:
                for key, number in ARMOR_SLOT_NUMBERS.items():
                    if slot == number:
                        slots[key] = stack
        for entry in data.get("EnderItems") or []:
            stack = ItemStack.from_nbt(entry)
            slot = entry.get("Slot") if isinstance(entry, dict) else None
            if stack is not None and isinstance(slot, int) and 0 <= slot < ENDER_CHEST:
                slots[f"enderchest.{slot}"] = stack
        hands = data.get("HandItems")
        if isinstance(hands, list):
            for key, entry in zip(("mainhand", "offhand"), hands, strict=False):
                stack = ItemStack.from_nbt(entry)
                if stack is not None:
                    slots[key] = stack
        armor = data.get("ArmorItems")
        if isinstance(armor, list):
            for key, entry in zip(("feet", "legs", "chest", "head"), armor, strict=False):
                stack = ItemStack.from_nbt(entry)
                if stack is not None:
                    slots[key] = stack
        for nbt_key, key in (("body_armor_item", "body"), ("SaddleItem", "saddle")):
            stack = ItemStack.from_nbt(data.get(nbt_key))
            if stack is not None:
                slots[key] = stack
        equipment = data.get("equipment")
        if isinstance(equipment, dict):
            for key, entry in equipment.items():
                stack = ItemStack.from_nbt(entry)
                if stack is not None and key in MOB_SLOTS:
                    slots[key] = stack
        selected = data.get("SelectedItemSlot")
        if isinstance(selected, int) and 0 <= selected < HOTBAR:
            self.selected = selected
        self.slots = slots


#: entity NBT keys that belong to the inventory
INVENTORY_KEYS = (
    "Inventory",
    "EnderItems",
    "SelectedItem",
    "SelectedItemSlot",
    "HandItems",
    "ArmorItems",
    "body_armor_item",
    "SaddleItem",
    "equipment",
)
