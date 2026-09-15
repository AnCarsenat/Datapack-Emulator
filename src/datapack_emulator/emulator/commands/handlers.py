"""Command implementations.

Each handler takes ``(command, context)`` and returns a
:class:`~datapack_emulator.emulator.commands.result.CommandResult`.  Handlers speak to the
player through ``context.chat`` / ``context.feedback`` / ``context.game_error``
so that game output stays separate from emulator diagnostics.

``noop`` covers commands that are dispatched and costed but change no state:
the world model has no blocks or inventories to change.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from datapack_emulator.emulator.commands.parser import (
    Command,
    Selector,
    Subcommand,
    parse_duration,
    resolve_position,
)
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import (
    as_int,
    flatten_text_component,
    load_text_component,
    merge_compound,
    nbt_get,
    nbt_remove,
    nbt_set,
    normalise_id,
    normalise_tagged_id,
    parse_snbt,
    parse_value,
)
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.world import Entity

Handler = Callable[[Command, ExecutionContext], CommandResult]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _targets(context: ExecutionContext, token: str) -> list[Entity]:
    return context.world.select(Selector.parse(token), context)


def _require_targets(context: ExecutionContext, token: str) -> list[Entity]:
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


def _known_id(context: ExecutionContext, registry: str, resource_id: str) -> bool:
    """Check an id against the loaded client jar; ``True`` when nothing to check.

    Without vanilla assets there is no registry to check against, so the
    command is let through rather than guessed at.
    """
    assets = context.emulator.vanilla
    if assets is None or resource_id.startswith(("#", "$", "@")):
        return True
    known = assets.knows(registry, resource_id)
    return True if known is None else known


def _require_id(
    context: ExecutionContext, registry: str, resource_id: str, key: str = "argument.id.unknown"
) -> bool:
    """Same, but reports the vanilla error for an id the version does not have."""
    if _known_id(context, registry, resource_id):
        return True
    context.game_error(key, resource_id)
    return False


def _holders(context: ExecutionContext, token: str) -> list[str]:
    """Score holders: entities from a selector, ``*`` for every tracked holder,
    or a fake player name."""
    if token.startswith("@"):
        return [entity.id for entity in _targets(context, token)]
    if token == "*":
        return context.world.scoreboard.tracked()
    return [token]


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def cmd_say(command: Command, context: ExecutionContext) -> CommandResult:
    who = context.executor.display if context.executor else "Server"
    context.chat(f"[{who}] {' '.join(command.arguments)}")
    return CommandResult(success=True, value=1)


def cmd_me(command: Command, context: ExecutionContext) -> CommandResult:
    who = context.executor.display if context.executor else "Server"
    context.chat(f"* {who} {' '.join(command.arguments)}")
    return CommandResult(success=True, value=1)


def cmd_msg(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    targets = _require_targets(context, command.arguments[0])
    text = " ".join(command.arguments[1:])
    for entity in targets:
        context.chat(f"[whisper -> {entity.display}] {text}")
    return CommandResult(success=bool(targets), value=len(targets))


def cmd_tellraw(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    component = load_text_component(" ".join(command.arguments[1:]))
    if component is None:
        context.game_error("command.unknown.argument")
        return CommandResult.failure()
    targets = _require_targets(context, command.arguments[0])
    for entity in targets:
        text = flatten_text_component(component, _text_resolver(context, entity))
        context.chat(f"[{entity.display}] {text}")
    return CommandResult(success=bool(targets), value=len(targets))


def _text_resolver(context: ExecutionContext, viewer: Entity | None):
    """Scores and selectors inside a text component, as ``viewer`` would see them."""

    def resolve(kind: str, value: Any) -> str:
        if kind == "score" and isinstance(value, dict):
            name = str(value.get("name", ""))
            if name == "*":
                holders = [viewer.id] if viewer is not None else []
            else:
                holders = _holders(context, name)
            if not holders:
                return ""
            score = context.world.scoreboard.get(holders[0], str(value.get("objective", "")))
            return "" if score is None else str(score)
        if kind == "selector":
            return ", ".join(entity.display for entity in _targets(context, str(value)))
        return ""

    return resolve


def cmd_title(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 2:
        return CommandResult.failure()
    targets = _require_targets(context, command.arguments[0])
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
            context.chat(f"[{entity.display} {action}] {text}")
    return CommandResult(success=bool(targets), value=len(targets))


# ---------------------------------------------------------------------------
# scoreboard
# ---------------------------------------------------------------------------


def cmd_scoreboard(command: Command, context: ExecutionContext) -> CommandResult:
    board = context.world.scoreboard
    arguments = command.arguments
    if not arguments:
        return CommandResult.failure()

    if arguments[0] == "objectives":
        action = arguments[1] if len(arguments) > 1 else ""
        if action == "add" and len(arguments) >= 3:
            criterion = arguments[3] if len(arguments) > 3 else "dummy"
            if board.add_objective(arguments[2], criterion):
                context.feedback("commands.scoreboard.objectives.add.success", arguments[2])
                return CommandResult(success=True, value=1)
            context.game_error("commands.scoreboard.objectives.add.duplicate")
            return CommandResult.failure()
        if action == "remove" and len(arguments) >= 3:
            return CommandResult(success=board.remove_objective(arguments[2]), value=1)
        return CommandResult(success=True, value=len(board.objectives))

    if arguments[0] == "players":
        action = arguments[1] if len(arguments) > 1 else ""
        if action == "display":
            # players display (name|numberformat) <targets> <objective> [...]
            objective = arguments[4] if len(arguments) > 4 else ""
            if objective not in board.objectives:
                context.game_error("arguments.objective.notFound", objective)
                return CommandResult.failure()
            return CommandResult(success=True, value=1)  # display is not modelled
        target = arguments[2] if len(arguments) > 2 else "@s"
        objective = arguments[3] if len(arguments) > 3 else ""
        if objective and objective not in board.objectives and action != "reset":
            context.game_error("arguments.objective.notFound", objective)
            return CommandResult.failure()
        holders = _holders(context, target)

        if action == "set" and len(arguments) >= 5:
            value = int(arguments[4])
            for holder in holders:
                board.set(holder, objective, value)
            if holders:
                context.feedback(
                    "commands.scoreboard.players.set.success.single",
                    objective,
                    holders[0],
                    value,
                )
            return CommandResult(success=bool(holders), value=value)

        if action in ("add", "remove") and len(arguments) >= 5:
            delta = int(arguments[4]) * (1 if action == "add" else -1)
            last = 0
            for holder in holders:
                last = board.add(holder, objective, delta)
            if holders:
                context.feedback(
                    "commands.scoreboard.players.add.success.single",
                    abs(delta),
                    objective,
                    holders[0],
                    last,
                )
            return CommandResult(success=bool(holders), value=last)

        if action == "reset":
            for holder in holders:
                board.reset(holder, objective or None)
            return CommandResult(success=bool(holders), value=len(holders))

        if action == "get":
            if not holders:
                context.game_error("argument.scoreHolder.empty")
                return CommandResult.failure()
            value = board.get(holders[0], objective)
            if value is None:
                context.game_error("commands.scoreboard.players.get.null", objective, holders[0])
                return CommandResult.failure()
            context.feedback(
                "commands.scoreboard.players.get.success", holders[0], value, objective
            )
            return CommandResult(success=True, value=value)

        if action == "enable":
            for holder in holders:
                board.enabled_triggers.add((holder, objective))
                if board.get(holder, objective) is None:
                    board.set(holder, objective, 0)
            return CommandResult(success=bool(holders), value=len(holders))

        if action == "operation" and len(arguments) >= 7:
            operator = arguments[4]
            sources = _holders(context, arguments[5])
            source_objective = arguments[6]
            last = 0
            for holder in holders:
                left = board.get(holder, objective) or 0
                right = (board.get(sources[0], source_objective) or 0) if sources else 0
                if operator in ("/=", "%=") and right == 0:
                    context.game_error("arguments.operation.div0")
                    return CommandResult.failure()
                if operator == "><" and sources:
                    board.set(sources[0], source_objective, left)  # swap both sides
                last = _apply_operation(operator, left, right)
                board.set(holder, objective, last)
            return CommandResult(success=bool(holders), value=last)
    return CommandResult.failure()


def _apply_operation(operator: str, left: int, right: int) -> int:
    return {
        "=": right,
        "+=": left + right,
        "-=": left - right,
        "*=": left * right,
        "/=": left // right if right else left,
        "%=": left % right if right else left,
        "<": min(left, right),
        ">": max(left, right),
        "><": right,  # swap: the caller writes the other side
    }.get(operator, left)


def cmd_trigger(command: Command, context: ExecutionContext) -> CommandResult:
    if not command.arguments or context.executor is None:
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
    if len(command.arguments) >= 3 and command.arguments[1] == "set":
        board.set(holder, objective, int(command.arguments[2]))
    elif len(command.arguments) >= 3 and command.arguments[1] == "add":
        board.add(holder, objective, int(command.arguments[2]))
    else:
        board.add(holder, objective, 1)
    board.enabled_triggers.discard((holder, objective))
    context.feedback("commands.trigger.simple.success", objective)
    return CommandResult(success=True, value=1)


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


def cmd_tag(command: Command, context: ExecutionContext) -> CommandResult:
    if len(command.arguments) < 3:
        return CommandResult.failure()
    targets = _require_targets(context, command.arguments[0])
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
        return CommandResult.failure()
    entity_type = normalise_id(command.arguments[0])
    if not _require_id(context, "entity_type", entity_type):
        return CommandResult.failure()
    position = resolve_position(command.arguments[1:4], context.position)
    nbt: dict[str, Any] = {}
    tags: set[str] = set()
    for argument in command.arguments[1:]:
        if argument.startswith("{"):
            nbt = parse_snbt(argument)
            for tag in nbt.get("Tags", []) or []:
                tags.add(str(tag))
            break
    entity = Entity(type=entity_type, position=position, nbt=nbt, tags=tags)
    context.world.spawn(entity)
    context.feedback("commands.summon.success", entity.display)
    return CommandResult(success=True, value=1)


def cmd_kill(command: Command, context: ExecutionContext) -> CommandResult:
    token = command.arguments[0] if command.arguments else "@s"
    victims = _require_targets(context, token)
    for entity in victims:
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
    if len(arguments) >= 4:  # tp <targets> <x y z> [rotation|facing ...]
        targets = _require_targets(context, arguments[0])
        destination = resolve_position(arguments[1:4], context.position)
    elif len(arguments) == 3:  # tp <x y z>
        targets = [context.executor] if context.executor else []
        destination = resolve_position(arguments, context.position)
    else:  # tp <destination entity> / tp <targets> <destination entity>
        targets = (
            _require_targets(context, arguments[0])
            if len(arguments) == 2
            else ([context.executor] if context.executor else [])
        )
        anchor = _require_targets(context, arguments[-1])
        if not anchor:
            return CommandResult.failure()
        destination = list(anchor[0].position)
    for entity in targets:
        if entity is not None:
            entity.position = list(destination)
    if len(targets) == 1 and targets[0] is not None:
        context.feedback(
            "commands.teleport.success.entity.single",
            targets[0].display,
            ", ".join(f"{value:.1f}" for value in destination),
        )
    return CommandResult(success=bool(targets), value=len(targets))


# ---------------------------------------------------------------------------
# control flow
# ---------------------------------------------------------------------------


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
    path = rest[3] if len(rest) > 3 else ""
    if source == "storage":
        store: Any = context.world.storage.get(normalise_id(target), {})
    elif source == "entity":
        entities = _targets(context, target)
        store = entities[0].nbt if entities else {}
    else:
        context.note_key_once(
            "emulator.unimplemented", f"function ... with {source}", context.emulator.version.id
        )
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
        result = context.emulator.run_command(inner, context) if inner else CommandResult.failure()
        return CommandResult(success=result.success, value=result.value, returned=True)
    if command.arguments and command.arguments[0] == "fail":
        return CommandResult(success=False, value=0, returned=True)
    value = int(command.arguments[0]) if command.arguments else 0
    return CommandResult(success=True, value=value, returned=True)


# ---------------------------------------------------------------------------
# data / storage
# ---------------------------------------------------------------------------


def cmd_data(command: Command, context: ExecutionContext) -> CommandResult:
    """``data get|merge|modify|remove`` on entities and storage."""
    arguments = command.arguments
    if len(arguments) < 3:
        return CommandResult.failure()
    action, holder_kind, target_token = arguments[0], arguments[1], arguments[2]

    if holder_kind == "storage":
        stores: list[dict[str, Any]] = [
            context.world.storage.setdefault(normalise_id(target_token), {})
        ]
    elif holder_kind == "entity":
        stores = [entity.nbt for entity in _require_targets(context, target_token)]
    else:  # block NBT is not modelled
        context.note_key_once(
            "emulator.unimplemented", f"data {action} block", context.emulator.version.id
        )
        return CommandResult.failure()

    if not stores:
        return CommandResult.failure()

    if action == "get":
        path = arguments[3] if len(arguments) > 3 else ""
        value = nbt_get(stores[0], path) if path else stores[0]
        if value is None:
            context.game_error("commands.data.get.unknown", path)
            return CommandResult.failure()
        scale = _float_or_none(arguments[4]) if len(arguments) > 4 else None
        if scale is not None and not isinstance(value, (int, float)):
            context.game_error("commands.data.get.invalid", path)
            return CommandResult.failure()
        return CommandResult(success=True, value=as_int(value, scale))

    if action == "merge" and len(arguments) >= 4:
        payload = parse_snbt(" ".join(arguments[3:]))
        changed = sum(1 for store in stores if merge_compound(store, payload))
        if not changed:
            context.game_error("commands.data.merge.failed")
            return CommandResult.failure()
        return CommandResult(success=True, value=changed)

    if action == "remove" and len(arguments) >= 4:
        removed = sum(1 for store in stores if nbt_remove(store, arguments[3]))
        return CommandResult(success=removed > 0, value=removed)

    if action == "modify" and len(arguments) >= 6:
        return _data_modify(context, stores, arguments[3], arguments[4], arguments[5:])
    return CommandResult.failure()


def _data_modify(
    context: ExecutionContext,
    stores: list[dict[str, Any]],
    path: str,
    operation: str,
    source: list[str],
) -> CommandResult:
    """``data modify <target> <path> <operation> (value|from|string) ...``"""
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
            return CommandResult.failure()
        if source[0] == "string":  # 1.19.4+: set string <source> [path] [start] [end]
            if isinstance(value, (dict, list)):
                context.game_error("commands.data.modify.expected_value", value)
                return CommandResult.failure()
            value = _substring(value, source[4:6] if len(source) > 3 else [])
    else:
        return CommandResult.failure()

    changed = 0
    for store in stores:
        current = nbt_get(store, path)
        if operation == "set":
            nbt_set(store, path, copy.deepcopy(value))
            changed += 1
        elif operation == "merge":
            if not isinstance(value, dict):
                context.game_error("commands.data.modify.expected_object", value)
                return CommandResult.failure()
            if current is None:
                nbt_set(store, path, copy.deepcopy(value))
                changed += 1
            elif not isinstance(current, dict):
                context.game_error("commands.data.modify.expected_object", current)
                return CommandResult.failure()
            elif merge_compound(current, value):
                changed += 1
        elif operation in ("append", "prepend", "insert"):
            if current is None:
                current = []
                nbt_set(store, path, current)
            elif not isinstance(current, list):
                context.game_error("commands.data.modify.expected_list", current)
                return CommandResult.failure()
            position = {"append": len(current), "prepend": 0}.get(operation, index)
            current.insert(position, copy.deepcopy(value))
            changed += 1
    return CommandResult(success=changed > 0, value=changed)


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
    """Read ``(storage <id> | entity <selector>) [<path>]`` for ``modify ... from``."""
    if len(source) < 2:
        return (False, None)
    kind, target = source[0], source[1]
    path = source[2] if len(source) > 2 else ""
    if kind == "storage":
        store: Any = context.world.storage.get(normalise_id(target), {})
    elif kind == "entity":
        entities = _require_targets(context, target)
        if not entities:
            return (False, None)
        store = entities[0].nbt
    else:  # block NBT is not modelled
        context.note_key_once(
            "emulator.unimplemented", "data modify ... from block", context.emulator.version.id
        )
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


def cmd_noop(command: Command, context: ExecutionContext) -> CommandResult:
    """Counted and costed, but no world state change is modelled."""
    return CommandResult(success=True, value=1)


def _resource_id(token: str) -> str:
    """``minecraft:stone[facing=up]{...}`` -> ``minecraft:stone``.

    Block states, item components, NBT and particle options all follow the id
    directly, so everything from the first ``[`` or ``{`` on is dropped.
    """
    cut = min((token.index(mark) for mark in "[{" if mark in token), default=len(token))
    return token[:cut]


def _checking(registry: str, index: int, key: str) -> Handler:
    """A no-op handler that still checks the id at ``index`` against the jar."""

    def handler(command: Command, context: ExecutionContext) -> CommandResult:
        if len(command.arguments) > index:
            resource = _resource_id(command.arguments[index])
            if not _require_id(context, registry, resource, key):
                return CommandResult.failure()
        return CommandResult(success=True, value=1)

    return handler


def cmd_effect(command: Command, context: ExecutionContext) -> CommandResult:
    """``effect give <targets> <effect>`` / ``effect clear``."""
    arguments = command.arguments
    if (
        len(arguments) >= 3
        and arguments[0] == "give"
        and not _require_id(context, "mob_effect", arguments[2])
    ):
        return CommandResult.failure()
    return CommandResult(success=True, value=1)


def cmd_setblock(command: Command, context: ExecutionContext) -> CommandResult:
    """Only the block id is checked; there is no block model to change."""
    if len(command.arguments) >= 4:
        block = _resource_id(command.arguments[3])
        if not _require_id(context, "block", block, "argument.block.id.invalid"):
            return CommandResult.failure()
    return CommandResult(success=True, value=1)


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


#: a store waiting for the command's result, bound to the context it appeared in
PendingStore = tuple[Subcommand, ExecutionContext]


def cmd_execute(command: Command, context: ExecutionContext) -> CommandResult:
    # each branch carries the stores seen so far, each bound to the context at
    # its position in the chain (vanilla records the location to store in there)
    branches: list[tuple[ExecutionContext, tuple[PendingStore, ...]]] = [(context, ())]
    subcommands = command.subcommands
    trailing_test = (
        command.child is None
        and bool(subcommands)
        and subcommands[-1].name
        in (
            "if",
            "unless",
        )
    )
    chain = subcommands[:-1] if trailing_test else subcommands

    for subcommand in chain:
        next_branches: list[tuple[ExecutionContext, tuple[PendingStore, ...]]] = []
        for current, stores in branches:
            for successor in _execute_step(subcommand.name, subcommand.arguments, current, context):
                pending = (
                    stores + ((subcommand, current),) if subcommand.name == "store" else stores
                )
                next_branches.append((successor, pending))
        branches = next_branches
        if not branches:
            break

    if command.child is None:
        # `execute ... if|unless <cond>` with no `run` is a test; its result is
        # the condition's count (how many entities matched for `if entity`)
        last = subcommands[-1] if trailing_test else None
        total = 0
        for current, stores in branches:
            if last is None:
                count = 1
            elif last.name == "if":
                count = condition_count(last.arguments, current)
            else:
                count = int(not evaluate_condition(last.arguments, current))
            total += count
            result = CommandResult(success=count > 0, value=count)
            for subcommand, bound in stores:
                _apply_store(subcommand, bound, result)
        if total:
            context.feedback("commands.execute.conditional.pass_count", total)
        else:
            context.feedback("commands.execute.conditional.fail")
        return CommandResult(success=total > 0, value=total)

    successes = 0
    total = 0
    for current, stores in branches:
        result = context.emulator.run_command(command.child, current)
        if result.success:
            successes += 1
        total += result.value
        for subcommand, bound in stores:
            _apply_store(subcommand, bound, result)
        if result.returned:
            # `execute ... run return` leaves the function on the first context
            # that reaches it; the remaining contexts never run.
            return CommandResult(success=result.success, value=result.value, returned=True)
    return CommandResult(success=successes > 0, value=total if total else successes)


def _execute_step(
    name: str, arguments: list[str], current: ExecutionContext, origin: ExecutionContext
) -> list[ExecutionContext]:
    """The contexts one ``execute`` subcommand turns ``current`` into."""
    world = origin.world
    if name == "as" and arguments:
        return [
            current.branch(executor=entity)
            for entity in world.select(Selector.parse(arguments[0]), current)
        ]
    if name == "at" and arguments:
        return [
            current.branch(
                position=list(entity.position),
                rotation=list(entity.rotation),
                dimension=entity.dimension,
            )
            for entity in world.select(Selector.parse(arguments[0]), current)
        ]
    if name == "positioned":
        if arguments and arguments[0] == "as" and len(arguments) > 1:
            return [
                current.branch(position=list(entity.position))
                for entity in world.select(Selector.parse(arguments[1]), current)
            ]
        return [current.branch(position=resolve_position(arguments, current.position))]
    if name == "rotated":
        if arguments and arguments[0] == "as" and len(arguments) > 1:
            return [
                current.branch(rotation=list(entity.rotation))
                for entity in world.select(Selector.parse(arguments[1]), current)
            ]
        return [current]
    if name == "in" and arguments:
        return [current.branch(dimension=normalise_id(arguments[0]))]
    if name in ("if", "unless"):
        return [current] if evaluate_condition(arguments, current) == (name == "if") else []
    if name == "summon" and arguments:
        entity = world.spawn(
            Entity(type=normalise_id(arguments[0]), position=list(current.position))
        )
        return [current.branch(executor=entity)]
    if name == "on":
        # vehicles, passengers, owners, leashes and attackers are not modelled:
        # vanilla ends the branch when the relation does not apply
        current.note_once(
            f"'execute on {' '.join(arguments)}': entity relations are not emulated, "
            "so the branch ends"
        )
        return []
    # store (bound by the caller), anchored, align, facing: nothing to change
    return [current]


def _apply_store(subcommand: Subcommand, context: ExecutionContext, result: CommandResult) -> None:
    arguments = subcommand.arguments
    if len(arguments) < 4:
        return
    mode, target = arguments[0], arguments[1]
    value = result.value if mode == "result" else int(result.success)
    if target == "score":
        for holder in _holders(context, arguments[2]):
            context.world.scoreboard.set(holder, arguments[3], value)
    elif target == "storage":
        store = context.world.storage.setdefault(normalise_id(arguments[2]), {})
        nbt_set(store, arguments[3], _stored_number(value, arguments[4:6]))
    elif target == "entity":
        for entity in _targets(context, arguments[2]):
            nbt_set(entity.nbt, arguments[3], _stored_number(value, arguments[4:6]))


def _stored_number(value: int, type_and_scale: list[str]) -> int | float:
    """``store ... <path> <type> <scale>``: integer types truncate, like vanilla."""
    numeric_type = type_and_scale[0] if type_and_scale else "int"
    try:
        scale = float(type_and_scale[1]) if len(type_and_scale) > 1 else 1.0
    except ValueError:
        scale = 1.0
    scaled = value * scale
    if numeric_type in ("float", "double"):
        return scaled
    return int(scaled)  # byte, short, int, long


def condition_count(arguments: list[str], context: ExecutionContext) -> int:
    """The value a passing ``if`` condition reports: matched entities for
    ``entity``, otherwise 1 (0 when it fails)."""
    if arguments and arguments[0] == "entity" and len(arguments) >= 2:
        return len(context.world.select(Selector.parse(arguments[1]), context))
    return int(evaluate_condition(arguments, context))


def evaluate_condition(arguments: list[str], context: ExecutionContext) -> bool:
    if not arguments:
        return False
    kind = arguments[0]
    board = context.world.scoreboard

    if kind == "score" and len(arguments) >= 5:
        holders = _holders(context, arguments[1])
        if not holders:
            return False
        value = board.get(holders[0], arguments[2])
        if value is None:
            return False
        if arguments[3] == "matches":
            from datapack_emulator.emulator.common import in_range

            return in_range(value, arguments[4])
        if len(arguments) >= 6:
            others = _holders(context, arguments[4])
            if not others:
                return False
            other = board.get(others[0], arguments[5])
            if other is None:
                return False
            return {
                "<": value < other,
                "<=": value <= other,
                "=": value == other,
                ">=": value >= other,
                ">": value > other,
            }.get(arguments[3], False)
        return False

    if kind == "entity" and len(arguments) >= 2:
        return bool(context.world.select(Selector.parse(arguments[1]), context))

    if kind == "data" and len(arguments) >= 4:
        if arguments[1] == "storage":
            store = context.world.storage.get(normalise_id(arguments[2]), {})
            return nbt_get(store, arguments[3]) is not None
        if arguments[1] == "entity":
            return any(
                nbt_get(entity.nbt, arguments[3]) is not None
                for entity in _targets(context, arguments[2])
            )
        return False

    if kind == "dimension" and len(arguments) >= 2:
        return context.dimension == normalise_id(arguments[1])

    if kind == "loaded":
        return True

    if kind == "function" and len(arguments) >= 2:
        target = normalise_tagged_id(arguments[1])
        ids = context.emulator.library.resolve_tag(target) if target.startswith("#") else [target]
        # vanilla: passes on the first function that returns a non-zero value;
        # a function that ends without `return` does not count
        for function_id in ids:
            result = context.emulator.run_function(function_id, context)
            if result.has_return and result.success and result.value != 0:
                return True
        return False

    context.note_key_once("emulator.condition", kind)
    return False


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

#: command name -> handler.  Anything vanilla knows but this table does not is
#: reported as "exists in <version> but is not emulated".
HANDLERS: dict[str, Handler] = {
    "say": cmd_say,
    "me": cmd_me,
    "msg": cmd_msg,
    "tell": cmd_msg,
    "w": cmd_msg,
    "teammsg": cmd_say,
    "tm": cmd_say,
    "tellraw": cmd_tellraw,
    "title": cmd_title,
    "scoreboard": cmd_scoreboard,
    "trigger": cmd_trigger,
    "tag": cmd_tag,
    "summon": cmd_summon,
    "kill": cmd_kill,
    "tp": cmd_teleport,
    "teleport": cmd_teleport,
    "execute": cmd_execute,
    "function": cmd_function,
    "schedule": cmd_schedule,
    "return": cmd_return,
    "data": cmd_data,
    "gamerule": cmd_gamerule,
    # dispatched and costed, but no state change is modelled
    "advancement": cmd_noop,
    "attribute": cmd_noop,
    "bossbar": cmd_noop,
    "clear": _checking("item", 1, "argument.item.id.invalid"),
    "clone": cmd_noop,
    "damage": cmd_noop,
    "difficulty": cmd_noop,
    "effect": cmd_effect,
    "enchant": cmd_noop,
    "experience": cmd_noop,
    "fill": cmd_noop,
    "fillbiome": cmd_noop,
    "forceload": cmd_noop,
    "gamemode": cmd_noop,
    "give": _checking("item", 1, "argument.item.id.invalid"),
    "item": cmd_noop,
    "loot": cmd_noop,  # "loot give @s loot <id>" needs the whole grammar to check
    "particle": _checking("particle", 0, "argument.id.unknown"),
    "place": cmd_noop,
    "playsound": cmd_noop,
    "random": cmd_noop,
    "recipe": cmd_noop,
    "replaceitem": cmd_noop,
    "ride": cmd_noop,
    "rotate": cmd_noop,
    "setblock": cmd_setblock,
    "setworldspawn": cmd_noop,
    "spawnpoint": cmd_noop,
    "spectate": cmd_noop,
    "spreadplayers": cmd_noop,
    "stopsound": cmd_noop,
    "team": cmd_noop,
    "tick": cmd_noop,
    "time": cmd_noop,
    "weather": cmd_noop,
    "worldborder": cmd_noop,
    "xp": cmd_noop,
}
