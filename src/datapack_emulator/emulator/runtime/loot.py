"""Loot tables and item modifiers.

Pools roll their ``rolls``, check their conditions, pick weighted entries
(``item``, ``tag``, ``loot_table``, ``empty``, the ``dynamic`` contents of a
mined shulker box, and the children of ``alternatives``, ``group`` and
``sequence``) and apply item functions. Conditions are evaluated by
:mod:`datapack_emulator.emulator.runtime.predicates`; what it cannot answer
(killers, damage, enchantment bonuses) fails and is listed in ``skipped``, as
are functions the emulator does not apply. ``bonus_rolls`` need luck, which
is always 0 here.
"""

from __future__ import annotations

import copy
import json
import math
import random
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import nbt_get, nbt_set, normalise_id, parse_snbt
from datapack_emulator.emulator.runtime.inventory import (
    ItemStack,
    uses_components,
    uses_equipment,
)
from datapack_emulator.emulator.runtime.predicates import (
    Context,
    check,
    integer,
    item_matches,
    number,
    to_float,
)

#: nested tables deeper than this are not followed (a table can include itself)
MAX_DEPTH = 16

TableLookup = Callable[[str], dict[str, Any] | None]

#: the older name of the evaluation context
LootContext = Context

#: ``set_damage`` can add to the current damage (and clamps)
DAMAGE_ADD_SINCE = "1.17"
#: item names and lore are text components, not JSON strings
TEXT_OBJECTS_SINCE = "1.21.5"
#: enchantment levels the functions can set
MAX_LEVEL = 255

_TOOLS = ("sword", "pickaxe", "axe", "shovel", "hoe")
#: durability of items that have some (their max_damage)
MAX_DAMAGE: dict[str, int] = {
    **{f"wooden_{tool}": 59 for tool in _TOOLS},
    **{f"stone_{tool}": 131 for tool in _TOOLS},
    **{f"iron_{tool}": 250 for tool in _TOOLS},
    **{f"golden_{tool}": 32 for tool in _TOOLS},
    **{f"diamond_{tool}": 1561 for tool in _TOOLS},
    **{f"netherite_{tool}": 2031 for tool in _TOOLS},
    "bow": 384, "crossbow": 465, "trident": 250, "shield": 336, "elytra": 432,
    "fishing_rod": 64, "shears": 238, "flint_and_steel": 64, "turtle_helmet": 275,
    "mace": 500, "brush": 64, "carrot_on_a_stick": 25, "warped_fungus_on_a_stick": 100,
    "leather_helmet": 55, "leather_chestplate": 80, "leather_leggings": 75, "leather_boots": 65,
    "chainmail_helmet": 165, "chainmail_chestplate": 240, "chainmail_leggings": 225,
    "chainmail_boots": 195, "iron_helmet": 165, "iron_chestplate": 240,
    "iron_leggings": 225, "iron_boots": 195, "golden_helmet": 77, "golden_chestplate": 112,
    "golden_leggings": 105, "golden_boots": 91, "diamond_helmet": 363,
    "diamond_chestplate": 528, "diamond_leggings": 495, "diamond_boots": 429,
    "netherite_helmet": 407, "netherite_chestplate": 592, "netherite_leggings": 555,
    "netherite_boots": 481,
    **{f"copper_{tool}": 190 for tool in _TOOLS},
    "copper_helmet": 121, "copper_chestplate": 176, "copper_leggings": 165,
    "copper_boots": 143, "wolf_armor": 64,
}  # fmt: skip


@dataclass
class LootResult:
    items: list[ItemStack] = field(default_factory=list)
    #: condition types and function names that were not evaluated
    skipped: set[str] = field(default_factory=set)
    #: tables that could not be found
    missing: set[str] = field(default_factory=set)


def _context(source: random.Random | Context | None) -> Context:
    if isinstance(source, Context):
        return source
    return Context(rng=source or random.Random())


def _passes(holder: dict[str, Any], context: Context) -> bool:
    conditions = holder.get("conditions")
    return not conditions or check(conditions, context)


