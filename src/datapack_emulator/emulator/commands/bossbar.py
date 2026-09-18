"""``bossbar``: custom boss bars, kept in the server state."""

from __future__ import annotations

from dataclasses import dataclass, field

from datapack_emulator.emulator.commands.helpers import integer, require_targets, text_argument
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.context import ExecutionContext

COLORS = ("blue", "green", "pink", "purple", "red", "white", "yellow")
STYLES = ("notched_6", "notched_10", "notched_12", "notched_20", "progress")


@dataclass
class Bossbar:
    id: str
    name: str
    color: str = "white"
    style: str = "progress"
    value: int = 0
    max: int = 100
    visible: bool = True
    #: player names
    players: list[str] = field(default_factory=list)

    @property
    def shown(self) -> str:
        return f"[{self.name}]"


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


def bossbars(context: ExecutionContext) -> dict[str, Bossbar]:
    return context.world.state.bossbars


def _bar(context: ExecutionContext, token: str) -> Bossbar | None:
    bar = bossbars(context).get(normalise_id(token))
    if bar is None:
        context.game_error("commands.bossbar.unknown", normalise_id(token))
    return bar


def cmd_bossbar(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if not arguments:
        return _usage(context)
    bars = bossbars(context)
    action = arguments[0]
    if action == "list":
        if not bars:
            context.feedback("commands.bossbar.list.bars.none")
        else:
            names = ", ".join(bar.shown for bar in bars.values())
            context.feedback("commands.bossbar.list.bars.some", len(bars), names)
        return CommandResult(success=True, value=len(bars))
    if action == "add" and len(arguments) < 3:
        return _usage(context)
    if action == "add":
        bar_id = normalise_id(arguments[1])
        if bar_id in bars:
            context.game_error("commands.bossbar.create.failed", bar_id)
            return CommandResult.failure()
        created = bars[bar_id] = Bossbar(bar_id, text_argument(" ".join(arguments[2:])))
        context.feedback("commands.bossbar.create.success", created.shown)
        return CommandResult(success=True, value=len(bars))
    if len(arguments) < 2:
        return _usage(context)
    bar = _bar(context, arguments[1])
    if bar is None:
        return CommandResult.failure()
    if action == "remove":
        del bars[bar.id]
        context.feedback("commands.bossbar.remove.success", bar.shown)
        return CommandResult(success=True, value=len(bars))
    if action == "get" and len(arguments) >= 3:
        what = arguments[2]
        if what == "value":
            context.feedback("commands.bossbar.get.value", bar.shown, bar.value)
            return CommandResult(success=True, value=bar.value)
        if what == "max":
            context.feedback("commands.bossbar.get.max", bar.shown, bar.max)
            return CommandResult(success=True, value=bar.max)
        if what == "visible":
            state = "visible" if bar.visible else "hidden"
            context.feedback(f"commands.bossbar.get.visible.{state}", bar.shown)
            return CommandResult(success=True, value=int(bar.visible))
        if what == "players":
            online = [name for name in bar.players if context.world.entity_by_id(name)]
            if online:
                context.feedback(
                    "commands.bossbar.get.players.some", bar.shown, len(online), ", ".join(online)
                )
            else:
                context.feedback("commands.bossbar.get.players.none", bar.shown)
            return CommandResult(success=True, value=len(online))
        return _usage(context)
    if action == "set" and len(arguments) >= 3:
        return _set(context, bar, arguments[2], arguments[3:])
    return _usage(context)


def _unchanged(context: ExecutionContext, key: str) -> CommandResult:
    context.game_error(key)
    return CommandResult.failure()


def _set(context: ExecutionContext, bar: Bossbar, what: str, rest: list[str]) -> CommandResult:
    if what == "players":
        if rest:
            found = require_targets(context, rest[0])
            if not found:
                return CommandResult.failure()
            if any(not entity.is_player for entity in found):
                context.game_error("argument.player.entities")
                return CommandResult.failure()
            names = [entity.display for entity in found]
        else:
            names = []
        if sorted(names) == sorted(bar.players):
            return _unchanged(context, "commands.bossbar.set.players.unchanged")
        bar.players = names
        if names:
            context.feedback(
                "commands.bossbar.set.players.success.some", bar.shown, len(names), ", ".join(names)
            )
        else:
            context.feedback("commands.bossbar.set.players.success.none", bar.shown)
        return CommandResult(success=True, value=len(names))
    if not rest:
        return _usage(context)
    if what == "name":
        name = text_argument(" ".join(rest))
        if name == bar.name:
            return _unchanged(context, "commands.bossbar.set.name.unchanged")
        bar.name = name
        context.feedback("commands.bossbar.set.name.success", bar.shown)
        return CommandResult(success=True, value=0)
    if what in ("color", "style"):
        choices = COLORS if what == "color" else STYLES
        if rest[0] not in choices:
            return _usage(context)
        if getattr(bar, what) == rest[0]:
            return _unchanged(context, f"commands.bossbar.set.{what}.unchanged")
        setattr(bar, what, rest[0])
        context.feedback(f"commands.bossbar.set.{what}.success", bar.shown)
        return CommandResult(success=True, value=0)
    if what in ("value", "max"):
        number = integer(context, rest[0])
        if number is None:
            return CommandResult.failure()
        low = 0 if what == "value" else 1
        if number < low:
            context.game_error("argument.integer.low", low, number)
            return CommandResult.failure()
        if getattr(bar, what) == number:
            return _unchanged(context, f"commands.bossbar.set.{what}.unchanged")
        setattr(bar, what, number)
        context.feedback(f"commands.bossbar.set.{what}.success", bar.shown, number)
        return CommandResult(success=True, value=number)
    if what == "visible":
        if rest[0] not in ("true", "false"):
            context.game_error("parsing.bool.invalid", rest[0])
            return CommandResult.failure()
        visible = rest[0] == "true"
        state = "visible" if visible else "hidden"
        if bar.visible == visible:
            return _unchanged(context, f"commands.bossbar.set.visibility.unchanged.{state}")
        bar.visible = visible
        context.feedback(f"commands.bossbar.set.visible.success.{state}", bar.shown)
        return CommandResult(success=True, value=0)
    return _usage(context)


def store_bossbar(context: ExecutionContext, bar_id: str, what: str, value: int) -> None:
    """``execute store … bossbar <id> value|max``"""
    bar = bossbars(context).get(normalise_id(bar_id))
    if bar is not None and what in ("value", "max"):
        setattr(bar, what, value)  # stored as it is, unlike the set subcommand


BOSSBAR_HANDLERS = {"bossbar": cmd_bossbar}
