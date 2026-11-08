"""``compute`` (26.3) and the ``compute`` source of ``data modify``.

``compute <default|entity <target>|block <pos>> integer <provider>`` and
``compute … float <provider> [scale]`` evaluate a context number provider
(:mod:`datapack_emulator.emulator.runtime.numbers`) and return it: an integer
as it is, a float multiplied by ``scale`` and floored (``Mth.floor``). An
arithmetic error, NaN or an infinity makes the command fail.

``data modify … compute <source> integer|float <provider>`` writes an int or a
float tag; there the provider's errors give 0, as everywhere outside
``/compute``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from datapack_emulator.emulator.commands.blocks import position_at
from datapack_emulator.emulator.commands.conditions import (
    pack_json,
    predicate_context,
    report_unchecked,
)
from datapack_emulator.emulator.commands.helpers import require_targets
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.common import NbtFloat, NbtInt, normalise_id, parse_snbt
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.numbers import (
    FLOAT_REGISTRY,
    INT_REGISTRY,
    NumberContext,
    ProviderError,
    ProviderFormatError,
    f32,
    float_unsafe,
    get_float,
    get_int,
    int_unsafe,
    jar_provider,
    java_float_text,
    java_floor,
)


@dataclass
class Computation:
    """A parsed ``<source> integer|float <provider>``: what is left after it
    is in ``rest``."""

    numbers: NumberContext
    integer: bool
    provider: Any
    #: the provider's id when it was named rather than written inline
    name: str | None
    rest: list[str]


def _resolver(context: ExecutionContext):
    def resolve(registry: str, resource_id: str) -> Any:
        found = pack_json(context, registry, resource_id)
        if found is not None:
            return found
        assets = context.emulator.vanilla
        return jar_provider(assets.jar_path, registry, resource_id) if assets else None

    return resolve


def parse_computation(context: ExecutionContext, tokens: list[str]) -> Computation | None:
    """``default | entity <target> | block <pos>``, then ``integer|float
    <provider>``; None once an error was reported."""
    if not tokens:
        context.game_error("command.unknown.command")
        return None
    source, target, rest = tokens[0], None, tokens[1:]
    if source == "default":
        loot = predicate_context(context)
    elif source == "entity" and rest:
        found = require_targets(context, rest[0])
        if not found:
            return None
        if len(found) > 1:
            context.game_error("argument.entity.toomany")
            return None
        target, rest = found[0], rest[1:]
        loot = predicate_context(context)
    elif source == "block" and len(rest) >= 3:
        position = position_at(context, rest[:3])
        if position is None:
            return None
        block = context.world.blocks.get(context.dimension, position)
        loot = predicate_context(context, block=block, block_position=position)
        rest = rest[3:]
    else:
        context.game_error("command.unknown.argument")
        return None
    if len(rest) < 2 or rest[0] not in ("integer", "float"):
        context.game_error("command.unknown.command")
        return None
    integer = rest[0] == "integer"
    registry = INT_REGISTRY if integer else FLOAT_REGISTRY
    token = rest[1]
    numbers = NumberContext(loot=loot, target_entity=target, resolve=_resolver(context))
    name: str | None = None
    if token.startswith("{"):
        provider: Any = parse_snbt(token)
        if not provider:
            context.game_error("argument.resource_or_id.failed_to_parse", token)
            return None
    else:  # an id comes first in the argument's grammar, so `5` is an id too
        name = normalise_id(token)
        provider = numbers.resolve(registry, name) if numbers.resolve else None
        if provider is None:
            context.game_error(
                "argument.resource_or_id.no_such_element", name, f"minecraft:{registry}"
            )
            return None
    return Computation(numbers, integer, provider, name, rest[2:])


def _invalid(context: ExecutionContext, computation: Computation, reason: str) -> CommandResult:
    if computation.name is not None:
        context.game_error("command.compute.result.named.invalid", computation.name, reason)
    else:
        context.game_error("command.compute.result.unnamed.invalid", reason)
    return CommandResult.failure()


def _scale(context: ExecutionContext, rest: list[str]) -> float | None:
    if not rest:
        return 1.0
    if len(rest) == 1:
        try:
            return f32(float(rest[0]))
        except ValueError:
            pass
    context.game_error("command.unknown.argument")
    return None


def cmd_compute(command: Command, context: ExecutionContext) -> CommandResult:
    """``compute``: the provider's value is the command's result."""
    computation = parse_computation(context, command.arguments)
    if computation is None:
        return CommandResult.failure()
    if computation.integer and computation.rest:
        context.game_error("command.unknown.argument")
        return CommandResult.failure()
    scale = 1.0 if computation.integer else _scale(context, computation.rest)
    if scale is None:
        return CommandResult.failure()
    numbers = computation.numbers
    try:
        if computation.integer:
            value: float = int_unsafe(computation.provider, numbers)
        else:
            value = float_unsafe(computation.provider, numbers)
    except ProviderError as error:
        return _invalid(context, computation, str(error))
    except ProviderFormatError as error:
        context.game_error("argument.resource_or_id.failed_to_parse", str(error))
        return CommandResult.failure()
    finally:
        report_unchecked(context, "compute", numbers.unchecked)
    if computation.integer:
        result = int(value)
        _exact(context, computation, result)
        return CommandResult(success=True, value=result)
    if not math.isfinite(value):
        return _invalid(context, computation, java_float_text(value))
    result = java_floor(f32(value * scale))
    if f32(float(result)) == value:  # vanilla compares (float) result
        _exact(context, computation, result)
    elif computation.name is not None:
        context.feedback(
            "command.compute.result.named.rounded",
            computation.name,
            java_float_text(value),
            result,
        )
    else:
        context.feedback("command.compute.result.unnamed.rounded", java_float_text(value), result)
    return CommandResult(success=True, value=result)


def _exact(context: ExecutionContext, computation: Computation, result: int) -> None:
    if computation.name is not None:
        context.feedback("command.compute.result.named.exact", computation.name, result)
    else:
        context.feedback("command.compute.result.unnamed.exact", result)


def compute_source(context: ExecutionContext, tokens: list[str]) -> tuple[bool, Any]:
    """``data modify … compute …``: ``(found, value)``, an ``IntTag`` or a
    ``FloatTag``; errors in the provider give 0 like ``getInt``/``getFloat``."""
    computation = parse_computation(context, tokens)
    if computation is None:
        return (False, None)
    if computation.rest:
        context.game_error("command.unknown.argument")
        return (False, None)
    numbers = computation.numbers
    try:
        if computation.integer:
            return (True, NbtInt(get_int(computation.provider, numbers)))
        return (True, NbtFloat(get_float(computation.provider, numbers)))
    except ProviderFormatError as error:
        context.game_error("argument.resource_or_id.failed_to_parse", str(error))
        return (False, None)
    finally:
        report_unchecked(context, "data modify … compute", numbers.unchecked)


COMPUTE_HANDLERS = {"compute": cmd_compute}
