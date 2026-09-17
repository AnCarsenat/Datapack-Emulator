"""``world`` and ``shell``: the world dock and the logs dock's command line.

``world`` runs some ticks (and commands) and prints the world: scores,
entities with their NBT, storage, a score's history. ``shell`` keeps a world
open and reads commands from the terminal or a script, like the command line
under the logs, with dot-commands for everything around it (step, run, tests,
packs, client jar, world views, explain, reports, save).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shlex
import sys
import time
from collections.abc import Iterable, Iterator
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
    format_record,
    load_inputs,
    parse_version,
    printing_bus,
    vanilla_for,
)
from datapack_emulator.cli.debug import (
    DEBUG_HELP,
    DebugCommands,
    add_debug_arguments,
    setup_debugger,
)
from datapack_emulator.cli.inspect import print_rows
from datapack_emulator.cli.runs import (
    TICK_SECONDS,
    add_world_arguments,
    parse_test,
    print_comparison,
    print_result,
    ticks_setting,
)
from datapack_emulator.emulator.analysis.explain import explain_line
from datapack_emulator.emulator.analysis.profiler import Profiler
from datapack_emulator.emulator.analysis.world_view import (
    blocks_text,
    entities_text,
    history_text,
    scoreboard_text,
    state_text,
    storage_text,
    world_to_dict,
)
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.datapack import DatapackSet
from datapack_emulator.emulator.runtime.debugger import Debugger, DebugStopped
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.snapshot import Snapshot, capture, compare, restore
from datapack_emulator.emulator.testing import (
    CommandTest,
    TestResult,
    TestSchedule,
    run_tests,
    valid_check,
    valid_range,
)
from datapack_emulator.emulator.vanilla import default_library
from datapack_emulator.emulator.versions import Version
from datapack_emulator.project import Project
from datapack_emulator.settings import EMULATION

#: the parts of the world ``world`` and the shell print
PARTS = ("scores", "entities", "storage", "blocks", "state")


class Session:
    """One open world, as the window keeps one: packs, a version, settings, tests."""

    def __init__(self, arguments: argparse.Namespace, inputs: Inputs, bus=None):
        self.arguments = arguments
        self.inputs = inputs
        project = inputs.project
        self.version: Version = inputs.version(arguments)
        self.players: int = inputs.setting(arguments, "players", EMULATION.DEFAULT_PLAYERS)
        self.seed: int = inputs.setting(arguments, "seed", EMULATION.DEFAULT_SEED)
        self.ticks: int = ticks_setting(arguments, inputs)
        self.tests: list[CommandTest] = list(inputs.tests)
        self.tests_during_runs = bool(project and project.tests_during_runs)
        self.step_on_command = bool(
            getattr(arguments, "step_on_command", False) or (project and project.step_on_command)
        )
        self.realtime = bool(
            getattr(arguments, "realtime", False) or (project and project.speed == "realtime")
        )
        #: settings changed in this session (or on the command line): saved as they are
        self.changed: set[str] = {
            name
            for name in ("version", "players", "seed", "ticks")
            if getattr(arguments, name, None) is not None
        }
        if getattr(arguments, "step_on_command", False):
            self.changed.add("step_on_command")
        if getattr(arguments, "realtime", False):
            self.changed.add("realtime")
        self.bus = bus if bus is not None else printing_bus(arguments)
        self.vanilla = vanilla_for(arguments, self.version, inputs)
        #: the last run tests' results, by position in ``tests``
        self.results: dict[int, TestResult] = {}
        #: worlds kept aside (.snapshot), in the order they were taken
        self.snapshots: list[Snapshot] = []
        #: a run kept to compare later runs against (.baseline)
        self.baseline: Profiler | None = None
        #: breakpoints and watches, kept across new worlds (checked in the shell)
        self.debugger = Debugger()
        if project is not None:
            for entry in self.debugger.load_strings(project.breakpoints):
                print(f"error: cannot read the project's breakpoint {entry!r}")
            self.debugger.watches = list(project.watches)
        self.emulator = self.new_world()

    @property
    def datapack(self) -> DatapackSet:
        return self.inputs.datapack

    def new_world(self) -> Emulator:
        self.emulator = Emulator(
            self.datapack,
            version=self.version,
            players=self.players,
            output=self.bus,
            seed=self.seed,
            vanilla=self.vanilla,
        )
        self.debugger.reset()
        self.emulator.debugger = self.debugger
        return self.emulator

    # -- ticking -----------------------------------------------------------

    def step(self, count: int = 1) -> bool:
        """``count`` more ticks in this world (tests of those ticks run after them
        when tests run during runs). ``count`` -1 ticks until Ctrl+C. Whether
        the debugger abandoned a tick."""
        emulator = self.emulator
        emulator.start()
        schedule = None
        if self.tests_during_runs:
            first = emulator.world.tick
            last = first + count if count >= 0 else None
            schedule = TestSchedule(
                [
                    test
                    if test.at_tick >= first and (last is None or test.at_tick < last)
                    else replace(test, enabled=False)
                    for test in self.tests
                ]
            )
        done = 0
        try:
            while count < 0 or done < count:
                started = time.perf_counter()
                tick = emulator.world.tick
                emulator.run_tick()
                done += 1
                if schedule is not None:
                    self._report(schedule, schedule.after_tick(emulator, tick))
                if self.realtime:
                    time.sleep(max(0.0, TICK_SECONDS - (time.perf_counter() - started)))
        except KeyboardInterrupt:
            print(f"stopped after {done} tick(s)")
        except DebugStopped as stop:
            self.stopped(stop)
            return True
        return False

    def stopped(self, stop: DebugStopped) -> None:
        """A tick abandoned from the debugger: the world keeps what it did."""
        self.debugger.reset()
        print(f"stopped at {stop}; the rest of that tick did not run (it still counts)")

    def _report(self, schedule: TestSchedule, indexes: list[int]) -> None:
        results = schedule.by_index(include_unreached=False)
        for index in indexes:
            self.results[index] = results[index]
            print_result(self.version, results[index], show_records=False)

    def run(self, ticks: int | None = None) -> None:
        """Run all: a fresh world for ``ticks`` ticks; tests during runs that
        the run does not reach are reported, as the window does."""
        self.new_world()
        self.results = {}
        count = self.ticks if ticks is None else ticks
        self.emulator.start()
        if count == 0:
            try:
                self.emulator.run(ticks=0)
            except DebugStopped as stop:
                self.stopped(stop)
        self.step(count)
        if self.tests_during_runs and count >= 0:
            for index, test in enumerate(self.tests):
                if test.enabled and index not in self.results:
                    result = TestResult(
                        test, False, f"not reached: the run ended before tick {test.at_tick}"
                    )
                    self.results[index] = result
                    print_result(self.version, result, show_records=False)

    def typed(self, line: str) -> None:
        if Command.parse(line.strip().removeprefix("/")) is None:
            return
        if self.tests_during_runs and (not self.emulator.started or self.emulator.world.tick == 0):
            # like a server that is up, with that tick's tests
            if self.step():
                return
            print("(started the world: ran the first tick)")
        try:
            result, started = self.emulator.run_typed(line)
        except DebugStopped as stop:
            self.stopped(stop)
            return
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
        try:
            results = run_tests(self.datapack, tests, emulator=self.emulator)
        except DebugStopped as stop:
            self.stopped(stop)
            return False
        enabled = [index for index, test in enumerate(tests) if test.enabled]
        self.results = dict(zip(enabled, results, strict=True))
        for result in results:
            print_result(self.version, result, show_records=False)
        passed = sum(result.passed for result in results)
        print(f"{passed}/{len(results)} passed")
        return passed == len(results)

    # -- packs and jars ------------------------------------------------------

    def set_packs(self, paths: list[Path]) -> None:
        datapack = DatapackSet.load(paths)
        for error in datapack.errors:
            print(f"error: {error}")
        self.inputs.datapack = datapack
        if self.inputs.project is not None:
            self.inputs.project.datapacks = list(paths)
        self.new_world()

    def set_jar(self, text: str) -> None:
        if text == "none":
            self.vanilla = None
        elif Path(text).is_file():
            try:
                self.vanilla = default_library().load_jar(Path(text))
            except Exception as exc:  # any unreadable file
                raise CliError(f"cannot read client jar {text}: {exc}") from exc
        else:
            version = parse_version(text) if text else self.version
            try:
                self.vanilla = default_library().load(
                    version.id, allow_download=getattr(self.arguments, "download", False)
                )
            except Exception as exc:  # a broken jar or a failed download
                raise CliError(f"cannot get the client jar for {version.id}: {exc}") from exc
            if self.vanilla is None:
                raise CliError(f"no client jar for {version.id} (start with --download to fetch)")
        self.changed.add("vanilla")
        self.new_world()

    # -- saving ------------------------------------------------------------

    def save(self, path: Path | None) -> Path:
        project = self.inputs.project
        changed = set(self.changed)
        if project is None:
            if path is None:
                raise CliError("no project open: give a file, .save FILE.dpemu")
            project = Project(name=path.stem, datapacks=list(self.datapack.paths))
            changed |= {"version", "players", "seed", "ticks", "realtime"}
        if "version" in changed:
            project.version = self.version.id
        for name in ("players", "seed", "ticks"):
            if name in changed:
                setattr(project, name, getattr(self, name))
        if "realtime" in changed:
            project.speed = "realtime" if self.realtime else "fast"
        if "vanilla" in changed:
            project.vanilla_jar = str(self.vanilla.jar_path) if self.vanilla else ""
        project.tests = [test.to_dict() for test in self.tests]
        project.breakpoints = self.debugger.to_strings()
        project.watches = list(self.debugger.watches)
        project.tests_during_runs = self.tests_during_runs
        project.step_on_command = self.step_on_command
        try:
            saved = project.save(path)
        except OSError as exc:
            raise CliError(f"cannot save {path or project.path}: {exc}") from exc
        self.inputs.project = project
        return saved


# ---------------------------------------------------------------------------
# printing the world
# ---------------------------------------------------------------------------


def chosen_parts(arguments: argparse.Namespace) -> list[str]:
    return [name for name in PARTS if getattr(arguments, name, False)] or list(PARTS)


def world_json(session: Session, arguments: argparse.Namespace) -> dict:
    """The world as JSON, limited to the parts and filters asked for."""
    data = world_to_dict(session.emulator.world, session.version)
    parts = chosen_parts(arguments)
    holder = (arguments.holder or "").lower()
    objective = (arguments.objective or "").lower()
    out = {"tick": data["tick"], "version": session.version.id}
    if "scores" in parts:
        out["objectives"] = {
            name: value for name, value in data["objectives"].items() if objective in name.lower()
        }
        out["scores"] = {
            name: {key: value for key, value in scores.items() if objective in key.lower()}
            for name, scores in data["scores"].items()
            if holder in name.lower()
        }
        out["enabled_triggers"] = data["enabled_triggers"]
    if "entities" in parts:
        out["entities"] = [
            entity
            for entity in data["entities"]
            if not holder
            or holder in entity["name"].lower()
            or holder in entity["uuid"]
            or holder in entity["type"]
        ]
    if "storage" in parts:
        out["storage"] = {
            key: value for key, value in data["storage"].items() if holder in key.lower()
        }
    if "blocks" in parts:
        out["blocks"] = [
            block
            for block in data["blocks"]
            if not holder
            or holder in block["block"]
            or holder in " ".join(str(value) for value in block["pos"])
        ]
    if "state" in parts:
        out["state"] = data["state"]
    out["gamerules"] = data["gamerules"]
    return out


def print_world(session: Session, arguments: argparse.Namespace) -> None:
    world = session.emulator.world
    version = session.version
    if getattr(arguments, "json", False):
        print(json.dumps(world_json(session, arguments), indent=2, default=str))
        return
    parts = chosen_parts(arguments)
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
    if "blocks" in parts:
        print("\n# blocks")
        print(blocks_text(world, arguments.holder or "", version))
    if "state" in parts:
        print("\n# server")
        print(state_text(world))


def command_world(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    # with --json, standard output is for the JSON alone: the rest goes to stderr
    side = sys.stderr if arguments.json else sys.stdout
    bus = printing_bus(arguments, stream=side)
    with contextlib.redirect_stdout(side):
        session = Session(arguments, inputs, bus=bus)
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
    group = parser.add_argument_group("what to print (default: everything)")
    group.add_argument("--scores", action="store_true", help="the scoreboard grid")
    group.add_argument("--entities", action="store_true", help="the entities")
    group.add_argument("--storage", action="store_true", help="command storage")
    group.add_argument("--blocks", action="store_true", help="the blocks commands placed")
    group.add_argument(
        "--state", action="store_true", help="time, weather, difficulty, border, teams"
    )
    group.add_argument("--nbt", action="store_true", help="with the entities' full NBT")
    group.add_argument(
        "--holder", default=None, help="filter holders / entities / storage / blocks"
    )
    group.add_argument("--objective", default=None, help="filter objectives")
    group.add_argument(
        "--history",
        nargs=2,
        metavar=("HOLDER", "OBJECTIVE"),
        help="the values a score took and when (the score graph)",
    )
    group.add_argument(
        "--json",
        action="store_true",
        help="print JSON (the parts and filters asked for); everything else goes to stderr",
    )


def register_world(subparsers) -> None:
    world = subparsers.add_parser(
        "world",
        help="run, then print the world: scores, entities, storage",
        description="The world dock: run the pack for --ticks ticks (the project's by "
        "default), run --command lines as typed commands, then print the scoreboard grid "
        "(* = trigger enabled), the entities, command storage and the blocks commands placed.",
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

SHELL_HELP = (
    """\
