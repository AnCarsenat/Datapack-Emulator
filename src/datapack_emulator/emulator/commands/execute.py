"""``execute``: the context chain, conditions and stores."""

from __future__ import annotations

import math

from datapack_emulator.emulator.commands.blocks import (
    block_condition,
    blocks_condition_count,
    position_at,
)
from datapack_emulator.emulator.commands.helpers import find_holders, find_targets
from datapack_emulator.emulator.commands.items import items_condition_count
from datapack_emulator.emulator.commands.parser import (
    Command,
    Selector,
    Subcommand,
    resolve_position,
)
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import nbt_get, nbt_set, normalise_id, normalise_tagged_id
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity, ints_to_uuid

#: a store waiting for the command's result, bound to the context it appeared in
PendingStore = tuple[Subcommand, ExecutionContext]


def cmd_execute(command: Command, context: ExecutionContext) -> CommandResult:
    # each branch carries the stores seen so far, each bound to the context at
    # its position in the chain (vanilla records the location to store in there)
    branches: list[tuple[ExecutionContext, tuple[PendingStore, ...]]] = [(context, ())]
    subcommands = command.subcommands
    trailing_test = (
        command.child is None
        and bool(subcommands)
        and subcommands[-1].name
        in (
            "if",
            "unless",
        )
    )
    chain = subcommands[:-1] if trailing_test else subcommands

    for subcommand in chain:
        next_branches: list[tuple[ExecutionContext, tuple[PendingStore, ...]]] = []
        for current, stores in branches:
            if subcommand.name == "store" and not _store_target_ok(current, subcommand):
                continue  # vanilla fails before running anything
            for successor in _execute_step(subcommand.name, subcommand.arguments, current, context):
                pending = (
                    stores + ((subcommand, current),) if subcommand.name == "store" else stores
                )
                next_branches.append((successor, pending))
        branches = next_branches
        if not branches:
            break

    if command.child is None:
        # `execute ... if|unless <cond>` with no `run` is a test; its result is
        # the condition's count (how many entities matched for `if entity`)
        last = subcommands[-1] if trailing_test else None
        total = 0
        for current, stores in branches:
            if last is None:
                count = 1
            elif last.name == "if":
                count = condition_count(last.arguments, current)
            else:
                count = int(not evaluate_condition(last.arguments, current))
            total += count
            result = CommandResult(success=count > 0, value=count)
            for subcommand, bound in stores:
                _apply_store(subcommand, bound, result)
        if total:
            context.feedback("commands.execute.conditional.pass_count", total)
        else:
            context.feedback("commands.execute.conditional.fail")
        return CommandResult(success=total > 0, value=total)

    successes = 0
    total = 0
    for current, stores in branches:
        result = context.emulator.run_command(command.child, current, nested=True)
        if result.success:
            successes += 1
        total += result.value
        for subcommand, bound in stores:
            _apply_store(subcommand, bound, result)
        if result.returned:
            # `execute ... run return` leaves the function on the first context
            # that reaches it; the remaining contexts never run.
            return CommandResult(success=result.success, value=result.value, returned=True)
    return CommandResult(success=successes > 0, value=total if total else successes)


def _execute_step(
    name: str, arguments: list[str], current: ExecutionContext, origin: ExecutionContext
) -> list[ExecutionContext]:
    """The contexts one ``execute`` subcommand turns ``current`` into."""
    world = origin.world
    if name == "as" and arguments:
        return [
            current.branch(executor=entity)
            for entity in world.select(Selector.parse(arguments[0]), current)
        ]
    if name == "at" and arguments:
        return [
            current.branch(
                position=list(entity.position),
                rotation=list(entity.rotation),
                dimension=entity.dimension,
            )
            for entity in world.select(Selector.parse(arguments[0]), current)
        ]
    if name == "positioned":
        if arguments and arguments[0] == "as" and len(arguments) > 1:
            return [
                current.branch(position=list(entity.position))
                for entity in world.select(Selector.parse(arguments[1]), current)
            ]
        return [current.branch(position=resolve_position(arguments, current.position))]
    if name == "rotated":
        if arguments and arguments[0] == "as" and len(arguments) > 1:
            return [
                current.branch(rotation=list(entity.rotation))
                for entity in world.select(Selector.parse(arguments[1]), current)
            ]
        return [current]
    if name == "align" and arguments:
        position = list(current.position)
        for axis, letter in enumerate("xyz"):
            if letter in arguments[0]:
                position[axis] = float(math.floor(position[axis]))
        return [current.branch(position=position)]
    if name == "in" and arguments:
        return [current.branch(dimension=normalise_id(arguments[0]))]
    if name in ("if", "unless"):
        return [current] if evaluate_condition(arguments, current) == (name == "if") else []
    if name == "summon" and arguments:
        entity = world.spawn(
            Entity(
                type=normalise_id(arguments[0]),
                uuid=world.new_uuid(),
                position=list(current.position),
            )
        )
        return [current.branch(executor=entity)]
    if name == "on" and arguments:
        executor = current.executor
        if executor is None:
            return []
        return [
            current.branch(executor=entity) for entity in related(current, executor, arguments[0])
        ]
    # store (bound by the caller), anchored, facing: nothing to change
    return [current]