def evaluate(
    table: dict[str, Any],
    lookup: TableLookup,
    rng: random.Random | Context | None = None,
    depth: int = 0,
    context: Context | None = None,
) -> LootResult:
    """Roll a table. ``context`` (or ``rng``, a bare random source) says what
    the conditions may look at."""
    context = context if context is not None else _context(rng)
    result = LootResult()
    for pool in table.get("pools", []) or []:
        if not isinstance(pool, dict) or not _passes(pool, context):
            continue
        rolls = integer(pool.get("rolls", 1), context)
        for _ in range(max(0, rolls)):
            entry = _pick(_expand(pool.get("entries", []) or [], context), context.rng)
            if entry is None:
                continue
            stacks = _entry_items(entry, lookup, depth, result, context)
            for function in pool.get("functions", []) or []:
                stacks = [apply(function, stack, context, result) for stack in stacks]
            result.items.extend(stack for stack in stacks if stack.count > 0)
    for function in table.get("functions", []) or []:
        result.items = [apply(function, stack, context, result) for stack in result.items]
    result.items = [stack for stack in result.items if stack.count > 0]
    result.skipped |= context.unchecked
    return result


def _expand(entries: Any, context: Context) -> list[dict[str, Any]]:
    """The candidates of a roll."""
    out: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        _expand_entry(entry, context, out)
    return out


def _expand_entry(entry: Any, context: Context, out: list[dict[str, Any]]) -> bool:
    """Vanilla's ``expand``: add an entry's candidates, and say whether it ran
    (its conditions passed). Alternatives stop at the first child that ran,
    sequences at the first that did not; a group runs every child and always
    counts as run. An expanded tag gives each of its items as a candidate."""
    if not isinstance(entry, dict) or not _passes(entry, context):
        return False
    kind = normalise_id(str(entry.get("type", "minecraft:item")))
    children = entry.get("children")
    children = children if isinstance(children, list) else []
    if kind == "minecraft:alternatives":
        return any(_expand_entry(child, context, out) for child in children)
    if kind == "minecraft:sequence":
        return all(_expand_entry(child, context, out) for child in children)
    if kind == "minecraft:group":
        for child in children:
            _expand_entry(child, context, out)
        return True
    if kind == "minecraft:tag" and entry.get("expand"):
        tag = "#" + normalise_id(str(entry.get("name", "")))
        members = context.tags("item", tag) if context.tags else None
        if members is not None:
            plain = {key: value for key, value in entry.items() if key != "conditions"}
            out.extend(
                {**plain, "type": "minecraft:item", "name": item_id} for item_id in sorted(members)
            )
            return True
    out.append(entry)
    return True


def _weight(entry: dict[str, Any]) -> int:
    # luck is always 0, so quality never counts
    return max(0, math.floor(to_float(entry.get("weight"), 1.0)))


def _pick(entries: list[dict[str, Any]], rng: random.Random) -> dict[str, Any] | None:
    weighted = [(entry, _weight(entry)) for entry in entries]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return None
    roll = rng.randrange(total)
    for entry, weight in weighted:
        if roll < weight:
            return entry
        roll -= weight
    return None


def _entry_items(
    entry: dict[str, Any], lookup: TableLookup, depth: int, result: LootResult, context: Context
) -> list[ItemStack]:
    kind = normalise_id(str(entry.get("type", "minecraft:item")))
    stacks: list[ItemStack] = []
    if kind == "minecraft:item":
        stacks = [ItemStack(normalise_id(str(entry.get("name", "minecraft:air"))), 1)]
    elif kind == "minecraft:tag":
        tag = "#" + normalise_id(str(entry.get("name", "")))
        members = context.tags("item", tag) if context.tags else None
        if members is None:
            result.skipped.add(f"tag entry {tag}")
        else:
            stacks = [ItemStack(item_id, 1) for item_id in sorted(members)]
    elif kind == "minecraft:loot_table" and depth < MAX_DEPTH:
        value = entry.get("value", entry.get("name"))
        nested = None
        if isinstance(value, dict):
            nested = value
        elif isinstance(value, str):
            nested = lookup(normalise_id(value))
            if nested is None:
                result.missing.add(normalise_id(value))
        if nested is not None:
            inner = evaluate(nested, lookup, depth=depth + 1, context=context)
            result.skipped |= inner.skipped
            result.missing |= inner.missing
            stacks = inner.items
    elif kind == "minecraft:dynamic":
        name = normalise_id(str(entry.get("name", "")))
        block = context.block
        items = getattr(block, "items", None)
        # only shulker boxes give their contents to the loot table
        if (
            name == "minecraft:contents"
            and items is not None
            and str(getattr(block, "id", "")).endswith("shulker_box")
        ):
            stacks = [items[slot].copy() for slot in sorted(items)]
        else:
            result.skipped.add(f"dynamic {name}")
    elif kind not in ("minecraft:empty", "minecraft:loot_table"):
        result.skipped.add(kind)
    functions = entry.get("functions")
    for function in functions if isinstance(functions, list) else []:
        stacks = [apply(function, stack, context, result) for stack in stacks]
    return [stack for stack in stacks if stack.id != "minecraft:air"]


