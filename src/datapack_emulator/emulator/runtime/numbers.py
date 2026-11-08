"""Context number providers (26.3): ``context_int_provider`` and
``context_float_provider``, what ``/compute`` and ``data modify … compute``
evaluate.

26.3-pre-1 split the old ``number_provider`` registry in two. Integer providers
compute with Java ``int`` arithmetic (``Math.addExact`` and friends), float
providers with Java ``float`` arithmetic, so every float step is rounded to 32
bits here too. Read from the 26.3 server classes
(``world.level.storage.loot.providers.number.ints`` / ``floats``):

* an arithmetic error (division by zero, overflow, ``0^0``, ``abs``/``negate``
  of ``-2^31``, a non-finite float turned into an int) raises
  :class:`ProviderError`, vanilla's ``ArithmeticException``. ``/compute``
  reports it and fails; everywhere else (``getInt``/``getFloat``,
  ``data modify … compute``) the provider gives 0, and a non-finite float too;
* int ``div``/``mod`` are Java's ``/`` and ``%`` (truncating, ``-2^31 / -1``
  wraps); ``floor_div`` is ``Math.floorDivExact``, ``floor_mod``
  ``Math.floorMod``;
* float ``mod`` is Java's ``%`` (26.3-pre-3: the standard remainder, sign of
  the left side) and NaN for a zero right side; float ``pow(0, 0)`` is NaN
  (26.3-pre-3), int ``pow(0, 0)`` an error, int ``pow`` with a negative
  exponent too (``Math.powExact``);
* ``score`` and ``storage`` use their ``fallback`` provider (default 0) when
  the score is not set or the path does not lead to exactly one number.

A provider is an inline value (a number is a constant), or the id of one in
the pack's ``context_int_provider`` / ``context_float_provider`` folder or in
the client jar. What the world model cannot answer (environment attributes,
enchantment levels, entity targets other than ``this`` and ``target_entity``,
provider tags in ``inputs``) is listed in ``unchecked`` and gives 0 (or the
fallback) so the caller can say so.
"""

from __future__ import annotations

import copy
import json
import math
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.common import nbt_get, normalise_id
from datapack_emulator.emulator.runtime import predicates

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.world import Entity

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1
LONG_MIN = -(2**63)
LONG_MAX = 2**63 - 1
FLOAT_MAX = 3.4028234663852886e38
INT_REGISTRY = "context_int_provider"
FLOAT_REGISTRY = "context_float_provider"
#: references deeper than this are a loop
MAX_DEPTH = 64


class ProviderError(ArithmeticError):
    """Vanilla's ``ArithmeticException`` while computing: ``/compute`` fails."""


class ProviderFormatError(ValueError):
    """The provider does not decode (unknown type, missing field, unknown id)."""


@dataclass
class NumberContext:
    """What a provider sees: the loot context of the command (``this``, the
    origin, a block), the ``target_entity`` of ``compute entity``, and how to
    find a provider by id."""

    loot: predicates.Context
    target_entity: Entity | None = None
    #: ``(registry, id)`` -> the provider's JSON, or None
    resolve: Callable[[str, str], Any] | None = None
    depth: int = 0
    #: what was read through ids, for the ``named`` feedback
    references: list[str] = field(default_factory=list)

    @property
    def unchecked(self) -> set[str]:
        return self.loot.unchecked


# ---------------------------------------------------------------------------
# Java number semantics
# ---------------------------------------------------------------------------


def f32(value: float) -> float:
    """Round to a Java ``float``."""
    if math.isnan(value) or math.isinf(value):
        return value
    try:
        return float(struct.unpack("f", struct.pack("f", value))[0])
    except OverflowError:
        return math.copysign(math.inf, value)


def _exact_int(value: int) -> int:
    """``Math.*Exact``: the int result, or "integer overflow"."""
    if not INT_MIN <= value <= INT_MAX:
        raise ProviderError("integer overflow")
    return value


def _long_to_int(value: int) -> int:
    """``ContextIntProvider.longToIntSafe``."""
    if not INT_MIN <= value <= INT_MAX:
        raise ProviderError(f"Value {value} can't be safely converted to int")
    return value


