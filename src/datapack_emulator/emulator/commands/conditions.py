"""Predicates and advancements in commands: ``execute if predicate``, the
``predicate=``/``advancements=`` selector arguments, the ``advancement``
command, and the evaluation context every loot table and item modifier gets.
"""

from __future__ import annotations

import json
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.helpers import require_targets
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id, parse_snbt, split_arguments
from datapack_emulator.emulator.runtime.advancements import Advancement
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.predicates import Context, check

#: registries whose tags predicates can ask about, and their tag folders
TAG_FOLDERS = {
    "item": ("tags/item", "tags/items"),
    "block": ("tags/block", "tags/blocks"),
    "entity_type": ("tags/entity_type", "tags/entity_types"),
}
#: ``execute if predicate`` takes an inline predicate (and names a missing
#: one with the resource-or-id error)
INLINE_PREDICATES_SINCE = "1.20.5"


def tag_members(
    context: ExecutionContext, registry: str, tag: str, seen: set[str] | None = None
) -> set[str] | None:
    """The ids of ``#tag`` in a registry, from the client jar and the pack;
    None when neither knows it."""
    tag_id = normalise_id(tag.lstrip("#"))
    seen = set() if seen is None else seen
    if tag_id in seen:
        return set()
    seen.add(tag_id)
    members: set[str] = set()
    found = False
    assets = context.emulator.vanilla
    if assets is not None and tag_id in assets.tags.get(registry, {}):
        members |= assets.resolve_tag(registry, tag_id)
        found = True
    for folder in TAG_FOLDERS.get(registry, (f"tags/{registry}",)):
        for resource in context.emulator.pack.registries.get(folder, {}).values():
            if getattr(resource, "tag_id", "") != f"#{tag_id}":
                continue
            found = True
            for entry in getattr(resource, "entries", []):
                if entry.value.startswith("#"):
                    members |= tag_members(context, registry, entry.value, seen) or set()
                else:
                    members.add(normalise_id(entry.value))
    return members if found else None


def pack_json(context: ExecutionContext, registry: str, resource_id: str) -> Any:
    """A JSON resource of the pack (``predicate``, ``item_modifier``, …)."""
    resource = context.emulator.pack.registries.get(registry, {}).get(normalise_id(resource_id))
    return getattr(resource, "content", None)


def predicate_context(
    context: ExecutionContext,
    entity=None,
    position: list[float] | None = None,
    block=None,
    block_position=None,
    tool=None,
    dimension: str | None = None,
) -> Context:
    """What a predicate or loot table sees from a command."""
    return Context(
        world=context.world,
        rng=context.world.random,
        entity=entity if entity is not None else context.executor,
        position=list(position if position is not None else context.position),
        dimension=dimension or context.dimension,
        block=block,
        block_position=block_position,
        tool=tool,
        version=context.emulator.version,
        lookup=lambda name: pack_json(context, "predicate", name),
        tags=lambda registry, tag: tag_members(context, registry, tag),
    )


def report_unchecked(context: ExecutionContext, what: str, unchecked: set[str]) -> None:
    if unchecked:
        context.note_once(
            f"{what}: the emulator cannot check {', '.join(sorted(unchecked))}, so those fail"
        )


# ---------------------------------------------------------------------------
# execute if predicate
# ---------------------------------------------------------------------------


def predicate_condition(argument: str, context: ExecutionContext) -> bool:
    """``if predicate <id>`` or, from 1.20.5, an inline predicate."""
    text = argument.strip()
    inline = context.emulator.version >= versions.parse(INLINE_PREDICATES_SINCE)
    if inline and text.startswith(("{", "[")):
        try:
            predicate = json.loads(text)
        except json.JSONDecodeError:
            predicate = parse_snbt(text) if text.startswith("{") else None
        if predicate is None:
            context.game_error("command.unknown.argument")
            return False
    else:
        predicate = pack_json(context, "predicate", text)
        if predicate is None:
            if inline:
                context.game_error("argument.resource_or_id.no_such_element", text, "predicate")
            else:
                context.game_error("predicate.unknown", normalise_id(text))
            return False
    evaluation = predicate_context(context)
    passed = check(predicate, evaluation)
    report_unchecked(context, f"predicate {text[:40]}", evaluation.unchecked)
    return passed


