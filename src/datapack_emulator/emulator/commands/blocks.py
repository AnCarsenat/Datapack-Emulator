"""Block commands: setblock, fill, clone, and the block helpers the other
command groups use (``data … block``, ``item … block``, ``execute if block``).

See :mod:`datapack_emulator.emulator.runtime.blocks` for the model.
"""

from __future__ import annotations

from datapack_emulator.emulator.commands.helpers import require_id
from datapack_emulator.emulator.commands.parser import Command, resolve_position
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.blocks import (
    LIMIT_RULES,
    MODIFICATION_LIMIT,
    Block,
    BlockPredicate,
    Position,
    block_position,
    box,
    parse_block,
    parse_block_predicate,
    positions,
    volume,
    world_height,
)
from datapack_emulator.emulator.runtime.context import ExecutionContext

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def position_at(context: ExecutionContext, tokens: list[str]) -> Position | None:
    """Three coordinate tokens -> a block position; None (error reported) when
    they are missing or outside the world."""
    if len(tokens) < 3 or not all(_coordinate(token) for token in tokens[:3]):
        context.game_error("command.unknown.argument")
        return None
    position = block_position(resolve_position(tokens[:3], context.position))
    low, high = world_height(context.dimension, context.emulator.version)
    if not low <= position[1] <= high:
        context.game_error("argument.pos.outofworld")
        return None
    return position


def _coordinate(token: str) -> bool:
    body = token[1:] if token[:1] in ("~", "^") else token
    if not body:
        return token[:1] in ("~", "^")
    try:
        float(body)
    except ValueError:
        return False
    return True


def block_argument(context: ExecutionContext, token: str) -> Block | None:
    """A block state argument, checked against the client jar."""
    block = parse_block(token)
    if block is None:
        context.game_error("argument.block.id.invalid", token)
        return None
    if not require_id(context, "block", block.id, "argument.block.id.invalid"):
        return None
    assets = context.emulator.vanilla
    known = assets.block_properties(block.id) if assets is not None else None
    if known:
        for key, value in block.properties.items():
            if key not in known:
                context.game_error("argument.block.property.unknown", block.id, key)
                return None
            if value not in known[key]:
                context.game_error("argument.block.property.invalid", block.id, value, key)
                return None
    if block.properties and not known:
        context.note_once(
            "block states are kept as given: default properties are not known to the emulator"
        )
    return block


def block_predicate(context: ExecutionContext, token: str) -> BlockPredicate | None:
    predicate = parse_block_predicate(token)
    if predicate is None:
        context.game_error("argument.block.id.invalid", token)
        return None
    if predicate.is_tag:
        predicate.tag_members = block_tag_members(context, predicate.id)
        if predicate.tag_members is None:
            context.note_once(
                f"block tag {predicate.id} is not known without a client jar: every block "
                "matches it"
            )
    elif not require_id(context, "block", predicate.id, "argument.block.id.invalid"):
        return None
    return predicate


def note_unknown_properties(context: ExecutionContext, predicate: BlockPredicate) -> None:
    if predicate.unknown_properties:
        context.note_once(
            "block predicate asks for "
            + ", ".join(sorted(predicate.unknown_properties))
            + ", which the block was not placed with: default block states are not "
            "modelled, so it does not match"
        )


def block_tag_members(context: ExecutionContext, tag: str) -> set[str] | None:
    """The block ids of ``#tag`` from the client jar and the pack; None if unknown."""
    tag_id = normalise_id(tag.lstrip("#"))
    members: set[str] = set()
    found = False
    assets = context.emulator.vanilla
    if assets is not None and tag_id in assets.tags.get("block", {}):
        members |= assets.resolve_tag("block", tag_id)
        found = True
    for registry in ("tags/block", "tags/blocks"):
        for resource in context.emulator.pack.registries.get(registry, {}).values():
            if getattr(resource, "tag_id", "") != f"#{tag_id}":
                continue
            found = True
            for entry in resource.entries:
                if entry.value.startswith("#"):
                    nested = block_tag_members(context, entry.value)
                    members |= nested or set()
                else:
                    members.add(normalise_id(entry.value))
    return members if found else None


def modification_limit(context: ExecutionContext) -> int:
    rules = context.world.gamerules
    for name in LIMIT_RULES:
        try:
            return max(1, int(rules[name]))
        except (KeyError, ValueError):
            continue
    return MODIFICATION_LIMIT