def related(context: ExecutionContext, entity: Entity, relation: str) -> list[Entity]:
    """``execute on``: the entities ``relation`` leads to (none when it does not apply)."""
    from datapack_emulator.emulator.runtime.living import OWNABLE

    world = context.world
    if relation == "vehicle":
        return [entity.vehicle] if entity.vehicle is not None else []
    if relation == "passengers":
        return list(entity.passengers)
    if relation == "controller":
        # only a mob with AI is steered by a mob riding it (players steering
        # saddled animals is not modelled)
        rider = entity.passengers[0] if entity.passengers else None
        if (
            rider is None
            or entity.is_player
            or entity.living is None
            or entity.type == "minecraft:armor_stand"
            or entity.nbt.get("NoAI")
            or rider.is_player
            or rider.living is None
            or rider.type == "minecraft:armor_stand"
        ):
            return []
        return [rider]
    keys: tuple[str, ...]
    if relation == "owner":
        keys = ("Owner",) if entity.type in OWNABLE else ()
    elif relation == "origin":
        keys = ("Thrower",) if entity.type == "minecraft:item" else ("Owner", "owner")
    elif relation == "leasher":
        keys = ("leash", "Leash")
    else:
        # attacker and target need combat and AI
        context.note_once(f"'execute on {relation}': not modelled, so the branch ends")
        return []
    for key in keys:
        value = entity.nbt.get(key)
        if isinstance(value, dict):
            value = value.get("UUID")
        uuid = ints_to_uuid(value) if isinstance(value, list) else None
        if uuid is None and isinstance(value, str):
            uuid = value
        if uuid is not None:
            found = world.entity_by_id(uuid)
            return [found] if found is not None else []
    return []


def _apply_store(subcommand: Subcommand, context: ExecutionContext, result: CommandResult) -> None:
    arguments = subcommand.arguments
    if len(arguments) < 4:
        return
    mode, target = arguments[0], arguments[1]
    value = result.value if mode == "result" else int(result.success)
    if target == "score":
        for holder in find_holders(context, arguments[2]):
            context.world.scoreboard.set(holder, arguments[3], value)
    elif target == "storage":
        store = context.world.storage.setdefault(normalise_id(arguments[2]), {})
        nbt_set(store, arguments[3], _stored_number(value, arguments[4:6]))
    elif target == "block" and len(arguments) >= 6:
        position, block = _store_block(context, arguments)
        if block is not None:
            data = block.data(context.emulator.version, position)
            nbt_set(data, arguments[5], _stored_number(value, arguments[6:8]))
            block.apply_data(data)
    elif target == "bossbar":
        from datapack_emulator.emulator.commands.bossbar import store_bossbar

        store_bossbar(context, arguments[2], arguments[3], value)
    elif target == "entity":
        for entity in find_targets(context, arguments[2]):
            if entity.is_player:  # player data cannot be modified
                continue
            data = entity.data(context.emulator.version)
            nbt_set(data, arguments[3], _stored_number(value, arguments[4:6]))
            entity.apply_data(data)


def _store_target_ok(context: ExecutionContext, subcommand: Subcommand) -> bool:
    kind = subcommand.arguments[1:2]
    if kind == ["block"]:
        return _store_block(context, subcommand.arguments)[1] is not None
    if kind == ["bossbar"] and len(subcommand.arguments) > 2:
        from datapack_emulator.emulator.commands.bossbar import bossbars

        bar_id = normalise_id(subcommand.arguments[2])
        if bar_id not in bossbars(context):
            context.game_error("commands.bossbar.unknown", bar_id)
            return False
    return True


