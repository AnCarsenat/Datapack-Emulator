"""Loot tables, evaluated well enough for ``loot`` commands.

Pools roll their ``rolls`` (constants and uniform ranges), pick weighted
entries (``item``, ``loot_table``, ``empty``, and the children of
``alternatives``, ``group`` and ``sequence``) and apply ``set_count``,
``set_components`` and ``set_nbt``. Conditions and every other function are
not evaluated: conditions count as passing, and :class:`LootResult` lists
what was skipped so the caller can say so.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator.common import normalise_id, parse_snbt
from datapack_emulator.emulator.runtime.inventory import ItemStack

#: nested tables deeper than this are not followed (a table can include itself)
MAX_DEPTH = 16

TableLookup = Callable[[str], dict[str, Any] | None]


@dataclass
class LootContext:
    """What a table can read: the mined block (``loot … mine``) and the tool."""

    #: a runtime.blocks.Block
    block: Any = None
    tool: ItemStack | None = None


@dataclass
class LootResult:
    items: list[ItemStack] = field(default_factory=list)
    #: condition types and function names that were not evaluated
    skipped: set[str] = field(default_factory=set)
    #: tables that could not be found
    missing: set[str] = field(default_factory=set)


def number(provider: Any, rng: random.Random) -> float:
    """A number provider: a constant, ``{min, max}`` or a typed provider."""
    if isinstance(provider, (int, float)) and not isinstance(provider, bool):
        return provider
    if isinstance(provider, dict):
        kind = normalise_id(str(provider.get("type", "minecraft:uniform")))
        if kind == "minecraft:constant":
            return float(provider.get("value", 0))
        if kind in ("minecraft:uniform",) or ("min" in provider and "max" in provider):
            low = number(provider.get("min", 0), rng)
            high = number(provider.get("max", low), rng)
            return rng.uniform(low, high)
        if kind == "minecraft:binomial":
            trials = int(number(provider.get("n", 0), rng))
            chance = number(provider.get("p", 0), rng)
            return sum(1 for _ in range(trials) if rng.random() < chance)
    return 0


def integer(provider: Any, rng: random.Random) -> int:
    """A number provider read as an int, like vanilla's ``getInt``: uniform
    ranges include both ends (floored), everything else is floored."""
    if isinstance(provider, dict):
        kind = normalise_id(str(provider.get("type", "minecraft:uniform")))
        if kind == "minecraft:uniform" or (
            kind not in ("minecraft:constant", "minecraft:binomial")
            and "min" in provider
            and "max" in provider
        ):
            low = math.floor(number(provider.get("min", 0), rng))
            high = math.floor(number(provider.get("max", low), rng))
            return rng.randint(min(low, high), max(low, high))
    return math.floor(number(provider, rng))


def evaluate(
    table: dict[str, Any],
    lookup: TableLookup,
    rng: random.Random,
    depth: int = 0,
    context: LootContext | None = None,
) -> LootResult:
    result = LootResult()
    context = context or LootContext()
    for pool in table.get("pools", []) or []:
        if not isinstance(pool, dict):
            continue
        _skip_conditions(pool, result)
        rolls = integer(pool.get("rolls", 1), rng)
        for _ in range(max(0, rolls)):
            entry = _pick(pool.get("entries", []) or [], rng)
            if entry is not None:
                stacks = _entry_items(entry, lookup, rng, depth, result, context)
                for function in pool.get("functions", []) or []:
                    stacks = [_apply(function, stack, rng, result) for stack in stacks]
                result.items.extend(stack for stack in stacks if stack.count > 0)
    for function in table.get("functions", []) or []:
        result.items = [_apply(function, stack, rng, result) for stack in result.items]
    result.items = [stack for stack in result.items if stack.count > 0]
    return result


def _skip_conditions(holder: dict[str, Any], result: LootResult) -> None:
    for condition in holder.get("conditions", []) or []:
        if isinstance(condition, dict):
            result.skipped.add(normalise_id(str(condition.get("condition", "condition"))))


def _pick(entries: list[Any], rng: random.Random) -> dict[str, Any] | None:
    weighted = [
        (entry, max(0, int(entry.get("weight", 1)))) for entry in entries if isinstance(entry, dict)
    ]
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
    entry: dict[str, Any],
    lookup: TableLookup,
    rng: random.Random,
    depth: int,
    result: LootResult,
    context: LootContext,
) -> list[ItemStack]:
    _skip_conditions(entry, result)
    kind = normalise_id(str(entry.get("type", "minecraft:item")))
    stacks: list[ItemStack] = []
    if kind == "minecraft:item":
        stacks = [ItemStack(normalise_id(str(entry.get("name", "minecraft:air"))), 1)]
    elif kind == "minecraft:loot_table":
        value = entry.get("value", entry.get("name"))
        if isinstance(value, dict):
            stacks = evaluate(value, lookup, rng, depth + 1, context).items
        elif isinstance(value, str) and depth < MAX_DEPTH:
            nested = lookup(normalise_id(value))
            if nested is None:
                result.missing.add(normalise_id(value))
            else:
                inner = evaluate(nested, lookup, rng, depth + 1, context)
                result.skipped |= inner.skipped
                result.missing |= inner.missing
                stacks = inner.items
    elif kind in ("minecraft:alternatives", "minecraft:group", "minecraft:sequence"):
        children = [child for child in entry.get("children", []) or [] if isinstance(child, dict)]
        if kind == "minecraft:alternatives":
            children = children[:1]  # conditions are not evaluated: the first one wins
        for child in children:
            stacks.extend(_entry_items(child, lookup, rng, depth, result, context))
    elif kind == "minecraft:dynamic":
        name = normalise_id(str(entry.get("name", "")))
        items = getattr(context.block, "items", None)
        if name == "minecraft:contents" and items is not None:
            stacks = [items[slot].copy() for slot in sorted(items)]
        else:
            result.skipped.add(f"dynamic {name}")
    elif kind != "minecraft:empty":
        result.skipped.add(kind)
    for function in entry.get("functions", []) or []:
        stacks = [_apply(function, stack, rng, result) for stack in stacks]
    return [stack for stack in stacks if stack.id != "minecraft:air"]


def _apply(
    function: Any, stack: ItemStack, rng: random.Random, result: LootResult, cap: bool = False
) -> ItemStack:
    """One item function. ``cap`` limits counts to the stack size (item
    modifiers); loot keeps the count and is split into stacks afterwards."""
    if not isinstance(function, dict):
        return stack
    _skip_conditions(function, result)
    name = normalise_id(str(function.get("function", "")))
    if name == "minecraft:set_count":
        amount = integer(function.get("count", 1), rng)
        stack.count = stack.count + amount if function.get("add") else amount
        stack.count = max(0, min(stack.count, stack.max_count) if cap else stack.count)
    elif name == "minecraft:set_components" and isinstance(function.get("components"), dict):
        for key, value in function["components"].items():
            qualified = key if ":" in key.lstrip("!") else f"minecraft:{key}"
            if qualified.startswith("!"):
                stack.components.pop(qualified[1:], None)
            else:
                stack.components[qualified] = value
    elif name == "minecraft:set_nbt" and isinstance(function.get("tag"), str):
        stack.tag.update(parse_snbt(function["tag"]))
    elif name:
        result.skipped.add(name)
    return stack
