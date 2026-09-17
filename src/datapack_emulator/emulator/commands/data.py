"""``data`` on entities and command storage, and ``gamerule``."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from datapack_emulator.emulator.commands.blocks import position_at
from datapack_emulator.emulator.commands.helpers import require_targets
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import (
    as_int,
    merge_compound,
    nbt_get,
    nbt_remove,
    nbt_set,
    normalise_id,
    parse_snbt,
    parse_value,
    to_snbt,
)
from datapack_emulator.emulator.runtime.blocks import Block, Position
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity


@dataclass
class _DataTarget:
    """What ``data`` reads and writes: a working copy of an entity's, a block's
    or a storage's NBT."""

    data: dict[str, Any]
    entity: Entity | None = None
    storage: str = ""
    block: Block | None = None
    position: Position | None = None

    def describe(self) -> tuple[Any, ...]:
        """The feedback arguments naming the target."""
        if self.entity is not None:
            return (self.entity.display,)
        if self.block is not None and self.position is not None:
            return self.position
        return (self.storage,)

    def commit(self, context: ExecutionContext) -> None:
        if self.entity is not None:
            self.entity.apply_data(self.data)
        elif self.block is not None:
            self.block.apply_data(self.data)
        else:
            context.world.storage[self.storage] = self.data

    @property
    def feedback_kind(self) -> str:
        if self.entity is not None:
            return "entity"
        return "block" if self.block is not None else "storage"


def _data_target(
    context: ExecutionContext, kind: str, tokens: list[str], action: str
) -> _DataTarget | None:
    """The target named by ``tokens`` (one id or selector, or three coordinates)."""
    token = tokens[0]
    if kind == "storage":
        storage = normalise_id(token)
        return _DataTarget(copy.deepcopy(context.world.storage.get(storage, {})), storage=storage)
    if kind == "entity":
        found = require_targets(context, token)
        if not found:
            return None
        if len(found) > 1:
            context.game_error("argument.entity.toomany")
            return None
        entity = found[0]
        if action != "get" and entity.is_player:
            context.game_error("commands.data.entity.invalid")
            return None
        return _DataTarget(entity.data(context.emulator.version), entity=entity)
    if kind == "block":
        return _block_target(context, tokens)
    context.game_error("command.unknown.argument")
    return None


def _block_target(context: ExecutionContext, tokens: list[str]) -> _DataTarget | None:
    """``<x> <y> <z>``: the block entity there."""
    position = position_at(context, tokens[:3])
    if position is None:
        return None
    block = context.world.blocks.stored(context.dimension, position)
    if block is None or not block.has_entity:
        context.game_error("commands.data.block.invalid")
        return None
    return _DataTarget(
        block.data(context.emulator.version, position), block=block, position=position
    )


