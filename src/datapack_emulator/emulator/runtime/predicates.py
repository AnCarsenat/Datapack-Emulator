"""Predicates: loot conditions, entity, item and location predicates.

One evaluator serves ``execute if predicate``, the ``predicate=`` selector
argument, loot tables, item modifiers and advancement criteria. It answers
what the world model knows — scores, NBT, positions, dimensions, blocks,
effects, equipment, game modes, levels, teams, weather, time, random
chance — and treats what it cannot know (damage sources, killers, enchantment
bonuses, biomes, light) as failing, listing them in ``unchecked`` so the
caller can say so.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import (
    nbt_get,
    nbt_matches,
    normalise_id,
    normalise_tagged_id,
    parse_snbt,
)
from datapack_emulator.emulator.runtime import item_sub_predicates
from datapack_emulator.emulator.runtime.components import namespace_ids, same_value
from datapack_emulator.emulator.runtime.inventory import ItemStack

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.world import Entity, World
    from datapack_emulator.emulator.versions import Version

#: predicates referencing each other deeper than this fail (a loop)
MAX_DEPTH = 32
#: ``getInt`` rounds (``Math.round``) instead of flooring
ROUNDED_INTS_SINCE = "1.17"
#: what an empty slot or hand holds (vanilla's ItemStack.EMPTY)
EMPTY_ID = "minecraft:air"


def empty_stack() -> ItemStack:
    return ItemStack(EMPTY_ID, 0)


def to_float(value: Any, default: float = 0.0) -> float:
    """A JSON number as a float; anything else (a malformed file) is ``default``."""
    if isinstance(value, (bool, int, float)):
        return float(value)
    return default


@dataclass
class Context:
    """What a predicate can look at (vanilla's LootContext)."""

    world: World | None = None
    rng: random.Random = field(default_factory=random.Random)
    #: the ``this`` entity, and where the check happens
    entity: Entity | None = None
    position: list[float] | None = None
    dimension: str = "minecraft:overworld"
    #: the mined block and the tool (loot … mine), and a block's position
    block: Any = None
    block_position: tuple[int, int, int] | None = None
    tool: ItemStack | None = None
    version: Version | None = None
    #: predicate id -> JSON, for ``reference`` conditions and (26.3) predicate ids
    lookup: Callable[[str], Any] | None = None
    #: item modifier id -> JSON, for ``reference`` functions and (26.3) modifier ids
    modifiers: Callable[[str], Any] | None = None
    #: tag id -> member ids for a registry (``entity_type``, ``item``, ``block``)
    tags: Callable[[str, str], set[str] | None] | None = None
    #: condition and predicate parts the world model cannot answer
    unchecked: set[str] = field(default_factory=set)
    depth: int = 0

    def entity_for(self, target: str) -> Entity | None:
        """``this`` (and its old names); killers and attackers are not modelled."""
        if target in ("this", "minecraft:this"):
            return self.entity
        self.unchecked.add(f"entity {target}")
        return None


# ---------------------------------------------------------------------------
# numbers and ranges
# ---------------------------------------------------------------------------


def number(provider: Any, context: Context | random.Random) -> float:
    """A number provider: constant, uniform, binomial, score, storage."""
    if isinstance(context, random.Random):
        context = Context(rng=context)
    if isinstance(provider, bool):
        return float(provider)
    if isinstance(provider, (int, float)):
        return float(provider)
    if not isinstance(provider, dict):
        return 0.0
    rng = context.rng
    kind = normalise_id(str(provider.get("type", "")) or "minecraft:uniform")
    if kind == "minecraft:constant":
        return to_float(provider.get("value"))
    if kind == "minecraft:binomial":
        trials = int(number(provider.get("n", 0), context))
        chance = number(provider.get("p", 0), context)
        return float(sum(1 for _ in range(trials) if rng.random() < chance))
    if kind == "minecraft:score":
        return _score_number(provider, context)
    if kind == "minecraft:storage":
        world = context.world
        storage = normalise_id(str(provider.get("storage", "")))
        store = world.storage.get(storage) if world else None
        value = nbt_get(store, str(provider.get("path", ""))) if isinstance(store, dict) else None
        return float(value) if isinstance(value, (int, float)) else 0.0
    if kind == "minecraft:enchantment_level":
        context.unchecked.add("enchantment_level")
        return 0.0
    if "min" in provider or "max" in provider or kind == "minecraft:uniform":
        low = number(provider.get("min", 0), context)
        high = number(provider.get("max", low), context)
        return rng.uniform(low, high)
    context.unchecked.add(kind)
    return 0.0


def _rounds(context: Context) -> bool:
    version = context.version
    return version is None or version >= versions.parse(ROUNDED_INTS_SINCE)


def java_round(value: float) -> int:
    """Java's ``Math.round``: halves go up."""
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return -(2**31) if value < 0 else 2**31 - 1
    return math.floor(value + 0.5)


def integer(provider: Any, context: Context | random.Random) -> int:
    """``getInt``: uniform ranges include both ends (``Mth.nextInt``: the low
    end when it is not below the high one); other providers round their value
    (floored before 1.17)."""
    if isinstance(context, random.Random):
        context = Context(rng=context)
    if isinstance(provider, dict):
        kind = normalise_id(str(provider.get("type", "")) or "minecraft:uniform")
        if kind == "minecraft:uniform" or (
            not provider.get("type") and "min" in provider and "max" in provider
        ):
            if _rounds(context):
                low = integer(provider.get("min", 0), context)
                high = integer(provider.get("max", 0), context)
            else:
                low = math.floor(number(provider.get("min", 0), context))
                high = math.floor(number(provider.get("max", 0), context))
            return low if low >= high else context.rng.randint(low, high)
    value = number(provider, context)
    return java_round(value) if _rounds(context) else math.floor(value)


def _score_number(provider: dict[str, Any], context: Context) -> float:
    world = context.world
    target = provider.get("target", "this")
    holder: str | None = None
    if isinstance(target, dict):
        kind = normalise_id(str(target.get("type", "")))
        if kind == "minecraft:fixed":
            holder = str(target.get("name", ""))
        else:
            entity = context.entity_for(str(target.get("target", "this")))
            holder = entity.id if entity else None
    else:
        entity = context.entity_for(str(target))
        holder = entity.id if entity else None
    if world is None or holder is None:
        return 0.0
    value = world.scoreboard.get(holder, str(provider.get("score", "")))
    scale = to_float(provider.get("scale"), 1.0)
    return float(value or 0) * scale


def in_bounds(value: float | None, bounds: Any, context: Context | None = None) -> bool:
    """A range: a number (exact), ``{min, max}`` (either optional; numbers or
    providers), or nothing (anything)."""
    if bounds is None:
        return True
    if value is None:
        return False
    if isinstance(bounds, (int, float)) and not isinstance(bounds, bool):
        return value == bounds
    if isinstance(bounds, dict):
        context = context or Context()
        low = _bound(bounds.get("min"), context)
        high = _bound(bounds.get("max"), context)
        if low is not None and value < low:
            return False
        return not (high is not None and value > high)
    return False


def _bound(bound: Any, context: Context) -> float | None:
    """A range end: a number, or (in int ranges) a provider read with getInt."""
    if isinstance(bound, dict):
        return integer(bound, context)
    if isinstance(bound, (int, float)):
        return float(bound)
    return None


# ---------------------------------------------------------------------------
# loot conditions (predicate files)
# ---------------------------------------------------------------------------


def typed_loot(context: Context) -> bool | None:
    """Whether the context's version reads the 26.3 loot format (``type``,
    ``condition``, ``modifier``, ids for references); None when no version is
    known (then either spelling is read, the older one first)."""
    version = context.version
    return None if version is None else versions.uses_typed_loot(version)


def kind_of(value: dict[str, Any], legacy_key: str, context: Context) -> str:
    """The type id of a condition (``legacy_key`` ``condition``) or item
    function (``function``): ``type`` from 26.3."""
    typed = typed_loot(context)
    if typed:
        raw = value.get("type")
    elif typed is False:
        raw = value.get(legacy_key)
    else:
        raw = value.get(legacy_key) or value.get("type")
    return normalise_id(str(raw)) if isinstance(raw, str) and raw else ""


def holder_field(holder: dict[str, Any], key: str, legacy_key: str, context: Context) -> Any:
    """A pool's, entry's or function's conditions (``condition``, before 26.3
    ``conditions``) or item functions (``modifier``, before 26.3 ``functions``)."""
    typed = typed_loot(context)
    if typed:
        return holder.get(key)
    if typed is False:
        return holder.get(legacy_key)
    return holder.get(legacy_key, holder.get(key))


def holder_passes(holder: dict[str, Any], context: Context) -> bool:
    """Do the conditions a pool, entry or item function carries pass?"""
    conditions = holder_field(holder, "condition", "conditions", context)
    return conditions is None or conditions == [] or check(conditions, context)


def expand_references(values: list[Any], registry: str, context: Context) -> list[Any]:
    """A 26.3 list: inline values, ids, and ``#tags`` of ids (each member in
    its place)."""
    out: list[Any] = []
    for value in values:
        if isinstance(value, str) and value.startswith("#") and typed_loot(context) is not False:
            members = context.tags(registry, value) if context.tags else None
            if members is None:
                context.unchecked.add(f"{registry} tag {normalise_tagged_id(value)}")
                out.append(False)
            else:
                out.extend(sorted(members))
        else:
            out.append(value)
    return out


def resolve(name: str, lookup: Callable[[str], Any] | None, what: str, context: Context) -> Any:
    """A predicate or item modifier named by id (26.3), or None (noted)."""
    target = lookup(normalise_id(name)) if lookup else None
    if target is None:
        context.unchecked.add(f"missing {what} {normalise_id(name)}")
    return target


def check(condition: Any, context: Context) -> bool:
    """One condition, or a list of them (all must pass). From 26.3 a condition
    can also be a predicate id, and a list can hold ``#tags`` of them."""
    if isinstance(condition, list):
        terms = expand_references(condition, "predicate", context)
        return all(check(entry, context) for entry in terms)
    if isinstance(condition, str) and typed_loot(context) is not False:
        if condition.startswith("#"):
            return check([condition], context)
        target = resolve(condition, context.lookup, "predicate", context)
        if target is None or context.depth >= MAX_DEPTH:
            return False
        context.depth += 1
        try:
            return check(target, context)
        finally:
            context.depth -= 1
    if not isinstance(condition, dict):
        return False
    kind = kind_of(condition, "condition", context)
    handler = CONDITIONS.get(kind)
    typed = typed_loot(context)
    if (
        handler is None
        or (typed and kind in LEGACY_ONLY)
        or (typed is False and kind in TYPED_ONLY)
    ):
        context.unchecked.add(kind or "condition without a type")
        return False
    return handler(condition, context)


def _inverted(condition: dict[str, Any], context: Context) -> bool:
    return not check(condition.get("term"), context)


def _terms(condition: dict[str, Any], context: Context) -> list[Any]:
    terms = condition.get("terms")
    return expand_references(terms, "predicate", context) if isinstance(terms, list) else []


def _all_of(condition: dict[str, Any], context: Context) -> bool:
    return all(check(term, context) for term in _terms(condition, context))


def _any_of(condition: dict[str, Any], context: Context) -> bool:
    return any(check(term, context) for term in _terms(condition, context))


def _random_chance(condition: dict[str, Any], context: Context) -> bool:
    return context.rng.random() < number(condition.get("chance", 0), context)


def _random_chance_bonus(condition: dict[str, Any], context: Context) -> bool:
    # without a killer's enchantments, the unenchanted chance applies
    context.unchecked.add("enchantment bonus")
    base = condition.get("unenchanted_chance", condition.get("chance", 0))
    return context.rng.random() < number(base, context)


def _entity_properties(condition: dict[str, Any], context: Context) -> bool:
    entity = context.entity_for(str(condition.get("entity", "this")))
    return entity is not None and entity_matches(condition.get("predicate"), entity, context)


def _entity_scores(condition: dict[str, Any], context: Context) -> bool:
    entity = context.entity_for(str(condition.get("entity", "this")))
    if entity is None or context.world is None:
        return False
    board = context.world.scoreboard
    scores = condition.get("scores")
    if not isinstance(scores, dict):
        return False
    for objective, bounds in scores.items():
        if not in_bounds(board.get(entity.id, objective), bounds, context):
            return False
    return True


def _value_check(condition: dict[str, Any], context: Context) -> bool:
    return in_bounds(number(condition.get("value"), context), condition.get("range"), context)


def _int_value_check(condition: dict[str, Any], context: Context) -> bool:
    """26.3: ``value`` read with getInt, in the ``test`` range."""
    return in_bounds(integer(condition.get("value"), context), condition.get("test"), context)


def _float_value_check(condition: dict[str, Any], context: Context) -> bool:
    """26.3: ``value`` read as a float, in the ``test`` range."""
    return in_bounds(number(condition.get("value"), context), condition.get("test"), context)


def _time_check(condition: dict[str, Any], context: Context) -> bool:
    if context.world is None:
        return False
    time = context.world.state.day_time
    period = condition.get("period")
    if isinstance(period, int) and period > 0:
        time %= period
    return in_bounds(time, condition.get("value"), context)


def _weather_check(condition: dict[str, Any], context: Context) -> bool:
    if context.world is None:
        return False
    weather = context.world.state.weather
    raining = condition.get("raining")
    thundering = condition.get("thundering")
    if raining is not None and bool(raining) != (weather in ("rain", "thunder")):
        return False
    return thundering is None or bool(thundering) == (weather == "thunder")


def _location_check(condition: dict[str, Any], context: Context) -> bool:
    position = context.position
    if position is None:
        return False
    offset = [
        position[0] + to_float(condition.get("offsetX")),
        position[1] + to_float(condition.get("offsetY")),
        position[2] + to_float(condition.get("offsetZ")),
    ]
    return location_matches(condition.get("predicate"), offset, context.dimension, context)


def _block_state(condition: dict[str, Any], context: Context) -> bool:
    block = context.block
    if block is None:
        return False
    if normalise_id(str(condition.get("block", ""))) != block.id:
        return False
    return _properties_match(condition.get("properties"), block.properties, context, block.id)


def _match_block(condition: dict[str, Any], context: Context) -> bool:
    """26.3: the loot context's block, against a block predicate (``blocks``,
    ``state``, ``nbt``) -- what ``block_state_property`` was."""
    block = context.block
    if block is None:
        return False
    return block_matches(condition, block, context.block_position, context)


def _match_tool(condition: dict[str, Any], context: Context) -> bool:
    tool = context.tool
    return tool is not None and item_matches(condition.get("predicate"), tool, context)


def _killed_by_player(condition: dict[str, Any], context: Context) -> bool:
    context.unchecked.add("killed_by_player")
    return False


def _survives_explosion(condition: dict[str, Any], context: Context) -> bool:
    return True  # no explosion happens in a command's loot


def _table_bonus(condition: dict[str, Any], context: Context) -> bool:
    # no enchantments in a command's context: the level-0 chance
    chances = condition.get("chances")
    first = chances[0] if isinstance(chances, list) and chances else 0
    return context.rng.random() < to_float(first)


def _reference(condition: dict[str, Any], context: Context) -> bool:
    name = normalise_id(str(condition.get("name", "")))
    target = context.lookup(name) if context.lookup else None
    if target is None or context.depth >= MAX_DEPTH:
        return False
    context.depth += 1
    try:
        return check(target, context)
    finally:
        context.depth -= 1


def _unknowable(name: str) -> Callable[[dict[str, Any], Context], bool]:
    def handler(condition: dict[str, Any], context: Context) -> bool:
        context.unchecked.add(name)
        return False

    return handler


CONDITIONS: dict[str, Callable[[dict[str, Any], Context], bool]] = {
    "minecraft:inverted": _inverted,
    "minecraft:all_of": _all_of,
    "minecraft:any_of": _any_of,
    "minecraft:alternative": _any_of,  # before 1.20
    "minecraft:random_chance": _random_chance,
    "minecraft:random_chance_with_looting": _random_chance_bonus,
    "minecraft:random_chance_with_enchanted_bonus": _random_chance_bonus,
    "minecraft:entity_properties": _entity_properties,
    "minecraft:entity_scores": _entity_scores,
    "minecraft:value_check": _value_check,
    "minecraft:int_value_check": _int_value_check,
    "minecraft:float_value_check": _float_value_check,
    "minecraft:time_check": _time_check,
    "minecraft:weather_check": _weather_check,
    "minecraft:location_check": _location_check,
    "minecraft:block_state_property": _block_state,
    "minecraft:match_block": _match_block,
    "minecraft:match_tool": _match_tool,
    "minecraft:killed_by_player": _killed_by_player,
    "minecraft:survives_explosion": _survives_explosion,
    "minecraft:table_bonus": _table_bonus,
    "minecraft:reference": _reference,
    "minecraft:damage_source_properties": _unknowable("damage_source_properties"),
    "minecraft:enchantment_active_check": _unknowable("enchantment_active_check"),
}
#: condition types 26.3 removed (``reference``: a predicate id instead) ...
LEGACY_ONLY = frozenset(
    {"minecraft:reference", "minecraft:block_state_property", "minecraft:value_check"}
)
#: ... and the ones it added in their place
TYPED_ONLY = frozenset(
    {"minecraft:match_block", "minecraft:int_value_check", "minecraft:float_value_check"}
)


# ---------------------------------------------------------------------------
# entity, item and location predicates
# ---------------------------------------------------------------------------


def _tag_members(context: Context, registry: str, tag: str) -> set[str] | None:
    if context.tags is None:
        return None
    return context.tags(registry, tag)


def _id_matches(wanted: Any, actual: str, registry: str, context: Context, part: str) -> bool:
    """An id, a ``#tag`` or a list of ids."""
    if wanted is None:
        return True
    entries = wanted if isinstance(wanted, list) else [wanted]
    for entry in entries:
        entry = normalise_tagged_id(str(entry))
        if entry.startswith("#"):
            members = _tag_members(context, registry, entry)
            if members is None:
                context.unchecked.add(f"{part} tag {entry}")
                return True
            if actual in members:
                return True
        elif entry == actual:
            return True
    return False


def _properties_match(
    wanted: Any, actual: dict[str, str], context: Context | None = None, block_id: str = ""
) -> bool:
    """Block-state values: a string (or number/boolean, compared as text) or a
    ``{min, max}`` range. A property the block was not placed with does not
    match (default states are not modelled), and is noted as such."""
    if not isinstance(wanted, dict):
        return True
    for key, value in wanted.items():
        have = actual.get(key)
        if have is None and context is not None:
            context.unchecked.add(f"default state {key} of {block_id or 'a block'}")
        if isinstance(value, dict):
            if have is None:
                return False
            low, high = value.get("min"), value.get("max")
            try:
                number = float(have)
                if low is not None and number < float(low):
                    return False
                if high is not None and number > float(high):
                    return False
            except (TypeError, ValueError):
                return False
        elif have != str(value).lower():
            return False
    return True


def _nbt_matches(pattern: Any, data: dict[str, Any]) -> bool:
    parsed = parse_snbt(pattern) if isinstance(pattern, str) else pattern
    return isinstance(parsed, dict) and nbt_matches(data, parsed)


#: sub-predicates of the 26.2 map form that have a field of the older form
ENTITY_FIELDS = frozenset(
    {"nbt", "location", "distance", "flags", "equipment", "slots", "effects", "team",
     "vehicle", "passenger", "stepping_on", "movement_affected_by", "periodic_tick",
     "targeted_entity", "movement", "components", "predicates"}
)  # fmt: skip


def _entity_fields(predicate: dict[str, Any], context: Context) -> dict[str, Any] | None:
    """The 26.2 map form (``minecraft:entity_type``, ``minecraft:flags``,
    ``minecraft:type_specific/player``, …) in the older field form; None (noted)
    when it holds a sub-predicate the world model cannot answer. Before 26.2
    the game ignores such keys, and so does this."""
    version = context.version
    if version is not None and not versions.uses_entity_predicate_map(version):
        return predicate
    if not any(":" in key for key in predicate):
        return predicate
    out: dict[str, Any] = {}
    for key, value in predicate.items():
        # only vanilla sub-predicates exist; another namespace is noted by its full id
        name = normalise_id(key).removeprefix("minecraft:") if ":" in key else key
        if name in ("entity_type", "type"):
            out["type"] = value
        elif name == "type_specific/player" and isinstance(value, dict):
            out["type_specific"] = {"type": "minecraft:player", **value}
        elif name in ENTITY_FIELDS:
            out[name] = value
        else:
            context.unchecked.add(f"entity {name}")
            return None
    return out


def entity_matches(predicate: Any, entity: Entity, context: Context) -> bool:
    """An entity predicate (``type``, ``nbt``, ``location``, ``distance``,
    ``flags``, ``equipment``, ``slots``, ``effects``, ``team``,
    ``type_specific``/``player``, ``vehicle``, ``passenger``; from 26.2 the
    same as namespaced sub-predicates, ``minecraft:entity_type`` …)."""
    if predicate is None:
        return True
    if not isinstance(predicate, dict):
        return False
    fields = _entity_fields(predicate, context)
    if fields is None:
        return False
    predicate = fields
    version = context.version
    if not _id_matches(predicate.get("type"), entity.type, "entity_type", context, "type"):
        return False
    if "nbt" in predicate and not _nbt_matches(predicate["nbt"], entity.data(version)):
        return False
    if "location" in predicate and not location_matches(
        predicate["location"], entity.position, entity.dimension, context
    ):
        return False
    distance = predicate.get("distance")
    if distance is not None:
        origin = context.position
        if origin is None:
            return False
        dx, dy, dz = (entity.position[i] - origin[i] for i in range(3))
        checks = {
            "x": abs(dx),
            "y": abs(dy),
            "z": abs(dz),
            "horizontal": math.hypot(dx, dz),
            "absolute": math.sqrt(dx * dx + dy * dy + dz * dz),
        }
        for key, value in checks.items():
            if key in distance and not in_bounds(value, distance[key], context):
                return False
    flags = predicate.get("flags")
    if isinstance(flags, dict) and not _flags_match(flags, entity):
        return False
    equipment = predicate.get("equipment")
    if isinstance(equipment, dict):
        if entity.living is None:
            return False
        for slot, item_predicate in equipment.items():
            if not item_matches(item_predicate, _equipment(entity, slot), context):
                return False
    slots = predicate.get("slots")
    if isinstance(slots, dict):
        for slot, item_predicate in slots.items():
            keys = entity.inventory.keys_for(slot) or []
            if not any(
                item_matches(item_predicate, entity.inventory.get(key) or empty_stack(), context)
                for key in keys
            ):
                return False
    effects = predicate.get("effects")
    if isinstance(effects, dict):
        active = entity.living.effects if entity.living else {}
        for effect_id, effect_predicate in effects.items():
            effect = active.get(normalise_id(effect_id))
            if effect is None:
                return False
            if isinstance(effect_predicate, dict) and not (
                in_bounds(effect.amplifier, effect_predicate.get("amplifier"), context)
                and in_bounds(effect.duration, effect_predicate.get("duration"), context)
            ):
                return False
    if "team" in predicate:
        team = context.world.state.team_of(entity.id) if context.world else None
        if team is None or team.name != predicate["team"]:
            return False
    player = predicate.get("type_specific") or predicate.get("player")
    if isinstance(player, dict) and not _player_matches(player, entity, context):
        return False
    for key, related in (("vehicle", [entity.vehicle]), ("passenger", entity.passengers)):
        if key in predicate and not any(
            other is not None and entity_matches(predicate[key], other, context)
            for other in related
        ):
            return False
    for key in ("stepping_on", "movement_affected_by", "periodic_tick", "targeted_entity",
                "movement", "components", "predicates"):  # fmt: skip
        if key in predicate:
            context.unchecked.add(f"entity {key}")
            return False
    return True


def _is_baby(entity: Entity) -> bool:
    # zombies and piglins keep a flag, breeding animals a negative age
    if "IsBaby" in entity.nbt:
        return bool(entity.nbt["IsBaby"])
    age = entity.nbt.get("Age")
    return isinstance(age, int) and age < 0


def _flags_match(flags: dict[str, Any], entity: Entity) -> bool:
    fire = entity.nbt.get("Fire", -1)
    if entity.living is None:  # only living entities are asked about age
        flags = {key: value for key, value in flags.items() if key != "is_baby"}
    known = {
        "is_on_fire": isinstance(fire, int) and fire > 0,
        "is_baby": _is_baby(entity),
        "is_sneaking": False,
        "is_sprinting": False,
        "is_swimming": False,
        "is_flying": bool(entity.nbt.get("FallFlying")),
        "is_on_ground": bool(entity.nbt.get("OnGround")),
    }
    return all(known.get(key, False) == bool(value) for key, value in flags.items())


def _equipment(entity: Entity, slot: str) -> ItemStack:
    name = {"mainhand": "weapon.mainhand", "offhand": "weapon.offhand", "body": "armor.body"}.get(
        slot, f"armor.{slot}"
    )
    keys = entity.inventory.keys_for(name) or []
    stack = entity.inventory.get(keys[0]) if keys else None
    return stack if stack is not None else empty_stack()


def _player_matches(predicate: dict[str, Any], entity: Entity, context: Context) -> bool:
    kind = normalise_id(str(predicate.get("type", "minecraft:player")))
    if kind != "minecraft:player":
        context.unchecked.add(f"type_specific {kind}")
        return False
    if not entity.is_player:
        return False
    modes = predicate.get("gamemode")
    if modes is not None:
        from datapack_emulator.emulator.runtime.state import GAME_MODES

        index = entity.nbt.get("playerGameType", 0)
        mode = GAME_MODES[index] if isinstance(index, int) and 0 <= index < 4 else "survival"
        if mode not in (modes if isinstance(modes, list) else [modes]):
            return False
    if "level" in predicate and not in_bounds(
        entity.nbt.get("XpLevel", 0), predicate["level"], context
    ):
        return False
    advancements = predicate.get("advancements")
    if isinstance(advancements, dict) and context.world is not None:
        progress = context.world.advancements
        for advancement, wanted in advancements.items():
            if not progress.matches(entity.id, normalise_id(advancement), wanted):
                return False
    for key in ("recipes", "stats", "looking_at", "input", "food"):
        if key in predicate:
            context.unchecked.add(f"player {key}")
            return False
    return True


def location_matches(
    predicate: Any, position: list[float], dimension: str, context: Context
) -> bool:
    if predicate is None:
        return True
    if not isinstance(predicate, dict):
        return False
    wanted = predicate.get("position")
    if isinstance(wanted, dict):
        for index, axis in enumerate("xyz"):
            if axis in wanted and not in_bounds(position[index], wanted[axis], context):
                return False
    if "dimension" in predicate and normalise_id(str(predicate["dimension"])) != dimension:
        return False
    block_predicate = predicate.get("block")
    if block_predicate is not None:
        world = context.world
        if world is None:
            return False
        at = (math.floor(position[0]), math.floor(position[1]), math.floor(position[2]))
        block = world.blocks.get(dimension, at)
        if not block_matches(block_predicate, block, at, context):
            return False
    for key in ("biome", "biomes", "structure", "structures", "light", "fluid",
                "smokey", "can_see_sky", "feature"):  # fmt: skip
        if key in predicate:
            context.unchecked.add(f"location {key}")
            return False
    return True


def block_matches(predicate: Any, block: Any, position: Any, context: Context) -> bool:
    if not isinstance(predicate, dict):
        return predicate is None
    wanted = predicate.get("blocks", predicate.get("block"))
    if wanted is not None and not _id_matches(wanted, block.id, "block", context, "block"):
        return False
    if "tag" in predicate and not _id_matches(
        "#" + str(predicate["tag"]).lstrip("#"), block.id, "block", context, "block"
    ):
        return False
    if not _properties_match(predicate.get("state"), block.properties, context, block.id):
        return False
    return "nbt" not in predicate or _nbt_matches(
        predicate["nbt"], block.data(context.version, position)
    )


def item_matches(predicate: Any, stack: ItemStack, context: Context) -> bool:
    """An item predicate: ``items`` (or the older ``item``/``tag``), ``count``,
    ``components``, ``nbt``; the ``predicates`` map and enchantment checks are
    compared against the item's components."""
    if predicate is None:
        return True
    if not isinstance(predicate, dict):
        return False
    wanted = predicate.get("items", predicate.get("item"))
    if wanted is not None and not _id_matches(wanted, stack.id, "item", context, "item"):
        return False
    if "tag" in predicate and not _id_matches(
        "#" + str(predicate["tag"]).lstrip("#"), stack.id, "item", context, "item"
    ):
        return False
    if not in_bounds(stack.count, predicate.get("count"), context):
        return False
    components = predicate.get("components")
    if isinstance(components, dict):
        # exact matches against the effective components (the item's defaults + patch)
        actual = stack.effective_components(context.version)
        for key, value in components.items():
            qualified = key if ":" in key else f"minecraft:{key}"
            if qualified not in actual or not same_value(
                actual[qualified], namespace_ids(qualified, value), qualified
            ):
                return False
    if "nbt" in predicate and not _nbt_matches(predicate["nbt"], stack.tag):
        return False
    sub_predicates = predicate.get("predicates")
    if isinstance(sub_predicates, dict):
        components = stack.effective_components(context.version)
        for name, value in sub_predicates.items():
            if name == "count" or not item_sub_predicates.checkable(name, value):
                context.unchecked.add(f"item predicates {name}")
                return False
            if not item_sub_predicates.matches(name, value, stack.count, components):
                return False
        predicate = {key: item for key, item in predicate.items() if key != "predicates"}
    for key in ("predicates", "enchantments", "stored_enchantments", "potion", "durability"):
        if key in predicate:
            context.unchecked.add(f"item {key}")
            return False
    return True