def container_at(
    context: ExecutionContext, position: Position, key: str = "commands.item.target.not_a_container"
) -> Block | None:
    block = context.world.blocks.stored(context.dimension, position)
    if block is None or not block.is_container:
        context.game_error(key, *position)
        return None
    return block


# ---------------------------------------------------------------------------
# setblock
# ---------------------------------------------------------------------------


def place(context: ExecutionContext, position: Position, block: Block, mode: str) -> bool:
    """Put ``block`` at ``position``; whether anything changed. ``destroy``
    drops nothing (block drops are not modelled)."""
    blocks = context.world.blocks
    current = blocks.get(context.dimension, position)
    if mode == "keep" and not current.is_air:
        return False
    if current.same(block):
        return False
    blocks.set(context.dimension, position, block.copy())
    return True


def cmd_setblock(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 4:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    position = position_at(context, arguments[:3])
    block = block_argument(context, arguments[3]) if position is not None else None
    if position is None or block is None:
        return CommandResult.failure()
    mode = arguments[4] if len(arguments) > 4 else "replace"
    if mode not in ("replace", "keep", "destroy", "strict"):
        context.game_error("command.unknown.argument")
        return CommandResult.failure()
    if mode == "destroy":
        context.note_once("setblock … destroy: block drops are not modelled")
    if not place(context, position, block, mode):
        context.game_error("commands.setblock.failed")
        return CommandResult.failure()
    context.feedback("commands.setblock.success", *position)
    return CommandResult(success=True, value=1)


# ---------------------------------------------------------------------------
# fill
# ---------------------------------------------------------------------------


def region(
    context: ExecutionContext, tokens: list[str], limit_key: str
) -> tuple[Position, Position] | None:
    start = position_at(context, tokens[:3])
    end = position_at(context, tokens[3:6]) if start is not None else None
    if start is None or end is None:
        return None
    low, high = box(start, end)
    size = volume(low, high)
    limit = modification_limit(context)
    if size > limit:
        context.game_error(limit_key, limit, size)
        return None
    return (low, high)


def cmd_fill(command: Command, context: ExecutionContext) -> CommandResult:
    arguments = command.arguments
    if len(arguments) < 7:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    corners = region(context, arguments[:6], "commands.fill.toobig")
    block = block_argument(context, arguments[6]) if corners is not None else None
    if corners is None or block is None:
        return CommandResult.failure()
    mode = arguments[7] if len(arguments) > 7 else "replace"
    rest = arguments[8:]
    if mode not in ("replace", "keep", "destroy", "hollow", "outline", "strict"):
        context.game_error("command.unknown.argument")
        return CommandResult.failure()
    filter_predicate = None
    if mode == "replace" and rest and rest[0] != "strict":
        filter_predicate = block_predicate(context, rest[0])
        if filter_predicate is None:
            return CommandResult.failure()
    low, high = corners
    blocks = context.world.blocks
    changed = 0
    for position in positions(low, high):
        edge = any(position[axis] in (low[axis], high[axis]) for axis in range(3))
        target = block
        if mode in ("hollow", "outline") and not edge:
            if mode == "outline":
                continue
            target = Block()
        if filter_predicate is not None and not filter_predicate.matches(
            blocks.get(context.dimension, position), context.emulator.version
        ):
            continue
        if place(context, position, target, "keep" if mode == "keep" else "replace"):
            changed += 1
    if filter_predicate is not None:
        note_unknown_properties(context, filter_predicate)
    if not changed:
        context.game_error("commands.fill.failed")
        return CommandResult.failure()
    context.feedback("commands.fill.success", changed)
    return CommandResult(success=True, value=changed)


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


def cmd_clone(command: Command, context: ExecutionContext) -> CommandResult:
    """``clone [from <dim>] <begin> <end> [to <dim>] <destination> [mask] [mode]``"""
    arguments = list(command.arguments)
    source_dimension = context.dimension
    target_dimension = context.dimension
    if arguments[:1] == ["from"] and len(arguments) > 1:
        source_dimension = normalise_id(arguments[1])
        arguments = arguments[2:]
    if len(arguments) < 9:
        context.game_error("command.unknown.command")
        return CommandResult.failure()
    corners = region(context, arguments[:6], "commands.clone.toobig")
    rest = arguments[6:]
    if rest[:1] == ["to"] and len(rest) > 1:
        target_dimension = normalise_id(rest[1])
        rest = rest[2:]
    destination = position_at(context, rest[:3]) if corners is not None else None
    if corners is None or destination is None:
        return CommandResult.failure()
    rest = rest[3:]
    mask = rest[0] if rest else "replace"
    filter_predicate = None
    if mask == "filtered":
        filter_predicate = block_predicate(context, rest[1]) if len(rest) > 1 else None
        if filter_predicate is None:
            return CommandResult.failure()
        rest = rest[2:]
    else:
        rest = rest[1:]
    mode = rest[0] if rest else "normal"
    if mask not in ("replace", "masked", "filtered") or mode not in ("normal", "force", "move"):
        context.game_error("command.unknown.argument")
        return CommandResult.failure()

    low, high = corners
    offset = tuple(destination[axis] - low[axis] for axis in range(3))
    target_low = destination
    target_high = tuple(high[axis] + offset[axis] for axis in range(3))
    overlaps = source_dimension == target_dimension and all(
        low[axis] <= target_high[axis] and target_low[axis] <= high[axis] for axis in range(3)
    )
    if overlaps and mode != "force":
        context.game_error("commands.clone.overlap")
        return CommandResult.failure()
    world_low, world_high = world_height(target_dimension, context.emulator.version)
    if target_low[1] < world_low or target_high[1] > world_high:
        context.game_error("argument.pos.outofworld")
        return CommandResult.failure()

    blocks = context.world.blocks
    copied = []
    for position in positions(low, high):
        block = blocks.get(source_dimension, position)
        if mask == "masked" and block.is_air:
            continue
        if filter_predicate is not None and not filter_predicate.matches(
            block, context.emulator.version
        ):
            continue
        target = tuple(position[axis] + offset[axis] for axis in range(3))
        copied.append((position, target, block.copy()))
    if filter_predicate is not None:
        note_unknown_properties(context, filter_predicate)
    if mode == "move":
        for position, _, _ in copied:
            blocks.set(source_dimension, position, Block())
    changed = 0
    for _, target, block in copied:
        current = blocks.get(target_dimension, target)
        if not current.same(block) or mode == "move":
            changed += 1
        blocks.set(target_dimension, target, block)
    if not changed:
        context.game_error("commands.clone.failed")
        return CommandResult.failure()
    context.feedback("commands.clone.success", changed)
    return CommandResult(success=True, value=changed)


# ---------------------------------------------------------------------------
# execute if block / blocks
# ---------------------------------------------------------------------------


def block_condition(arguments: list[str], context: ExecutionContext) -> bool:
    """``block <pos> <predicate>``"""
    if len(arguments) < 5:
        return False
    position = position_at(context, arguments[1:4])
    predicate = block_predicate(context, arguments[4]) if position is not None else None
    if position is None or predicate is None:
        return False
    block = context.world.blocks.get(context.dimension, position)
    matched = predicate.matches(block, context.emulator.version)
    note_unknown_properties(context, predicate)
    return matched


def blocks_condition_count(arguments: list[str], context: ExecutionContext) -> int | None:
    """``blocks <start> <end> <destination> all|masked``: the number of blocks
    compared when the regions match, 0 when they differ, None on an error."""
    if len(arguments) < 11:
        return None
    start = position_at(context, arguments[1:4])
    end = position_at(context, arguments[4:7]) if start is not None else None
    destination = position_at(context, arguments[7:10]) if end is not None else None
    if start is None or end is None or destination is None:
        return None
    mode = arguments[10]
    if mode not in ("all", "masked"):
        context.game_error("command.unknown.argument")
        return None
    low, high = box(start, end)
    size = volume(low, high)
    limit = modification_limit(context)
    if size > limit:
        context.game_error("commands.execute.blocks.toobig", limit, size)
        return None
    blocks = context.world.blocks
    version = context.emulator.version
    compared = 0
    for position in positions(low, high):
        source = blocks.get(context.dimension, position)
        if mode == "masked" and source.is_air:
            continue
        target = tuple(position[axis] - low[axis] + destination[axis] for axis in range(3))
        other = blocks.get(context.dimension, target)
        if (
            source.id != other.id
            or source.properties != other.properties
            or source.data(version) != other.data(version)
        ):
            return 0
        compared += 1
    return compared


__all__ = [
    "block_condition",
    "blocks_condition_count",
    "cmd_clone",
    "cmd_fill",
    "cmd_setblock",
    "container_at",
    "position_at",
]
