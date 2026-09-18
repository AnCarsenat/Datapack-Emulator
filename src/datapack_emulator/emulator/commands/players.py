"""Player and team commands: gamemode, defaultgamemode, experience/xp, team,
teammsg/tm.

Game modes and experience live in the player's NBT (``playerGameType``,
``XpLevel``, ``XpP``, ``XpTotal``), so ``data get entity`` and selectors see
them; ``level`` and ``xp`` scoreboard criteria follow.
"""

from __future__ import annotations

import math
import re

from datapack_emulator.emulator.commands.helpers import (
    find_holders,
    integer,
    require_targets,
    text_argument,
)
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import load_text_component
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.state import (
    COLLISION,
    GAME_MODES,
    TEAM_COLORS,
    VISIBILITY,
    Team,
    xp_for_next_level,
)
from datapack_emulator.emulator.runtime.world import Entity


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


def player_targets(context: ExecutionContext, token: str | None) -> list[Entity] | None:
    """Player targets (the executor when omitted); None once an error was reported."""
    if token is None:
        if context.executor is None or not context.executor.is_player:
            context.game_error("permissions.requires.player")
            return None
        return [context.executor]
    found = require_targets(context, token)
    if not found:
        return None
    if any(not entity.is_player for entity in found):
        context.game_error("argument.player.entities")
        return None
    return found


# ---------------------------------------------------------------------------
# game modes
# ---------------------------------------------------------------------------


def game_mode(entity: Entity) -> str:
    index = entity.nbt.get("playerGameType", 0)
    return GAME_MODES[index] if isinstance(index, int) and 0 <= index < 4 else "survival"


