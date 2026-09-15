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
* and, if ``expect`` is set, that text appears in the game output the command
  produced.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.common import in_range
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandTest:
        return cls(
            command=str(data.get("command", "")),
            at_tick=max(0, _as_int(data.get("at_tick"))),
            expect=str(data.get("expect", "")),
            enabled=bool(data.get("enabled", True)),
            expect_value=str(data.get("expect_value", "")).strip(),
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
        if not _valid_range(test.expect_value):
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
    detail = f"passed (value {result.value})"
    return TestResult(test, True, detail, result.value, records)


def _valid_range(expression: str) -> bool:
    """``5``, ``1..``, ``..3`` or ``1..4``: numbers on at least one side."""
    parts = expression.split("..") if ".." in expression else [expression]
    if len(parts) > 2 or not any(parts):
        return False
    try:
        for part in parts:
            if part:
                float(part)
    except ValueError:
        return False
    return True


def describe_range(expression: str) -> str:
    """``1..5`` -> "between 1 and 5"."""
    if ".." not in expression:
        return f"exactly {expression}"
    low, _, high = expression.partition("..")
    if low and high:
        return f"between {low} and {high}"
    return f"{low} or more" if low else f"{high} or less"