# ---------------------------------------------------------------------------
# item functions
# ---------------------------------------------------------------------------


def _modern(context: Context) -> bool:
    return uses_components(context.version)


def _component(key: str) -> str:
    bare = key.lstrip("!")
    qualified = bare if ":" in bare else f"minecraft:{bare}"
    return ("!" if key.startswith("!") else "") + qualified


def _text_objects(context: Context) -> bool:
    version = context.version
    return version is None or version >= versions.parse(TEXT_OBJECTS_SINCE)


def _text(value: Any, context: Context) -> Any:
    """A text component as the item stores it: an object, or before 1.21.5 a
    JSON string."""
    return value if _text_objects(context) else json.dumps(value)


def _read_text(value: Any, context: Context) -> Any:
    """The component of a stored text (a JSON string before 1.21.5)."""
    if isinstance(value, str) and not _text_objects(context):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _set_name(function: dict[str, Any], stack: ItemStack, context: Context) -> None:
    name = function.get("name")
    if name is None:
        return
    if _modern(context):
        target = normalise_id(str(function.get("target", "custom_name")))
        stack.components[target] = _text(name, context)
    else:
        stack.tag.setdefault("display", {})["Name"] = _text(name, context)


def _offset(function: dict[str, Any], key: str, default: int) -> int:
    return max(0, math.floor(to_float(function.get(key), default)))


def _set_lore(function: dict[str, Any], stack: ItemStack, context: Context) -> None:
    lore = function.get("lore")
    lore = list(lore) if isinstance(lore, list) else []
    if _modern(context):
        stored = stack.components.get("minecraft:lore")
    else:
        display = stack.tag.get("display")
        stored = display.get("Lore") if isinstance(display, dict) else None
    current = [_read_text(line, context) for line in stored] if isinstance(stored, list) else []
    mode = str(function.get("mode", "replace_all" if function.get("replace") else "append"))
    offset = _offset(function, "offset", 0)
    if mode == "replace_all":
        current = lore
    elif mode in ("insert", "replace_section") and offset > len(current):
        return  # vanilla logs the bad offset and leaves the lore alone
    elif mode == "insert":
        current[offset:offset] = lore
    elif mode == "replace_section":
        size = _offset(function, "size", len(lore))
        current[offset : offset + size] = lore
    else:
        current = current + lore
    lines = [_text(line, context) for line in current]
    if _modern(context):
        stack.components["minecraft:lore"] = lines
    else:
        stack.tag.setdefault("display", {})["Lore"] = lines


def enchant(stack: ItemStack, levels: dict[str, int], context: Context, add: bool) -> None:
    """Enchant a stack in the version's format (books become enchanted books,
    whose enchantments are stored)."""
    book = stack.id in ("minecraft:book", "minecraft:enchanted_book")
    if book:
        stack.id = "minecraft:enchanted_book"
    if _modern(context):
        key = "minecraft:stored_enchantments" if book else "minecraft:enchantments"
        current = stack.components.get(key)
        current = dict(current) if isinstance(current, dict) else {}
        flat = uses_equipment(context.version)  # 1.21.5: the levels map is the component
        table = current if flat else dict(current.get("levels", {}) or {})
        for enchantment, level in levels.items():
            old = table.get(enchantment, 0)
            new = _level((old if isinstance(old, int) else 0) + level if add else level)
            if new:
                table[enchantment] = new
            else:
                table.pop(enchantment, None)
        stack.components[key] = table if flat else {**current, "levels": table}
        return
    key = "StoredEnchantments" if book else "Enchantments"
    entries = [e for e in stack.tag.get(key, []) if isinstance(e, dict)]
    existing = {normalise_id(str(e.get("id", ""))): e for e in entries}
    for enchantment, level in levels.items():
        if enchantment in existing:
            old = existing[enchantment].get("lvl", 0)
            new = _level((old if isinstance(old, int) else 0) + level if add else level)
        else:
            new = _level(level)
        if not new:
            entries = [e for e in entries if e is not existing.get(enchantment)]
        elif enchantment in existing:
            existing[enchantment]["lvl"] = new
        else:
            entries.append({"id": enchantment, "lvl": new})
    stack.tag[key] = entries


