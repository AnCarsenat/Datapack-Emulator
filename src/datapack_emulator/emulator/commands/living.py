"""Commands on living entities: effect, attribute, damage, ride.

See :mod:`datapack_emulator.emulator.runtime.living` for the model.
"""

from __future__ import annotations

import re

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.helpers import (
    integer,
    require_id,
    require_targets,
)
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.living import (
    INFINITE_EFFECTS_SINCE,
    INSTANT_EFFECTS,
    MODIFIER_IDS_SINCE,
    OPERATIONS_NEW,
    OPERATIONS_OLD,
    UNDEAD,
    Effect,
    Modifier,
    attribute_id,
    attribute_key,
    health,
    hurt,
    set_health,
)
from datapack_emulator.emulator.runtime.world import Entity, dismount, mount, root_vehicle

UUID_RE = re.compile(
    r"[0-9a-fA-F]{1,8}-[0-9a-fA-F]{1,4}-[0-9a-fA-F]{1,4}-[0-9a-fA-F]{1,4}-[0-9a-fA-F]{1,12}"
)


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


def _float(context: ExecutionContext, token: str) -> float | None:
    try:
        value = float(token)
    except ValueError:
        context.game_error("parsing.double.invalid", token)
        return None
    if value != value or value in (float("inf"), float("-inf")):
        context.game_error("parsing.double.invalid", token)
        return None
    return value


def _one(context: ExecutionContext, token: str) -> Entity | None:
    found = require_targets(context, token)
    if not found:
        return None
    if len(found) > 1:
        context.game_error("argument.entity.toomany")
        return None
    return found[0]


def java_number(value: float) -> str:
    """How Java prints a float or double in these messages (``20.0``, ``0.1``)."""
    return repr(float(value))


def _translated(context: ExecutionContext, key: str, fallback: str) -> str:
    return context.render(key) if context.emulator.messages.template(key) else fallback


def effect_name(context: ExecutionContext, effect_id: str) -> str:
    path = effect_id.split(":", 1)[1]
    return _translated(context, f"effect.minecraft.{path}", path.replace("_", " ").title())


# ---------------------------------------------------------------------------
# effect
# ---------------------------------------------------------------------------


def cmd_effect(command: Command, context: ExecutionContext) -> CommandResult:
    """``effect give <targets> <effect> [seconds|infinite] [amplifier] [hideParticles]``,
    ``effect clear [targets] [effect]``."""
    arguments = command.arguments
    if not arguments:
        return _usage(context)
    version = context.emulator.version
    if arguments[0] == "give" and len(arguments) >= 3:
        effect_id = normalise_id(arguments[2])
        if not require_id(context, "mob_effect", effect_id):
            return CommandResult.failure()
        instant = effect_id in INSTANT_EFFECTS
        duration = 1 if instant else 600
        if len(arguments) > 3:
            if arguments[3] == "infinite" and version >= versions.parse(INFINITE_EFFECTS_SINCE):
                duration = -1
            else:
                seconds = integer(context, arguments[3])
                if seconds is None:
                    return CommandResult.failure()
                if not 1 <= seconds <= 1_000_000:
                    low = seconds < 1
                    context.game_error(
                        "argument.integer.low" if low else "argument.integer.big",
                        1 if low else 1_000_000,
                        seconds,
                    )
                    return CommandResult.failure()
                duration = seconds if instant else seconds * 20
        amplifier = integer(context, arguments[4]) if len(arguments) > 4 else 0
        if amplifier is None:
            return CommandResult.failure()
        if not 0 <= amplifier <= 255:
            context.game_error(
                "argument.integer.big" if amplifier > 0 else "argument.integer.low",
                255 if amplifier > 0 else 0,
                amplifier,
            )
            return CommandResult.failure()
        hide = len(arguments) > 5 and arguments[5] == "true"
        targets = require_targets(context, arguments[1])
        applied = []
        for entity in targets:
            if entity.living is None:
                continue
            if instant:
                _instant(context, entity, effect_id, amplifier)
                applied.append(entity)
                continue
            effect = Effect(effect_id, amplifier, duration, show_particles=not hide)
            current = entity.living.effects.get(effect_id)
            if current is not None and not effect.stronger_than(current):
                continue
            entity.living.effects[effect_id] = effect
            applied.append(entity)
        if not applied:
            if targets:
                context.game_error("commands.effect.give.failed")
            return CommandResult.failure()
        name = effect_name(context, effect_id)
        if len(targets) == 1:
            context.feedback("commands.effect.give.success.single", name, targets[0].display)
        else:
            context.feedback("commands.effect.give.success.multiple", name, len(targets))
        return CommandResult(success=True, value=len(applied))
    if arguments[0] == "clear":
        if len(arguments) > 1:
            targets = require_targets(context, arguments[1])
        elif context.executor is not None:
            targets = [context.executor]
        else:
            context.game_error("permissions.requires.entity")
            return CommandResult.failure()
        effect_id = normalise_id(arguments[2]) if len(arguments) > 2 else None
        if effect_id is not None and not require_id(context, "mob_effect", effect_id):
            return CommandResult.failure()
        cleared = []
        for entity in targets:
            effects = entity.living.effects if entity.living else {}
            if effect_id is None and effects:
                effects.clear()
                cleared.append(entity)
            elif effect_id is not None and effect_id in effects:
                del effects[effect_id]
                cleared.append(entity)
        if not cleared:
            if targets:
                which = "everything" if effect_id is None else "specific"
                context.game_error(f"commands.effect.clear.{which}.failed")
            return CommandResult.failure()
        amount = "single" if len(targets) == 1 else "multiple"
        who = targets[0].display if len(targets) == 1 else len(targets)
        if effect_id is None:
            context.feedback(f"commands.effect.clear.everything.success.{amount}", who)
        else:
            context.feedback(
                f"commands.effect.clear.specific.success.{amount}",
                effect_name(context, effect_id),
                who,
            )
        return CommandResult(success=True, value=len(cleared))
    return _usage(context)


