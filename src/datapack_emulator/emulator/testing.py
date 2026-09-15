"""Command tests: run a command in a fresh world and check what happened.

A test is what you would type into a server console, or into chat as a player,
once the pack is running — ``function hat:tick``, ``say hi``, ``trigger hat``,
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
from datapack_emulator.emulator.commands.parser import Command, Selector
from datapack_emulator.emulator.commands.result import CommandResult
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
    #: who types it: "" for the server console, or a player name / selector
    run_as: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandTest:
        return cls(
            command=str(data.get("command", "")),
            at_tick=max(0, _as_int(data.get("at_tick"))),
            expect=str(data.get("expect", "")),
            enabled=bool(data.get("enabled", True)),
            run_as=str(data.get("run_as", "")).strip(),
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
) -> list[TestResult]:
    """Run ``tests`` in one fresh world, in tick order."""
    bus = output or OutputBus()
    emulator = Emulator(
        datapack,
        version=versions.parse(version),
        players=players,
        output=bus,
        seed=seed,
        vanilla=vanilla,
    )
    emulator.start()

    results: dict[int, TestResult] = {}
    ordered = sorted(
        ((index, test) for index, test in enumerate(tests) if test.enabled),
        key=lambda pair: pair[1].at_tick,
    )
    for index, test in ordered:
        # the game time is N + 1 once server tick N has run its functions
        while emulator.world.tick <= test.at_tick:
            emulator.run_tick()
        results[index] = run_one(emulator, test)
    return [results[index] for index in sorted(results)]


def run_one(emulator: Emulator, test: CommandTest) -> TestResult:
    command = Command.parse(test.command, source="<test>")
    if command is None:
        return TestResult(test, False, "nothing to run: the command is empty or a comment")
    first = len(emulator.output.records)
    who = f" as {test.run_as}" if test.run_as else ""
    emulator.output.app(f"test: {test.command}{who} (tick {test.at_tick})")
    result = run_as(emulator, command, test.run_as)
    records = emulator.output.records[first:]

    visible = [r for r in records if r.failure and r.level >= LogLevel.ERROR]
    crashed = [r for r in records if r.source is LogSource.EMULATOR and r.level >= LogLevel.ERROR]
    game_text = "\n".join(r.message for r in records if r.source is LogSource.GAME)

    if crashed:
        return TestResult(test, False, crashed[0].message, result.value, records)
    if visible:
        return TestResult(test, False, visible[0].message, result.value, records)
    if not result.success:
        return TestResult(test, False, "the command did not succeed", result.value, records)
    if test.expect and test.expect not in game_text:
        return TestResult(
            test, False, f"expected output not found: {test.expect!r}", result.value, records
        )
    detail = f"passed (value {result.value})"
    return TestResult(test, True, detail, result.value, records)


def run_as(emulator: Emulator, command: Command, who: str = "") -> CommandResult:
    """Run ``command`` the way it would be typed: on the console (``who`` empty)
    or by each entity ``who`` selects, at its position — like ``execute as <who>
    at @s run <command>``."""
    root = emulator.root_context()
    if not who:
        return emulator.run_command(command, root)
    selector = Selector.parse(who)
    found = emulator.world.select(selector, root)
    if not found:
        key = (
            "argument.entity.notfound.player"
            if selector.is_player_only or selector.kind == "literal"
            else "argument.entity.notfound.entity"
        )
        root.game_error(key)
        return CommandResult.failure()
    successes = total = 0
    for entity in found:
        context = root.branch(
            executor=entity,
            position=list(entity.position),
            rotation=list(entity.rotation),
            dimension=entity.dimension,
        )
        result = emulator.run_command(command, context)
        successes += int(result.success)
        total += result.value
    return CommandResult(success=successes > 0, value=total if total else successes)
