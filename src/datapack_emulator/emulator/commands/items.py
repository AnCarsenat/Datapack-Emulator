"""Commands on items and inventories: give, clear, item, replaceitem, enchant,
loot, and the ``items`` condition of execute.

Blocks are not modelled, so container blocks (``item … block``, ``loot insert``,
``execute if items block``) are noted and fail. See
:mod:`datapack_emulator.emulator.runtime.inventory` for the model.
"""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.commands.handlers import (
    _integer,
    _require_id,
    _require_targets,
    _targets,
)
from datapack_emulator.emulator.commands.parser import Command, resolve_position
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.inventory import (
    Inventory,
    ItemPredicate,
    ItemStack,
    parse_item,
    parse_item_predicate,
    uses_components,
    uses_equipment,
)
from datapack_emulator.emulator.runtime.loot import evaluate
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
    found = _require_targets(context, token)
    if not found:
        return None
    if any(not entity.is_player for entity in found):
        context.game_error("argument.player.entities")
        return None
    return found


def _stack(context: ExecutionContext, token: str) -> ItemStack | None:
    stack = parse_item(token)
    if stack is None:
        context.game_error("argument.item.id.invalid", token)
        return None
    if not _require_id(context, "item", stack.id, "argument.item.id.invalid"):
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


def _blocks_not_modelled(context: ExecutionContext, what: str) -> CommandResult:
    context.note_key_once("emulator.unimplemented", what, context.emulator.version.id)
    return CommandResult.failure()


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
    count = _integer(context, arguments[2]) if stack is not None and len(arguments) > 2 else 1
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
    max_count = _integer(context, arguments[2]) if len(arguments) > 2 else -1
    if max_count is None:
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
    """``entity <source> <slot>`` -> (found, the stack or None for an empty slot)."""
    if len(arguments) < 3:
        _usage(context)
        return (False, None)
    if arguments[0] == "block":
        _blocks_not_modelled(context, "item … from block")
        return (False, None)
    sources = _require_targets(context, arguments[1])
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
    targets = _require_targets(context, token)
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
        return _blocks_not_modelled(context, f"item {action} block")
    token, slot, rest = arguments[2], arguments[3], arguments[4:]
    if action == "replace" and rest and rest[0] == "with" and len(rest) > 1:
        stack = _stack(context, rest[1])
        count = _integer(context, rest[2]) if stack is not None and len(rest) > 2 else 1
        if stack is None or count is None:
            return CommandResult.failure()
        stack.count = count
        return _set_slots(context, token, slot, stack)
    if action == "replace" and rest and rest[0] == "from":
        found, stack = _source_stack(context, rest[1:])
        if not found:
            return CommandResult.failure()
        if len(rest) > 4 and stack is not None:
            stack = _modify(context, stack, rest[4])
        return _set_slots(context, token, slot, stack)
    if action == "modify" and rest:
        targets = _require_targets(context, token)
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


def _modify(context: ExecutionContext, stack: ItemStack, modifier_id: str) -> ItemStack:
    """Apply an item modifier from the pack (set_count, set_components, set_nbt)."""
    from datapack_emulator.emulator.runtime.loot import LootResult, _apply

    modifiers = context.emulator.pack.registries.get("item_modifier", {})
    resource = modifiers.get(normalise_id(modifier_id))
    content: Any = getattr(resource, "content", None)
    if content is None:
        context.game_error("argument.resource_or_id.no_such_element", modifier_id, "item_modifier")
        return stack
    result = LootResult()
    for function in content if isinstance(content, list) else [content]:
        stack = _apply(function, stack, context.world.random, result)
    if result.skipped:
        context.note_once(
            "item modifier parts not evaluated by the emulator: "
            + ", ".join(sorted(result.skipped))
        )
    return stack


