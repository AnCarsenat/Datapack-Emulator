"""``world`` and ``shell``: the world dock and the logs dock's command line.

``world`` runs some ticks (and commands) and prints the world: scores,
entities with their NBT, storage, a score's history. ``shell`` keeps a world
open and reads commands from the terminal or a script, like the command line
under the logs, with dot-commands for everything around it (step, run, tests,
world views, explain, save).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shlex
import sys
import time
from dataclasses import replace
from pathlib import Path

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    CliError,
    Inputs,
    add_output_filter,
    add_source_arguments,
    add_vanilla_arguments,
    load_inputs,
    parse_version,
    printing_bus,
    vanilla_for,
)
from datapack_emulator.cli.inspect import print_rows
from datapack_emulator.cli.runs import (
    TICK_SECONDS,
    add_world_arguments,
    parse_test,
    print_result,
    ticks_setting,
)
from datapack_emulator.emulator.analysis.explain import explain_line
from datapack_emulator.emulator.analysis.world_view import (
    entities_text,
    history_text,
    scoreboard_text,
    storage_text,
    world_to_dict,
)
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.testing import CommandTest, TestSchedule, run_tests
from datapack_emulator.emulator.versions import Version
from datapack_emulator.project import Project
from datapack_emulator.settings import EMULATION


class Session:
    """One open world, as the window keeps one: a version, settings, tests."""

    def __init__(self, arguments: argparse.Namespace, inputs: Inputs):
        self.arguments = arguments
        self.inputs = inputs
        self.version: Version = inputs.version(arguments)
        self.players: int = inputs.setting(arguments, "players", EMULATION.DEFAULT_PLAYERS)
        self.seed: int = inputs.setting(arguments, "seed", EMULATION.DEFAULT_SEED)
        self.ticks: int = ticks_setting(arguments, inputs)
        self.tests: list[CommandTest] = list(inputs.tests)
        self.tests_during_runs = bool(inputs.project and inputs.project.tests_during_runs)
        self.step_on_command = bool(
            getattr(arguments, "step_on_command", False)
            or (inputs.project and inputs.project.step_on_command)
        )
        self.realtime = bool(
            getattr(arguments, "realtime", False)
            or (inputs.project and inputs.project.speed == "realtime")
        )
        self.bus = printing_bus(arguments)
        self.vanilla = vanilla_for(arguments, self.version, inputs)
        self.emulator = self.new_world()

    def new_world(self) -> Emulator:
        self.emulator = Emulator(
            self.inputs.datapack,
            version=self.version,
            players=self.players,
            output=self.bus,
            seed=self.seed,
            vanilla=self.vanilla,
        )
        return self.emulator

    # -- ticking -----------------------------------------------------------

    def step(self, count: int = 1) -> None:
        """``count`` more ticks in this world (tests of those ticks run after them
        when tests run during runs)."""
        emulator = self.emulator
        emulator.start()
        schedule = None
        if self.tests_during_runs:
            first = emulator.world.tick
            schedule = TestSchedule(
                [
                    test if first <= test.at_tick < first + count else replace(test, enabled=False)
                    for test in self.tests
                ]
            )
        for _ in range(count):
            started = time.perf_counter()
            tick = emulator.world.tick
            emulator.run_tick()
            if schedule is not None:
                for index in schedule.after_tick(emulator, tick):
                    result = schedule.by_index(include_unreached=False)[index]
                    print_result(self.version, result, show_records=False)
            if self.realtime:
                time.sleep(max(0.0, TICK_SECONDS - (time.perf_counter() - started)))

    def run(self, ticks: int | None = None) -> None:
        """Run all: a fresh world for ``ticks`` ticks."""
        self.new_world()
        count = self.ticks if ticks is None else ticks
        self.emulator.start()
        if count <= 0:
            self.emulator.run(ticks=0)
        self.step(count)

    def typed(self, line: str) -> None:
        result, started = self.emulator.run_typed(line)
        if result is None:
            return
        if started:
            print("(started the world: ran the first tick)")
        outcome = "succeeded" if result.success else "failed"
        message = f"→ {outcome} (value {result.value}) at game time {self.emulator.world.tick}"
        if self.step_on_command:
            self.step()
            message += f", stepped to {self.emulator.world.tick}"
        print(message)

    def run_tests(self, indexes: list[int] | None = None) -> bool:
        """The window's run tests: the enabled tests (or some) in a fresh world."""
        tests = [
            test if indexes is None or index in indexes else replace(test, enabled=False)
            for index, test in enumerate(self.tests)
        ]
        if not any(test.enabled for test in tests):
            print("no enabled test to run")
            return True
        self.new_world()
        results = run_tests(self.inputs.datapack, tests, emulator=self.emulator)
        for result in results:
            print_result(self.version, result, show_records=False)
        passed = sum(result.passed for result in results)
        print(f"{passed}/{len(results)} passed")
        return passed == len(results)

    # -- saving ------------------------------------------------------------

    def save(self, path: Path | None) -> Path:
        project = self.inputs.project
        if project is None:
            project = Project(
                name=(path.stem if path else self.inputs.datapack.name),
                datapacks=list(self.inputs.datapack.paths),
            )
            self.inputs.project = project
        project.version = self.version.id
        project.players = self.players
        project.seed = self.seed
        project.ticks = self.ticks
        project.tests = [test.to_dict() for test in self.tests]
        project.tests_during_runs = self.tests_during_runs
        project.step_on_command = self.step_on_command
        if self.vanilla is not None:
            project.vanilla_jar = str(self.vanilla.jar_path)
        return project.save(path)