def _instant(context: ExecutionContext, entity: Entity, effect_id: str, amplifier: int) -> None:
    heal = effect_id == "minecraft:instant_health"
    if effect_id == "minecraft:saturation":
        return
    if entity.type in UNDEAD:
        heal = not heal
    if heal:
        set_health(context.world, entity, health(entity) + float(4 << amplifier))
    else:
        _damage(context, entity, float(6 << amplifier))


# ---------------------------------------------------------------------------
# damage
# ---------------------------------------------------------------------------


def _invulnerable(entity: Entity) -> bool:
    return bool(entity.nbt.get("Invulnerable")) or (
        entity.is_player and entity.nbt.get("playerGameType") in (1, 3)
    )


def _damage(context: ExecutionContext, entity: Entity, amount: float) -> None:
    if hurt(context.world, entity, amount):
        if entity.is_player:
            from datapack_emulator.emulator.commands.items import drop_inventory_on_death

            drop_inventory_on_death(context, entity)
        context.world.kill(entity)


def cmd_damage(command: Command, context: ExecutionContext) -> CommandResult:
    """``damage <target> <amount> [<type>] [at <pos> | by <entity> [from <entity>]]``"""
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    target = _one(context, arguments[0])
    amount = _float(context, arguments[1]) if target is not None else None
    if target is None or amount is None:
        return CommandResult.failure()
    if amount < 0:
        context.game_error("argument.float.low", 0, amount)
        return CommandResult.failure()
    if len(arguments) > 2 and not require_id(context, "damage_type", normalise_id(arguments[2])):
        return CommandResult.failure()
    if target.living is None or _invulnerable(target):
        context.game_error("commands.damage.invulnerable")
        return CommandResult.failure()
    _damage(context, target, amount)
    context.feedback("commands.damage.success", java_number(amount), target.display)
    return CommandResult(success=True, value=1)


# ---------------------------------------------------------------------------
# attribute
# ---------------------------------------------------------------------------


def _modifier_id(context: ExecutionContext, token: str) -> str | None:
    if context.emulator.version < versions.parse(MODIFIER_IDS_SINCE):
        if not UUID_RE.fullmatch(token):
            context.game_error("argument.uuid.invalid")
            return None
        return token.lower()
    return normalise_id(token)