def cmd_data(command: Command, context: ExecutionContext) -> CommandResult:
    """``data get|merge|modify|remove`` on entities, blocks and storage."""
    arguments = command.arguments
    width = 3 if len(arguments) > 1 and arguments[1] == "block" else 1
    if len(arguments) < 2 + width:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    action, kind = arguments[0], arguments[1]
    target = _data_target(context, kind, arguments[2 : 2 + width], action)
    if target is None:
        return CommandResult.failure()
    # the rest, lined up as if the target were one token
    arguments = [action, kind, "", *arguments[2 + width :]]
    where = target.feedback_kind

    if action == "get":
        path = arguments[3] if len(arguments) > 3 else ""
        if not path:
            context.feedback(
                f"commands.data.{where}.query", *target.describe(), to_snbt(target.data)
            )
            return CommandResult(success=True, value=1)
        value = nbt_get(target.data, path)
        if value is None:
            context.game_error("commands.data.get.unknown", path)
            return CommandResult.failure()
        scale = _float_or_none(arguments[4]) if len(arguments) > 4 else None
        if scale is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                context.game_error("commands.data.get.invalid", path)
                return CommandResult.failure()
            result = as_int(value, scale)
            context.feedback(
                f"commands.data.{where}.get", path, *target.describe(), _number(scale), result
            )
            return CommandResult(success=True, value=result)
        context.feedback(f"commands.data.{where}.query", *target.describe(), to_snbt(value))
        return CommandResult(success=True, value=as_int(value))

    if action == "merge" and len(arguments) >= 4:
        changed = merge_compound(target.data, parse_snbt(" ".join(arguments[3:])))
    elif action == "remove" and len(arguments) >= 4:
        changed = nbt_remove(target.data, arguments[3])
    elif action == "modify" and len(arguments) >= 6:
        modified = _data_modify(context, [target.data], arguments[3], arguments[4], arguments[5:])
        if modified is None:
            return CommandResult.failure()
        changed = modified > 0
    else:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    if not changed:
        context.game_error("commands.data.merge.failed")
        return CommandResult.failure()
    target.commit(context)
    context.feedback(f"commands.data.{where}.modified", *target.describe())
    return CommandResult(success=True, value=1)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _data_modify(
    context: ExecutionContext,
    stores: list[dict[str, Any]],
    path: str,
    operation: str,
    source: list[str],
) -> int | None:
    """``data modify <target> <path> <operation> (value|from|string) ...``

    Returns how many elements changed, or None once an error was reported.
    """
    index = 0
    if operation == "insert" and source:
        index = int(source[0]) if source[0].lstrip("-").isdigit() else 0
        source = source[1:]
    value: Any = None
    if source and source[0] == "value":
        value = parse_value(" ".join(source[1:]))
    elif source and source[0] in ("from", "string"):
        found, value = _data_source(context, source[1:])
        if not found:
            return None
        if source[0] == "string":  # 1.19.4+: set string <source> [path] [start] [end]
            if isinstance(value, (dict, list)):
                context.game_error("commands.data.modify.expected_value", value)
                return None
            width = 3 if len(source) > 1 and source[1] == "block" else 1
            bounds = source[3 + width : 5 + width] if len(source) > 2 + width else []
            value = _substring(value, bounds)
    else:
        return None

    changed = 0
    for store in stores:
        current = nbt_get(store, path)
        if operation == "set":
            if current == value:
                continue  # vanilla counts only elements that actually change
            if not nbt_set(store, path, copy.deepcopy(value)):
                context.game_error("arguments.nbtpath.nothing_found", path)
                return None
            changed += 1
        elif operation == "merge":
            if not isinstance(value, dict):
                context.game_error("commands.data.modify.expected_object", value)
                return None
            if current is None:
                if not nbt_set(store, path, copy.deepcopy(value)):
                    context.game_error("arguments.nbtpath.nothing_found", path)
                    return None
                changed += 1
            elif not isinstance(current, dict):
                context.game_error("commands.data.modify.expected_object", current)
                return None
            elif merge_compound(current, value):
                changed += 1
        elif operation in ("append", "prepend", "insert"):
            if current is None:
                current = []
                if not nbt_set(store, path, current):
                    context.game_error("arguments.nbtpath.nothing_found", path)
                    return None
            elif not isinstance(current, list):
                context.game_error("commands.data.modify.expected_list", current)
                return None
            position = {"append": len(current), "prepend": 0}.get(operation, index)
            current.insert(position, copy.deepcopy(value))
            changed += 1
    return changed


def _float_or_none(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _substring(value: Any, bounds: list[str]) -> str:
    """``set string``: the source as text, optionally sliced like vanilla
    (negative indices count from the end)."""
    text = value if isinstance(value, str) else str(value)
    start = int(bounds[0]) if bounds else 0
    end = int(bounds[1]) if len(bounds) > 1 else len(text)
    return text[start:end]


def _data_source(context: ExecutionContext, source: list[str]) -> tuple[bool, Any]:
    """Read ``(storage <id> | entity <selector> | block <pos>) [<path>]`` for
    ``modify ... from``."""
    if len(source) < 2:
        return (False, None)
    kind, target = source[0], source[1]
    width = 3 if kind == "block" else 1
    path = source[1 + width] if len(source) > 1 + width else ""
    if kind == "storage":
        store: Any = context.world.storage.get(normalise_id(target), {})
    elif kind == "entity":
        entities = require_targets(context, target)
        if not entities:
            return (False, None)
        store = entities[0].data(context.emulator.version)
    elif kind == "block":
        found = _block_target(context, source[1:4])
        if found is None:
            return (False, None)
        store = found.data
    else:
        context.game_error("command.unknown.argument")
        return (False, None)
    value = nbt_get(store, path) if path else store
    if value is None:
        context.game_error("commands.data.get.unknown", path)
        return (False, None)
    return (True, value)


def cmd_gamerule(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) >= 2:
        context.world.gamerules[command.arguments[0]] = command.arguments[1]
        return CommandResult(success=True, value=1)
    if command.arguments:
        value = context.world.gamerules.get(command.arguments[0], "")
        return CommandResult(success=bool(value), value=as_int(value))
    return CommandResult.failure()
