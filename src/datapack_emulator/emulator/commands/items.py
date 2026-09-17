"""Commands on items and inventories: give, clear, item, replaceitem, enchant,
loot, and the ``items`` condition of execute.

Container blocks work too (``item … block``, ``loot insert``, ``loot … mine``,
``execute if items block``). See :mod:`datapack_emulator.emulator.runtime.inventory`
and :mod:`datapack_emulator.emulator.runtime.blocks` for the models.
"""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.commands.helpers import (
    find_targets,
    integer,
    require_id,
    require_targets,
)
from datapack_emulator.emulator.commands.parser import Command, resolve_position
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.blocks import Block, Position
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.inventory import (
    SLOT_NAMES,
    Inventory,
    ItemPredicate,
    ItemStack,
    parse_item,
    parse_item_predicate,
    slot_number,
    uses_components,
    uses_equipment,
)
from datapack_emulator.emulator.runtime.loot import LootContext, evaluate
from datapack_emulator.emulator.runtime.world import Entity

#: how many of an item give accepts at once, in stacks
GIVE_STACK_LIMIT = 100


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def item_name(context: ExecutionContext, item_id: str) -> str:
    """An item as feedback shows it: its translated name in brackets."""
    path = item_id.split(":", 1)[-1]
    catalogue = context.emulator.messages
    for prefix in ("item", "block"):
        template = catalogue.template(f"{prefix}.minecraft.{path}")
        if template:
            return f"[{template}]"
    return "[" + path.replace("_", " ").title() + "]"


def _players(context: ExecutionContext, token: str) -> list[Entity] | None:
    """Targets that must all be players; None once an error was reported."""
    found = require_targets(context, token)
    if not found:
        return None
    if any(not entity.is_player for entity in found):
        context.game_error("argument.player.entities")
        return None
    return found


def _stack(context: ExecutionContext, token: str, allow_air: bool = False) -> ItemStack | None:
    """An item argument; ``allow_air`` accepts air, which empties a slot."""
    if allow_air and normalise_id(token.split("[")[0].split("{")[0]) == "minecraft:air":
        return ItemStack("minecraft:air", 1)
    stack = parse_item(token)
    if stack is None:
        context.game_error("argument.item.id.invalid", token)
        return None
    if not require_id(context, "item", stack.id, "argument.item.id.invalid"):
        return None
    return stack


def item_tag_members(context: ExecutionContext, tag: str) -> set[str] | None:
    """The item ids of ``#tag`` from the client jar or the pack; None if unknown."""
    tag_id = normalise_id(tag.lstrip("#"))
    members: set[str] = set()
    found = False
    assets = context.emulator.vanilla
    if assets is not None and tag_id in assets.tags.get("item", {}):
        members |= assets.resolve_tag("item", tag_id)
        found = True
    pack_tags = context.emulator.pack.registries.get("tags/item", {})
    for resource in pack_tags.values():
        if getattr(resource, "tag_id", "") == f"#{tag_id}":
            members |= {normalise_id(entry.value) for entry in resource.entries}
            found = True
    return members if found else None


def _predicate(context: ExecutionContext, token: str) -> ItemPredicate:
    predicate = parse_item_predicate(token)
    if predicate.id.startswith("#"):
        predicate.tag_members = item_tag_members(context, predicate.id)
        if predicate.tag_members is None:
            context.note_once(
                f"item tag {predicate.id} is not known without a client jar: every item matches it"
            )
    if predicate.unchecked:
        context.note_once(
            "item predicate parts are not checked by the emulator: "
            + ", ".join(predicate.unchecked)
        )
    return predicate


def _slot_exists(slot: str) -> bool:
    return (
        Inventory(player=True).keys_for(slot) is not None
        or Inventory(player=False).keys_for(slot) is not None
    )