def cmd_attribute(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 3:
        return _usage(context)
    version = context.emulator.version
    entity = _one(context, arguments[0])
    if entity is None:
        return CommandResult.failure()
    name = attribute_key(arguments[1])
    shown_id = arguments[1] if ":" in arguments[1] else f"minecraft:{arguments[1]}"
    if name is None or normalise_id(arguments[1]) != attribute_id(name, version):
        context.game_error("argument.resource.not_found", arguments[1], "minecraft:attribute")
        return CommandResult.failure()
    path = shown_id.split(":", 1)[1]
    shown = _translated(context, f"attribute.name.{path}", path)
    if entity.living is None:
        context.game_error("commands.attribute.failed.entity", entity.display)
        return CommandResult.failure()
    attribute = entity.living.attribute(name)
    rest = arguments[2:]

    def scaled(value: float, index: int) -> int | None:
        scale = _float(context, rest[index]) if len(rest) > index else 1.0
        return None if scale is None else int(value * scale)

    if rest[0] == "get":
        value = attribute.value
        result = scaled(value, 1)
        if result is None:
            return CommandResult.failure()
        context.feedback(
            "commands.attribute.value.get.success", shown, entity.display, java_number(value)
        )
        return CommandResult(success=True, value=result)
    if rest[0] == "base" and len(rest) >= 2:
        if rest[1] == "get":
            result = scaled(attribute.base, 2)
            if result is None:
                return CommandResult.failure()
            context.feedback(
                "commands.attribute.base_value.get.success",
                shown,
                entity.display,
                java_number(attribute.base),
            )
            return CommandResult(success=True, value=result)
        if rest[1] == "set" and len(rest) >= 3:
            value = _float(context, rest[2])
            if value is None:
                return CommandResult.failure()
            attribute.base = value
            _clamp_health(context, entity, name)
            context.feedback(
                "commands.attribute.base_value.set.success",
                shown,
                entity.display,
                java_number(value),
            )
            return CommandResult(success=True, value=1)
        if rest[1] == "reset":
            from datapack_emulator.emulator.runtime.living import default_base

            attribute.base = default_base(entity.type, name)
            _clamp_health(context, entity, name)
            context.feedback(
                "commands.attribute.base_value.reset.success",
                shown,
                entity.display,
                java_number(attribute.base),
            )
            return CommandResult(success=True, value=1)
    if rest[0] == "modifier" and len(rest) >= 3:
        action = rest[1]
        modern = version >= versions.parse(MODIFIER_IDS_SINCE)
        if action == "add":
            # modern: add <id> <value> <operation>; older: add <uuid> <name> <value> <operation>
            needed = 5 if modern else 6
            if len(rest) < needed:
                return _usage(context)
            identifier = _modifier_id(context, rest[2])
            if identifier is None:
                return CommandResult.failure()
            label = "" if modern else rest[3]
            value = _float(context, rest[needed - 2])
            operations = OPERATIONS_NEW if modern else OPERATIONS_OLD
            operation = rest[needed - 1]
            if value is None or operation not in operations:
                return CommandResult.failure() if value is None else _usage(context)
            if identifier in attribute.modifiers:
                context.game_error(
                    "commands.attribute.failed.modifier_already_present",
                    identifier,
                    shown,
                    entity.display,
                )
                return CommandResult.failure()
            attribute.modifiers[identifier] = Modifier(
                identifier, value, operations.index(operation), label
            )
            _clamp_health(context, entity, name)
            context.feedback(
                "commands.attribute.modifier.add.success", identifier, shown, entity.display
            )
            return CommandResult(success=True, value=1)
        if action in ("remove", "value") and len(rest) >= 3:
            index = 3 if action == "value" else 2
            if action == "value" and (rest[2] != "get" or len(rest) < 4):
                return _usage(context)
            identifier = _modifier_id(context, rest[index])
            if identifier is None:
                return CommandResult.failure()
            modifier = attribute.modifiers.get(identifier)
            if modifier is None:
                context.game_error(
                    "commands.attribute.failed.no_modifier", shown, entity.display, identifier
                )
                return CommandResult.failure()
            if action == "remove":
                del attribute.modifiers[identifier]
                _clamp_health(context, entity, name)
                context.feedback(
                    "commands.attribute.modifier.remove.success", identifier, shown, entity.display
                )
                return CommandResult(success=True, value=1)
            result = scaled(modifier.amount, 4)
            if result is None:
                return CommandResult.failure()
            context.feedback(
                "commands.attribute.modifier.value.get.success",
                identifier,
                shown,
                entity.display,
                java_number(modifier.amount),
            )
            return CommandResult(success=True, value=result)
    return _usage(context)


def _clamp_health(context: ExecutionContext, entity: Entity, name: str) -> None:
    if name == "max_health" and "Health" in entity.nbt:
        set_health(context.world, entity, health(entity))


# ---------------------------------------------------------------------------
# ride
# ---------------------------------------------------------------------------


def cmd_ride(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    rider = _one(context, arguments[0])
    if rider is None:
        return CommandResult.failure()
    if arguments[1] == "dismount":
        vehicle = rider.vehicle
        if vehicle is None:
            context.game_error("commands.ride.not_riding", rider.display)
            return CommandResult.failure()
        dismount(rider)
        context.feedback("commands.ride.dismount.success", rider.display, vehicle.display)
        return CommandResult(success=True, value=1)
    if arguments[1] == "mount" and len(arguments) >= 3:
        vehicle = _one(context, arguments[2])
        if vehicle is None:
            return CommandResult.failure()
        if rider.vehicle is not None:
            context.game_error("commands.ride.already_riding", rider.display, rider.vehicle.display)
            return CommandResult.failure()
        if vehicle.is_player:
            context.game_error("commands.ride.mount.failure.cant_ride_players")
            return CommandResult.failure()
        if vehicle is rider or root_vehicle(vehicle) is rider:
            context.game_error("commands.ride.mount.failure.loop")
            return CommandResult.failure()
        if vehicle.dimension != rider.dimension:
            context.game_error("commands.ride.mount.failure.wrong_dimension")
            return CommandResult.failure()
        mount(rider, vehicle)
        context.feedback("commands.ride.mount.success", rider.display, vehicle.display)
        return CommandResult(success=True, value=1)
    return _usage(context)


LIVING_HANDLERS = {
    "effect": cmd_effect,
    "damage": cmd_damage,
    "attribute": cmd_attribute,
    "ride": cmd_ride,
}