def _wrap_int(value: int) -> int:
    return (value - INT_MIN) % 2**32 + INT_MIN


def _wrap_long(value: int) -> int:
    return (value - LONG_MIN) % 2**64 + LONG_MIN


def d2i(value: float) -> int:
    """Java's ``(int)`` of a double or float: truncates, saturates, NaN is 0."""
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return INT_MAX if value > 0 else INT_MIN
    return max(INT_MIN, min(INT_MAX, int(value)))


def d2l(value: float) -> int:
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return LONG_MAX if value > 0 else LONG_MIN
    return max(LONG_MIN, min(LONG_MAX, int(value)))


def int_value(value: Any) -> int:
    """``Number.intValue()``: whole numbers wrap to 32 bits, decimals truncate
    and saturate."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return d2i(value)
    return _wrap_int(int(value))


def java_round(value: float) -> int:
    """``Math.round(float)``."""
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return INT_MAX if value > 0 else INT_MIN
    return max(INT_MIN, min(INT_MAX, math.floor(value + 0.5)))


def java_floor(value: float) -> int:
    """``Mth.floor(float)``: ``(int) Math.floor(value)``."""
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return INT_MAX if value > 0 else INT_MIN
    return max(INT_MIN, min(INT_MAX, math.floor(value)))


def _java_ceil(value: float) -> int:
    if math.isnan(value) or math.isinf(value):
        return java_floor(value)
    return max(INT_MIN, min(INT_MAX, math.ceil(value)))


def _fdiv(left: float, right: float) -> float:
    if right == 0:
        if left == 0 or math.isnan(left):
            return math.nan
        sign = math.copysign(1.0, left) * math.copysign(1.0, right)
        return math.copysign(math.inf, sign)
    return f32(left / right)


def _frem(left: float, right: float) -> float:
    """Java's float ``%``."""
    if math.isnan(left) or math.isnan(right) or math.isinf(left) or right == 0:
        return math.nan
    if math.isinf(right):
        return left
    return f32(math.fmod(left, right))


def _java_pow(base: float, exponent: float) -> float:
    """``Math.pow`` where Python raises."""
    try:
        return math.pow(base, exponent)
    except OverflowError:
        odd = float(exponent).is_integer() and int(exponent) % 2 == 1
        return -math.inf if base < 0 and odd else math.inf
    except (ValueError, ZeroDivisionError):
        if base == 0 and exponent < 0:
            odd = float(exponent).is_integer() and int(exponent) % 2 == 1
            return -math.inf if odd and math.copysign(1.0, base) < 0 else math.inf
        return math.nan


def _int_pow(base: int, exponent: int) -> int:
    """``Math.powExact(int, int)``."""
    if exponent < 0:
        raise ProviderError("negative exponent")
    if base in (0, 1) or exponent == 0:
        return 1 if exponent == 0 else base
    if base == -1:
        return 1 if exponent % 2 == 0 else -1
    if exponent > 32:  # |base| >= 2 overflows long before this
        raise ProviderError("integer overflow")
    return _exact_int(base**exponent)


@cache
def _sin_table() -> tuple[float, ...]:
    return tuple(f32(math.sin(index / 10430.378350470453)) for index in range(65536))


def mth_sin(value: float) -> float:
    """``Mth.sin``: vanilla's 65 536-entry table, not ``Math.sin``."""
    return _sin_table()[d2l(value * 10430.378350470453) & 65535]


def mth_cos(value: float) -> float:
    return _sin_table()[d2l(value * 10430.378350470453 + 16384.0) & 65535]