def cmd_replaceitem(command: Command, context: ExecutionContext) -> CommandResult:
    """``replaceitem entity <targets> <slot> <item> [count]`` (before 1.17)."""
    arguments = command.arguments
    if len(arguments) >= 1 and arguments[0] == "block":
        return _blocks_not_modelled(context, "replaceitem block")
    if len(arguments) < 4 or arguments[0] != "entity":
        return _usage(context)
    stack = _stack(context, arguments[3])
    count = _integer(context, arguments[4]) if stack is not None and len(arguments) > 4 else 1
    if stack is None or count is None:
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
    if not _require_id(context, "enchantment", enchantment, "enchantment.unknown"):
        return CommandResult.failure()
    level = _integer(context, arguments[2]) if len(arguments) > 2 else 1
    if level is None:
        return CommandResult.failure()
    targets = _require_targets(context, arguments[0])
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


def cmd_loot(command: Command, context: ExecutionContext) -> CommandResult:
    """``loot (give|spawn|replace entity) … loot <table>``; other sources are noted."""
    arguments = command.arguments
    if len(arguments) < 3:
        return _usage(context)
    target = arguments[0]
    if target == "insert" or (
        target == "replace" and len(arguments) > 1 and arguments[1] == "block"
    ):
        return _blocks_not_modelled(context, f"loot {target} block")
    consumed = {"give": 2, "spawn": 4}.get(target)
    if target == "replace":
        # replace entity <targets> <slot> [<count>] <source>
        consumed = 5 if len(arguments) > 5 and arguments[4].lstrip("-").isdigit() else 4
    if consumed is None or len(arguments) <= consumed:
        return _usage(context)
    source = arguments[consumed:]
    if source[0] != "loot" or len(source) < 2:
        context.note_once(
            f"loot … {source[0]}: only 'loot <table>' sources are emulated (fish, kill and "
            "mine need blocks, tools or the entity's own table)"
        )
        return CommandResult.failure()
    table = _loot_table(context, source[1])
    if table is None:
        context.game_error("argument.resource_or_id.no_such_element", source[1], "loot_table")
        return CommandResult.failure()
    result = evaluate(table, lambda tid: _loot_table(context, tid), context.world.random)
    if result.skipped:
        context.note_once(
            "loot table parts not evaluated by the emulator (conditions pass, functions are "
            "skipped): " + ", ".join(sorted(result.skipped))
        )
    items = result.items
    if target == "give":
        players = _players(context, arguments[1])
        if players is None:
            return CommandResult.failure()
        for player in players:
            for stack in items:
                leftover = player.inventory.add(stack.copy())
                if leftover:
                    drop(context, player.position, stack.copy(leftover))
    elif target == "spawn":
        position = resolve_position(arguments[1:4], context.position)
        for stack in items:
            drop(context, position, stack)
    else:
        targets = _require_targets(context, arguments[2])
        slot = arguments[3]
        limit = int(arguments[4]) if consumed == 5 else len(items)
        name, _, first = slot.rpartition(".")
        for entity in targets:
            for offset in range(limit):
                key_slot = f"{name}.{int(first) + offset}" if first.isdigit() else slot
                keys = entity.inventory.keys_for(key_slot)
                if keys and len(keys) == 1:
                    entity.inventory.set(
                        keys[0], items[offset].copy() if offset < len(items) else None
                    )
    total = sum(stack.count for stack in items)
    if len(items) == 1:
        stack = items[0]
        context.feedback("commands.drop.success.single", stack.count, item_name(context, stack.id))
    else:
        context.feedback("commands.drop.success.multiple", total)
    return CommandResult(success=bool(items), value=total)


# ---------------------------------------------------------------------------
# execute if items
# ---------------------------------------------------------------------------


def items_condition_count(arguments: list[str], context: ExecutionContext) -> int | None:
    """``items entity <targets> <slots> <predicate>``: matching items; None when
    the condition cannot be evaluated (blocks)."""
    if len(arguments) >= 2 and arguments[1] == "block":
        context.note_key_once("emulator.condition", "items block")
        return None
    if len(arguments) < 5:
        return 0
    predicate = _predicate(context, arguments[4])
    total = 0
    for entity in _targets(context, arguments[2]):
        for key in entity.inventory.keys_for(arguments[3]) or []:
            stack = entity.inventory.get(key)
            if stack is not None and predicate.matches(stack):
                total += stack.count
    return total


def drop_inventory_on_death(context: ExecutionContext, player: Entity) -> None:
    """A killed player drops what they carry unless keepInventory is on."""
    if context.world.gamerules.get("keepInventory", "false").lower() == "true":
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
