"""Server-state commands: time, weather, difficulty, worldborder, random,
seed, list, forceload, setworldspawn, spawnpoint, tick.

See :mod:`datapack_emulator.emulator.runtime.state` for what is modelled.
"""

from __future__ import annotations

import math

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.helpers import integer, require_targets
from datapack_emulator.emulator.commands.parser import Command, parse_duration, resolve_position
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.state import (
    BORDER_CENTER_LIMIT,
    BORDER_MAX,
    DIFFICULTIES,
)

#: named times of ``time set``
NAMED_TIMES = {"day": 1000, "noon": 6000, "night": 13000, "midnight": 18000}
INT_MAX = 2**31 - 1


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


def _ticks(context: ExecutionContext, token: str) -> int | None:
    """A time argument: ``5``, ``5t``, ``5s``, ``5d``; negative is an error."""
    body = token[:-1] if token[-1:] in ("t", "s", "d") else token
    try:
        float(body)
    except ValueError:
        context.game_error("argument.time.invalid_unit")
        return None
    ticks = parse_duration(token)
    if ticks < 0:
        context.game_error("argument.time.invalid_tick_count")
        return None
    return ticks


def _number(context: ExecutionContext, token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        context.game_error("parsing.float.invalid", token)
        return None


# ---------------------------------------------------------------------------
# time and weather
# ---------------------------------------------------------------------------


def cmd_time(command: Command, context: ExecutionContext) -> CommandResult:
    """``time set|add <time>``, ``time query daytime|gametime|day``."""
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    state = context.world.state
    action, value = arguments[0], arguments[1]
    if action == "query":
        if value == "daytime":
            result = state.day_time % 24000
        elif value == "gametime":
            result = context.world.tick % (INT_MAX + 1)
        elif value == "day":
            result = state.day_time // 24000 % (INT_MAX + 1)
        else:
            return _usage(context)
        context.feedback("commands.time.query", result)
        return CommandResult(success=True, value=result)
    if action not in ("set", "add"):
        return _usage(context)
    ticks = NAMED_TIMES.get(value) if action == "set" else None
    if ticks is None:
        ticks = _ticks(context, value)
        if ticks is None:
            return CommandResult.failure()
    state.day_time = ticks if action == "set" else state.day_time + ticks
    result = state.day_time % 24000
    context.feedback("commands.time.set", ticks if action == "set" else result)
    return CommandResult(success=True, value=result)


def cmd_weather(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if not arguments or arguments[0] not in ("clear", "rain", "thunder"):
        return _usage(context)
    duration = 0
    if len(arguments) > 1:
        ticks = _ticks(context, arguments[1])
        if ticks is None:
            return CommandResult.failure()
        # the duration was a number of seconds before it became a time argument
        duration = ticks if versions.weather_takes_time(context.emulator.version) else ticks * 20
    state = context.world.state
    state.weather = arguments[0]
    state.weather_duration = duration
    context.feedback(f"commands.weather.set.{arguments[0]}")
    return CommandResult(success=True, value=duration or 1)


# ---------------------------------------------------------------------------
# difficulty
# ---------------------------------------------------------------------------


def cmd_difficulty(command: Command, context: ExecutionContext) -> CommandResult:
    state = context.world.state
    if not command.arguments:
        name = context.render(f"options.difficulty.{state.difficulty}")
        context.feedback("commands.difficulty.query", name)
        return CommandResult(success=True, value=DIFFICULTIES.index(state.difficulty))
    wanted = command.arguments[0]
    if wanted not in DIFFICULTIES:
        return _usage(context)
    name = context.render(f"options.difficulty.{wanted}")
    if state.difficulty == wanted:
        context.game_error("commands.difficulty.failure", wanted)
        return CommandResult.failure()
    state.difficulty = wanted
    context.feedback("commands.difficulty.success", name)
    return CommandResult(success=True, value=DIFFICULTIES.index(wanted))


# ---------------------------------------------------------------------------
# world border
# ---------------------------------------------------------------------------


def _border_number(value: float) -> str:
    return f"{value:.1f}"


def cmd_worldborder(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if not arguments:
        return _usage(context)
    border = context.world.state.border
    action = arguments[0]
    if action == "get":
        size = int(border.size + 0.5)
        context.feedback("commands.worldborder.get", f"{border.size:.0f}")
        return CommandResult(success=True, value=size)
    if action in ("set", "add") and len(arguments) >= 2:
        amount = _number(context, arguments[1])
        seconds = integer(context, arguments[2]) if len(arguments) > 2 else 0
        if amount is None or seconds is None:
            return CommandResult.failure()
        size = amount if action == "set" else border.size + amount
        if size == border.size:
            context.game_error("commands.worldborder.set.failed.nochange")
            return CommandResult.failure()
        if size < 1:
            context.game_error("commands.worldborder.set.failed.small")
            return CommandResult.failure()
        if size > BORDER_MAX:
            context.game_error("commands.worldborder.set.failed.big", f"{BORDER_MAX:.0f}")
            return CommandResult.failure()
        grow = size > border.size
        change = int(size - border.size)
        border.size = size
        if seconds > 0:
            key = "grow" if grow else "shrink"
            context.feedback(f"commands.worldborder.set.{key}", _border_number(size), seconds)
            context.note_once("worldborder: a timed change is applied at once")
        else:
            context.feedback("commands.worldborder.set.immediate", _border_number(size))
        return CommandResult(success=True, value=change)
    if action == "center" and len(arguments) >= 3:
        x = resolve_position([arguments[1], "0", arguments[2]], context.position)
        center = (math.floor(x[0]) + 0.5, math.floor(x[2]) + 0.5)
        if max(abs(center[0]), abs(center[1])) > BORDER_CENTER_LIMIT:
            context.game_error("commands.worldborder.set.failed.far", f"{BORDER_CENTER_LIMIT:.0f}")
            return CommandResult.failure()
        if center == border.center:
            context.game_error("commands.worldborder.center.failed")
            return CommandResult.failure()
        border.center = center
        context.feedback(
            "commands.worldborder.center.success", f"{center[0]:.2f}", f"{center[1]:.2f}"
        )
        return CommandResult(success=True, value=0)
    if action == "damage" and len(arguments) >= 3:
        amount = _number(context, arguments[2])
        if amount is None or arguments[1] not in ("amount", "buffer"):
            return CommandResult.failure()
        attribute = "damage_amount" if arguments[1] == "amount" else "damage_buffer"
        if getattr(border, attribute) == amount:
            context.game_error(f"commands.worldborder.damage.{arguments[1]}.failed")
            return CommandResult.failure()
        setattr(border, attribute, amount)
        context.feedback(f"commands.worldborder.damage.{arguments[1]}.success", f"{amount:.2f}")
        return CommandResult(success=True, value=int(amount))
    if action == "warning" and len(arguments) >= 3 and arguments[1] in ("distance", "time"):
        amount = integer(context, arguments[2])
        if amount is None:
            return CommandResult.failure()
        attribute = f"warning_{arguments[1]}"
        if getattr(border, attribute) == amount:
            context.game_error(f"commands.worldborder.warning.{arguments[1]}.failed")
            return CommandResult.failure()
        setattr(border, attribute, amount)
        context.feedback(f"commands.worldborder.warning.{arguments[1]}.success", amount)
        return CommandResult(success=True, value=amount)
    return _usage(context)


# ---------------------------------------------------------------------------
# random
# ---------------------------------------------------------------------------


def _range(context: ExecutionContext, token: str) -> tuple[int, int] | None:
    low, sep, high = token.partition("..")
    try:
        bounds = (int(low), int(high)) if sep else (int(token), int(token))
    except ValueError:
        context.game_error("command.unknown.argument")
        return None
    size = bounds[1] - bounds[0] + 1
    if size < 2:
        context.game_error("commands.random.error.range_too_small")
        return None
    if size > INT_MAX:
        context.game_error("commands.random.error.range_too_large")
        return None
    return bounds


def cmd_random(command: Command, context: ExecutionContext) -> CommandResult:
    """``random value|roll <range> [sequence]``, ``random reset <*|sequence> …``"""
    arguments = command.arguments
    if len(arguments) < 2:
        return _usage(context)
    state = context.world.state
    action = arguments[0]
    if action in ("value", "roll"):
        bounds = _range(context, arguments[1])
        if bounds is None:
            return CommandResult.failure()
        rng = (
            state.sequence(normalise_id(arguments[2]))
            if len(arguments) > 2
            else context.world.random
        )
        value = rng.randint(*bounds)
        if action == "value":
            context.feedback("commands.random.sample.success", value)
        else:
            who = context.executor.display if context.executor else "Server"
            context.chat(context.render("commands.random.roll", who, value, *bounds), recipient="*")
        return CommandResult(success=True, value=value)
    if action == "reset":
        seed = integer(context, arguments[2]) if len(arguments) > 2 else 0
        if seed is None:
            return CommandResult.failure()
        world_seed = arguments[3] != "false" if len(arguments) > 3 else True
        with_id = arguments[4] != "false" if len(arguments) > 4 else True
        if arguments[1] == "*":
            count = len(state.sequences)
            for name in list(state.sequences):
                state.reset_sequence(name, seed, world_seed, with_id)
            context.feedback("commands.random.reset.all.success", count)
            return CommandResult(success=True, value=count)
        name = normalise_id(arguments[1])
        state.reset_sequence(name, seed, world_seed, with_id)
        context.feedback("commands.random.reset.success", name)
        return CommandResult(success=True, value=1)
    return _usage(context)


# ---------------------------------------------------------------------------
# seed, list, tick
# ---------------------------------------------------------------------------


def cmd_seed(command: Command, context: ExecutionContext) -> CommandResult:
    seed = context.world.state.seed
    context.feedback("commands.seed.success", f"[{seed}]")
    return CommandResult(success=True, value=seed & 0xFFFFFFFF if seed > INT_MAX else seed)


def cmd_list(command: Command, context: ExecutionContext) -> CommandResult:
    players = context.world.players
    uuids = bool(command.arguments and command.arguments[0] == "uuids")
    names = ", ".join(
        context.render("commands.list.nameAndId", player.display, player.uuid)
        if uuids
        else player.display
        for player in players
    )
    context.feedback("commands.list.players", len(players), 20, names)
    return CommandResult(success=True, value=len(players))


def cmd_tick(command: Command, context: ExecutionContext) -> CommandResult:
    """``tick query|rate|freeze|unfreeze|step|sprint``: the rate and the frozen
    flag are kept, but the emulator's ticks are not slowed or stopped."""
    arguments = command.arguments
    state = context.world.state
    action = arguments[0] if arguments else ""
    if action == "rate" and len(arguments) > 1:
        rate = _number(context, arguments[1])
        if rate is None:
            return CommandResult.failure()
        if not 1.0 <= rate <= 10000.0:
            context.game_error("argument.float.low" if rate < 1 else "argument.float.big", 1, rate)
            return CommandResult.failure()
        state.tick_rate = rate
        context.feedback("commands.tick.rate.success", f"{rate:.1f}")
        return CommandResult(success=True, value=int(rate))
    if action in ("freeze", "unfreeze"):
        state.frozen = action == "freeze"
        context.feedback(
            "commands.tick.status.frozen" if state.frozen else "commands.tick.status.running"
        )
        context.note_once("tick freeze: the emulator keeps running the tick functions")
        return CommandResult(success=True, value=int(state.frozen))
    if action == "query":
        key = "commands.tick.status.frozen" if state.frozen else "commands.tick.status.running"
        context.feedback(key)
        return CommandResult(success=True, value=int(state.tick_rate))
    context.note_once(f"tick {action}: stepping and sprinting are not modelled")
    return CommandResult(success=True, value=1)


# ---------------------------------------------------------------------------
# chunks and spawn points
# ---------------------------------------------------------------------------


def _chunk(value: float) -> int:
    return math.floor(value) >> 4


def cmd_forceload(command: Command, context: ExecutionContext) -> CommandResult:
    """``forceload add|remove <from> [to]``, ``remove all``, ``query [pos]``."""
    arguments = command.arguments
    if not arguments:
        return _usage(context)
    dimension = context.dimension
    chunks = context.world.state.forced_chunks.setdefault(dimension, set())
    action = arguments[0]
    if action == "query":
        if len(arguments) >= 3:
            position = resolve_position([arguments[1], "0", arguments[2]], context.position)
            chunk = (_chunk(position[0]), _chunk(position[2]))
            if chunk not in chunks:
                context.game_error(
                    "commands.forceload.query.failure", f"[{chunk[0]}, {chunk[1]}]", dimension
                )
                return CommandResult.failure()
            context.feedback(
                "commands.forceload.query.success", f"[{chunk[0]}, {chunk[1]}]", dimension
            )
            return CommandResult(success=True, value=1)
        listed = ", ".join(f"[{x}, {z}]" for x, z in sorted(chunks))
        if not chunks:
            context.feedback("commands.forceload.added.none", dimension)
        elif len(chunks) == 1:
            context.feedback("commands.forceload.list.single", dimension, listed)
        else:
            context.feedback("commands.forceload.list.multiple", len(chunks), dimension, listed)
        return CommandResult(success=True, value=len(chunks))
    if action == "remove" and arguments[1:2] == ["all"]:
        chunks.clear()
        context.feedback("commands.forceload.removed.all", dimension)
        return CommandResult(success=True, value=0)
    if action not in ("add", "remove") or len(arguments) < 3:
        return _usage(context)
    start = resolve_position([arguments[1], "0", arguments[2]], context.position)
    end = (
        resolve_position([arguments[3], "0", arguments[4]], context.position)
        if len(arguments) >= 5
        else start
    )
    xs = sorted((_chunk(start[0]), _chunk(end[0])))
    zs = sorted((_chunk(start[2]), _chunk(end[2])))
    area = (xs[1] - xs[0] + 1) * (zs[1] - zs[0] + 1)
    if area > 256:
        context.game_error("commands.forceload.toobig", 256, area)
        return CommandResult.failure()
    changed = []
    for x in range(xs[0], xs[1] + 1):
        for z in range(zs[0], zs[1] + 1):
            present = (x, z) in chunks
            if action == "add" and not present:
                chunks.add((x, z))
                changed.append((x, z))
            elif action == "remove" and present:
                chunks.discard((x, z))
                changed.append((x, z))
    verb = "added" if action == "add" else "removed"
    if not changed:
        context.game_error(f"commands.forceload.{verb}.failure")
        return CommandResult.failure()
    if len(changed) == 1:
        x, z = changed[0]
        context.feedback(f"commands.forceload.{verb}.single", f"[{x}, {z}]", dimension)
    else:
        context.feedback(
            f"commands.forceload.{verb}.multiple",
            len(changed),
            dimension,
            f"[{xs[0]}, {zs[0]}]",
            f"[{xs[1]}, {zs[1]}]",
        )
    return CommandResult(success=True, value=len(changed))


def cmd_setworldspawn(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    position = (
        resolve_position(arguments[:3], context.position)
        if len(arguments) >= 3
        else list(context.position)
    )
    angle = _number(context, arguments[3]) if len(arguments) > 3 else 0.0
    if angle is None:
        return CommandResult.failure()
    state = context.world.state
    state.spawn = (math.floor(position[0]), math.floor(position[1]), math.floor(position[2]))
    state.spawn_angle = angle
    context.feedback("commands.setworldspawn.success", *state.spawn, angle)
    return CommandResult(success=True, value=1)


def cmd_spawnpoint(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if arguments:
        targets = require_targets(context, arguments[0])
    else:
        targets = [context.executor] if context.executor is not None else []
    if not targets:
        return CommandResult.failure()
    position = (
        resolve_position(arguments[1:4], context.position)
        if len(arguments) >= 4
        else list(context.position)
    )
    block = [math.floor(value) for value in position]
    angle = _number(context, arguments[4]) if len(arguments) > 4 else 0.0
    if angle is None:
        return CommandResult.failure()
    for entity in targets:
        if entity.is_player:
            entity.nbt["SpawnX"], entity.nbt["SpawnY"], entity.nbt["SpawnZ"] = block
            entity.nbt["SpawnAngle"] = angle
            entity.nbt["SpawnDimension"] = context.dimension
    if len(targets) == 1:
        context.feedback(
            "commands.spawnpoint.success.single",
            *block,
            angle,
            context.dimension,
            targets[0].display,
        )
    else:
        context.feedback(
            "commands.spawnpoint.success.multiple", *block, angle, context.dimension, len(targets)
        )
    return CommandResult(success=True, value=len(targets))


STATE_HANDLERS = {
    "time": cmd_time,
    "weather": cmd_weather,
    "difficulty": cmd_difficulty,
    "worldborder": cmd_worldborder,
    "random": cmd_random,
    "seed": cmd_seed,
    "list": cmd_list,
    "tick": cmd_tick,
    "forceload": cmd_forceload,
    "setworldspawn": cmd_setworldspawn,
    "spawnpoint": cmd_spawnpoint,
}