def java_float_text(value: float) -> str:
    """``Float.toString``: the shortest digits that read back as the same
    float, in Java's ``1.5`` / ``1.0E10`` forms."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    shortest = next(
        text
        for text in (f"{value:.{precision}g}" for precision in range(1, 10))
        if f32(float(text)) == value
    )
    if 1e-3 <= abs(value) < 1e7:
        return repr(float(shortest))
    sign, digits, exponent = Decimal(shortest).normalize().as_tuple()
    assert isinstance(exponent, int)
    text = "".join(map(str, digits))
    mantissa = text[0] + "." + (text[1:] or "0")
    return f"{'-' if sign else ''}{mantissa}E{len(digits) - 1 + exponent}"


# ---------------------------------------------------------------------------
# decoding helpers
# ---------------------------------------------------------------------------


def _resolve(value: Any, registry: str, context: NumberContext) -> Any:
    """An id (a holder reference) -> the provider it names."""
    if not isinstance(value, str):
        return value
    resource = normalise_id(value)
    found = context.resolve(registry, resource) if context.resolve is not None else None
    if found is None:
        raise ProviderFormatError(f"Can't find element '{resource}' in registry '{registry}'")
    context.references.append(resource)
    return found


def _type(provider: dict[str, Any]) -> str:
    kind = provider.get("type")
    if not isinstance(kind, str) or not kind:
        raise ProviderFormatError("a number provider needs a type")
    return normalise_id(kind)


def _field(provider: dict[str, Any], name: str) -> Any:
    if name not in provider:
        raise ProviderFormatError(f"No key {name} in {provider}")
    return provider[name]


def _inputs(provider: dict[str, Any], context: NumberContext) -> list[Any]:
    """A holder set: one provider, a list (inline or ids), or ``#tag``."""
    value = _field(provider, "inputs")
    if isinstance(value, str) and value.startswith("#"):
        context.unchecked.add(f"provider tag {value}")
        return []
    return value if isinstance(value, list) else [value]


def _condition(condition: Any, context: NumberContext) -> bool:
    """A predicate: an id or inline. The shared evaluator reads it in the
    format of the context's version (26.3: ``type`` keys at every level)."""
    return predicates.check(condition, context.loot)


def _dispatch(provider: dict[str, Any], context: NumberContext) -> Any:
    """``number_dispatcher``: the first case whose condition passes."""
    cases = _field(provider, "cases")
    for case in cases if isinstance(cases, list) else []:
        if isinstance(case, dict) and _condition(case.get("condition"), context):
            return _field(case, "value")
    return provider.get("default", 0)


def _weighted(provider: dict[str, Any], context: NumberContext) -> Any:
    entries = _field(provider, "distribution")
    if not isinstance(entries, list) or not entries:
        raise ProviderFormatError("distribution must not be empty")
    weights = [max(0, int_value(entry.get("weight", 0))) for entry in entries]
    total = sum(weights)
    if total <= 0:
        raise ProviderError("no weighted entry to choose")
    pick = context.loot.rng.randrange(total)
    for entry, weight in zip(entries, weights, strict=True):
        pick -= weight
        if pick < 0:
            return _field(entry, "data")
    return _field(entries[-1], "data")  # pragma: no cover - the loop always returns


def _entity(name: str, context: NumberContext) -> Entity | None:
    name = name.removeprefix("minecraft:")
    if name == "this":
        return context.loot.entity
    if name == "target_entity":
        return context.target_entity
    context.unchecked.add(f"entity {name}")
    return None


def _score_holder(target: Any, context: NumberContext) -> str | None:
    """A score name provider: ``fixed`` (a name), ``context`` or a bare entity
    target (``this``, ``target_entity``)."""
    if isinstance(target, dict):
        if normalise_id(str(target.get("type", ""))) == "minecraft:fixed":
            return str(target.get("name", ""))
        target = target.get("target", "this")
    entity = _entity(str(target), context)
    return entity.id if entity is not None else None


def _stored_number(provider: dict[str, Any], context: NumberContext) -> int | float | None:
    """``storage``: the one numeric tag at ``path``, else None."""
    world = context.loot.world
    store = world.storage.get(normalise_id(str(_field(provider, "storage")))) if world else None
    if not isinstance(store, dict):
        return None
    value = nbt_get(store, str(_field(provider, "path")))
    return value if isinstance(value, (bool, int, float)) else None


def _enter(context: NumberContext) -> None:
    context.depth += 1
    if context.depth > MAX_DEPTH:
        raise ProviderFormatError("number providers reference each other in a loop")


# ---------------------------------------------------------------------------
# int providers
# ---------------------------------------------------------------------------


