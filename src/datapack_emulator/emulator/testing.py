"""Command tests: run a command in a fresh world and check what happened.

A test is what you would type into a server console once the pack is running —
``function hat:tick``, ``say hi``, ``execute as Player1 run trigger hat``,
``scoreboard players get #global counter`` — optionally at a later tick and
with text the game output must contain.

Timing follows the game: commands typed by a player or on the console are
handled *after* the functions of the server tick they arrive in. A test at tick
N therefore runs in server tick N once ``#minecraft:tick`` (and, in tick 0,
``#minecraft:load``) has run — tick functions have run N + 1 times.

It passes when:

* the command succeeds (vanilla's success count is above zero),
* nothing fails *visibly* while it runs — the command itself, or anything it
  runs directly; failures inside called functions stay silent, as in game,
* if ``expect`` is set, that text appears in the game output the command
  produced,
* and every line of ``checks`` holds in the world afterwards (see
  :func:`check_world`). A test with checks may leave the command empty: it
  then only looks at the world.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.common import in_range, nbt_get, normalise_id, parse_snbt, to_snbt
from datapack_emulator.emulator.datapack import Datapack
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus
from datapack_emulator.emulator.vanilla import VanillaAssets
from datapack_emulator.emulator.versions import Version


def _as_int(value: Any, default: int = 0) -> int:
    """A number from a hand-edited project file; anything unreadable is ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class CommandTest:
    command: str
    #: the server tick it runs in, after that tick's functions (0 = the first tick)
    at_tick: int = 0
    #: text the game output of the command must contain ("" = no check)
    expect: str = ""
    enabled: bool = True
    #: range the command's result must be in, like a score check: 5, 1.., ..3, 1..4
    expect_value: str = ""
    #: what must hold in the world afterwards, one check per entry (``check_world``)
    checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        """The command and its expectations on one line."""
        expect = []
        if self.expect:
            expect.append(f"output ~ {self.expect!r}")
        if self.expect_value:
            expect.append(f"value {self.expect_value}")
        expect.extend(f"check {line}" for line in self.checks)
        return (self.command or "(checks only)") + (f"  ({'; '.join(expect)})" if expect else "")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandTest:
        return cls(
            command=str(data.get("command", "")),
            at_tick=max(0, _as_int(data.get("at_tick"))),
            expect=str(data.get("expect", "")),
            enabled=bool(data.get("enabled", True)),
            expect_value=str(data.get("expect_value", "")).strip(),
            checks=[str(line).strip() for line in data.get("checks") or [] if str(line).strip()]
            if isinstance(data.get("checks"), list)
            else [],
        )


@dataclass
class TestResult:
    test: CommandTest
    passed: bool
    reason: str
    value: int = 0
    #: every record emitted while the command ran
    records: list[LogRecord] = field(default_factory=list)

    #: not a pytest test class, despite the name
    __test__ = False


def run_tests(
    datapack: Datapack,
    tests: list[CommandTest],
    version: str | Version | None = None,
    players: int = 1,
    seed: int = 0,
    vanilla: VanillaAssets | None = None,
    output: OutputBus | None = None,
    emulator: Emulator | None = None,
) -> list[TestResult]:
    """Run ``tests`` in one fresh world, in tick order.

    Pass ``emulator`` (a new one, not yet started) to keep the world afterwards;
    the other settings are then taken from it.
    """
    if emulator is None:
        emulator = Emulator(
            datapack,
            version=versions.parse(version),
            players=players,
            output=output or OutputBus(),
            seed=seed,
            vanilla=vanilla,
        )
    emulator.start()
    schedule = TestSchedule(tests)
    while not schedule.done:
        tick = emulator.world.tick
        emulator.run_tick()
        schedule.after_tick(emulator, tick)
    return schedule.results()


class TestSchedule:
    """Tests waiting for their tick while something else drives the ticks — a
    run in the window, a version in the engine, or :func:`run_tests`.

    Call :meth:`after_tick` once server tick N has run (the game time was N when
    it started): every enabled test at tick N, or an earlier one not run yet,
    runs then.
    """

    #: not a pytest test class, despite the name
    __test__ = False

    def __init__(self, tests: list[CommandTest]):
        self.tests = list(tests)
        self._pending = sorted(
            ((index, test) for index, test in enumerate(self.tests) if test.enabled),
            key=lambda pair: pair[1].at_tick,
        )
        self._results: dict[int, TestResult] = {}

    @property
    def done(self) -> bool:
        return not self._pending

    def after_tick(self, emulator: Emulator, tick: int) -> list[int]:
        """Run the tests due after server tick ``tick``; their indexes in ``tests``."""
        ran = []
        while self._pending and self._pending[0][1].at_tick <= tick:
            index, test = self._pending.pop(0)
            self._results[index] = run_one(emulator, test)
            ran.append(index)
        return ran

    def by_index(self, include_unreached: bool = True) -> dict[int, TestResult]:
        """Results keyed by position in ``tests``; tests the run never reached
        fail with "not reached" (disabled tests have no entry)."""
        results = dict(self._results)
        if include_unreached:
            for index, test in self._pending:
                results[index] = TestResult(
                    test, False, f"not reached: the run ended before tick {test.at_tick}"
                )
        return results

    def results(self) -> list[TestResult]:
        """One result per enabled test, in input order."""
        results = self.by_index()
        return [results[index] for index in sorted(results)]