def _store_block(context: ExecutionContext, arguments: list[str]):
    """``store … block <pos>``: the block entity, or ``(None, None)`` after an error."""
    position = position_at(context, arguments[2:5])
    if position is None:
        return (None, None)
    block = context.world.blocks.stored(context.dimension, position)
    if block is None or not block.has_entity:
        context.game_error("commands.data.block.invalid")
        return (None, None)
    return (position, block)


def _stored_number(value: int, type_and_scale: list[str]) -> int | float:
    """``store ... <path> <type> <scale>``: integer types truncate, like vanilla."""
    numeric_type = type_and_scale[0] if type_and_scale else "int"
    try:
        scale = float(type_and_scale[1]) if len(type_and_scale) > 1 else 1.0
    except ValueError:
        scale = 1.0
    scaled = value * scale
    if numeric_type in ("float", "double"):
        return scaled
    return int(scaled)  # byte, short, int, long


def condition_count(arguments: list[str], context: ExecutionContext) -> int:
    """The value a passing ``if`` condition reports: matched entities for
    ``entity``, otherwise 1 (0 when it fails)."""
    if arguments and arguments[0] == "entity" and len(arguments) >= 2:
        return len(context.world.select(Selector.parse(arguments[1]), context))
    if arguments and arguments[0] == "items":
        return items_condition_count(arguments, context) or 0
    if arguments and arguments[0] == "blocks":
        return blocks_condition_count(arguments, context) or 0
    return int(evaluate_condition(arguments, context))


def evaluate_condition(arguments: list[str], context: ExecutionContext) -> bool:
    if not arguments:
        return False
    kind = arguments[0]
    board = context.world.scoreboard

    if kind == "score" and len(arguments) >= 5:
        holders = find_holders(context, arguments[1])
        if not holders:
            return False
        value = board.get(holders[0], arguments[2])
        if value is None:
            return False
        if arguments[3] == "matches":
            from datapack_emulator.emulator.common import in_range

            return in_range(value, arguments[4])
        if len(arguments) >= 6:
            others = find_holders(context, arguments[4])
            if not others:
                return False
            other = board.get(others[0], arguments[5])
            if other is None:
                return False
            return {
                "<": value < other,
                "<=": value <= other,
                "=": value == other,
                ">=": value >= other,
                ">": value > other,
            }.get(arguments[3], False)
        return False

    if kind == "entity" and len(arguments) >= 2:
        return bool(context.world.select(Selector.parse(arguments[1]), context))

    if kind == "block":
        return block_condition(arguments, context)

    if kind == "predicate" and len(arguments) >= 2:
        from datapack_emulator.emulator.commands.conditions import predicate_condition

        return predicate_condition(arguments[1], context)

    if kind == "blocks":
        return bool(blocks_condition_count(arguments, context))

    if kind == "data" and len(arguments) >= 4:
        if arguments[1] == "block" and len(arguments) >= 6:
            position = position_at(context, arguments[2:5])
            if position is None:
                return False
            block = context.world.blocks.stored(context.dimension, position)
            if block is None or not block.has_entity:
                context.game_error("commands.data.block.invalid")
                return False
            data = block.data(context.emulator.version, position)
            return nbt_get(data, arguments[5]) is not None
        if arguments[1] == "storage":
            store = context.world.storage.get(normalise_id(arguments[2]), {})
            return nbt_get(store, arguments[3]) is not None
        if arguments[1] == "entity":
            return any(
                nbt_get(entity.data(context.emulator.version), arguments[3]) is not None
                for entity in find_targets(context, arguments[2])
            )
        return False

    if kind == "items":
        return bool(items_condition_count(arguments, context))

    if kind == "dimension" and len(arguments) >= 2:
        return context.dimension == normalise_id(arguments[1])

    if kind == "loaded":
        return True

    if kind == "function" and len(arguments) >= 2:
        target = normalise_tagged_id(arguments[1])
        ids = context.emulator.library.resolve_tag(target) if target.startswith("#") else [target]
        # vanilla: passes on the first function that returns a non-zero value;
        # a function that ends without `return` does not count
        for function_id in ids:
            result = context.emulator.run_function(function_id, context)
            if result.has_return and result.success and result.value != 0:
                return True
        return False

    context.note_key_once("emulator.condition", kind)
    return False