def int_unsafe(provider: Any, context: NumberContext) -> int:
    """``ContextIntProvider.getIntUnsafe``: raises :class:`ProviderError`."""
    _enter(context)
    try:
        return _int(_resolve(provider, INT_REGISTRY, context), context)
    finally:
        context.depth -= 1


def get_int(provider: Any, context: NumberContext) -> int:
    """``getInt``: an arithmetic error gives 0."""
    try:
        return int_unsafe(provider, context)
    except ProviderError:
        return 0


def _int(provider: Any, context: NumberContext) -> int:
    if isinstance(provider, (bool, int, float)):
        return int_value(provider)
    if not isinstance(provider, dict):
        raise ProviderFormatError(f"Not a number provider: {provider!r}")
    kind = _type(provider)
    handler = INT_PROVIDERS.get(kind)
    if handler is None:
        if kind in _UNMODELLED_INT:
            context.unchecked.add(kind)
            return 0
        raise ProviderFormatError(f"Unknown int provider type: {kind}")
    return handler(provider, context)


def _int_input(provider: dict[str, Any], name: str, context: NumberContext) -> int:
    return int_unsafe(_field(provider, name), context)


def _int_sum(provider: dict[str, Any], context: NumberContext) -> int:
    total = sum(int_unsafe(entry, context) for entry in _inputs(provider, context))
    return _long_to_int(total)


def _int_product(provider: dict[str, Any], context: NumberContext) -> int:
    total = 1
    for entry in _inputs(provider, context):
        total = _wrap_long(total * int_unsafe(entry, context))  # a Java long
    return _long_to_int(total)


def _int_min(provider: dict[str, Any], context: NumberContext) -> int:
    result = INT_MAX
    for entry in _inputs(provider, context):
        result = min(result, int_unsafe(entry, context))
    return result


def _int_max(provider: dict[str, Any], context: NumberContext) -> int:
    result = INT_MIN
    for entry in _inputs(provider, context):
        result = max(result, int_unsafe(entry, context))
    return result


def _int_average(provider: dict[str, Any], context: NumberContext) -> int:
    values = [int_unsafe(entry, context) for entry in _inputs(provider, context)]
    if not values:
        raise ProviderError("/ by zero")
    total = sum(values)
    quotient = abs(total) // len(values)  # Java's long division truncates
    return _long_to_int(quotient if total >= 0 else -quotient)


def _binary(provider: dict[str, Any], context: NumberContext) -> tuple[int, int]:
    return _int_input(provider, "left", context), _int_input(provider, "right", context)


def _int_sub(provider: dict[str, Any], context: NumberContext) -> int:
    left, right = _binary(provider, context)
    return _exact_int(left - right)


def _truncated_division(left: int, right: int) -> int:
    if right == 0:
        raise ProviderError("/ by zero")
    quotient = abs(left) // abs(right)
    return quotient if (left >= 0) == (right > 0) else -quotient


def _int_div(provider: dict[str, Any], context: NumberContext) -> int:
    left, right = _binary(provider, context)
    return _wrap_int(_truncated_division(left, right))  # -2^31 / -1 wraps


def _int_mod(provider: dict[str, Any], context: NumberContext) -> int:
    left, right = _binary(provider, context)
    return left - right * _truncated_division(left, right)


