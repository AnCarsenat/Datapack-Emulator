"""``scoreboard`` and ``trigger``."""

from __future__ import annotations

from collections.abc import Callable

from datapack_emulator.emulator.commands.helpers import find_holders, integer, require_targets
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import flatten_text_component, load_text_component
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import wrap_int

#: criteria accepted by `scoreboard objectives add` besides the prefixed families
CRITERIA = frozenset(
    {
        "dummy",
        "trigger",
        "deathCount",
        "playerKillCount",
        "totalKillCount",
        "health",
        "xp",
        "level",
        "food",
        "air",
        "armor",
    }
)
#: display slots, with both spellings of the one under player names
_COLOURS = [
    "black",
    "dark_blue",
    "dark_green",
    "dark_aqua",
    "dark_red",
    "dark_purple",
    "gold",
    "gray",
    "dark_gray",
    "blue",
    "green",
    "aqua",
    "red",
    "light_purple",
    "yellow",
    "white",
]
DISPLAY_SLOTS = frozenset(
    {"list", "sidebar", "belowName", "below_name", *(f"sidebar.team.{c}" for c in _COLOURS)}
)
#: teamkill.<colour>, killedByTeam.<colour>, minecraft.<stat type>:<id>
CRITERIA_PREFIXES = ("teamkill.", "killedByTeam.", "minecraft.")