# ---------------------------------------------------------------------------
# printing the world
# ---------------------------------------------------------------------------


def print_world(session: Session, arguments: argparse.Namespace) -> None:
    world = session.emulator.world
    version = session.version
    parts = [
        name for name in ("scores", "entities", "storage") if getattr(arguments, name, False)
    ] or ["scores", "entities", "storage"]
    if getattr(arguments, "json", False):
        print(json.dumps(world_to_dict(world, version), indent=2, default=str))
        return
    print(f"game time {world.tick} · {version.id} · {len(world.entities)} entities")
    if "scores" in parts:
        print("\n# scoreboard")
        print(scoreboard_text(world, arguments.holder or "", arguments.objective or ""))
    if "entities" in parts:
        print("\n# entities")
        print(entities_text(world, arguments.holder or "", version, nbt=arguments.nbt))
    if "storage" in parts:
        print("\n# storage")
        print(storage_text(world, arguments.holder or ""))


def command_world(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    session = Session(arguments, inputs)
    session.run()
    for line in arguments.command or []:
        print(f"> {line}")
        session.typed(line)
    if arguments.history:
        holder, objective = arguments.history
        board = session.emulator.world.scoreboard
        if arguments.json:
            changes = board.history.get((holder, objective), ())
            print(json.dumps([{"tick": tick, "value": value} for tick, value in changes]))
        else:
            print(history_text(board, holder, objective, shown=board.HISTORY))
        return OK
    print_world(session, arguments)
    return OK


def add_view_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("what to print (default: all three)")
    group.add_argument("--scores", action="store_true", help="the scoreboard grid")
    group.add_argument("--entities", action="store_true", help="the entities")
    group.add_argument("--storage", action="store_true", help="command storage")
    group.add_argument("--nbt", action="store_true", help="with the entities' full NBT")
    group.add_argument("--holder", default=None, help="filter holders / entities / storage")
    group.add_argument("--objective", default=None, help="filter objectives")
    group.add_argument(
        "--history",
        nargs=2,
        metavar=("HOLDER", "OBJECTIVE"),
        help="the values a score took and when (the score graph)",
    )
    group.add_argument("--json", action="store_true", help="everything as JSON")


def register_world(subparsers) -> None:
    world = subparsers.add_parser(
        "world",
        help="run, then print the world: scores, entities, storage",
        description="The world dock: run the pack for --ticks ticks (the project's by "
        "default), run --command lines as typed commands, then print the scoreboard grid "
        "(* = trigger enabled), the entities and command storage.",
    )
    add_source_arguments(world)
    world.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )
    add_world_arguments(world)
    world.add_argument(
        "--command",
        "-c",
        action="append",
        metavar="COMMAND",
        help="a command to run after the ticks (repeatable)",
    )
    add_view_arguments(world)
    add_output_filter(world, default_level="warn")
    add_vanilla_arguments(world)
    world.set_defaults(handler=command_world)


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------

SHELL_HELP = """\
Commands are run on the server console, as typed in the logs dock
(`execute as Player1 run trigger hat` to act as a player; `/` is optional).
Lines starting with a dot control the session:

  .step [N]              N more ticks (F7)
  .run [N]               a fresh world for N ticks (default: --ticks) (F5)
  .reset                 a fresh world, no ticks
  .tick                  the game time
  .scores [FILTER]       the scoreboard grid (* = trigger enabled)
  .entities [FILTER]     entities; .nbt [FILTER] with their NBT
  .storage [FILTER]      command storage
  .world                 all three
  .json                  the world as JSON
  .history HOLDER OBJ    the values a score took and when
  .explain COMMAND       analyze a command line
  .profile               the per-tick call tree
  .version [VERSION]     show or switch the version (a fresh world)
  .players N / .seed N   change them (a fresh world)
  .ticks N               ticks of .run
  .realtime on|off       20 ticks per second, or as fast as possible
  .step-on-command on|off   one more tick after every command
  .tests                 list the tests
  .test [TICK:]COMMAND   add a test
  .expect N TEXT / .expect-value N RANGE   set test N's expectations
  .enable N / .disable N / .remove N / .move N UP|DOWN   edit tests
  .runtests [N ...]      run the enabled tests (or some) in a fresh world (F8)
  .during-runs on|off    run the tests during .step and .run
  .save [PATH]           save the project (.dpemu)
  .help                  this text
  .quit                  leave (also Ctrl+D)
"""