def _level(level: int) -> int:
    """Levels are kept between 0 and 255; 0 removes the enchantment."""
    return max(0, min(MAX_LEVEL, level))


def _max_damage(stack: ItemStack) -> int | None:
    value = stack.components.get("minecraft:max_damage")
    if isinstance(value, int):
        return value
    return MAX_DAMAGE.get(stack.id.split(":", 1)[-1])


def apply(
    function: Any,
    stack: ItemStack,
    context: Context | random.Random | None,
    result: LootResult,
    cap: bool = False,
) -> ItemStack:
    """One item function (a list is a sequence). Counts are not capped here:
    loot is split into stacks afterwards, and an item modifier caps the
    result once (``modify``)."""
    context = _context(context)
    if isinstance(function, list):
        for part in function:
            stack = apply(part, stack, context, result, cap)
        return stack
    if not isinstance(function, dict) or not _passes(function, context):
        return stack
    name = normalise_id(str(function.get("function", "")))
    handler = FUNCTIONS.get(name)
    if handler is None:
        result.skipped.add(name or "function")
        return stack
    return handler(function, stack, context, result, cap) or stack


def _set_count(function, stack, context, result, cap):
    amount = integer(function.get("count", 1), context)
    stack.count = max(0, stack.count + amount if function.get("add") else amount)


def _limit_count(function, stack, context, result, cap):
    limit = function.get("limit", {})
    if isinstance(limit, (int, float)) and not isinstance(limit, bool):
        stack.count = int(limit)  # an exact range clamps both ways
        return
    if not isinstance(limit, dict):
        return
    if "min" in limit:
        stack.count = max(stack.count, integer(limit["min"], context))
    if "max" in limit:
        stack.count = min(stack.count, integer(limit["max"], context))


def _set_components(function, stack, context, result, cap):
    components = function.get("components")
    if not isinstance(components, dict):
        return
    for key, value in components.items():
        qualified = _component(key)
        if qualified.startswith("!"):
            stack.components.pop(qualified[1:], None)
        else:
            stack.components[qualified] = copy.deepcopy(value)