def cmd_scoreboard(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 2:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    group, action, rest = arguments[0], arguments[1], arguments[2:]
    handler = (_SCOREBOARD_OBJECTIVES if group == "objectives" else _SCOREBOARD_PLAYERS).get(action)
    if group not in ("objectives", "players") or handler is None:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    return handler(rest, context)


# -- scoreboard objectives ----------------------------------------------------


def _objectives_list(rest: list[str], context: ExecutionContext) -> CommandResult:
    names = list(context.world.scoreboard.objectives)
    if not names:
        context.feedback("commands.scoreboard.objectives.list.empty")
    else:
        listed = ", ".join(f"[{name}]" for name in names)
        context.feedback("commands.scoreboard.objectives.list.success", len(names), listed)
    return CommandResult(success=True, value=len(names))


def _objectives_add(rest: list[str], context: ExecutionContext) -> CommandResult:
    if len(rest) < 2:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    name, criterion = rest[0], rest[1]
    if criterion not in CRITERIA and not criterion.startswith(CRITERIA_PREFIXES):
        context.game_error("argument.criteria.invalid", criterion)
        return CommandResult.failure()
    board = context.world.scoreboard
    display = _display_text(" ".join(rest[2:])) if len(rest) > 2 else name
    if not board.add_objective(name, criterion, display):
        context.game_error("commands.scoreboard.objectives.add.duplicate")
        return CommandResult.failure()
    context.feedback("commands.scoreboard.objectives.add.success", f"[{display}]")
    return CommandResult(success=True, value=len(board.objectives))


def _objectives_remove(rest: list[str], context: ExecutionContext) -> CommandResult:
    name = _objective(context, rest[0] if rest else "")
    if name is None:
        return CommandResult.failure()
    board = context.world.scoreboard
    display = board.display_names.get(name, name)
    board.remove_objective(name)
    context.feedback("commands.scoreboard.objectives.remove.success", f"[{display}]")
    return CommandResult(success=True, value=len(board.objectives))


def _objectives_setdisplay(rest: list[str], context: ExecutionContext) -> CommandResult:
    if not rest:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    slot, board = rest[0], context.world.scoreboard
    if slot not in DISPLAY_SLOTS:
        context.game_error("argument.scoreboardDisplaySlot.invalid", slot)
        return CommandResult.failure()
    if len(rest) < 2:
        if board.display_slots.pop(slot, None) is None:
            context.game_error("commands.scoreboard.objectives.display.alreadyEmpty")
            return CommandResult.failure()
        context.feedback("commands.scoreboard.objectives.display.cleared", slot)
        return CommandResult(success=True, value=0)
    name = _objective(context, rest[1])
    if name is None:
        return CommandResult.failure()
    if board.display_slots.get(slot) == name:
        context.game_error("commands.scoreboard.objectives.display.alreadySet")
        return CommandResult.failure()
    board.display_slots[slot] = name
    context.feedback("commands.scoreboard.objectives.display.set", slot, _shown(context, name))
    return CommandResult(success=True, value=0)


def _objectives_modify(rest: list[str], context: ExecutionContext) -> CommandResult:
    name = _objective(context, rest[0] if rest else "")
    if name is None or len(rest) < 2:
        return CommandResult.failure()
    board = context.world.scoreboard
    if rest[1] == "displayname" and len(rest) > 2:
        board.display_names[name] = _display_text(" ".join(rest[2:]))
        context.feedback(
            "commands.scoreboard.objectives.modify.displayname", name, board.display_names[name]
        )
    elif rest[1] == "rendertype":
        context.feedback("commands.scoreboard.objectives.modify.rendertype", name)
    # displayautoupdate and numberformat only change how the sidebar looks
    return CommandResult(success=True, value=0)


_SCOREBOARD_OBJECTIVES = {
    "list": _objectives_list,
    "add": _objectives_add,
    "remove": _objectives_remove,
    "setdisplay": _objectives_setdisplay,
    "modify": _objectives_modify,
}


def _display_text(payload: str) -> str:
    component = load_text_component(payload)
    return payload.strip('"') if component is None else flatten_text_component(component)


def _objective(context: ExecutionContext, name: str, writable: bool = False) -> str | None:
    """An existing objective (and, for writes, one commands may change)."""
    board = context.world.scoreboard
    if name not in board.objectives:
        context.game_error("arguments.objective.notFound", name)
        return None
    if writable and board.is_read_only(name):
        context.game_error("arguments.objective.readonly", name)
        return None
    return name


def _score_holders(context: ExecutionContext, token: str) -> list[str]:
    """Holders for a command that needs at least one; reports vanilla's error."""
    if token.startswith("@"):
        return [entity.id for entity in require_targets(context, token)]
    holders = find_holders(context, token)
    if not holders:
        context.game_error("argument.scoreHolder.empty")
    return holders


def _shown(context: ExecutionContext, objective: str) -> str:
    """An objective as feedback shows it: its display name in brackets."""
    return f"[{context.world.scoreboard.display_names.get(objective, objective)}]"


def _holder_name(context: ExecutionContext, holder: str) -> str:
    """How feedback names a holder: an entity's display name, else the holder itself."""
    for entity in context.world.entities:
        if entity.id == holder:
            return entity.display
    return holder


# -- scoreboard players ---------------------------------------------------------


def _players_list(rest: list[str], context: ExecutionContext) -> CommandResult:
    board = context.world.scoreboard
    if not rest:
        holders = board.tracked()
        if not holders:
            context.feedback("commands.scoreboard.players.list.empty")
        else:
            names = ", ".join(_holder_name(context, holder) for holder in holders)
            context.feedback("commands.scoreboard.players.list.success", len(holders), names)
        return CommandResult(success=True, value=len(holders))
    holders = _score_holders(context, rest[0])
    if not holders:
        return CommandResult.failure()
    holder = holders[0]
    scores = board.scores.get(holder, {})
    name = _holder_name(context, holder)
    if not scores:
        context.feedback("commands.scoreboard.players.list.entity.empty", name)
    else:
        context.feedback("commands.scoreboard.players.list.entity.success", name, len(scores))
        for objective, value in scores.items():
            display = board.display_names.get(objective, objective)
            context.feedback("commands.scoreboard.players.list.entity.entry", f"[{display}]", value)
    return CommandResult(success=True, value=len(scores))


def _players_get(rest: list[str], context: ExecutionContext) -> CommandResult:
    if len(rest) < 2:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    holders = _score_holders(context, rest[0])
    objective = _objective(context, rest[1]) if holders else None
    if not holders or objective is None:
        return CommandResult.failure()
    if len(holders) > 1:
        context.game_error("argument.entity.toomany")
        return CommandResult.failure()
    value = context.world.scoreboard.get(holders[0], objective)
    name = _holder_name(context, holders[0])
    if value is None:
        context.game_error("commands.scoreboard.players.get.null", objective, name)
        return CommandResult.failure()
    context.feedback(
        "commands.scoreboard.players.get.success", name, value, _shown(context, objective)
    )
    return CommandResult(success=True, value=value)


def _players_set(rest: list[str], context: ExecutionContext) -> CommandResult:
    if len(rest) < 3:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    value = integer(context, rest[2])
    holders = _score_holders(context, rest[0]) if value is not None else []
    objective = _objective(context, rest[1], writable=True) if holders else None
    if value is None or objective is None:
        return CommandResult.failure()
    board = context.world.scoreboard
    for holder in holders:
        board.set(holder, objective, value)
    if len(holders) == 1:
        name = _holder_name(context, holders[0])
        context.feedback(
            "commands.scoreboard.players.set.success.single",
            _shown(context, objective),
            name,
            value,
        )
    else:
        context.feedback(
            "commands.scoreboard.players.set.success.multiple",
            _shown(context, objective),
            len(holders),
            value,
        )
    return CommandResult(success=True, value=wrap_int(value * len(holders)))


def _players_add_or_remove(sign: int) -> Callable[[list[str], ExecutionContext], CommandResult]:
    verb = "add" if sign > 0 else "remove"

    def handler(rest: list[str], context: ExecutionContext) -> CommandResult:
        if len(rest) < 3:
            context.game_error("command.unknown.command")
            return CommandResult.failure()
        amount = integer(context, rest[2])
        if amount is not None and amount < 0:
            context.game_error("argument.integer.low", 0, amount)
            return CommandResult.failure()
        holders = _score_holders(context, rest[0]) if amount is not None else []
        objective = _objective(context, rest[1], writable=True) if holders else None
        if amount is None or objective is None:
            return CommandResult.failure()
        board = context.world.scoreboard
        total = 0
        for holder in holders:
            total += board.add(holder, objective, sign * amount)
        if len(holders) == 1:
            context.feedback(
                f"commands.scoreboard.players.{verb}.success.single",
                amount,
                _shown(context, objective),
                _holder_name(context, holders[0]),
                board.get(holders[0], objective),
            )
        else:
            context.feedback(
                f"commands.scoreboard.players.{verb}.success.multiple",
                amount,
                _shown(context, objective),
                len(holders),
            )
        return CommandResult(success=True, value=wrap_int(total))

    return handler


def _players_reset(rest: list[str], context: ExecutionContext) -> CommandResult:
    if not rest:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    holders = _score_holders(context, rest[0])
    objective = None
    if holders and len(rest) > 1:
        objective = _objective(context, rest[1])
        if objective is None:
            return CommandResult.failure()
    if not holders:
        return CommandResult.failure()
    board = context.world.scoreboard
    for holder in holders:
        board.reset(holder, objective)
    single = len(holders) == 1
    target = _holder_name(context, holders[0]) if single else len(holders)
    amount = "single" if single else "multiple"
    if objective is None:
        context.feedback(f"commands.scoreboard.players.reset.all.{amount}", target)
    else:
        context.feedback(
            f"commands.scoreboard.players.reset.specific.{amount}",
            _shown(context, objective),
            target,
        )
    return CommandResult(success=True, value=len(holders))


def _players_enable(rest: list[str], context: ExecutionContext) -> CommandResult:
    if len(rest) < 2:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    holders = _score_holders(context, rest[0])
    objective = _objective(context, rest[1]) if holders else None
    if objective is None:
        return CommandResult.failure()
    board = context.world.scoreboard
    if board.objectives[objective] != "trigger":
        context.game_error("commands.scoreboard.players.enable.invalid")
        return CommandResult.failure()
    changed = 0
    for holder in holders:
        if (holder, objective) not in board.enabled_triggers:
            board.enabled_triggers.add((holder, objective))
            changed += 1
        if board.get(holder, objective) is None:
            board.set(holder, objective, 0)
    if not changed:
        context.game_error("commands.scoreboard.players.enable.failed")
        return CommandResult.failure()
    if len(holders) == 1:
        context.feedback(
            "commands.scoreboard.players.enable.success.single",
            _shown(context, objective),
            _holder_name(context, holders[0]),
        )
    else:
        context.feedback(
            "commands.scoreboard.players.enable.success.multiple",
            _shown(context, objective),
            len(holders),
        )
    return CommandResult(success=True, value=changed)


def _players_operation(rest: list[str], context: ExecutionContext) -> CommandResult:
    """``operation <targets> <objective> <op> <sources> <objective>``: every target
    is combined with every source in turn; missing scores count as 0 and are created."""
    if len(rest) < 5:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    target_token, target_objective, operator, source_token, source_objective = rest[:5]
    if operator not in _OPERATIONS:
        context.game_error("arguments.operation.invalid")
        return CommandResult.failure()
    targets = _score_holders(context, target_token)
    objective = _objective(context, target_objective, writable=True) if targets else None
    sources = _score_holders(context, source_token) if objective is not None else []
    other = _objective(context, source_objective) if sources else None
    if other is None or objective is None:
        return CommandResult.failure()
    board = context.world.scoreboard
    total = 0
    for target in targets:
        for source in sources:
            # vanilla reads both through getOrCreatePlayerScore: missing ones become 0
            for holder, name in ((target, objective), (source, other)):
                if board.get(holder, name) is None:
                    board.set(holder, name, 0)
            left = board.get(target, objective) or 0
            right = board.get(source, other) or 0
            if operator in ("/=", "%=") and right == 0:
                context.game_error("arguments.operation.div0")
                return CommandResult.failure()
            if operator == "><":
                board.set(source, other, left)
            board.set(target, objective, _OPERATIONS[operator](left, right))
        total += board.get(target, objective) or 0
    if len(targets) == 1:
        context.feedback(
            "commands.scoreboard.players.operation.success.single",
            _shown(context, objective),
            _holder_name(context, targets[0]),
            board.get(targets[0], objective),
        )
    else:
        context.feedback(
            "commands.scoreboard.players.operation.success.multiple",
            _shown(context, objective),
            len(targets),
        )
    return CommandResult(success=True, value=wrap_int(total))


#: Java int arithmetic: floorDiv / floorMod, which Python's // and % already are
_OPERATIONS: dict[str, Callable[[int, int], int]] = {
    "=": lambda left, right: right,
    "+=": lambda left, right: left + right,
    "-=": lambda left, right: left - right,
    "*=": lambda left, right: left * right,
    "/=": lambda left, right: left // right,
    "%=": lambda left, right: left % right,
    "<": min,
    ">": max,
    "><": lambda left, right: right,  # the source gets the old target value
}


def _players_display(rest: list[str], context: ExecutionContext) -> CommandResult:
    """``players display name|numberformat <targets> <objective> ...``: sidebar looks only."""
    if len(rest) < 3:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    holders = _score_holders(context, rest[1])
    if not holders or _objective(context, rest[2]) is None:
        return CommandResult.failure()
    return CommandResult(success=True, value=len(holders))


_SCOREBOARD_PLAYERS = {
    "list": _players_list,
    "get": _players_get,
    "set": _players_set,
    "add": _players_add_or_remove(1),
    "remove": _players_add_or_remove(-1),
    "reset": _players_reset,
    "enable": _players_enable,
    "operation": _players_operation,
    "display": _players_display,
}


def cmd_trigger(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments:
        return CommandResult.failure()
    if context.executor is None or not context.executor.is_player:
        # the console, a command block or a non-player entity cannot trigger
        context.game_error("permissions.requires.player")
        return CommandResult.failure()
    objective = command.arguments[0]
    board = context.world.scoreboard
    holder = context.executor.id
    criterion = board.objectives.get(objective)
    if criterion is None:
        context.game_error("arguments.objective.notFound", objective)
        return CommandResult.failure()
    if criterion != "trigger":
        context.game_error("commands.trigger.failed.invalid")
        return CommandResult.failure()
    if (holder, objective) not in board.enabled_triggers:
        context.game_error("commands.trigger.failed.unprimed")
        return CommandResult.failure()
    mode = command.arguments[1] if len(command.arguments) >= 3 else ""
    amount = integer(context, command.arguments[2]) if mode in ("set", "add") else 1
    if amount is None:
        return CommandResult.failure()
    board.enabled_triggers.discard((holder, objective))
    if mode == "set":
        board.set(holder, objective, amount)
        context.feedback("commands.trigger.set.success", _shown(context, objective), amount)
    elif mode == "add":
        board.add(holder, objective, amount)
        context.feedback("commands.trigger.add.success", _shown(context, objective), amount)
    else:
        board.add(holder, objective, 1)
        context.feedback("commands.trigger.simple.success", _shown(context, objective))
    return CommandResult(success=True, value=1)