Commands are run on the server console, as typed in the logs dock
(`execute as Player1 run trigger hat` to act as a player; `/` is optional).
Lines starting with a dot control the session:

  world    .step [N]             N more ticks (F7); -1 until Ctrl+C
           .run [N]              a fresh world for N ticks (default: --ticks) (F5)
           .reset                a fresh world, no ticks
           .tick                 the game time
           .scores [FILTER]      the scoreboard grid (* = trigger enabled)
           .entities [FILTER]    entities; .nbt [FILTER] with their NBT
           .storage [FILTER]     command storage
           .blocks [FILTER]      the blocks commands placed
           .state                time, weather, difficulty, border, teams
           .world / .json        everything, as text or JSON
           .history HOLDER OBJ   the values a score took and when
  worlds   .snapshot [LABEL]     keep this world aside; .snapshots lists them
           .rewind N             put snapshot N back
           .diff N [M]           what changed between N and M (or the world now)
           .unsnapshot N|all
  analyze  .explain COMMAND      analyze a command line
           .profile              the per-tick call tree
           .hot [N]              the lines that cost the most
           .baseline / .compare  keep this run, then compare a later one
           .report [FILE]        write the HTML profiler report
           .dot [FILE]           write the call graph for Graphviz
  settings .version [V]          show or switch the version (a fresh world)
           .players [N] / .seed [N] / .ticks [N]
           .realtime on|off      20 ticks per second, or as fast as possible
           .step-on-command on|off   one more tick after every command
           .during-runs on|off   run the tests during .step and .run
  packs    .packs                list the datapacks and the client jar
           .add-pack DIR / .remove-pack N / .move-pack N up|down / .reload
           .jar [VERSION|FILE|none]  the client jar (default: the version's)
  tests    .tests                list the tests (with their last result)
           .test [TICK:]COMMAND  add a test
           .expect N TEXT / .expect-value N RANGE / .at N TICK
           .check N CHECK        what must hold afterwards (score #g t = 3, …)
           .uncheck N M|all      remove test N's check M
           .enable N… / .disable N… / .remove N… / .duplicate N / .move N up|down
           .runtests [N …]       run the enabled tests (or some) in a fresh world (F8)
           .records N            the records of test N's last run
  project  .save [FILE]          save the project (.dpemu)
  shell    .help, .quit          (Ctrl+D quits too)
"""
    + DEBUG_HELP
)


def _flag(text: str) -> bool:
    if text.lower() in ("on", "yes", "true", "1"):
        return True
    if text.lower() in ("off", "no", "false", "0"):
        return False
    raise CliError(f"expected on or off, not {text!r}")


def _position(items: list, text: str, what: str = "test") -> int:
    try:
        index = int(text) - 1
    except ValueError:
        raise CliError(f"not a {what} number: {text!r}") from None
    if not 0 <= index < len(items):
        raise CliError(f"no {what} {text} (there are {len(items)})")
    return index


def _number(text: str, minimum: int | None = 0) -> int:
    try:
        value = int(text)
    except ValueError:
        raise CliError(f"not a whole number: {text!r}") from None
    if minimum is not None and value < minimum:
        raise CliError(f"must be {minimum} or more: {value}")
    return value


def _words(rest: str) -> list[str]:
    try:
        return shlex.split(rest)
    except ValueError as exc:
        raise CliError(f"cannot read the arguments: {exc}") from None


def _move(items: list, index: int, direction: str) -> None:
    if direction.lower() not in ("up", "down"):
        raise CliError("move up or down")
    target = index + (-1 if direction.lower() == "up" else 1)
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]


def session_name(session: Session) -> str:
    return f"{session.datapack.name.replace(' + ', '+')}-{session.version.id}"


class Shell(DebugCommands):
    def __init__(self, session: Session, arguments: argparse.Namespace):
        self.session: Session = session
        self.arguments = arguments
        self.failed = False
        self.quit = False
        self._lines: Iterator[str] = iter(())
        session.debugger.on_pause = self.on_pause

    @staticmethod
    def view_arguments(**overrides) -> argparse.Namespace:
        values: dict = {name: False for name in PARTS}
        values.update(nbt=False, holder="", objective="", json=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def dot(self, line: str) -> None:
        """Handle one dot-command; mistakes are raised as CliError."""
        name, _, rest = line[1:].partition(" ")
        handler = getattr(self, "do_" + name.replace("-", "_"), None)
        if not name or handler is None:
            raise CliError(f"unknown dot-command .{name} (see .help)")
        handler(self.session, rest.strip())

    # -- the world -------------------------------------------------------

    def do_quit(self, session: Session, rest: str) -> None:
        self.quit = True

    do_exit = do_q = do_quit

    def do_help(self, session: Session, rest: str) -> None:
        print(SHELL_HELP)

    def do_step(self, session: Session, rest: str) -> None:
        session.step(_number(rest, -1) if rest else 1)
        print(f"game time {session.emulator.world.tick}")

    def do_run(self, session: Session, rest: str) -> None:
        session.run(_number(rest, -1) if rest else None)
        print(f"ran {session.emulator.profiler.ticks} tick(s) on {session.version.id}")
        print(session.emulator.profiler.summary())

    def do_reset(self, session: Session, rest: str) -> None:
        session.new_world()
        print(f"fresh world on {session.version.id}")

    def do_tick(self, session: Session, rest: str) -> None:
        print(f"game time {session.emulator.world.tick}")

    def do_scores(self, session: Session, rest: str) -> None:
        print(scoreboard_text(session.emulator.world, rest))

    def do_entities(self, session: Session, rest: str) -> None:
        print(entities_text(session.emulator.world, rest, session.version))

    def do_nbt(self, session: Session, rest: str) -> None:
        print(entities_text(session.emulator.world, rest, session.version, nbt=True))

    def do_storage(self, session: Session, rest: str) -> None:
        print(storage_text(session.emulator.world, rest))

    def do_blocks(self, session: Session, rest: str) -> None:
        print(blocks_text(session.emulator.world, rest, session.version))

    def do_state(self, session: Session, rest: str) -> None:
        print(state_text(session.emulator.world))

    def do_world(self, session: Session, rest: str) -> None:
        print_world(session, self.view_arguments(holder=rest))

    def do_json(self, session: Session, rest: str) -> None:
        print_world(session, self.view_arguments(json=True, holder=rest))

    # -- snapshots -------------------------------------------------------

    def do_snapshot(self, session: Session, rest: str) -> None:
        session.snapshots.append(capture(session.emulator, rest.strip()))
        print(f"{len(session.snapshots)}. {session.snapshots[-1].describe()}")

    def do_snapshots(self, session: Session, rest: str) -> None:
        if not session.snapshots:
            print("no snapshots (.snapshot [LABEL] takes one)")
        for number, snapshot in enumerate(session.snapshots, start=1):
            print(f"{number}. {snapshot.describe()}")

    def _snapshot(self, session: Session, text: str) -> Snapshot:
        return session.snapshots[_position(session.snapshots, text, "snapshot")]

    def do_rewind(self, session: Session, rest: str) -> None:
        if not rest:
            raise CliError("usage: .rewind N (see .snapshots)")
        snapshot = self._snapshot(session, rest)
        restore(session.emulator, snapshot)
        session.results = {}
        print(f"rewound to {snapshot.name}: game time {session.emulator.world.tick}")

    def do_unsnapshot(self, session: Session, rest: str) -> None:
        if rest == "all":
            session.snapshots.clear()
        elif rest:
            del session.snapshots[_position(session.snapshots, rest, "snapshot")]
        else:
            raise CliError("usage: .unsnapshot N|all")
        self.do_snapshots(session, "")

    def do_diff(self, session: Session, rest: str) -> None:
        words = _words(rest)
        if not words:
            raise CliError("usage: .diff N [M] (M, or the world now)")
        before = self._snapshot(session, words[0])
        after = self._snapshot(session, words[1]) if len(words) > 1 else capture(session.emulator)
        changes = compare(before, after, session.version)
        for change in changes:
            print(change.format())
        where = after.name if len(words) > 1 else "the world now"
        print(f"{len(changes)} change(s) from {before.name} to {where}")

    def do_history(self, session: Session, rest: str) -> None:
        words = _words(rest)
        if len(words) != 2:
            raise CliError("usage: .history HOLDER OBJECTIVE")
        board = session.emulator.world.scoreboard
        print(history_text(board, words[0], words[1], shown=board.HISTORY))

    # -- analysis --------------------------------------------------------

    def do_explain(self, session: Session, rest: str) -> None:
        if not rest:
            raise CliError("usage: .explain COMMAND")
        view = session.datapack.view_for(session.version)
        print_rows(explain_line(rest, session.version, view=view, vanilla=session.vanilla))

    def do_profile(self, session: Session, rest: str) -> None:
        profiler = session.emulator.profiler
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

    def do_baseline(self, session: Session, rest: str) -> None:
        session.baseline = session.emulator.profiler.snapshot()
        print(
            f"kept this run as the baseline: {session.baseline.ticks} tick(s), "
            f"{session.baseline.total_us / 1000:.2f} ms"
        )

    def do_compare(self, session: Session, rest: str) -> None:
        if session.baseline is None:
            raise CliError("no baseline yet: .baseline keeps the current run")
        print_comparison(session.emulator.profiler, session.baseline)

    def do_hot(self, session: Session, rest: str) -> None:
        limit = _number(rest, 1) if rest else 10
        rows = session.emulator.profiler.hot_commands(limit)
        if not rows:
            print("no commands run yet")
        for function_id, line, stats in rows:
            print(
                f"{function_id}:{line:<4} {stats['self_us'] / 1000:8.3f} ms  "
                f"{int(stats['runs']):5d} run(s)  {str(stats['raw'])[:60]}"
            )

    def do_report(self, session: Session, rest: str) -> None:
        path = Path(rest) if rest else Path("generated") / "index.html"
        try:
            written = session.emulator.profiler.write_html(
                path,
                f"Function profiler — {session.datapack.name}",
                f"{session.version.id} (pack_format {session.version.format_string})",
                baseline=session.baseline,
            )
        except OSError as exc:
            raise CliError(f"cannot write {path}: {exc}") from exc
        print(f"report: {written}")

    def do_dot(self, session: Session, rest: str) -> None:
        path = Path(rest) if rest else Path("generated") / f"{session_name(session)}.dot"
        try:
            written = session.emulator.call_graph().write_dot(path)
        except OSError as exc:
            raise CliError(f"cannot write {path}: {exc}") from exc
        print(f"dot: {written}")

    # -- settings --------------------------------------------------------

    def do_version(self, session: Session, rest: str) -> None:
        if rest:
            session.version = parse_version(rest)
            session.vanilla = vanilla_for(self.arguments, session.version, session.inputs)
            session.changed.add("version")
            session.new_world()
        print(f"{session.version.id} (pack_format {session.version.format_string})")

    def _setting(self, session: Session, name: str, rest: str, minimum: int | None) -> None:
        if rest:
            setattr(session, name, _number(rest, minimum))
            session.changed.add(name)
            if name != "ticks":
                session.new_world()
        print(f"{name} = {getattr(session, name)}")

    def do_players(self, session: Session, rest: str) -> None:
        self._setting(session, "players", rest, 0)

    def do_seed(self, session: Session, rest: str) -> None:
        self._setting(session, "seed", rest, None)

    def do_ticks(self, session: Session, rest: str) -> None:
        self._setting(session, "ticks", rest, -1)

    def _toggle(self, session: Session, name: str, rest: str, label: str) -> None:
        value = _flag(rest) if rest else not getattr(session, name)
        setattr(session, name, value)
        session.changed.add(name)
        print(f"{label} {'on' if value else 'off'}")

    def do_realtime(self, session: Session, rest: str) -> None:
        self._toggle(session, "realtime", rest, "realtime")

    def do_step_on_command(self, session: Session, rest: str) -> None:
        self._toggle(session, "step_on_command", rest, "step on command")

    def do_during_runs(self, session: Session, rest: str) -> None:
        self._toggle(session, "tests_during_runs", rest, "tests during runs")

    # -- packs and jars --------------------------------------------------

    def do_packs(self, session: Session, rest: str) -> None:
        for number, path in enumerate(session.datapack.paths, start=1):
            print(f"{number}. {path.name}  ({path})")
        jar = session.vanilla
        print(f"client jar: {f'{jar.version_id} ({jar.jar_path})' if jar else 'none'}")

    def do_add_pack(self, session: Session, rest: str) -> None:
        path = Path(rest)
        if not rest or not (path / "pack.mcmeta").is_file():
            raise CliError(f"not a datapack folder (no pack.mcmeta): {rest}")
        session.set_packs([*session.datapack.paths, path])
        self.do_packs(session, "")

    def do_remove_pack(self, session: Session, rest: str) -> None:
        paths = list(session.datapack.paths)
        index = _position(paths, rest, "datapack")
        if len(paths) == 1:
            raise CliError("the last datapack cannot be removed")
        del paths[index]
        session.set_packs(paths)
        self.do_packs(session, "")

    def do_move_pack(self, session: Session, rest: str) -> None:
        words = _words(rest)
        if len(words) != 2:
            raise CliError("usage: .move-pack N up|down")
        paths = list(session.datapack.paths)
        _move(paths, _position(paths, words[0], "datapack"), words[1])
        session.set_packs(paths)
        self.do_packs(session, "")

    def do_reload(self, session: Session, rest: str) -> None:
        session.set_packs(list(session.datapack.paths))
        print(f"reloaded {session.datapack.name}; fresh world on {session.version.id}")

    def do_jar(self, session: Session, rest: str) -> None:
        session.set_jar(rest)
        self.do_packs(session, "")

    # -- tests -------------------------------------------------------------

    def do_tests(self, session: Session, rest: str) -> None:
        if not session.tests:
            print("no tests")
        for index, test in enumerate(session.tests):
            box = "x" if test.enabled else " "
            result = session.results.get(index)
            outcome = "" if result is None else f"  → {'PASS' if result.passed else 'FAIL'}"
            print(f"{index + 1:3}. [{box}] tick {test.at_tick:<4} {test.describe()}{outcome}")

    def _edited(self, session: Session) -> None:
        session.results = {}
        self.do_tests(session, "")

    def do_test(self, session: Session, rest: str) -> None:
        if not rest:
            raise CliError("usage: .test [TICK:]COMMAND")
        session.tests.append(parse_test(rest))
        self._edited(session)

    def _test_and_value(self, session: Session, rest: str) -> tuple[CommandTest, str]:
        number, _, value = rest.partition(" ")
        return session.tests[_position(session.tests, number)], value.strip()

    def do_expect(self, session: Session, rest: str) -> None:
        test, value = self._test_and_value(session, rest)
        test.expect = value
        self._edited(session)

    def do_check(self, session: Session, rest: str) -> None:
        test, value = self._test_and_value(session, rest)
        if not value:
            raise CliError("usage: .check N CHECK")
        problem = valid_check(value)
        if problem:
            raise CliError(problem)
        test.checks = [*test.checks, value]
        self._edited(session)

    def do_uncheck(self, session: Session, rest: str) -> None:
        test, value = self._test_and_value(session, rest)
        if not value:
            raise CliError("usage: .uncheck N M|all")
        if value == "all":
            test.checks = []
        else:
            index = _position(test.checks, value, "check")
            test.checks = [line for number, line in enumerate(test.checks) if number != index]
        self._edited(session)

    def do_expect_value(self, session: Session, rest: str) -> None:
        test, value = self._test_and_value(session, rest)
        if value and not valid_range(value):
            raise CliError(f"invalid expected value {value!r} (5, 1.., ..3, 1..4)")
        test.expect_value = value
        self._edited(session)

    def do_at(self, session: Session, rest: str) -> None:
        test, value = self._test_and_value(session, rest)
        test.at_tick = _number(value)
        self._edited(session)

    def _indexes(self, session: Session, rest: str) -> list[int]:
        words = _words(rest)
        if not words:
            raise CliError("give one or more test numbers")
        return sorted({_position(session.tests, word) for word in words})

    def do_enable(self, session: Session, rest: str) -> None:
        for index in self._indexes(session, rest):
            session.tests[index].enabled = True
        self._edited(session)

    def do_disable(self, session: Session, rest: str) -> None:
        for index in self._indexes(session, rest):
            session.tests[index].enabled = False
        self._edited(session)

    def do_remove(self, session: Session, rest: str) -> None:
        for index in reversed(self._indexes(session, rest)):
            del session.tests[index]
        self._edited(session)

    def do_duplicate(self, session: Session, rest: str) -> None:
        index = _position(session.tests, rest)
        session.tests.insert(index + 1, replace(session.tests[index]))
        self._edited(session)

    def do_move(self, session: Session, rest: str) -> None:
        words = _words(rest)
        if len(words) != 2:
            raise CliError("usage: .move N up|down")
        _move(session.tests, _position(session.tests, words[0]), words[1])
        self._edited(session)

    def do_runtests(self, session: Session, rest: str) -> None:
        indexes = self._indexes(session, rest) if rest else None
        if not session.run_tests(indexes):
            self.failed = True

    def do_records(self, session: Session, rest: str) -> None:
        index = _position(session.tests, rest)
        result = session.results.get(index)
        if result is None:
            raise CliError(f"test {rest} has no result yet: .runtests first")
        print(f"{'PASS' if result.passed else 'FAIL'}: {result.reason}")
        for record in result.records:
            print(f"  {format_record(self.arguments, record)}")

    # -- project -----------------------------------------------------------

    def do_save(self, session: Session, rest: str) -> None:
        path = session.save(Path(rest) if rest else None)
        print(f"saved {path}")

    # -- input -------------------------------------------------------------

    def handle(self, line: str) -> None:
        line = line.strip()
        if not line or line.startswith("#"):
            return
        try:
            if line.startswith("."):
                self.dot(line)
            else:
                self.session.typed(line)
        except CliError as exc:
            print(f"error: {exc}")
            self.failed = True
        except DebugStopped as stop:  # a stop no command caught (.runtests, …)
            self.session.stopped(stop)

    def next_line(self, prompt: str) -> str | None:
        """The next line from the keyboard or the script (None at the end);
        the debugger's prompt reads from the same place."""
        if self.interactive:
            while True:
                try:
                    return input(prompt)
                except EOFError:
                    print()
                    return None
                except KeyboardInterrupt:
                    print()
        for line in self._lines:
            if line.strip():
                print(f"{prompt}{line.rstrip()}")
                return line
        return None

    def set_input(self, lines, interactive: bool) -> None:
        """Where lines (commands, and answers at a stop) come from next."""
        self._lines = iter(lines)
        self.interactive = interactive
        self.muted = False

    def loop(self, lines=None, interactive: bool = False) -> None:
        if lines is not None:
            self.set_input(lines, interactive)
        interactive = self.interactive
        if interactive:
            with contextlib.suppress(ImportError):
                import readline  # noqa: F401  (line editing and history for input())
            print(
                f"{self.session.datapack.name} on {self.session.version.id} — "
                "type a command, or .help"
            )
        while not self.quit:
            prompt = f"[{self.session.emulator.world.tick}]> " if interactive else "> "
            line = self.next_line(prompt)
            if line is None:
                return
            self.handle(line)


def command_shell(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    scripts = []
    for script in arguments.script or []:
        try:
            scripts.append(Path(script).read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            raise CliError(f"cannot read {script}: {exc}") from exc
    session = Session(arguments, inputs)
    shell = Shell(session, arguments)
    setup_debugger(session.debugger, session.emulator, arguments)
    sources: list[tuple[Iterable[str], bool]] = [(lines, False) for lines in scripts] or [
        (sys.stdin, sys.stdin.isatty())
    ]
    # a stop during --run answers from the first input, like the rest
    shell.set_input(*sources[0])
    if arguments.run:
        try:
            session.run()
        except DebugStopped as stop:  # pragma: no cover - run() handles its own
            session.stopped(stop)
    for index, (lines, interactive) in enumerate(sources):
        if shell.quit:
            break
        shell.loop(None if index == 0 else lines, interactive)
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
    add_debug_arguments(shell)
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
