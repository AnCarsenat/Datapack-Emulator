"""Helpers every command module uses: selectors, ids checked against the
client jar, score holders, integer arguments."""

from __future__ import annotations

from collections.abc import Callable

from datapack_emulator.emulator.commands.parser import Command, Selector
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import flatten_text_component, load_text_component
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity

Handler = Callable[[Command, ExecutionContext], CommandResult]


def find_targets(context: ExecutionContext, token: str) -> list[Entity]:
    return context.world.select(Selector.parse(token), context)


def require_targets(context: ExecutionContext, token: str) -> list[Entity]:
    """Resolve a selector, reporting vanilla's "No entity was found" if empty."""
    selector = Selector.parse(token)
    found = context.world.select(selector, context)
    if not found:
        key = (
            "argument.entity.notfound.player"
            if selector.is_player_only
            else "argument.entity.notfound.entity"
        )
        context.game_error(key)
    return found


def known_id(context: ExecutionContext, registry: str, resource_id: str) -> bool:
    """Check an id against the loaded client jar; ``True`` when nothing to check.

    Without vanilla assets there is no registry to check against, so the
    command is let through rather than guessed at.
    """
    assets = context.emulator.vanilla
    if assets is None or resource_id.startswith(("#", "$", "@")):
        return True
    known = assets.knows(registry, resource_id)
    return True if known is None else known


def require_id(
    context: ExecutionContext, registry: str, resource_id: str, key: str = "argument.id.unknown"
) -> bool:
    """Same, but reports the vanilla error for an id the version does not have."""
    if known_id(context, registry, resource_id):
        return True
    context.game_error(key, resource_id)
    return False


def find_holders(context: ExecutionContext, token: str) -> list[str]:
    """Score holders: entities from a selector, ``*`` for every tracked holder,
    or a fake player name."""
    if token.startswith("@"):
        return [entity.id for entity in find_targets(context, token)]
    if token == "*":
        return context.world.scoreboard.tracked()
    return [token]


def integer(context: ExecutionContext, token: str) -> int | None:
    """A Java int argument; vanilla rejects anything else before running."""
    try:
        value = int(token)
    except ValueError:
        context.game_error("parsing.int.invalid", token)
        return None
    if not -(2**31) <= value < 2**31:
        context.game_error("parsing.int.invalid", token)
        return None
    return value


def resource_id(token: str) -> str:
    """``minecraft:stone[facing=up]{...}`` -> ``minecraft:stone``.

    Block states, item components, NBT and particle options all follow the id
    directly, so everything from the first ``[`` or ``{`` on is dropped.
    """
    cut = min((token.index(mark) for mark in "[{" if mark in token), default=len(token))
    return token[:cut]


def checking(registry: str, index: int, key: str) -> Handler:
    """A no-op handler that still checks the id at ``index`` against the jar."""

    def handler(command: Command, context: ExecutionContext) -> CommandResult:
        if len(command.arguments) > index:
            resource = resource_id(command.arguments[index])
            if not require_id(context, registry, resource, key):
                return CommandResult.failure()
        return CommandResult(success=True, value=1)

    return handler


def text_argument(payload: str) -> str:
    """A text component argument as plain text (a bare word is kept as it is)."""
    component = load_text_component(payload)
    return payload.strip('"') if component is None else flatten_text_component(component)
