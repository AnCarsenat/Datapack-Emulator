"""Item sub-predicates: ``*[custom_data~{…}]`` in commands, the ``predicates``
map of an item predicate in JSON.

The ids come from 26.3's ``DataComponentPredicates`` registry
(``attribute_modifiers bundle_contents container custom_data damage
enchantments firework_explosion fireworks jukebox_playable potion_contents
stored_enchantments trim villager writable_book_content
written_book_content``) plus the ``count`` pseudo-predicate of the command
argument (``ItemPredicateArgument.PSEUDO_PREDICATES``). The ones in
:data:`CHECKED` are evaluated; the caller notes the others as unchecked.

What each checked one does (as the game's classes do):

* ``custom_data`` — ``NbtUtils.compareNbt(value, custom_data, partial)``:
  :func:`nbt_matches`; an item without the component is ``{}``
* ``damage`` — ``{durability, damage}`` ranges; needs ``max_damage`` and
  ``damage`` (durability is ``max_damage - damage``)
* ``enchantments`` / ``stored_enchantments`` — a list of
  ``{enchantments?, levels?}``; each entry needs one of the listed
  enchantments (any, without the field) at a level in range
* ``potion_contents`` — a potion id or a list of them
* ``count`` — a range on the stack size
"""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.common import nbt_matches, normalise_id, parse_snbt

CHECKED = frozenset(
    {
        "minecraft:custom_data",
        "minecraft:damage",
        "minecraft:enchantments",
        "minecraft:stored_enchantments",
        "minecraft:potion_contents",
        "minecraft:count",
    }
)


def qualify(name: str) -> str:
    name = name.strip()
    return name if ":" in name else f"minecraft:{name}"


def has_tag(value: Any) -> bool:
    """Whether ``value`` names a ``#tag`` somewhere (the caller cannot resolve it here)."""
    if isinstance(value, str):
        return value.startswith("#")
    if isinstance(value, list):
        return any(has_tag(item) for item in value)
    if isinstance(value, dict):
        return any(has_tag(item) for item in value.values())
    return False


def checkable(name: str, value: Any) -> bool:
    """Whether :func:`matches` answers ``name`` with ``value``."""
    name = qualify(name)
    if name not in CHECKED:
        return False
    if name in ("minecraft:enchantments", "minecraft:stored_enchantments"):
        return isinstance(value, list) and not has_tag(value)
    if name == "minecraft:potion_contents":
        return isinstance(value, (str, list)) and not has_tag(value)
    return True


def in_range(value: float | None, bounds: Any) -> bool:
    """An int range: a number (exact), ``{min, max}`` (either optional) or nothing."""
    if bounds is None:
        return True
    if value is None:
        return False
    if isinstance(bounds, (int, float)) and not isinstance(bounds, bool):
        return value == bounds
    if isinstance(bounds, dict):
        low, high = bounds.get("min"), bounds.get("max")
        if isinstance(low, (int, float)) and value < low:
            return False
        return not (isinstance(high, (int, float)) and value > high)
    return False


def _levels(component: Any) -> dict[str, int]:
    """``{id: level}`` from an enchantments component (both forms)."""
    if not isinstance(component, dict):
        return {}
    levels = component.get("levels")
    if not isinstance(levels, dict):  # 1.21.5+: the map itself
        levels = component
    return {
        normalise_id(key): int(value)
        for key, value in levels.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [normalise_id(value)]
    if isinstance(value, list):
        return [normalise_id(item) for item in value if isinstance(item, str)]
    return []


def _enchantments_match(wanted: Any, component: Any) -> bool:
    levels = _levels(component)
    for entry in wanted if isinstance(wanted, list) else []:
        entry = entry if isinstance(entry, dict) else {}
        bounds = entry.get("levels")
        if "enchantments" in entry:
            candidates = [(key, levels.get(key, 0)) for key in _ids(entry["enchantments"])]
        else:
            candidates = list(levels.items())
        if not any(level > 0 and in_range(level, bounds) for _, level in candidates):
            return False
    return True


def matches(name: str, value: Any, count: int, components: dict[str, Any]) -> bool:
    """Whether a stack of ``count`` with these effective ``components`` passes
    sub-predicate ``name`` (one of :data:`CHECKED`) given ``value``."""
    name = qualify(name)
    if name == "minecraft:count":
        return in_range(count, value)
    if name == "minecraft:custom_data":
        pattern = parse_snbt(value) if isinstance(value, str) else value
        actual = components.get("minecraft:custom_data", {})
        if isinstance(actual, str):
            actual = parse_snbt(actual)
        return nbt_matches(actual, pattern)
    if name == "minecraft:damage":
        maximum = components.get("minecraft:max_damage")
        damage = components.get("minecraft:damage")
        if not isinstance(maximum, (int, float)) or not isinstance(damage, (int, float)):
            return False
        value = value if isinstance(value, dict) else {}
        return in_range(maximum - damage, value.get("durability")) and in_range(
            damage, value.get("damage")
        )
    if name in ("minecraft:enchantments", "minecraft:stored_enchantments"):
        if name not in components:
            return False
        return _enchantments_match(value, components[name])
    if name == "minecraft:potion_contents":
        contents = components.get("minecraft:potion_contents")
        potion = contents.get("potion") if isinstance(contents, dict) else contents
        return isinstance(potion, str) and normalise_id(potion) in _ids(value)
    raise ValueError(f"{name} is not a checked item sub-predicate")
