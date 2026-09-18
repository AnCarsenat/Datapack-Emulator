"""Chat commands: say, me, msg, tellraw, title, and text components."""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.commands.helpers import find_holders, find_targets, require_targets
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import (
    flatten_text_component,
    load_text_component,
    nbt_get,
    normalise_id,
    to_snbt,
)
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity


def cmd_say(command: Command, context: ExecutionContext) -> CommandResult:
    who = context.executor.display if context.executor else "Server"
    context.chat(f"[{who}] {' '.join(command.arguments)}", recipient="*")
    return CommandResult(success=True, value=1)


def cmd_me(command: Command, context: ExecutionContext) -> CommandResult:
    who = context.executor.display if context.executor else "Server"
    context.chat(f"* {who} {' '.join(command.arguments)}", recipient="*")
    return CommandResult(success=True, value=1)


def cmd_msg(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    targets = require_targets(context, command.arguments[0])
    text = " ".join(command.arguments[1:])
    who = context.executor.display if context.executor else "Server"
    for entity in targets:
        context.chat(f"{who} whispers to {entity.display}: {text}", recipient=entity.display)
    return CommandResult(success=bool(targets), value=len(targets))


def cmd_tellraw(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    component = load_text_component(" ".join(command.arguments[1:]))
    if component is None:
        context.game_error("command.unknown.argument")
        return CommandResult.failure()
    targets = require_targets(context, command.arguments[0])
    for entity in targets:
        text = flatten_text_component(component, _text_resolver(context, entity))
        # one record per player, saying who reads it
        context.chat(f"to {entity.display}: {text}", recipient=entity.display)
    return CommandResult(success=bool(targets), value=len(targets))


def _text_resolver(context: ExecutionContext, viewer: Entity | None):
    """Scores and selectors inside a text component, as ``viewer`` would see them."""

    def resolve(kind: str, value: Any) -> str:
        if kind == "score" and isinstance(value, dict):
            name = str(value.get("name", ""))
            if name == "*":
                holders = [viewer.id] if viewer is not None else []
            else:
                holders = find_holders(context, name)
            if not holders:
                return ""
            score = context.world.scoreboard.get(holders[0], str(value.get("objective", "")))
            return "" if score is None else str(score)
        if kind == "selector":
            return ", ".join(entity.display for entity in find_targets(context, str(value)))
        if kind == "translate" and isinstance(value, dict):
            key = str(value.get("translate", ""))
            template = context.emulator.messages.template(key)
            if template is None:
                return str(value.get("fallback", key))
            arguments = [
                flatten_text_component(argument, resolve) for argument in value.get("with", [])
            ]
            return context.render(key, *arguments)
        if kind == "nbt" and isinstance(value, dict):
            return _nbt_text(context, value)
        return ""

    return resolve


def _nbt_text(context: ExecutionContext, component: dict[str, Any]) -> str:
    """An ``nbt`` text component: the value at a path of a storage, entity or block."""
    path = str(component.get("nbt", ""))
    if "storage" in component:
        store: Any = context.world.storage.get(normalise_id(str(component["storage"])), {})
    elif "entity" in component:
        entities = find_targets(context, str(component["entity"]))
        store = entities[0].data(context.emulator.version) if entities else None
    elif "block" in component:
        from datapack_emulator.emulator.commands.blocks import parse_block_position

        position, _ = parse_block_position(context, str(component["block"]).split())
        store = None
        if position is not None:
            block = context.world.blocks.stored(context.dimension, position)
            if block is not None:
                store = block.data(context.emulator.version, position) or None
    else:
        return ""
    value = nbt_get(store, path) if isinstance(store, dict) else None
    if value is None:
        return ""
    return value if isinstance(value, str) else to_snbt(value)


def cmd_title(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    targets = require_targets(context, command.arguments[0])
    action = command.arguments[1]
    if action in ("title", "subtitle", "actionbar") and len(command.arguments) > 2:
        payload = " ".join(command.arguments[2:])
        component = load_text_component(payload)
        for entity in targets:
            text = (
                payload
                if component is None
                else flatten_text_component(component, _text_resolver(context, entity))
            )
            context.chat(f"to {entity.display} ({action}): {text}", recipient=entity.display)
    return CommandResult(success=bool(targets), value=len(targets))