def drop(context: ExecutionContext, position: list[float], stack: ItemStack) -> None:
    """Spawn item entities for a stack, split into full stacks."""
    remaining = stack.count
    while remaining > 0:
        count = min(remaining, stack.max_count)
        remaining -= count
        entity = Entity(type="minecraft:item", position=list(position))
        entity.nbt = {
            "Item": stack.copy(count).to_nbt(context.emulator.version),
            "PickupDelay": 10,
            "Age": 0,
        }
        context.world.spawn(entity)


def _container(
    context: ExecutionContext, tokens: list[str], key: str = "commands.item.target.not_a_container"
) -> tuple[Position, Block] | None:
    from datapack_emulator.emulator.commands.blocks import container_at, position_at

    position = position_at(context, tokens[:3])
    block = container_at(context, position, key) if position is not None else None
    return (position, block) if block is not None else None


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


# ---------------------------------------------------------------------------
# give / clear
# ---------------------------------------------------------------------------


def cmd_give(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    stack = _stack(context, arguments[1])
    count = integer(context, arguments[2]) if stack is not None and len(arguments) > 2 else 1
    if stack is None or count is None:
        return CommandResult.failure()
    if count < 1:
        context.game_error("argument.integer.low", 1, count)
        return CommandResult.failure()
    limit = stack.max_count * GIVE_STACK_LIMIT
    if count > limit:
        context.game_error("commands.give.failed.toomanyitems", limit, item_name(context, stack.id))
        return CommandResult.failure()
    players = _players(context, arguments[0])
    if players is None:
        return CommandResult.failure()
    for player in players:
        leftover = player.inventory.add(stack.copy(count))
        if leftover:
            drop(context, player.position, stack.copy(leftover))
    name = item_name(context, stack.id)
    if len(players) == 1:
        context.feedback("commands.give.success.single", count, name, players[0].display)
    else:
        context.feedback("commands.give.success.multiple", count, name, len(players))
    return CommandResult(success=True, value=len(players))


def cmd_clear(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if arguments:
        players = _players(context, arguments[0])
    elif context.executor is not None and context.executor.is_player:
        players = [context.executor]
    else:
        context.game_error("permissions.requires.player")
        return CommandResult.failure()
    if players is None:
        return CommandResult.failure()
    predicate = _predicate(context, arguments[1]) if len(arguments) > 1 else ItemPredicate()
    max_count = integer(context, arguments[2]) if len(arguments) > 2 else -1
    if max_count is None:
        return CommandResult.failure()
    if len(arguments) > 2 and max_count < 0:
        context.game_error("argument.integer.low", 0, max_count)
        return CommandResult.failure()
    total = 0
    for player in players:
        if max_count == 0:
            total += player.inventory.count(predicate)
        else:
            total += player.inventory.remove(predicate, max_count)
    single = len(players) == 1
    who = players[0].display if single else len(players)
    amount = "single" if single else "multiple"
    if total == 0:
        context.game_error(f"commands.clear.failed.{amount}", who)
        return CommandResult.failure()
    if max_count == 0:
        context.feedback(f"commands.clear.test.{amount}", total, who)
    else:
        context.feedback(f"commands.clear.success.{amount}", total, who)
    return CommandResult(success=True, value=total)


# ---------------------------------------------------------------------------
# item / replaceitem
# ---------------------------------------------------------------------------


def _source_stack(context: ExecutionContext, arguments: list[str]) -> tuple[bool, ItemStack | None]:
    """``entity <source> <slot>`` / ``block <pos> <slot>`` -> (found, the stack
    or None for an empty slot)."""
    if len(arguments) < 3:
        _usage(context)
        return (False, None)
    if arguments[0] == "block":
        if len(arguments) < 5:
            _usage(context)
            return (False, None)
        found = _container(context, arguments[1:4], "commands.item.source.not_a_container")
        if found is None:
            return (False, None)
        _, block = found
        slots = block.slot_keys(arguments[4])
        if slots is None or len(slots) != 1:
            context.game_error("commands.item.source.no_such_slot", slot_number(arguments[4]))
            return (False, None)
        stack = block.get_item(slots[0])
        return (True, stack.copy() if stack is not None else None)
    sources = require_targets(context, arguments[1])
    if not sources:
        return (False, None)
    if len(sources) > 1:
        context.game_error("argument.entity.toomany")
        return (False, None)
    keys = sources[0].inventory.keys_for(arguments[2])
    if keys is None or len(keys) != 1:
        context.game_error("commands.item.source.no_such_slot", arguments[2])
        return (False, None)
    stack = sources[0].inventory.get(keys[0])
    return (True, stack.copy() if stack is not None else None)


def _set_slots(
    context: ExecutionContext, token: str, slot: str, stack: ItemStack | None
) -> CommandResult:
    if not _slot_exists(slot):
        context.game_error("slot.unknown", slot)
        return CommandResult.failure()
    targets = require_targets(context, token)
    if not targets:
        return CommandResult.failure()
    changed = []
    for entity in targets:
        keys = entity.inventory.keys_for(slot)
        if keys is None or len(keys) != 1:
            continue
        entity.inventory.set(keys[0], stack.copy() if stack is not None else None)
        changed.append(entity)
    if not changed:
        context.game_error("commands.item.target.no_changes", slot)
        return CommandResult.failure()
    name = item_name(context, stack.id if stack is not None else "minecraft:air")
    if len(changed) == 1:
        context.feedback("commands.item.entity.set.success.single", changed[0].display, name)
    else:
        context.feedback("commands.item.entity.set.success.multiple", len(changed), name)
    return CommandResult(success=True, value=len(changed))


def cmd_item(command: Command, context: ExecutionContext) -> CommandResult:
    """``item replace|modify (entity <targets>|block <pos>) <slot> …``"""
    arguments = command.arguments
    if len(arguments) < 4:
        return _usage(context)
    action, kind = arguments[0], arguments[1]
    if kind == "block":
        return _item_block(context, arguments)
    token, slot, rest = arguments[2], arguments[3], arguments[4:]
    if action == "replace" and rest and rest[0] == "with" and len(rest) > 1:
        stack = _stack(context, rest[1], allow_air=True)
        count = integer(context, rest[2]) if stack is not None and len(rest) > 2 else 1
        if stack is None or count is None or not _valid_count(context, stack, count):
            return CommandResult.failure()
        stack.count = count
        return _set_slots(context, token, slot, stack)
    if action == "replace" and rest and rest[0] == "from":
        found, stack = _source_stack(context, rest[1:])
        if not found:
            return CommandResult.failure()
        modifier_at = 6 if len(rest) > 1 and rest[1] == "block" else 4
        if len(rest) > modifier_at and stack is not None:
            stack = _modify(context, stack, rest[modifier_at])
            if stack is None:
                return CommandResult.failure()
        return _set_slots(context, token, slot, stack)
    if action == "modify" and rest:
        if _modifier(context, rest[0]) is None:
            return CommandResult.failure()
        targets = require_targets(context, token)
        changed = 0
        for entity in targets:
            keys = entity.inventory.keys_for(slot) or []
            current = entity.inventory.get(keys[0]) if len(keys) == 1 else None
            if current is not None:
                entity.inventory.set(keys[0], _modify(context, current, rest[0]))
                changed += 1
        if not changed:
            context.game_error("commands.item.target.no_changes", slot)
            return CommandResult.failure()
        return CommandResult(success=True, value=changed)
    return _usage(context)


def _item_block(context: ExecutionContext, arguments: list[str]) -> CommandResult:
    """``item replace|modify block <pos> <slot> …``"""
    if len(arguments) < 7:
        return _usage(context)
    action, slot, rest = arguments[0], arguments[5], arguments[6:]
    found = _container(context, arguments[2:5])
    if found is None:
        return CommandResult.failure()
    position, block = found
    if not _slot_exists(slot):
        context.game_error("slot.unknown", slot)
        return CommandResult.failure()
    slots = block.slot_keys(slot)
    if not slots or len(slots) != 1:
        context.game_error("commands.item.target.no_such_slot", slot_number(slot))
        return CommandResult.failure()
    if action == "modify":
        current = block.get_item(slots[0])
        if _modifier(context, rest[0]) is None:
            return CommandResult.failure()
        # vanilla applies the modifier to whatever is there, even nothing
        stack = _modify(context, current, rest[0]) if current is not None else None
    elif action == "replace" and rest[0] == "with" and len(rest) > 1:
        stack = _stack(context, rest[1], allow_air=True)
        count = integer(context, rest[2]) if stack is not None and len(rest) > 2 else 1
        if stack is None or count is None or not _valid_count(context, stack, count):
            return CommandResult.failure()
        stack.count = count
    elif action == "replace" and rest[0] == "from":
        found_source, stack = _source_stack(context, rest[1:])
        width = 3 if len(rest) > 1 and rest[1] == "block" else 1
        modifier = rest[3 + width] if len(rest) > 3 + width else None
        if not found_source:
            return CommandResult.failure()
        if modifier is not None and stack is not None:
            stack = _modify(context, stack, modifier)
            if stack is None:
                return CommandResult.failure()
    else:
        return _usage(context)
    block.set_item(slots[0], stack.copy() if stack is not None else None)
    name = item_name(context, stack.id if stack is not None else "minecraft:air")
    context.feedback("commands.item.block.set.success", *position, name)
    return CommandResult(success=True, value=1)


def _valid_count(context: ExecutionContext, stack: ItemStack, count: int) -> bool:
    """``item replace … with <item> <count>``: 1–99 (1–64 before 1.20.5), and
    no more than the item stacks to."""
    highest = 99 if uses_components(context.emulator.version) else 64
    if count < 1:
        context.game_error("argument.integer.low", 1, count)
        return False
    if count > highest:
        context.game_error("argument.integer.big", highest, count)
        return False
    if count > stack.max_count:
        context.game_error(
            "arguments.item.overstacked", item_name(context, stack.id), stack.max_count
        )
        return False
    return True


def _modifier(context: ExecutionContext, modifier_id: str) -> Any:
    modifiers = context.emulator.pack.registries.get("item_modifier", {})
    content: Any = getattr(modifiers.get(normalise_id(modifier_id)), "content", None)
    if content is None:
        context.game_error("argument.resource_or_id.no_such_element", modifier_id, "item_modifier")
    return content


def _modify(context: ExecutionContext, stack: ItemStack, modifier_id: str) -> ItemStack | None:
    """Apply an item modifier from the pack; None, with the error reported, when
    the pack has no such modifier."""
    from datapack_emulator.emulator.commands.conditions import (
        predicate_context,
        report_unchecked,
    )
    from datapack_emulator.emulator.runtime.loot import modify

    content = _modifier(context, modifier_id)
    if content is None:
        return None
    stack, result = modify(content, stack, predicate_context(context))
    report_unchecked(context, f"item modifier {modifier_id}", result.skipped)
    return stack


def cmd_replaceitem(command: Command, context: ExecutionContext) -> CommandResult:
    """``replaceitem (entity <targets> | block <pos>) <slot> <item> [count]`` (before 1.17)."""
    arguments = command.arguments
    if len(arguments) >= 6 and arguments[0] == "block":
        rest = ["with", *arguments[5:]]
        return _item_block(context, ["replace", "block", *arguments[1:5], *rest])
    if len(arguments) < 4 or arguments[0] != "entity":
        return _usage(context)
    stack = _stack(context, arguments[3], allow_air=True)
    count = integer(context, arguments[4]) if stack is not None and len(arguments) > 4 else 1
    if stack is None or count is None or not _valid_count(context, stack, count):
        return CommandResult.failure()
    stack.count = count
    return _set_slots(context, arguments[1], arguments[2], stack)


# ---------------------------------------------------------------------------
# enchant
# ---------------------------------------------------------------------------


def cmd_enchant(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    enchantment = normalise_id(arguments[1])
    if not require_id(context, "enchantment", enchantment, "enchantment.unknown"):
        return CommandResult.failure()
    level = integer(context, arguments[2]) if len(arguments) > 2 else 1
    if level is None:
        return CommandResult.failure()
    targets = require_targets(context, arguments[0])
    context.note_once(
        "enchant does not check which items accept an enchantment or clashing enchantments"
    )
    version = context.emulator.version
    changed = []
    for entity in targets:
        keys = entity.inventory.keys_for("weapon.mainhand") or []
        stack = entity.inventory.get(keys[0]) if keys else None
        if stack is None:
            if len(targets) == 1:
                context.game_error("commands.enchant.failed.itemless", entity.display)
            continue
        if _has_enchantment(stack, enchantment, version):
            # an enchantment is incompatible with itself: vanilla refuses a second one
            if len(targets) == 1:
                context.game_error(
                    "commands.enchant.failed.incompatible", item_name(context, stack.id)
                )
            continue
        _add_enchantment(stack, enchantment, level, version)
        changed.append(entity)
    if not changed:
        if len(targets) != 1:
            context.game_error("commands.enchant.failed")
        return CommandResult.failure()
    if len(changed) == 1:
        context.feedback("commands.enchant.success.single", enchantment, changed[0].display)
    else:
        context.feedback("commands.enchant.success.multiple", enchantment, len(changed))
    return CommandResult(success=True, value=len(changed))


def _has_enchantment(stack: ItemStack, enchantment: str, version) -> bool:
    if not uses_components(version):
        return any(
            isinstance(entry, dict) and normalise_id(str(entry.get("id", ""))) == enchantment
            for entry in stack.tag.get("Enchantments", [])
        )
    current = stack.components.get("minecraft:enchantments")
    if not isinstance(current, dict):
        return False
    levels = current.get("levels", current) if not uses_equipment(version) else current
    return isinstance(levels, dict) and enchantment in {normalise_id(k) for k in levels}


def _add_enchantment(stack: ItemStack, enchantment: str, level: int, version) -> None:
    if not uses_components(version):
        entries = [e for e in stack.tag.get("Enchantments", []) if isinstance(e, dict)]
        entries = [e for e in entries if normalise_id(str(e.get("id", ""))) != enchantment]
        entries.append({"id": enchantment, "lvl": level})
        stack.tag["Enchantments"] = entries
        return
    current = stack.components.get("minecraft:enchantments")
    current = dict(current) if isinstance(current, dict) else {}
    if uses_equipment(version):  # 1.21.5: the levels map is the component itself
        levels = {k: v for k, v in current.items() if k != "levels"}
        levels[enchantment] = level
        stack.components["minecraft:enchantments"] = levels
    else:
        levels = dict(current.get("levels", {})) if isinstance(current.get("levels"), dict) else {}
        levels[enchantment] = level
        current["levels"] = levels
        stack.components["minecraft:enchantments"] = current


# ---------------------------------------------------------------------------
# loot
# ---------------------------------------------------------------------------


def _loot_table(context: ExecutionContext, table_id: str) -> dict[str, Any] | None:
    table_id = normalise_id(table_id)
    resource = context.emulator.pack.registries.get("loot_table", {}).get(table_id)
    content = getattr(resource, "content", None)
    if isinstance(content, dict):
        return content
    assets = context.emulator.vanilla
    return assets.loot_table(table_id) if assets is not None else None


def _loot_source(
    context: ExecutionContext, source: list[str], loot: LootContext
) -> tuple[str, dict] | None:
    """``loot <table>``, ``fish <table> <pos> [tool]``, ``kill <target>`` or
    ``mine <pos> [tool]`` -> (table id, table); None once an error was reported."""
    kind = source[0] if source else ""
    if kind in ("loot", "fish") and len(source) >= 2:
        table_id = normalise_id(source[1])
        if kind == "fish":
            context.note_once("loot … fish: the fishing context (luck, open water) is not modelled")
    elif kind == "kill" and len(source) >= 2:
        victims = require_targets(context, source[1])
        if not victims:
            return None
        if len(victims) > 1:
            context.game_error("argument.entity.toomany")
            return None
        entity_type = victims[0].type.split(":", 1)
        table_id = f"{entity_type[0]}:entities/{entity_type[-1]}"
        loot.entity = victims[0]
        loot.position = list(victims[0].position)
    elif kind == "mine" and len(source) >= 4:
        from datapack_emulator.emulator.commands.blocks import position_at

        position = position_at(context, source[1:4])
        if position is None:
            return None
        block = context.world.blocks.get(context.dimension, position)
        loot.block = block
        loot.block_position = position
        loot.position = [value + 0.5 for value in position]
        if len(source) > 4 and source[4] not in ("mainhand", "offhand"):
            loot.tool = parse_item(source[4])
        elif len(source) > 4 and context.executor is not None:
            keys = context.executor.inventory.keys_for(f"weapon.{source[4]}") or []
            loot.tool = context.executor.inventory.get(keys[0]) if keys else None
        namespace, _, path = block.id.partition(":")
        table_id = f"{namespace}:blocks/{path}"
    else:
        _usage(context)
        return None
    table = _loot_table(context, table_id)
    if table is None:
        if kind in ("kill", "mine"):  # nothing to drop is not an error
            return (table_id, {})
        context.game_error("argument.resource_or_id.no_such_element", table_id, "loot_table")
        return None
    return (table_id, table)


#: how many tokens each loot target takes after its own name
_TARGET_WIDTH = {"give": 1, "spawn": 3, "insert": 3}


def cmd_loot(command: Command, context: ExecutionContext) -> CommandResult:
    """``loot <target> <source>``: give, spawn, insert, replace entity|block;
    loot, fish, kill and mine sources."""
    arguments = command.arguments
    if len(arguments) < 3:
        return _usage(context)
    target = arguments[0]
    width = _TARGET_WIDTH.get(target)
    if target == "replace":
        # replace entity <targets> <slot> [<count>] | replace block <pos> <slot> [<count>]
        head = 4 if len(arguments) > 1 and arguments[1] == "block" else 2
        count_at = 1 + head + 1
        width = head + 1 + (1 if len(arguments) > count_at and _is_int(arguments[count_at]) else 0)
    if width is None or len(arguments) <= width + 1:
        return _usage(context)
    from datapack_emulator.emulator.commands.conditions import predicate_context

    loot = predicate_context(context)
    found = _loot_source(context, arguments[1 + width :], loot)
    if found is None:
        return CommandResult.failure()
    _, table = found
    result = evaluate(
        table, lambda tid: _loot_table(context, tid), context.world.random, context=loot
    )
    if result.skipped:
        context.note_once(
            "loot table parts the emulator cannot evaluate (such conditions fail, such "
            "functions change nothing): " + ", ".join(sorted(result.skipped))
        )
    items = split_stacks(result.items)
    if target == "give":
        players = _players(context, arguments[1])
        if players is None:
            return CommandResult.failure()
        for player in players:
            for stack in items:
                player.inventory.add(stack.copy())  # what does not fit is lost, as in vanilla
    elif target == "spawn":
        position = resolve_position(arguments[1:4], context.position)
        for stack in items:
            drop(context, position, stack)
    elif target == "insert":
        container = _container(context, arguments[1:4])
        if container is None:
            return CommandResult.failure()
        # what does not fit is lost; only stacks that went in are counted
        items = [stack for stack in items if container[1].insert(stack)]
    elif arguments[1] == "block":
        container = _container(context, arguments[2:5])
        if container is None:
            return CommandResult.failure()
        block = container[1]
        first = block.slot_keys(arguments[5])
        if first is None or len(first) != 1:
            if slot_number(arguments[5]) is None:
                context.game_error("slot.unknown", arguments[5])
            else:
                context.game_error("commands.item.target.no_such_slot", slot_number(arguments[5]))
            return CommandResult.failure()
        count = int(arguments[6]) if width == 6 else len(items)
        for offset in range(count):
            slot = first[0] + offset
            if block.slot_keys(f"container.{slot}") is not None:
                block.set_item(slot, items[offset].copy() if offset < len(items) else None)
    else:
        first = slot_number(arguments[3])
        if first is None:
            context.game_error("slot.unknown", arguments[3])
            return CommandResult.failure()
        count = int(arguments[4]) if width == 4 else len(items)
        for entity in require_targets(context, arguments[2]):
            for offset in range(count):
                name = SLOT_NAMES.get(first + offset)
                keys = entity.inventory.keys_for(name) if name else None
                if keys and len(keys) == 1:
                    stack = items[offset].copy() if offset < len(items) else None
                    entity.inventory.set(keys[0], stack)
    # vanilla reports and returns the number of stacks
    if len(items) == 1:
        stack = items[0]
        context.feedback("commands.drop.success.single", stack.count, item_name(context, stack.id))
    else:
        context.feedback("commands.drop.success.multiple", len(items))
    return CommandResult(success=bool(items), value=len(items))


def _is_int(token: str) -> bool:
    return token.lstrip("-").isdecimal()


def split_stacks(stacks: list[ItemStack]) -> list[ItemStack]:
    """Stacks bigger than the item's limit become several full stacks."""
    out = []
    for stack in stacks:
        remaining = stack.count
        while remaining > 0:
            size = min(remaining, stack.max_count)
            out.append(stack.copy(size))
            remaining -= size
    return out


# ---------------------------------------------------------------------------
# execute if items
# ---------------------------------------------------------------------------


def items_condition_count(arguments: list[str], context: ExecutionContext) -> int | None:
    """``items (entity <targets> | block <pos>) <slots> <predicate>``: how many
    matching items; None when the condition cannot be evaluated."""
    if len(arguments) >= 2 and arguments[1] == "block":
        if len(arguments) < 7:
            return 0
        from datapack_emulator.emulator.commands.blocks import position_at

        position = position_at(context, arguments[2:5])
        if position is None:
            return None
        block = context.world.blocks.stored(context.dimension, position)
        if block is None or not block.is_container:
            context.game_error("commands.item.source.not_a_container", *position)
            return None
        slots = block.slot_keys(arguments[5])
        if not slots:
            return 0
        predicate = _predicate(context, arguments[6])
        return sum(
            stack.count
            for stack in (block.get_item(slot) for slot in slots)
            if stack is not None and predicate.matches(stack)
        )
    if len(arguments) < 5:
        return 0
    predicate = _predicate(context, arguments[4])
    total = 0
    for entity in find_targets(context, arguments[2]):
        for key in entity.inventory.keys_for(arguments[3]) or []:
            stack = entity.inventory.get(key)
            if stack is not None and predicate.matches(stack):
                total += stack.count
    return total


def drop_inventory_on_death(context: ExecutionContext, player: Entity) -> None:
    """A killed player drops what they carry unless keepInventory is on."""
    rules = context.world.gamerules
    names = ("keepInventory", "keep_inventory", "minecraft:keep_inventory")  # renamed in 1.21.11
    if any(str(rules.get(name, "false")).lower() == "true" for name in names):
        return
    for stack in player.inventory.clear_all():
        drop(context, player.position, stack)


ITEM_HANDLERS = {
    "give": cmd_give,
    "clear": cmd_clear,
    "item": cmd_item,
    "replaceitem": cmd_replaceitem,
    "enchant": cmd_enchant,
    "loot": cmd_loot,
}

__all__ = ["ITEM_HANDLERS", "drop", "drop_inventory_on_death", "items_condition_count"]
