"""Entity commands: tag, summon, kill, tp/teleport."""

from __future__ import annotations

from datapack_emulator.emulator.commands.helpers import require_id, require_targets
from datapack_emulator.emulator.commands.items import drop_inventory_on_death
from datapack_emulator.emulator.commands.parser import Command, resolve_position
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id, parse_snbt
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity, ints_to_uuid, normalise_rotation


def cmd_tag(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 3:
        return CommandResult.failure()
    targets = require_targets(context, command.arguments[0])
    action, name = command.arguments[1], command.arguments[2]
    changed = 0
    for entity in targets:
        if action == "add" and name not in entity.tags:
            entity.tags.add(name)
            changed += 1
        elif action == "remove" and name in entity.tags:
            entity.tags.discard(name)
            changed += 1
    if targets and changed == 0 and action in ("add", "remove"):
        context.game_error(f"commands.tag.{action}.failed")
    elif changed == 1 and action in ("add", "remove"):
        context.feedback(f"commands.tag.{action}.success.single", name, targets[0].display)
    return CommandResult(success=changed > 0, value=changed)


def cmd_summon(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    entity_type = normalise_id(command.arguments[0])
    if not require_id(context, "entity_type", entity_type):
        return CommandResult.failure()
    position = resolve_position(command.arguments[1:4], context.position)
    payload = next((a for a in command.arguments[1:] if a.startswith("{")), "")
    data = parse_snbt(payload) if payload else {}
    entity = Entity(type=entity_type)
    uuid = ints_to_uuid(data.get("UUID"))
    if uuid is not None:
        if any(other.uuid == uuid for other in context.world.entities):
            context.game_error("commands.summon.failed.uuid")
            return CommandResult.failure()
        entity.uuid = uuid
    data["Pos"] = position  # the position argument wins over Pos in the NBT
    entity.apply_data(data)
    context.world.spawn(entity)
    context.feedback("commands.summon.success", entity.display)
    return CommandResult(success=True, value=1)


def cmd_kill(command: Command, context: ExecutionContext) -> CommandResult:
    token = command.arguments[0] if command.arguments else "@s"
    victims = require_targets(context, token)
    for entity in victims:
        if entity.is_player:
            drop_inventory_on_death(context, entity)
        context.world.kill(entity)
    if len(victims) == 1:
        context.feedback("commands.kill.success.single", victims[0].display)
    elif victims:
        context.feedback("commands.kill.success.multiple", len(victims))
    return CommandResult(success=bool(victims), value=len(victims))


def cmd_teleport(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments:
        return CommandResult.failure()
    arguments = command.arguments
    rotation: list[float] | None = None
    if len(arguments) >= 4:  # tp <targets> <x y z> [<yaw> <pitch> | facing ...]
        targets = require_targets(context, arguments[0])
        destination = resolve_position(arguments[1:4], context.position)
        if len(arguments) >= 6 and arguments[4] != "facing":
            # relative rotation is relative to the command source, like positions
            rotation = normalise_rotation(
                resolve_position([*arguments[4:6], "0"], [*context.rotation, 0.0])[:2]
            )
    elif len(arguments) == 3:  # tp <x y z>
        targets = [context.executor] if context.executor else []
        destination = resolve_position(arguments, context.position)
    else:  # tp <destination entity> / tp <targets> <destination entity>
        targets = (
            require_targets(context, arguments[0])
            if len(arguments) == 2
            else ([context.executor] if context.executor else [])
        )
        anchor = require_targets(context, arguments[-1])
        if not anchor:
            return CommandResult.failure()
        destination = list(anchor[0].position)
    for entity in targets:
        if entity is not None:
            entity.position = list(destination)
            if rotation is not None:
                entity.rotation = list(rotation)
    if len(targets) == 1 and targets[0] is not None:
        context.feedback(
            "commands.teleport.success.entity.single",
            targets[0].display,
            ", ".join(f"{value:.1f}" for value in destination),
        )
    return CommandResult(success=bool(targets), value=len(targets))