def _flag(text: str) -> bool:
    if text.lower() in ("on", "yes", "true", "1"):
        return True
    if text.lower() in ("off", "no", "false", "0"):
        return False
    raise CliError(f"expected on or off, not {text!r}")


def _index(session: Session, text: str) -> int:
    try:
        index = int(text) - 1
    except ValueError:
        raise CliError(f"not a test number: {text!r}") from None
    if not 0 <= index < len(session.tests):
        raise CliError(f"no test {text} (there are {len(session.tests)})")
    return index


def _number(text: str, minimum: int = 0) -> int:
    try:
        value = int(text)
    except ValueError:
        raise CliError(f"not a whole number: {text!r}") from None
    if value < minimum:
        raise CliError(f"must be {minimum} or more: {value}")
    return value


class Shell:
    def __init__(self, session: Session, arguments: argparse.Namespace):
        self.session = session
        self.arguments = arguments
        self.failed = False

    def view_arguments(self, **overrides) -> argparse.Namespace:
        values = {
            "scores": False,
            "entities": False,
            "storage": False,
            "nbt": False,
            "holder": "",
            "objective": "",
            "json": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def dot(self, line: str) -> bool:
        """Handle a dot-command; ``False`` means quit."""
        session = self.session
        name, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        words = shlex.split(rest) if rest else []
        world = session.emulator.world
        if name in ("quit", "exit", "q"):
            return False
        if name == "help":
            print(SHELL_HELP)
        elif name == "step":
            session.step(_number(words[0], 1) if words else 1)
            print(f"game time {session.emulator.world.tick}")
        elif name == "run":
            session.run(_number(words[0]) if words else None)
            print(f"ran {session.emulator.profiler.ticks} tick(s) on {session.version.id}")
            print(session.emulator.profiler.summary())
        elif name == "reset":
            session.new_world()
            print(f"fresh world on {session.version.id}")
        elif name == "tick":
            print(f"game time {world.tick}")
        elif name == "scores":
            print(scoreboard_text(world, rest))
        elif name in ("entities", "nbt"):
            print(entities_text(world, rest, session.version, nbt=name == "nbt"))
        elif name == "storage":
            print(storage_text(world, rest))
        elif name == "world":
            print_world(session, self.view_arguments())
        elif name == "json":
            print_world(session, self.view_arguments(json=True))
        elif name == "history":
            if len(words) != 2:
                raise CliError("usage: .history HOLDER OBJECTIVE")
            print(
                history_text(world.scoreboard, words[0], words[1], shown=world.scoreboard.HISTORY)
            )
        elif name == "explain":
            if not rest:
                raise CliError("usage: .explain COMMAND")
            view = session.inputs.datapack.view_for(session.version)
            print_rows(explain_line(rest, session.version, view=view, vanilla=session.vanilla))
        elif name == "profile":
            self.profile()
        elif name == "version":
            if words:
                session.version = parse_version(words[0])
                session.vanilla = vanilla_for(self.arguments, session.version, session.inputs)
                session.new_world()
            print(f"{session.version.id} (pack_format {session.version.format_string})")
        elif name in ("players", "seed", "ticks"):
            if not words:
                print(getattr(session, name))
            else:
                value = int(words[0]) if name == "seed" else _number(words[0])
                setattr(session, name, value)
                if name != "ticks":
                    session.new_world()
                print(f"{name} = {value}")
        elif name == "realtime":
            session.realtime = _flag(words[0]) if words else not session.realtime
            print(f"realtime {'on' if session.realtime else 'off'}")
        elif name == "step-on-command":
            session.step_on_command = _flag(words[0]) if words else not session.step_on_command
            print(f"step on command {'on' if session.step_on_command else 'off'}")
        elif name == "during-runs":
            session.tests_during_runs = _flag(words[0]) if words else not session.tests_during_runs
            print(f"tests during runs {'on' if session.tests_during_runs else 'off'}")
        elif name == "tests":
            self.list_tests()
        elif name == "test":
            if not rest:
                raise CliError("usage: .test [TICK:]COMMAND")
            session.tests.append(parse_test(rest))
            self.list_tests()
        elif name in ("expect", "expect-value"):
            number, _, value = rest.partition(" ")
            test = session.tests[_index(session, number)]
            if name == "expect":
                test.expect = value
            else:
                test.expect_value = value.strip()
            self.list_tests()
        elif name in ("enable", "disable", "remove"):
            index = _index(session, rest)
            if name == "remove":
                del session.tests[index]
            else:
                session.tests[index].enabled = name == "enable"
            self.list_tests()
        elif name == "move":
            if len(words) != 2 or words[1].lower() not in ("up", "down"):
                raise CliError("usage: .move N up|down")
            index = _index(session, words[0])
            target = index + (-1 if words[1].lower() == "up" else 1)
            if 0 <= target < len(session.tests):
                tests = session.tests
                tests[index], tests[target] = tests[target], tests[index]
            self.list_tests()
        elif name == "runtests":
            indexes = [_index(session, word) for word in words] if words else None
            if not session.run_tests(indexes):
                self.failed = True
        elif name == "save":
            path = session.save(Path(rest) if rest else None)
            print(f"saved {path}")
        else:
            raise CliError(f"unknown dot-command .{name} (see .help)")
        return True

    def list_tests(self) -> None:
        if not self.session.tests:
            print("no tests")
        for number, test in enumerate(self.session.tests, start=1):
            box = "x" if test.enabled else " "
            expect = []
            if test.expect:
                expect.append(f"output ~ {test.expect!r}")
            if test.expect_value:
                expect.append(f"value {test.expect_value}")
            print(
                f"{number:3}. [{box}] tick {test.at_tick:<4} {test.command}"
                + (f"  ({'; '.join(expect)})" if expect else "")
            )

    def profile(self) -> None:
        profiler = self.session.emulator.profiler
        print(profiler.summary())
        print(
            f"{'call path':50} {'ms/tick':>9} {'share':>7} {'self ms':>9} {'calls':>7} {'cmds':>7}"
        )
        for path, one in profiler.tree_rows():
            label = "  " * (len(path) - 1) + path[-1]
            print(
                f"{label:50} {one['total_us'] / 1000:9.4f} {one['share']:7.1%} "
                f"{one['self_us'] / 1000:9.4f} {one['calls']:7.2f} {one['commands']:7.1f}"
            )

    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line or line.startswith("#"):
            return True
        try:
            if line.startswith("."):
                return self.dot(line)
            self.session.typed(line)
        except CliError as exc:
            print(f"error: {exc}")
            self.failed = True
        return True

    def loop(self, stream, interactive: bool) -> None:
        if interactive:
            with contextlib.suppress(ImportError):
                import readline  # noqa: F401  (line editing and history for input())
            print(
                f"{self.session.inputs.datapack.name} on {self.session.version.id} — "
                "type a command, or .help"
            )
            while True:
                try:
                    line = input(f"[{self.session.emulator.world.tick}]> ")
                except EOFError:
                    print()
                    return
                except KeyboardInterrupt:
                    print()
                    continue
                if not self.handle(line):
                    return
        for line in stream:
            if not line.strip():
                continue
            print(f"> {line.rstrip()}")
            if not self.handle(line):
                return


def command_shell(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    session = Session(arguments, inputs)
    shell = Shell(session, arguments)
    if arguments.run:
        session.run()
    if arguments.script:
        for script in arguments.script:
            try:
                with open(script, encoding="utf-8") as stream:
                    shell.loop(stream, interactive=False)
            except OSError as exc:
                raise CliError(f"cannot read {script}: {exc}") from exc
    elif sys.stdin.isatty():
        shell.loop(sys.stdin, interactive=True)
    else:
        shell.loop(sys.stdin, interactive=False)
    return FAILED if shell.failed and arguments.strict else OK


def register_shell(subparsers) -> None:
    shell = subparsers.add_parser(
        "shell",
        help="an open world: type commands, step, test, inspect",
        description="The window's command line, step and tests in a terminal. Reads commands "
        "from the keyboard, from --script files or from standard input; lines starting with "
        "a dot control the session (.help lists them).",
        epilog=SHELL_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_source_arguments(shell)
    shell.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )
    add_world_arguments(shell)
    shell.add_argument("--run", action="store_true", help="run all (--ticks ticks) first")
    shell.add_argument(
        "--script", action="append", metavar="FILE", help="read lines from FILE (repeatable)"
    )
    shell.add_argument(
        "--step-on-command", action="store_true", help="one more tick after every command"
    )
    shell.add_argument("--realtime", action="store_true", help="20 ticks per second")
    shell.add_argument(
        "--strict",
        action="store_true",
        help="exit with 1 when a dot-command failed or .runtests had a failure",
    )
    add_output_filter(shell)
    add_vanilla_arguments(shell)
    shell.set_defaults(handler=command_shell)


def register(subparsers) -> None:
    register_world(subparsers)
    register_shell(subparsers)