def run_one(emulator: Emulator, test: CommandTest) -> TestResult:
    command = Command.parse(test.command, source="<test>")
    if command is None:
        if test.checks:
            return _checked(emulator, test, 0, [], "passed (checks only)")
        return TestResult(test, False, "nothing to run: the command is empty or a comment")
    # listen rather than slice the bus: a full bus drops its oldest records
    records: list[LogRecord] = []
    emulator.output.listeners.append(records.append)
    try:
        emulator.output.app(f"test: {test.command} (tick {test.at_tick})")
        result = emulator.run_command(command, emulator.root_context())
    finally:
        emulator.output.listeners.remove(records.append)

    visible = [r for r in records if r.failure and r.level >= LogLevel.ERROR]
    crashed = [r for r in records if r.source is LogSource.EMULATOR and r.level >= LogLevel.ERROR]
    game_text = "\n".join(r.message for r in records if r.source is LogSource.GAME)

    if crashed:
        return TestResult(test, False, crashed[0].message, result.value, records)
    if visible:
        return TestResult(test, False, visible[0].message, result.value, records)
    if not result.success:
        return TestResult(test, False, "the command did not succeed", result.value, records)
    if test.expect_value:
        if not valid_range(test.expect_value):
            return TestResult(
                test, False, f"invalid expected value {test.expect_value!r}", result.value, records
            )
        if not in_range(result.value, test.expect_value):
            return TestResult(
                test,
                False,
                f"value {result.value} is not {describe_range(test.expect_value)}",
                result.value,
                records,
            )
    if test.expect and test.expect not in game_text:
        return TestResult(
            test, False, f"expected output not found: {test.expect!r}", result.value, records
        )
    return _checked(emulator, test, result.value, records, f"passed (value {result.value})")


def _checked(
    emulator: Emulator, test: CommandTest, value: int, records: list[LogRecord], detail: str
) -> TestResult:
    for line in test.checks:
        passed, message = check_world(emulator, line)
        if not passed:
            return TestResult(test, False, message, value, records)
    if test.checks:
        detail += f", {len(test.checks)} check(s) held"
    return TestResult(test, True, detail, value, records)


# ---------------------------------------------------------------------------
# world checks
# ---------------------------------------------------------------------------

#: the operator between what is read and what is expected
_OPERATOR = re.compile(r"\s(!?=)\s")

CHECK_HELP = """\
score HOLDER OBJECTIVE = RANGE        #global counter = 3, @p points != 0
storage ID PATH = SNBT                 storage ns:mem x = 1b
entity SELECTOR [PATH] = SNBT          entity @e[type=pig,limit=1] Tags = ["a"]
SELECTOR = RANGE                       @e[type=pig] = 2 (how many match)
block X Y Z = BLOCK                    block 0 64 0 = minecraft:chest[facing=north]
if CONDITION / unless CONDITION        if entity @a[tag=winner]"""


def valid_check(line: str) -> str:
    """Why a check line cannot be read ("" when it can)."""
    text = line.strip()
    if text.startswith(("if ", "unless ")):
        return ""
    match = _OPERATOR.search(text)
    if match is None:
        return f"a check needs ' = ' or ' != ': {text!r}"
    left, right = text[: match.start()].split(), text[match.end() :].strip()
    if not left or not right:
        return f"a check needs something on both sides: {text!r}"
    kind = left[0]
    if kind == "score" and len(left) != 3:
        return "score HOLDER OBJECTIVE = RANGE"
    if kind in ("score",) or kind.startswith("@"):
        if not valid_range(right):
            return f"not a range: {right!r} (5, 1.., ..3, 1..4)"
        if kind.startswith("@") and len(left) != 1:
            return "SELECTOR = RANGE"
        return ""
    if kind == "storage" and len(left) in (2, 3):
        return ""
    if kind == "entity" and len(left) in (2, 3):
        return ""
    if kind == "block" and len(left) == 4:
        return ""
    return f"unknown check {text!r}; one of:\n{CHECK_HELP}"


