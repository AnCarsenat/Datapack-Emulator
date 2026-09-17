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
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
)

#: nested tables deeper than this are not followed (a table can include itself)
MAX_DEPTH = 16

TableLookup = Callable[[str], dict[str, Any] | None]

#: the older name of the evaluation context
LootContext = Context

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


def _expand(entries: list[Any], context: Context) -> list[dict[str, Any]]:
    """The candidates of a roll: entries whose conditions pass, composites
    resolved (alternatives: the first child that passes; sequence: children
    until one fails)."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not _passes(entry, context):
            continue
        kind = normalise_id(str(entry.get("type", "minecraft:item")))
        children = [child for child in entry.get("children", []) or [] if isinstance(child, dict)]
        if kind == "minecraft:alternatives":
            for child in children:
                expanded = _expand([child], context)
                if expanded:
                    out.extend(expanded)
                    break
        elif kind == "minecraft:group":
            out.extend(_expand(children, context))
        elif kind == "minecraft:sequence":
            for child in children:
                expanded = _expand([child], context)
                if not expanded:
                    break
                out.extend(expanded)
        else:
            out.append(entry)
    return out


def _pick(entries: list[dict[str, Any]], rng: random.Random) -> dict[str, Any] | None:
    weighted = [(entry, max(0, int(entry.get("weight", 1)))) for entry in entries]
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
            ordered = sorted(members)
            if entry.get("expand"):
                ordered = [context.rng.choice(ordered)] if ordered else []
            stacks = [ItemStack(item_id, 1) for item_id in ordered]
    elif kind == "minecraft:loot_table":
        value = entry.get("value", entry.get("name"))
        if isinstance(value, dict):
            stacks = evaluate(value, lookup, depth=depth + 1, context=context).items
        elif isinstance(value, str) and depth < MAX_DEPTH:
            nested = lookup(normalise_id(value))
            if nested is None:
                result.missing.add(normalise_id(value))
            else:
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
    elif kind != "minecraft:empty":
        result.skipped.add(kind)
    for function in entry.get("functions", []) or []:
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


def _set_name(function: dict[str, Any], stack: ItemStack, context: Context) -> None:
    name = function.get("name")
    if name is None:
        return
    if _modern(context):
        stack.components[normalise_id(str(function.get("target", "custom_name")))] = name
    else:
        stack.tag.setdefault("display", {})["Name"] = json.dumps(name)


def _set_lore(function: dict[str, Any], stack: ItemStack, context: Context) -> None:
    lore = list(function.get("lore", []) or [])
    if _modern(context):
        current = list(stack.components.get("minecraft:lore", []) or [])
    else:
        current = [json.loads(line) for line in stack.tag.get("display", {}).get("Lore", [])]
    mode = str(function.get("mode", "replace_all" if function.get("replace") else "append"))
    if mode == "replace_all":
        current = lore
    elif mode == "insert":
        offset = int(function.get("offset", 0))
        current[offset:offset] = lore
    elif mode == "replace_section":
        offset = int(function.get("offset", 0))
        size = int(function.get("size", len(lore)))
        current[offset : offset + size] = lore
    else:
        current = current + lore
    if _modern(context):
        stack.components["minecraft:lore"] = current
    else:
        stack.tag.setdefault("display", {})["Lore"] = [json.dumps(line) for line in current]


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
            table[enchantment] = (int(table.get(enchantment, 0)) + level) if add else level
        stack.components[key] = table if flat else {**current, "levels": table}
        return
    key = "StoredEnchantments" if book else "Enchantments"
    entries = [e for e in stack.tag.get(key, []) if isinstance(e, dict)]
    existing = {normalise_id(str(e.get("id", ""))): e for e in entries}
    for enchantment, level in levels.items():
        if enchantment in existing:
            old = int(existing[enchantment].get("lvl", 0))
            existing[enchantment]["lvl"] = old + level if add else level
        else:
            entries.append({"id": enchantment, "lvl": level})
    stack.tag[key] = entries


def _max_damage(stack: ItemStack) -> int | None:
    value = stack.components.get("minecraft:max_damage")
    if isinstance(value, int):
        return value
    return MAX_DAMAGE.get(stack.id.split(":", 1)[1])


def apply(
    function: Any,
    stack: ItemStack,
    context: Context | random.Random | None,
    result: LootResult,
    cap: bool = False,
) -> ItemStack:
    """One item function (a list is a sequence). ``cap`` limits counts to the
    stack size (item modifiers); loot keeps the count and is split into stacks
    afterwards."""
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
    stack.count = stack.count + amount if function.get("add") else amount
    stack.count = max(0, min(stack.count, stack.max_count) if cap else stack.count)


def _limit_count(function, stack, context, result, cap):
    limit = function.get("limit", {})
    if isinstance(limit, (int, float)):
        stack.count = min(stack.count, int(limit))
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


def _set_nbt(function, stack, context, result, cap):
    tag = function.get("tag")
    if isinstance(tag, str):
        stack.tag.update(parse_snbt(tag))


def _set_custom_data(function, stack, context, result, cap):
    tag = function.get("tag")
    data = parse_snbt(tag) if isinstance(tag, str) else tag
    if isinstance(data, dict):
        current = stack.components.get("minecraft:custom_data")
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(copy.deepcopy(data))
        stack.components["minecraft:custom_data"] = merged


def _set_damage(function, stack, context, result, cap):
    maximum = _max_damage(stack)
    if not maximum:
        result.skipped.add("set_damage (no durability known)")
        return
    wanted = number(function.get("damage", 1.0), context)
    if function.get("add"):
        current = stack.components.get("minecraft:damage", stack.tag.get("Damage", 0))
        wanted += 1.0 - float(current) / maximum
    wanted = max(0.0, min(1.0, wanted))
    damage = int((1.0 - wanted) * maximum)
    if _modern(context):
        stack.components["minecraft:damage"] = damage
    else:
        stack.tag["Damage"] = damage


def _set_enchantments(function, stack, context, result, cap):
    levels = {
        normalise_id(key): integer(value, context)
        for key, value in (function.get("enchantments") or {}).items()
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
    name = None
    if context.block is not None:
        name = getattr(context.block, "nbt", {}).get("CustomName")
    elif context.entity is not None:
        name = context.entity.nbt.get("CustomName")
    if name is not None:
        _set_name({"name": name}, stack, context)


def _set_contents(function, stack, context, result, cap):
    items: list[ItemStack] = []
    for entry in function.get("entries", []) or []:
        if isinstance(entry, dict):
            items.extend(_entry_items(entry, lambda _id: None, MAX_DEPTH, result, context))
    if _modern(context):
        stack.components["minecraft:container"] = [
            {"slot": slot, "item": item.to_nbt(context.version)} for slot, item in enumerate(items)
        ]
    else:
        stack.tag.setdefault("BlockEntityTag", {})["Items"] = [
            {"Slot": slot, **item.to_nbt(context.version)} for slot, item in enumerate(items)
        ]


def _copy_nbt(function, stack, context, result, cap):
    """``copy_nbt`` from the mined block entity, or the entity (before 1.20.5)."""
    source = str(function.get("source", "block_entity"))
    if isinstance(function.get("source"), dict):
        source = str(function["source"].get("type", ""))
    if "block_entity" in source and context.block is not None:
        data = context.block.data(context.version)
    elif context.entity is not None and "this" in source:
        data = context.entity.data(context.version)
    else:
        result.skipped.add(f"copy_nbt from {source}")
        return
    for op in function.get("ops", []) or []:
        value = nbt_get(data, str(op.get("source", "")))
        if value is not None:
            nbt_set(stack.tag, str(op.get("target", "")), copy.deepcopy(value))


def _filtered(function, stack, context, result, cap):
    if item_matches(function.get("item_filter"), stack, context):
        return apply(function.get("modifier", function.get("on_pass")), stack, context, result, cap)
    if "on_fail" in function:
        return apply(function["on_fail"], stack, context, result, cap)
    return stack


def _sequence(function, stack, context, result, cap):
    for part in function.get("functions", []) or []:
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
    "minecraft:filtered": _filtered,
    "minecraft:sequence": _sequence,
    "minecraft:discard": _discard,
    "minecraft:set_potion": _set_potion,
    "minecraft:explosion_decay": _unchanged,
    "minecraft:apply_bonus": _unchanged,
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
    """An item modifier (one function or a list), counts capped to the stack size."""
    result = LootResult()
    stack = apply(modifier, stack, context, result, cap=True)
    result.skipped |= context.unchecked
    return stack, result