def cmd_gamemode(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if not arguments or arguments[0] not in GAME_MODES:
        return _usage(context)
    mode = arguments[0]
    players = player_targets(context, arguments[1] if len(arguments) > 1 else None)
    if players is None:
        return CommandResult.failure()
    name = context.render(f"gameMode.{mode}")
    changed = 0
    for player in players:
        if game_mode(player) == mode:
            continue
        player.nbt["previousPlayerGameType"] = GAME_MODES.index(game_mode(player))
        player.nbt["playerGameType"] = GAME_MODES.index(mode)
        changed += 1
        if player is context.executor:
            context.feedback("commands.gamemode.success.self", name)
        else:
            context.feedback("commands.gamemode.success.other", player.display, name)
    return CommandResult(success=True, value=changed)


def cmd_defaultgamemode(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments or command.arguments[0] not in GAME_MODES:
        return _usage(context)
    context.world.state.default_game_mode = command.arguments[0]
    context.feedback(
        "commands.defaultgamemode.success", context.render(f"gameMode.{command.arguments[0]}")
    )
    return CommandResult(success=True, value=0)


# ---------------------------------------------------------------------------
# experience
# ---------------------------------------------------------------------------


def experience(player: Entity) -> tuple[int, int]:
    """``(level, points into that level)``"""
    level = int(player.nbt.get("XpLevel", 0) or 0)
    progress = float(player.nbt.get("XpP", 0.0) or 0.0)
    return level, math.floor(progress * xp_for_next_level(level))


def _sync_criteria(context: ExecutionContext, player: Entity) -> None:
    board = context.world.scoreboard
    for objective, criterion in board.objectives.items():
        if criterion == "level":
            board.set(player.id, objective, int(player.nbt.get("XpLevel", 0)))
        elif criterion == "xp":
            board.set(player.id, objective, int(player.nbt.get("XpTotal", 0)))


def give_points(context: ExecutionContext, player: Entity, amount: int) -> None:
    """Player.giveExperiencePoints: progress moves, levels roll over both ways,
    and the total changes by the amount (never below 0)."""
    level = int(player.nbt.get("XpLevel", 0) or 0)
    progress = float(player.nbt.get("XpP", 0.0) or 0.0)
    total = int(player.nbt.get("XpTotal", 0) or 0)
    progress += amount / xp_for_next_level(level)
    player.nbt["XpTotal"] = max(0, min(total + amount, 2**31 - 1))
    while progress < 0.0:
        remainder = progress * xp_for_next_level(level)
        if level > 0:
            level -= 1
            progress = 1.0 + remainder / xp_for_next_level(level)
        else:
            level, progress = 0, 0.0
    while progress >= 1.0:
        progress = (progress - 1.0) * xp_for_next_level(level)
        level += 1
        progress /= xp_for_next_level(level)
    player.nbt["XpLevel"] = level
    player.nbt["XpP"] = progress
    _sync_criteria(context, player)


def give_levels(context: ExecutionContext, player: Entity, amount: int) -> None:
    """Player.giveExperienceLevels: the progress fraction is kept; below 0
    everything resets."""
    level = int(player.nbt.get("XpLevel", 0) or 0) + amount
    if level < 0:
        player.nbt.update(XpLevel=0, XpP=0.0, XpTotal=0)
    else:
        player.nbt["XpLevel"] = level
    _sync_criteria(context, player)


def set_points(context: ExecutionContext, player: Entity, points: int) -> None:
    level = int(player.nbt.get("XpLevel", 0) or 0)
    needed = xp_for_next_level(level)
    player.nbt["XpP"] = min(points / needed, (needed - 1) / needed)
    _sync_criteria(context, player)


def set_levels(context: ExecutionContext, player: Entity, levels: int) -> None:
    player.nbt["XpLevel"] = levels
    _sync_criteria(context, player)


def cmd_experience(command: Command, context: ExecutionContext) -> CommandResult:
    """``experience add|set <targets> <amount> [levels|points]``,
    ``experience query <target> levels|points``."""
    arguments = command.arguments
    if len(arguments) < 3:
        return _usage(context)
    action = arguments[0]
    if action == "query":
        players = player_targets(context, arguments[1])
        if players is None:
            return CommandResult.failure()
        if len(players) > 1:
            context.game_error("argument.player.toomany")
            return CommandResult.failure()
        level, points = experience(players[0])
        kind = arguments[2]
        if kind not in ("levels", "points"):
            return _usage(context)
        value = level if kind == "levels" else points
        context.feedback(f"commands.experience.query.{kind}", players[0].display, value)
        return CommandResult(success=True, value=value)
    if action not in ("add", "set"):
        return _usage(context)
    amount = integer(context, arguments[2])
    kind = arguments[3] if len(arguments) > 3 else "points"
    if amount is None:
        return CommandResult.failure()
    if kind not in ("levels", "points"):
        return _usage(context)
    if action == "set" and amount < 0:
        context.game_error("argument.integer.low", 0, amount)
        return CommandResult.failure()
    players = player_targets(context, arguments[1])
    if players is None:
        return CommandResult.failure()
    changed = 0
    for player in players:
        level, _ = experience(player)
        if action == "add":
            (give_levels if kind == "levels" else give_points)(context, player, amount)
        elif kind == "levels":
            set_levels(context, player, amount)
        else:
            if amount > xp_for_next_level(level):
                continue
            set_points(context, player, amount)
        changed += 1
    if action == "set" and kind == "points" and not changed:
        context.game_error("commands.experience.set.points.invalid")
        return CommandResult.failure()
    amount_key = "single" if len(players) == 1 else "multiple"
    who = players[0].display if len(players) == 1 else len(players)
    context.feedback(f"commands.experience.{action}.{kind}.success.{amount_key}", amount, who)
    return CommandResult(success=True, value=len(players))


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------


#: what a team name may contain (Brigadier's word)
WORD = re.compile(r"[0-9A-Za-z_\-.+]+")
#: color names without underscores -> the real ones
COLOR_NAMES = {name.replace("_", ""): name for name in TEAM_COLORS}


def _text(context: ExecutionContext, payload: str) -> str | None:
    """A component argument: JSON or SNBT; a bare word is taken as plain text."""
    stripped = payload.strip()
    if stripped[:1] in ("{", "[") and load_text_component(stripped) is None:
        context.game_error("command.unknown.argument")
        return None
    return text_argument(payload)


def _team(context: ExecutionContext, name: str) -> Team | None:
    team = context.world.state.teams.get(name)
    if team is None:
        context.game_error("team.notFound", name)
    return team


def _members(context: ExecutionContext, token: str) -> list[str]:
    """Score holders for team join/leave; ``*`` is every holder the scoreboard knows."""
    holders = find_holders(context, token)
    if not holders:
        if token.startswith("@"):
            require_targets(context, token)
        else:
            context.game_error("argument.scoreHolder.empty")
    return holders


def _holder_display(context: ExecutionContext, holder: str) -> str:
    entity = context.world.entity_by_id(holder)
    return entity.display if entity is not None else holder


def cmd_team(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if not arguments:
        return _usage(context)
    state = context.world.state
    action = arguments[0]
    if action == "list":
        if len(arguments) > 1:
            team = _team(context, arguments[1])
            if team is None:
                return CommandResult.failure()
            if not team.members:
                context.feedback("commands.team.list.members.empty", team.shown)
            else:
                names = ", ".join(team.members)
                context.feedback(
                    "commands.team.list.members.success", team.shown, len(team.members), names
                )
            return CommandResult(success=True, value=len(team.members))
        if not state.teams:
            context.feedback("commands.team.list.teams.empty")
        else:
            names = ", ".join(team.shown for team in state.teams.values())
            context.feedback("commands.team.list.teams.success", len(state.teams), names)
        return CommandResult(success=True, value=len(state.teams))
    if action == "add" and len(arguments) >= 2:
        name = arguments[1]
        if not WORD.fullmatch(name):
            context.game_error("command.unknown.argument")
            return CommandResult.failure()
        if name in state.teams:
            context.game_error("commands.team.add.duplicate")
            return CommandResult.failure()
        display = _text(context, " ".join(arguments[2:])) if len(arguments) > 2 else ""
        if display is None:
            return CommandResult.failure()
        team = state.teams[name] = Team(name, display_name=display)
        context.feedback("commands.team.add.success", team.shown)
        return CommandResult(success=True, value=len(state.teams))
    if action in ("remove", "empty") and len(arguments) >= 2:
        team = _team(context, arguments[1])
        if team is None:
            return CommandResult.failure()
        if action == "remove":
            del state.teams[team.name]
            context.feedback("commands.team.remove.success", team.shown)
            return CommandResult(success=True, value=len(state.teams))
        count = len(team.members)
        if not count:
            context.game_error("commands.team.empty.unchanged")
            return CommandResult.failure()
        team.members.clear()
        context.feedback("commands.team.empty.success", count, team.shown)
        return CommandResult(success=True, value=count)
    if action == "join" and len(arguments) >= 2:
        team = _team(context, arguments[1])
        if team is None:
            return CommandResult.failure()
        if len(arguments) > 2:
            holders = _members(context, arguments[2])
        elif context.executor is not None:
            holders = [context.executor.id]
        else:
            context.game_error("permissions.requires.entity")
            return CommandResult.failure()
        if not holders:
            return CommandResult.failure()
        for holder in holders:
            state.join(holder, team)
        if len(holders) == 1:
            context.feedback(
                "commands.team.join.success.single",
                _holder_display(context, holders[0]),
                team.shown,
            )
        else:
            context.feedback("commands.team.join.success.multiple", len(holders), team.shown)
        return CommandResult(success=True, value=len(holders))
    if action == "leave" and len(arguments) >= 2:
        holders = _members(context, arguments[1])
        if not holders:
            return CommandResult.failure()
        for holder in holders:
            state.leave(holder)
        if len(holders) == 1:
            context.feedback(
                "commands.team.leave.success.single", _holder_display(context, holders[0])
            )
        else:
            context.feedback("commands.team.leave.success.multiple", len(holders))
        return CommandResult(success=True, value=len(holders))
    if action == "modify" and len(arguments) >= 4:
        team = _team(context, arguments[1])
        if team is None:
            return CommandResult.failure()
        return _modify(context, team, arguments[2], " ".join(arguments[3:]))
    return _usage(context)


_TOGGLES = {
    "friendlyFire": ("friendly_fire", "friendlyfire"),
    "seeFriendlyInvisibles": ("see_friendly_invisibles", "seeFriendlyInvisibles"),
}
_CHOICES = {
    "nametagVisibility": ("nametag_visibility", VISIBILITY),
    "deathMessageVisibility": ("death_message_visibility", VISIBILITY),
    "collisionRule": ("collision_rule", COLLISION),
}


def _modify(context: ExecutionContext, team: Team, option: str, value: str) -> CommandResult:
    shown = team.shown
    if option == "displayName":
        text = _text(context, value)
        if text is None:
            return CommandResult.failure()
        if text == team.display_name:
            context.game_error("commands.team.option.name.unchanged")
            return CommandResult.failure()
        team.display_name = text
        context.feedback("commands.team.option.name.success", team.shown)
        return CommandResult(success=True, value=0)
    if option == "color":
        value = re.sub(r"[^a-z]", "", value.lower().replace("_", "")) if value else value
        value = COLOR_NAMES.get(value, value)
        if value not in TEAM_COLORS:
            context.game_error("argument.color.invalid", value)
            return CommandResult.failure()
        if value == team.color:
            context.game_error("commands.team.option.color.unchanged")
            return CommandResult.failure()
        team.color = value
        if value == "reset":
            context.feedback("commands.team.option.color.clear.success", shown)
        else:
            context.feedback("commands.team.option.color.success", shown, value)
        return CommandResult(success=True, value=0)
    if option in _TOGGLES:
        attribute, key = _TOGGLES[option]
        if value not in ("true", "false"):
            context.game_error("parsing.bool.invalid", value)
            return CommandResult.failure()
        wanted = value == "true"
        if getattr(team, attribute) == wanted:
            state = "alreadyEnabled" if wanted else "alreadyDisabled"
            context.game_error(f"commands.team.option.{key}.{state}")
            return CommandResult.failure()
        setattr(team, attribute, wanted)
        context.feedback(f"commands.team.option.{key}.{'enabled' if wanted else 'disabled'}", shown)
        return CommandResult(success=True, value=0)
    if option in _CHOICES:
        attribute, choices = _CHOICES[option]
        if value not in choices:
            context.game_error("command.unknown.argument")
            return CommandResult.failure()
        if getattr(team, attribute) == value:
            context.game_error(f"commands.team.option.{option}.unchanged")
            return CommandResult.failure()
        setattr(team, attribute, value)
        kind = "collision" if option == "collisionRule" else "visibility"
        context.feedback(
            f"commands.team.option.{option}.success", shown, context.render(f"team.{kind}.{value}")
        )
        return CommandResult(success=True, value=0)
    if option in ("prefix", "suffix"):
        text = _text(context, value)
        if text is None:
            return CommandResult.failure()
        setattr(team, option, text)
        context.feedback(f"commands.team.option.{option}.success", text)
        return CommandResult(success=True, value=1)
    return _usage(context)


def cmd_teammsg(command: Command, context: ExecutionContext) -> CommandResult:
    executor = context.executor
    if executor is None:
        context.game_error("permissions.requires.entity")
        return CommandResult.failure()
    team = context.world.state.team_of(executor.id)
    if team is None:
        context.game_error("commands.teammsg.failed.noteam")
        return CommandResult.failure()
    text = " ".join(command.arguments)
    readers = [entity for entity in context.world.players if entity.id in team.members]
    for reader in readers:
        key = "chat.type.team.sent" if reader is executor else "chat.type.team.text"
        context.chat(
            context.render(key, team.shown, executor.display, text), recipient=reader.display
        )
    return CommandResult(success=True, value=len(readers))


PLAYER_HANDLERS = {
    "gamemode": cmd_gamemode,
    "defaultgamemode": cmd_defaultgamemode,
    "experience": cmd_experience,
    "xp": cmd_experience,
    "team": cmd_team,
    "teammsg": cmd_teammsg,
    "tm": cmd_teammsg,
}

__all__ = ["PLAYER_HANDLERS", "experience", "game_mode"]