def check_world(emulator: Emulator, line: str) -> tuple[bool, str]:
    """Check one line against the world: (passed, why not)."""
    from datapack_emulator.emulator.commands.helpers import find_holders, find_targets
    from datapack_emulator.emulator.runtime.debugger import Debugger

    text = line.strip()
    problem = valid_check(text)
    if problem:
        return False, f"check {text!r}: {problem}"
    context = emulator.root_context()
    if text.startswith(("if ", "unless ")):
        passed, error = Debugger().test(text, context)
        return passed, "" if passed else f"check failed: {text}" + (f" ({error})" if error else "")
    match = _OPERATOR.search(text)
    assert match is not None
    negate = match.group(1) == "!="
    words = text[: match.start()].split()
    expected = text[match.end() :].strip()
    kind = words[0]
    world = emulator.world
    version = emulator.version

    if kind == "score" or kind.startswith("@"):
        if kind == "score":
            holders = find_holders(context, words[1])
            value = world.scoreboard.get(holders[0], words[2]) if holders else None
            what = f"score {words[1]} {words[2]}"
        else:
            value = len(find_targets(context, kind))
            what = f"{kind} count"
        wanted = ("not " if negate else "") + describe_range(expected)
        if value is None:
            return negate, "" if negate else f"{what}: expected {wanted}, but it is not set"
        holds = in_range(value, expected) != negate
        return holds, "" if holds else f"{what}: expected {wanted}, got {value}"

    if kind == "block":
        from datapack_emulator.emulator.commands.blocks import parse_block_position
        from datapack_emulator.emulator.runtime.blocks import parse_block_predicate

        position, error = parse_block_position(context, words[1:4])
        if position is None:
            return False, f"check {text!r}: {context.render(error)}"
        predicate = parse_block_predicate(expected)
        if predicate is None:
            return False, f"check {text!r}: not a block: {expected!r}"
        block = world.blocks.get(context.dimension, position)
        holds = predicate.matches(block, version) != negate
        shown = block.id + (
            "[" + ",".join(f"{k}={v}" for k, v in sorted(block.properties.items())) + "]"
            if block.properties
            else ""
        )
        where = " ".join(str(value) for value in position)
        return (
            holds,
            ""
            if holds
            else f"block {where}: expected {'not ' if negate else ''}{expected}, got {shown}",
        )

    # NBT: storage ID [PATH] / entity SELECTOR [PATH]
    if kind == "storage":
        data = world.storage.get(normalise_id(words[1]), {})
        what = f"storage {normalise_id(words[1])}"
    else:
        found = find_targets(context, words[1])
        if not found:
            return negate, "" if negate else f"entity {words[1]}: no entity matches"
        data = found[0].data(version)
        what = f"entity {found[0].display}"
    path = words[2] if len(words) > 2 else ""
    actual = nbt_get(data, path) if path else data
    wanted = snbt_value(expected)
    same = actual is not None and nbt_equal(actual, wanted)
    holds = same != negate
    if holds:
        return True, ""
    shown = "nothing" if actual is None else to_snbt(actual)
    label = f"{what} {path}".rstrip()
    if negate:
        return False, f"{label}: expected anything but {to_snbt(wanted)}"
    diff = nbt_diff(actual, wanted)
    return False, f"{label}: expected {to_snbt(wanted)}, got {shown}" + (
        f" ({diff})" if diff else ""
    )


def snbt_value(text: str) -> Any:
    """Any SNBT value (``5b``, ``"hi"``, ``[1, 2]``, ``{a: 1}``); text that is
    not SNBT is taken as a string."""
    parsed = parse_snbt("{v: " + text + "}")
    return parsed["v"] if "v" in parsed else text.strip().strip('"')


def nbt_equal(actual: Any, wanted: Any) -> bool:
    """Equal as NBT values, ignoring number types (``1b`` is ``1``)."""
    if isinstance(wanted, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == wanted.keys()
            and all(nbt_equal(actual[key], wanted[key]) for key in wanted)
        )
    if isinstance(wanted, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(wanted)
            and all(nbt_equal(a, w) for a, w in zip(actual, wanted, strict=True))
        )
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isclose(float(actual), float(wanted), rel_tol=1e-6, abs_tol=1e-9)
        ) or (isinstance(actual, bool) and float(actual) == float(wanted))
    if isinstance(wanted, bool):
        return isinstance(actual, (int, float)) and float(actual) == float(wanted)
    return actual == wanted


def nbt_diff(actual: Any, wanted: Any, prefix: str = "") -> str:
    """Where two compounds differ: missing, extra and changed keys."""
    if not isinstance(actual, dict) or not isinstance(wanted, dict):
        return ""
    parts = []
    for key in wanted:
        where = f"{prefix}{key}"
        if key not in actual:
            parts.append(f"missing {where}")
        elif not nbt_equal(actual[key], wanted[key]):
            inner = nbt_diff(actual[key], wanted[key], f"{where}.")
            parts.append(inner or f"{where} is {to_snbt(actual[key])}")
    parts.extend(f"extra {prefix}{key}" for key in actual if key not in wanted)
    return "; ".join(parts)


def valid_range(expression: str) -> bool:
    """``5``, ``1..``, ``..3`` or ``1..4``: numbers on at least one side."""
    parts = expression.split("..") if ".." in expression else [expression]
    if len(parts) > 2 or not any(parts):
        return False
    try:
        numbers = [float(part) for part in parts if part]
    except ValueError:
        return False
    if any(number != number or number in (float("inf"), float("-inf")) for number in numbers):
        return False  # nan / inf
    return len(numbers) < 2 or numbers[0] <= numbers[1]


def describe_range(expression: str) -> str:
    """``1..5`` -> "between 1 and 5"."""
    if ".." not in expression:
        return f"exactly {expression}"
    low, _, high = expression.partition("..")
    if low and high:
        return f"between {low} and {high}"
    return f"{low} or more" if low else f"{high} or less"