def merge_compound(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Vanilla's ``CompoundTag.merge``: compounds merge key by key, anything
    else is replaced."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_compound(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _set_nbt(function, stack, context, result, cap):
    tag = function.get("tag")
    data = parse_snbt(tag) if isinstance(tag, str) else None
    if isinstance(data, dict):
        merge_compound(stack.tag, data)


def _set_custom_data(function, stack, context, result, cap):
    tag = function.get("tag")
    data = parse_snbt(tag) if isinstance(tag, str) else tag
    if isinstance(data, dict):
        current = stack.components.get("minecraft:custom_data")
        merged = copy.deepcopy(current) if isinstance(current, dict) else {}
        stack.components["minecraft:custom_data"] = merge_compound(merged, data)


def _set_damage(function, stack, context, result, cap):
    maximum = _max_damage(stack)
    if not maximum:
        result.skipped.add("set_damage (no durability known)")
        return
    wanted = _f32(number(function.get("damage", 1.0), context))
    version = context.version
    if version is not None and version < versions.parse(DAMAGE_ADD_SINCE):
        damage = max(0, math.floor(_f32(_f32(1.0 - wanted) * maximum)))
    else:
        base = 0.0
        if function.get("add"):
            current = stack.components.get("minecraft:damage", stack.tag.get("Damage", 0))
            if not isinstance(current, int) or isinstance(current, bool):
                current = 0
            base = _f32(1.0 - _f32(_f32(current) / _f32(maximum)))
        kept = _f32(1.0 - max(0.0, min(1.0, _f32(wanted + base))))
        damage = max(0, min(maximum, math.floor(_f32(kept * maximum))))
    if _modern(context):
        stack.components["minecraft:damage"] = damage
    else:
        stack.tag["Damage"] = damage


def _f32(value: float) -> float:
    """``value`` as a Java float (the functions do their maths in floats)."""
    try:
        return struct.unpack("f", struct.pack("f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def _set_enchantments(function, stack, context, result, cap):
    enchantments = function.get("enchantments")
    if not isinstance(enchantments, dict):
        enchantments = {}
    levels = {
        normalise_id(str(key)): integer(value, context) for key, value in enchantments.items()
    }
    enchant(stack, levels, context, add=bool(function.get("add")))


def _enchant_randomly(function, stack, context, result, cap):
    options = function.get("options", function.get("enchantments"))
    if isinstance(options, str):
        options = [options]
    choices = [normalise_id(str(o)) for o in options or [] if not str(o).startswith("#")]
    if not choices:
        result.skipped.add("enchant_randomly (every enchantment or a tag)")
        return
    enchant(stack, {context.rng.choice(choices): 1}, context, add=False)
    result.skipped.add("enchant_randomly (levels are 1)")


def _set_item(function, stack, context, result, cap):
    item = function.get("item")
    if isinstance(item, str):
        stack.id = normalise_id(item)


def _copy_name(function, stack, context, result, cap):
    source = normalise_id(str(function.get("source", "")))
    holder = None
    if source == "minecraft:block_entity":
        holder = context.block
    elif source == "minecraft:this":
        holder = context.entity
    else:
        result.skipped.add(f"copy_name from {source}")
        return
    name = getattr(holder, "nbt", {}).get("CustomName") if holder is not None else None
    if name is not None:
        # names are stored like item names: JSON strings before 1.21.5
        _set_name({"name": _read_text(name, context)}, stack, context)


def _set_contents(function, stack, context, result, cap):
    """Every candidate of the entries (their conditions checked, composites
    resolved), into ``container`` or the ``component`` asked for."""
    from datapack_emulator.emulator.commands.items import split_stacks

    items: list[ItemStack] = []
    for entry in _expand(function.get("entries"), context):
        items.extend(_entry_items(entry, lambda _id: None, MAX_DEPTH, result, context))
    items = split_stacks(items)
    if _modern(context):
        component = normalise_id(str(function.get("component", "minecraft:container")))
        if component == "minecraft:container":
            stack.components[component] = [
                {"slot": slot, "item": item.to_nbt(context.version)}
                for slot, item in enumerate(items)
            ]
        else:  # bundle_contents, charged_projectiles: a plain list
            stack.components[component] = [item.to_nbt(context.version) for item in items]
    else:
        stack.tag.setdefault("BlockEntityTag", {})["Items"] = [
            {"Slot": slot, **item.to_nbt(context.version)} for slot, item in enumerate(items)
        ]


def _copy_source(function: dict[str, Any], context: Context, result: LootResult) -> Any:
    """The compound ``copy_nbt``/``copy_custom_data`` read: the mined block
    entity, ``this`` entity, or a storage."""
    source = function.get("source", "block_entity")
    if isinstance(source, dict):
        kind = normalise_id(str(source.get("type", "")))
        if kind == "minecraft:storage":
            storage = normalise_id(str(source.get("source", "")))
            store = context.world.storage.get(storage) if context.world else None
            return store if isinstance(store, dict) else {}
        target = normalise_id(str(source.get("target", "")))
    else:
        target = normalise_id(str(source))
    if target == "minecraft:block_entity" and context.block is not None:
        return context.block.data(context.version, context.block_position)
    if target == "minecraft:this" and context.entity is not None:
        return context.entity.data(context.version)
    result.skipped.add(f"{function.get('function')} from {target}")
    return None


def _copy_ops(function: dict[str, Any], data: Any, target: dict[str, Any]) -> None:
    ops = function.get("ops")
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        value = nbt_get(data, str(op.get("source", "")))
        if value is None:
            continue
        path = str(op.get("target", ""))
        mode = str(op.get("op", "replace"))
        current = nbt_get(target, path)
        if mode == "append" and isinstance(current, list):
            current.extend(copy.deepcopy(value) if isinstance(value, list) else [value])
        elif mode == "merge" and isinstance(current, dict) and isinstance(value, dict):
            merge_compound(current, value)
        else:
            nbt_set(target, path, copy.deepcopy(value))


def _copy_nbt(function, stack, context, result, cap):
    """``copy_nbt`` (before 1.20.5) into the item's tag."""
    data = _copy_source(function, context, result)
    if data is not None:
        _copy_ops(function, data, stack.tag)


def _copy_custom_data(function, stack, context, result, cap):
    """``copy_custom_data`` (1.20.5+) into the ``custom_data`` component."""
    data = _copy_source(function, context, result)
    if data is None:
        return
    current = stack.components.get("minecraft:custom_data")
    target = copy.deepcopy(current) if isinstance(current, dict) else {}
    _copy_ops(function, data, target)
    stack.components["minecraft:custom_data"] = target


def _filtered(function, stack, context, result, cap):
    if item_matches(function.get("item_filter"), stack, context):
        return apply(function.get("modifier", function.get("on_pass")), stack, context, result, cap)
    if "on_fail" in function:
        return apply(function["on_fail"], stack, context, result, cap)
    return stack


def _sequence(function, stack, context, result, cap):
    parts = function.get("functions")
    for part in parts if isinstance(parts, list) else []:
        stack = apply(part, stack, context, result, cap)
    return stack


def _discard(function, stack, context, result, cap):
    stack.count = 0


def _set_potion(function, stack, context, result, cap):
    potion = normalise_id(str(function.get("id", "")))
    if _modern(context):
        current = stack.components.get("minecraft:potion_contents")
        current = dict(current) if isinstance(current, dict) else {}
        current["potion"] = potion
        stack.components["minecraft:potion_contents"] = current
    else:
        stack.tag["Potion"] = potion


def _apply_bonus(function, stack, context, result, cap):
    """With no enchantments in a command's tool, the level is 0: only the
    binomial formula still adds its ``extra`` trials."""
    if context.tool is None:
        return
    if context.tool.components.get("minecraft:enchantments") or context.tool.tag.get(
        "Enchantments"
    ):
        result.skipped.add("apply_bonus (enchantment levels)")
    formula = normalise_id(str(function.get("formula", "")))
    parameters = function.get("parameters")
    if formula != "minecraft:binomial_with_bonus_count" or not isinstance(parameters, dict):
        return
    trials = math.floor(to_float(parameters.get("extra")))
    chance = to_float(parameters.get("probability"))
    stack.count += sum(1 for _ in range(max(0, trials)) if context.rng.random() < chance)


def _unchanged(function, stack, context, result, cap):
    """Needs what commands do not give (a killer, the tool's enchantments, an
    explosion): with none of it, vanilla leaves the item as it is too."""


def _skipped(name: str):
    def handler(function, stack, context, result, cap):
        result.skipped.add(name)

    return handler


FUNCTIONS: dict[str, Callable[..., ItemStack | None]] = {
    "minecraft:set_count": _set_count,
    "minecraft:limit_count": _limit_count,
    "minecraft:set_components": _set_components,
    "minecraft:set_nbt": _set_nbt,
    "minecraft:set_custom_data": _set_custom_data,
    "minecraft:set_name": lambda f, s, c, r, cap: _set_name(f, s, c),
    "minecraft:set_lore": lambda f, s, c, r, cap: _set_lore(f, s, c),
    "minecraft:set_damage": _set_damage,
    "minecraft:set_enchantments": _set_enchantments,
    "minecraft:enchant_randomly": _enchant_randomly,
    "minecraft:set_item": _set_item,
    "minecraft:copy_name": _copy_name,
    "minecraft:set_contents": _set_contents,
    "minecraft:copy_nbt": _copy_nbt,
    "minecraft:copy_custom_data": _copy_custom_data,
    "minecraft:filtered": _filtered,
    "minecraft:sequence": _sequence,
    "minecraft:discard": _discard,
    "minecraft:set_potion": _set_potion,
    "minecraft:explosion_decay": _unchanged,
    "minecraft:apply_bonus": _apply_bonus,
    "minecraft:looting_enchant": _unchanged,
    "minecraft:enchanted_count_increase": _unchanged,
    "minecraft:enchant_with_levels": _skipped("minecraft:enchant_with_levels"),
    "minecraft:furnace_smelt": _skipped("minecraft:furnace_smelt"),
    "minecraft:set_attributes": _skipped("minecraft:set_attributes"),
    "minecraft:fill_player_head": _skipped("minecraft:fill_player_head"),
    "minecraft:exploration_map": _skipped("minecraft:exploration_map"),
    "minecraft:set_stew_effect": _skipped("minecraft:set_stew_effect"),
    "minecraft:copy_components": _skipped("minecraft:copy_components"),
}


def modify(modifier: Any, stack: ItemStack, context: Context) -> tuple[ItemStack, LootResult]:
    """An item modifier (one function or a list); the result is capped to the
    stack size once, like ``item modify``."""
    result = LootResult()
    stack = apply(modifier, stack, context, result, cap=True)
    stack.count = max(0, min(stack.count, stack.max_count))
    result.skipped |= context.unchecked
    return stack, result