def _int_floor_div(provider: dict[str, Any], context: NumberContext) -> int:
    left, right = _binary(provider, context)
    if right == 0:
        raise ProviderError("/ by zero")
    return _exact_int(left // right)


def _int_floor_mod(provider: dict[str, Any], context: NumberContext) -> int:
    left, right = _binary(provider, context)
    if right == 0:
        raise ProviderError("/ by zero")
    return left % right


def _int_abs(provider: dict[str, Any], context: NumberContext) -> int:
    result = abs(_int_input(provider, "input", context))
    if result > INT_MAX:
        raise ProviderError("Overflow to represent absolute value of Integer.MIN_VALUE")
    return result


def _int_negate(provider: dict[str, Any], context: NumberContext) -> int:
    return _exact_int(-_int_input(provider, "input", context))


def _int_pow_provider(provider: dict[str, Any], context: NumberContext) -> int:
    base = _int_input(provider, "base", context)
    exponent = _int_input(provider, "exponent", context)
    if base == 0 and exponent == 0:
        raise ProviderError("Result of 0 to the power of 0 is undefined")
    return _int_pow(base, exponent)


def _int_from_float(provider: dict[str, Any], context: NumberContext) -> int:
    value = float_unsafe(_field(provider, "input"), context)
    if math.isnan(value) or math.isinf(value):
        raise ProviderError(f"Value {java_float_text(value)} can't be safely converted to int")
    return _long_to_int(d2l(value))


def _int_constant(provider: dict[str, Any], context: NumberContext) -> int:
    return int_value(_field(provider, "value"))


def _int_uniform(provider: dict[str, Any], context: NumberContext) -> int:
    low = _int_input(provider, "min", context)
    high = _int_input(provider, "max", context)
    return low if low >= high else context.loot.rng.randint(low, high)


def _int_binomial(provider: dict[str, Any], context: NumberContext) -> int:
    trials = _int_input(provider, "n", context)
    chance = float_unsafe(_field(provider, "p"), context)
    if math.isnan(chance) or math.isinf(chance):
        raise ProviderError(f"Invalid value: {java_float_text(chance)}")
    rng = context.loot.rng
    return sum(1 for _ in range(max(0, trials)) if rng.random() < chance)


def _int_score(provider: dict[str, Any], context: NumberContext) -> int:
    holder = _score_holder(_field(provider, "target"), context)
    world = context.loot.world
    objective = str(_field(provider, "score"))
    value = world.scoreboard.get(holder, objective) if world and holder is not None else None
    if value is None:
        return int_unsafe(provider.get("fallback", 0), context)
    return value


def _int_storage(provider: dict[str, Any], context: NumberContext) -> int:
    value = _stored_number(provider, context)
    if value is None:
        return int_unsafe(provider.get("fallback", 0), context)
    return int_value(value)


def _int_conditional(provider: dict[str, Any], context: NumberContext) -> int:
    chosen = (
        _field(provider, "on_true")
        if _condition(_field(provider, "condition"), context)
        else provider.get("on_false", 0)
    )
    return int_unsafe(chosen, context)


def _int_dispatcher(provider: dict[str, Any], context: NumberContext) -> int:
    return int_unsafe(_dispatch(provider, context), context)


def _int_weighted(provider: dict[str, Any], context: NumberContext) -> int:
    return int_unsafe(_weighted(provider, context), context)


#: provider types the world model cannot evaluate: 0, listed in ``unchecked``
_UNMODELLED_INT = frozenset({"minecraft:environment_attribute"})
_UNMODELLED_FLOAT = frozenset({"minecraft:environment_attribute", "minecraft:enchantment_level"})

INT_PROVIDERS: dict[str, Callable[[dict[str, Any], NumberContext], int]] = {
    "minecraft:constant": _int_constant,
    "minecraft:uniform": _int_uniform,
    "minecraft:binomial": _int_binomial,
    "minecraft:score": _int_score,
    "minecraft:storage": _int_storage,
    "minecraft:add": _int_sum,
    "minecraft:mul": _int_product,
    "minecraft:min": _int_min,
    "minecraft:max": _int_max,
    "minecraft:avg": _int_average,
    "minecraft:sub": _int_sub,
    "minecraft:div": _int_div,
    "minecraft:mod": _int_mod,
    "minecraft:floor_div": _int_floor_div,
    "minecraft:floor_mod": _int_floor_mod,
    "minecraft:abs": _int_abs,
    "minecraft:negate": _int_negate,
    "minecraft:pow": _int_pow_provider,
    "minecraft:from_float": _int_from_float,
    "minecraft:conditional": _int_conditional,
    "minecraft:number_dispatcher": _int_dispatcher,
    "minecraft:weighted_list": _int_weighted,
}


# ---------------------------------------------------------------------------
# float providers
# ---------------------------------------------------------------------------


def float_unsafe(provider: Any, context: NumberContext) -> float:
    """``ContextFloatProvider.getFloatUnsafe``: a 32-bit value, possibly NaN or
    infinite; raises :class:`ProviderError` on an int error inside."""
    _enter(context)
    try:
        return _float(_resolve(provider, FLOAT_REGISTRY, context), context)
    finally:
        context.depth -= 1


def get_float(provider: Any, context: NumberContext) -> float:
    """``getFloat``: an error or a non-finite result gives 0."""
    try:
        value = float_unsafe(provider, context)
    except ProviderError:
        return 0.0
    return value if math.isfinite(value) else 0.0


def _float(provider: Any, context: NumberContext) -> float:
    if isinstance(provider, (bool, int, float)):
        return f32(float(provider))
    if not isinstance(provider, dict):
        raise ProviderFormatError(f"Not a number provider: {provider!r}")
    kind = _type(provider)
    handler = FLOAT_PROVIDERS.get(kind)
    if handler is None:
        if kind in _UNMODELLED_FLOAT:
            context.unchecked.add(kind)
            return 0.0
        raise ProviderFormatError(f"Unknown float provider type: {kind}")
    return handler(provider, context)


def _float_input(provider: dict[str, Any], name: str, context: NumberContext) -> float:
    return float_unsafe(_field(provider, name), context)


def _float_values(provider: dict[str, Any], context: NumberContext) -> list[float]:
    return [float_unsafe(entry, context) for entry in _inputs(provider, context)]


def _float_sum(provider: dict[str, Any], context: NumberContext) -> float:
    total = 0.0
    for value in _float_values(provider, context):
        total = f32(total + value)
    return total


def _float_product(provider: dict[str, Any], context: NumberContext) -> float:
    total = 1.0
    for value in _float_values(provider, context):
        total = f32(total * value)
    return total


def _java_min(left: float, right: float) -> float:
    return math.nan if math.isnan(left) or math.isnan(right) else min(left, right)


def _java_max(left: float, right: float) -> float:
    return math.nan if math.isnan(left) or math.isnan(right) else max(left, right)


def _float_min(provider: dict[str, Any], context: NumberContext) -> float:
    result = FLOAT_MAX
    for value in _float_values(provider, context):
        result = _java_min(result, value)
    return result


def _float_max(provider: dict[str, Any], context: NumberContext) -> float:
    result = -FLOAT_MAX
    for value in _float_values(provider, context):
        result = _java_max(result, value)
    return result


def _float_average(provider: dict[str, Any], context: NumberContext) -> float:
    total, count = 0.0, 0
    for value in _float_values(provider, context):
        total = f32(total + value)
        count += 1
    return _fdiv(total, float(count))


def _float_length(provider: dict[str, Any], context: NumberContext) -> float:
    total = 0.0
    for value in _float_values(provider, context):
        total = f32(total + f32(value * value))
    return _sqrt(total)


def _sqrt(value: float) -> float:
    if math.isnan(value) or value < 0:
        return math.nan
    return f32(math.sqrt(value))


def _float_sub(provider: dict[str, Any], context: NumberContext) -> float:
    left = _float_input(provider, "left", context)
    return f32(left - _float_input(provider, "right", context))


def _float_div(provider: dict[str, Any], context: NumberContext) -> float:
    left = _float_input(provider, "left", context)
    return _fdiv(left, _float_input(provider, "right", context))


def _float_mod(provider: dict[str, Any], context: NumberContext) -> float:
    right = _float_input(provider, "right", context)  # vanilla reads the right side first
    if right == 0:
        return math.nan
    return _frem(_float_input(provider, "left", context), right)


def _float_pow(provider: dict[str, Any], context: NumberContext) -> float:
    base = _float_input(provider, "base", context)
    exponent = _float_input(provider, "exponent", context)
    if base == 0 and exponent == 0:
        return math.nan  # 26.3-pre-3: an error, like the int variant
    return f32(_java_pow(base, exponent))


def _unary(operation: Callable[[float], float]) -> Callable[[dict[str, Any], NumberContext], float]:
    def handler(provider: dict[str, Any], context: NumberContext) -> float:
        return operation(_float_input(provider, "input", context))

    return handler


def _truncate(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return value
    return f32(float(math.floor(value) if value > 0 else math.ceil(value)))


def _floor(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return value
    return f32(float(math.floor(value)))


def _float_from_int(provider: dict[str, Any], context: NumberContext) -> float:
    return f32(float(int_unsafe(_field(provider, "input"), context)))


def _float_constant(provider: dict[str, Any], context: NumberContext) -> float:
    value = _field(provider, "value")
    if not isinstance(value, (bool, int, float)):
        raise ProviderFormatError(f"Not a number: {value!r}")
    return f32(float(value))


def _float_uniform(provider: dict[str, Any], context: NumberContext) -> float:
    low = _float_input(provider, "min", context)
    high = _float_input(provider, "max", context)
    if not low < high:
        return low
    return f32(f32(context.loot.rng.random() * f32(high - low)) + low)


def _float_storage(provider: dict[str, Any], context: NumberContext) -> float:
    value = _stored_number(provider, context)
    if value is None:
        return float_unsafe(provider.get("fallback", 0), context)
    return f32(float(value))


def _float_conditional(provider: dict[str, Any], context: NumberContext) -> float:
    chosen = (
        _field(provider, "on_true")
        if _condition(_field(provider, "condition"), context)
        else provider.get("on_false", 0)
    )
    return float_unsafe(chosen, context)


def _float_dispatcher(provider: dict[str, Any], context: NumberContext) -> float:
    return float_unsafe(_dispatch(provider, context), context)


def _float_weighted(provider: dict[str, Any], context: NumberContext) -> float:
    return float_unsafe(_weighted(provider, context), context)


FLOAT_PROVIDERS: dict[str, Callable[[dict[str, Any], NumberContext], float]] = {
    "minecraft:constant": _float_constant,
    "minecraft:uniform": _float_uniform,
    "minecraft:storage": _float_storage,
    "minecraft:add": _float_sum,
    "minecraft:mul": _float_product,
    "minecraft:min": _float_min,
    "minecraft:max": _float_max,
    "minecraft:avg": _float_average,
    "minecraft:length": _float_length,
    "minecraft:sub": _float_sub,
    "minecraft:div": _float_div,
    "minecraft:mod": _float_mod,
    "minecraft:pow": _float_pow,
    "minecraft:abs": _unary(abs),
    "minecraft:negate": _unary(lambda value: -value),
    "minecraft:sqrt": _unary(_sqrt),
    "minecraft:floor": _unary(_floor),
    "minecraft:ceil": _unary(lambda value: float(_java_ceil(value))),
    "minecraft:round": _unary(lambda value: float(java_round(value))),
    "minecraft:truncate": _unary(_truncate),
    "minecraft:sin": _unary(mth_sin),
    "minecraft:cos": _unary(mth_cos),
    "minecraft:from_int": _float_from_int,
    "minecraft:conditional": _float_conditional,
    "minecraft:number_dispatcher": _float_dispatcher,
    "minecraft:weighted_list": _float_weighted,
}


# ---------------------------------------------------------------------------
# finding providers by id
# ---------------------------------------------------------------------------


def jar_provider(jar_path: Any, registry: str, resource_id: str) -> Any:
    """A vanilla provider file from the client jar (it may be a bare number).

    The jar's provider files are read once per jar: opening the client jar
    costs a fifth of a second, far too much for a ``compute`` that runs every
    tick."""
    namespace, _, path = normalise_id(resource_id).partition(":")
    found = _jar_providers(str(jar_path)).get(f"data/{namespace}/{registry}/{path}.json")
    return copy.deepcopy(found)


@cache
def _jar_providers(jar_path: str) -> dict[str, Any]:
    """Every ``context_int_provider`` / ``context_float_provider`` file of a
    jar, by its name in the archive."""
    found: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(jar_path) as archive:
            for name in archive.namelist():
                parts = name.split("/")
                if (
                    len(parts) >= 4
                    and parts[0] == "data"
                    and parts[2] in (INT_REGISTRY, FLOAT_REGISTRY)
                    and name.endswith(".json")
                ):
                    try:
                        found[name] = json.loads(archive.read(name).decode("utf-8"))
                    except ValueError:
                        continue
    except (OSError, zipfile.BadZipFile):
        return {}
    return found
