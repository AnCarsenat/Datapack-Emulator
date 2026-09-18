"""Control flow: function (and macros), schedule, return."""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.commands.blocks import position_at
from datapack_emulator.emulator.commands.helpers import find_targets
from datapack_emulator.emulator.commands.parser import Command, parse_duration
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import nbt_get, normalise_id, normalise_tagged_id, parse_snbt
from datapack_emulator.emulator.runtime.context import ExecutionContext


def cmd_function(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments:
        return CommandResult.failure()
    target = normalise_tagged_id(command.arguments[0])
    macro_arguments = _macro_arguments(command.arguments[1:], context)
    if target.startswith("#"):
        function_ids = context.emulator.library.resolve_tag(target)
        if not function_ids:
            context.game_error("commands.function.scheduled.no_functions", target)
            return CommandResult.failure()
        total = 0
        for function_id in function_ids:
            total += context.emulator.run_function(function_id, context, macro_arguments).value
        return CommandResult(success=total > 0, value=total)
    return context.emulator.run_function(target, context, macro_arguments)


def _macro_arguments(rest: list[str], context: ExecutionContext) -> dict[str, Any]:
    """``function ns:f {a:1}`` / ``... with storage ns:mem path`` -> a dict."""
    if not rest:
        return {}
    if rest[0].startswith("{"):
        return parse_snbt(" ".join(rest))
    if rest[0] != "with" or len(rest) < 3:
        return {}
    source, target = rest[1], rest[2]
    width = 3 if source == "block" else 1
    path = rest[2 + width] if len(rest) > 2 + width else ""
    if source == "storage":
        store: Any = context.world.storage.get(normalise_id(target), {})
    elif source == "entity":
        entities = find_targets(context, target)
        store = entities[0].data(context.emulator.version) if entities else {}
    elif source == "block":
        position = position_at(context, rest[2:5])
        if position is None:
            return {}
        block = context.world.blocks.stored(context.dimension, position)
        if block is None or not block.has_entity:
            context.game_error("commands.data.block.invalid")
            return {}
        store = block.data(context.emulator.version, position)
    else:
        context.game_error("command.unknown.argument")
        return {}
    if path:
        store = nbt_get(store, path)
    if store is None:
        return {}
    if not isinstance(store, dict):
        context.game_error("commands.function.error.argument_not_compound", type(store).__name__)
        return {}
    return store


def cmd_schedule(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) >= 2 and arguments[0] == "clear":
        target = normalise_tagged_id(arguments[1])
        removed = context.emulator.clear_schedule(target)
        if removed:
            context.feedback("commands.schedule.cleared.success", removed, target)
            return CommandResult(success=True, value=removed)
        context.game_error("commands.schedule.cleared.failure", target)
        return CommandResult.failure()
    if len(arguments) >= 3 and arguments[0] == "function":
        target = normalise_tagged_id(arguments[1])  # a function or a #tag
        delay = parse_duration(arguments[2])
        if delay <= 0:
            context.game_error("commands.schedule.same_tick")
            return CommandResult.failure()
        mode = arguments[3] if len(arguments) > 3 else "replace"
        when = context.world.tick + delay
        context.emulator.add_schedule(target, when, replace=mode == "replace")
        context.feedback("commands.schedule.created.function", target, delay, when)
        return CommandResult(success=True, value=1)
    return CommandResult.failure()


def cmd_return(command: Command, context: ExecutionContext) -> CommandResult:
    if command.arguments and command.arguments[0] == "run":
        inner = Command.parse(" ".join(command.arguments[1:]), context.function_id, command.line)
        result = (
            context.emulator.run_command(inner, context, nested=True)
            if inner
            else CommandResult.failure()
        )
        return CommandResult(success=result.success, value=result.value, returned=True)
    if command.arguments and command.arguments[0] == "fail":
        return CommandResult(success=False, value=0, returned=True)
    value = int(command.arguments[0]) if command.arguments else 0
    return CommandResult(success=True, value=value, returned=True)