def selector_predicates(entity, values: list[str], context: ExecutionContext) -> bool:
    """``predicate=id`` / ``predicate=!id``, all of them."""
    for raw in values:
        negated = raw.startswith("!")
        name = raw.lstrip("!")
        predicate = pack_json(context, "predicate", name)
        if predicate is None:
            context.note_once(f"selector predicate={name}: no such predicate, it fails")
            return False
        evaluation = predicate_context(
            context, entity=entity, position=entity.position, dimension=entity.dimension
        )
        if check(predicate, evaluation) == negated:
            report_unchecked(context, f"predicate {name}", evaluation.unchecked)
            return False
    return True


def selector_advancements(entity, raw: str, context: ExecutionContext) -> bool:
    """``advancements={ns:id=true, ns:other={criterion=false}}`` (players only)."""
    if not entity.is_player:
        return False
    progress = context.world.advancements
    for key, value in split_arguments(raw.strip()[1:-1]):
        advancement = normalise_id(key)
        if value.startswith("{"):
            wanted: Any = {
                name: flag == "true" for name, flag in split_arguments(value.strip()[1:-1])
            }
        else:
            wanted = value == "true"
        if not progress.matches(entity.id, advancement, wanted):
            return False
    return True


# ---------------------------------------------------------------------------
# advancement
# ---------------------------------------------------------------------------


def _usage(context: ExecutionContext) -> CommandResult:
    context.game_error("command.unknown.command")
    return CommandResult.failure()


def _selected(context: ExecutionContext, mode: str, rest: list[str]) -> list[Advancement] | None:
    tree = context.world.advancements.tree
    if mode == "everything":
        return tree.all()
    if not rest:
        _usage(context)
        return None
    advancement = tree.get(normalise_id(rest[0]))
    if advancement is None:
        context.game_error("advancement.advancementNotFound", normalise_id(rest[0]))
        return None
    if mode == "only":
        return [advancement]
    if mode == "from":
        return [advancement, *tree.descendants(advancement.id)]
    if mode == "until":
        return [*tree.ancestors(advancement.id), advancement]
    if mode == "through":
        return [
            *tree.ancestors(advancement.id),
            advancement,
            *tree.descendants(advancement.id),
        ]
    _usage(context)
    return None


def cmd_advancement(command: Command, context: ExecutionContext) -> CommandResult:
    """``advancement grant|revoke <targets> everything|only|from|through|until …``"""
    arguments = command.arguments
    if len(arguments) < 3 or arguments[0] not in ("grant", "revoke"):
        return _usage(context)
    action, mode, rest = arguments[0], arguments[2], arguments[3:]
    players = require_targets(context, arguments[1])
    if not players:
        return CommandResult.failure()
    if any(not player.is_player for player in players):
        context.game_error("argument.player.entities")
        return CommandResult.failure()
    selected = _selected(context, mode, rest)
    if selected is None:
        return CommandResult.failure()
    progress = context.world.advancements
    change = progress.grant if action == "grant" else progress.revoke
    who = players[0].display if len(players) == 1 else len(players)
    target = "one" if len(players) == 1 else "many"

    if mode == "only" and len(rest) > 1:
        advancement, criterion = selected[0], rest[1]
        if criterion not in advancement.criteria:
            context.game_error(
                "commands.advancement.criterionNotFound", advancement.shown, criterion
            )
            return CommandResult.failure()
        changed = sum(change(player.id, advancement, criterion) for player in players)
        key = f"commands.advancement.{action}.criterion.to.{target}"
        if not changed:
            context.game_error(f"{key}.failure", criterion, advancement.shown, who)
            return CommandResult.failure()
        context.feedback(f"{key}.success", criterion, advancement.shown, who)
        return CommandResult(success=True, value=changed)

    changed = 0
    for player in players:
        for advancement in selected:
            changed += change(player.id, advancement)
    amount = "one" if len(selected) == 1 else "many"
    key = f"commands.advancement.{action}.{amount}.to.{target}"
    what = selected[0].shown if len(selected) == 1 else len(selected)
    if not changed:
        context.game_error(f"{key}.failure", what, who)
        return CommandResult.failure()
    context.feedback(f"{key}.success", what, who)
    return CommandResult(success=True, value=changed)


CONDITION_HANDLERS = {"advancement": cmd_advancement}
