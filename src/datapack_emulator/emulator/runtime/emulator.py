"""Running a datapack for one Minecraft version.

One :class:`Emulator` = one pack + one version + one world.  The version
decides which pack overlays apply and which commands exist, so running the
same pack against a range of versions is just a list of emulators — see
:mod:`datapack_emulator.emulator.engine`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from datapack_emulator.emulator import costs, versions
from datapack_emulator.emulator.analysis.profiler import Profiler
from datapack_emulator.emulator.commands import Command, CommandResult, command_set
from datapack_emulator.emulator.commands.handlers import COSMETIC, UNMODELLED
from datapack_emulator.emulator.commands.registry import CommandSet
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.datapack import Datapack, PackView
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.library import FunctionLibrary
from datapack_emulator.emulator.runtime.living import tick_entities
from datapack_emulator.emulator.runtime.messages import MessageCatalogue, unknown_command
from datapack_emulator.emulator.runtime.output import LogLevel, LogSource, OutputBus
from datapack_emulator.emulator.runtime.world import World
from datapack_emulator.emulator.vanilla import VanillaAssets
from datapack_emulator.emulator.versions import Version

log = logging.getLogger(__name__)


#: the call tree's root for scheduled functions
SCHEDULE_ROOT = "<schedule>"
#: the gamerule that stops the time of day (renamed with the gamerule overhaul)
DAYLIGHT_RULES = ("doDaylightCycle", "advance_time", "minecraft:advance_time")


class Emulator:
    """Runs ``#minecraft:load`` once, then ``#minecraft:tick`` every tick."""

    #: not a vanilla rule — vanilla only limits maxCommandChainLength — but a
    #: guard so a runaway recursion cannot exhaust the Python stack
    MAX_DEPTH = 1024
    MAX_COMMANDS_PER_TICK = 65_536
    #: Python frames one nested function call uses, with headroom (measured: ~6)
    FRAMES_PER_DEPTH = 10

    def __init__(
        self,
        datapack: Datapack,
        version: str | Version | None = None,
        players: int = 1,
        output: OutputBus | None = None,
        seed: int = 0,
        vanilla: VanillaAssets | None = None,
    ):
        self.datapack = datapack
        self.version: Version = versions.parse(version)
        self.pack: PackView = datapack.view_for(self.version)
        self.commands: CommandSet = command_set(self.version)
        #: what a server of this version actually loads from the pack
        self.library = FunctionLibrary.build(self.pack, self.commands)
        #: base-game content read from a client jar, when one was loaded
        self.vanilla = vanilla
        self.messages = MessageCatalogue(
            vanilla.lang if vanilla else None,
            source=f"{vanilla.version_id} client.jar" if vanilla else "built-in",
        )
        self.output = output or OutputBus()
        self.players = players
        self.seed = seed
        self.world = World(players=players, seed=seed)
        self.profiler = Profiler()
        self.commands_run = 0
        self.schedules: list[tuple[int, str]] = []  # (absolute tick, function id)
        self.loaded = False
        self._pending_load = False
        #: diagnostics already reported this run (see ExecutionContext.note_once)
        self.noted: set[str] = set()

    # -- lifecycle --------------------------------------------------------

    def reset(self, players: int | None = None) -> None:
        self.players = players if players is not None else self.players
        self.world = World(players=self.players, seed=self.seed)
        self.profiler.reset()
        self.commands_run = 0
        self.schedules.clear()
        self.loaded = False
        self._pending_load = False
        self.noted.clear()
        self.output.set_tick(None)

    @contextmanager
    def _stack_headroom(self) -> Iterator[None]:
        """Deep datapack recursion maps onto Python recursion; make room for it."""
        needed = self.MAX_DEPTH * self.FRAMES_PER_DEPTH + 1000
        previous = sys.getrecursionlimit()
        if previous < needed:
            sys.setrecursionlimit(needed)
        try:
            yield
        finally:
            if previous < needed:
                sys.setrecursionlimit(previous)

    #: until 1.19.2 the first tick ran #minecraft:tick before #minecraft:load
    LOAD_BEFORE_TICK_SINCE = "1.19.3"

    def report_load(self) -> None:
        """What the server logs while loading the pack (once per run)."""
        for failure in self.library.failures:
            self.output.game(
                failure.message + (f": {failure.detail}" if failure.detail else ""),
                level=LogLevel.WARNING,
                function=failure.resource_id if not failure.resource_id.startswith("#") else "",
                line=failure.line,
                version=self.version.id,
                key=failure.key,
            )

    def run_load(self) -> float:
        """Run ``#minecraft:load`` now (``run()`` places it in the first tick)."""
        if not self.started:
            self.report_load()
        self.loaded = True
        self.output.set_tick(self.world.tick)
        with self._stack_headroom():
            return self._run_tag("#minecraft:load")

    def run_tick(self) -> float:
        """Run one server tick the way vanilla orders it.

        ``#minecraft:tick`` runs first; then the level ticks, which advances the
        game time and runs every schedule that is now due. A ``schedule ... 1t``
        made by a tick function therefore runs later in the same server tick.
        """
        self.commands_run = 0
        self.output.set_tick(self.world.tick)
        start = self.profiler.total_us

        with self._stack_headroom():
            if self._pending_load and self.version >= versions.parse(self.LOAD_BEFORE_TICK_SINCE):
                self._pending_load = False
                self.run_load()
            self._run_tag("#minecraft:tick")
            if self._pending_load:  # 1.16.1–1.19.2: load after the first tick
                self._pending_load = False
                self.run_load()
            game_time = self.world.tick + 1
            self.world.tick = game_time  # schedules made from here on count from here
            if self.world.rule_enabled(DAYLIGHT_RULES):
                self.world.state.day_time += 1
            due = [entry for entry in self.schedules if entry[0] <= game_time]
            self.schedules = [entry for entry in self.schedules if entry[0] > game_time]
            for _, target in due:
                self.run_scheduled(target)
            tick_entities(self.world, self.version)

        elapsed = self.profiler.total_us - start
        self.profiler.record_tick(elapsed)
        if elapsed > costs.TICK_BUDGET_US:
            self.output.emulator(
                f"tick {self.world.tick - 1} took an estimated {elapsed / 1000:.1f} ms, over the "
                f"{costs.TICK_BUDGET_US / 1000:.0f} ms budget",
                level=LogLevel.WARNING,
                version=self.version.id,
            )
        return elapsed

    @property
    def started(self) -> bool:
        return self.loaded or self._pending_load

    def start(self) -> None:
        """Start the server: log what failed to load and queue #minecraft:load.

        Idempotent; ``run()`` calls it, and callers that tick one at a time
        (the window, the test runner) call it before their first ``run_tick``.
        """
        if not self.started:
            self.report_load()
            self._pending_load = True

    def run(self, ticks: int = 20) -> Profiler:
        """Start the server, then run ``ticks`` ticks.

        ``#minecraft:load`` runs in the first tick: after ``#minecraft:tick``
        before 1.19.3, before it from 1.19.3 on.
        """
        self.start()
        if ticks <= 0 and self._pending_load:
            self._pending_load = False
            self.run_load()
        for _ in range(ticks):
            self.run_tick()
        return self.profiler

    def _run_tag(self, tag_id: str) -> float:
        start = self.profiler.total_us
        targets = self.library.resolve_tag(tag_id)
        if not targets and tag_id not in self.noted:
            self.noted.add(tag_id)
            self.output.emulator(
                f"{tag_id} is empty, missing or failed to load",
                level=LogLevel.INFO,
                version=self.version.id,
            )
        self.profiler.enter(tag_id)
        try:
            for function_id in targets:
                self.run_function(function_id, self.root_context())
        finally:
            self.profiler.leave()
        return self.profiler.total_us - start

    def root_context(self) -> ExecutionContext:
        return ExecutionContext(emulator=self, function_id="<server>", executor=None)

    # -- schedules --------------------------------------------------------

    def run_scheduled(self, target: str) -> None:
        """Run a due schedule; ``#tag`` targets run every function in the tag."""
        self.profiler.enter(SCHEDULE_ROOT)
        try:
            if target.startswith("#"):
                for function_id in self.library.resolve_tag(target):
                    self.run_function(function_id, self.root_context())
            else:
                self.run_function(target, self.root_context())
        finally:
            self.profiler.leave()

    def add_schedule(self, function_id: str, tick: int, replace: bool = True) -> None:
        if replace:
            self.schedules = [entry for entry in self.schedules if entry[1] != function_id]
        self.schedules.append((tick, function_id))

    def clear_schedule(self, function_id: str) -> int:
        before = len(self.schedules)
        self.schedules = [entry for entry in self.schedules if entry[1] != function_id]
        return before - len(self.schedules)

    # -- execution --------------------------------------------------------

    def run_typed(self, line: str) -> tuple[CommandResult | None, bool]:
        """A command typed on the server console: ``(result, started)``.

        A leading ``/`` is optional. A world that has not ticked yet runs its
        first tick first, like a server that is up (``started`` says so). The
        result is ``None`` for an empty line or a comment.
        """
        command = Command.parse(line.strip().removeprefix("/"), source="<console>")
        if command is None:
            return (None, False)
        started = False
        if not self.started or self.world.tick == 0:
            self.start()
            self.run_tick()
            started = True
        with self._stack_headroom():
            return (self.run_command(command, self.root_context()), started)

    def run_command(self, command: Command, context: ExecutionContext) -> CommandResult:
        """Dispatch one command, after checking it exists in this version."""
        inner = context.branch(line=command.line)
        self.profiler.charge(inner.function_id, command.estimate_cost(len(self.world.entities)))
        self.commands_run += 1

        spec = self.commands.spec(command.name)
        if not spec.available:
            # exactly what the game answers for a command it does not know
            since = spec.since.id if spec.since else "a later version"
            inner.note(
                f"'{command.name}' does not exist in {self.version.id}"
                + (f" (added in {since})" if spec.since else "")
                + (f", removed in {spec.removed.id}" if spec.removed else "")
            )
            self.output.log(
                LogSource.GAME,
                LogLevel.DEBUG if inner.silent else LogLevel.ERROR,
                unknown_command(command.name, self.messages),
                function=inner.function_id,
                line=command.line,
                command=command.raw,
                version=self.version.id,
                key="command.unknown.command",
                failure=True,
            )
            return CommandResult.failure()

        missing = self.commands.missing_features(command.features())
        for feature, since in missing:
            if feature.startswith("command:"):
                continue  # the command itself, or a `run` child that reports itself
            inner.note(
                f"'{feature}' is not available in {self.version.id}"
                + (f" (added in {since.id})" if since else "")
            )

        if spec.handler is None:
            inner.note(
                f"'{command.name}' exists in {self.version.id} but is not emulated",
                level=LogLevel.DEBUG,
            )
            return CommandResult(success=True, value=1)

        reason = UNMODELLED.get(command.name)
        if reason and command.name not in COSMETIC:
            inner.note_key_once("emulator.not_modelled", command.name, reason)
        try:
            return spec.handler(command, inner)
        except Exception as exc:  # a broken line must not kill the emulation
            self.output.emulator(
                f"{command.source}:{command.line} '{command.raw}' raised {exc!r}",
                level=LogLevel.ERROR,
                function=command.source,
                line=command.line,
                version=self.version.id,
            )
            return CommandResult.failure()

    def run_function(
        self,
        function_id: str,
        context: ExecutionContext,
        macro_arguments: dict[str, Any] | None = None,
    ) -> CommandResult:
        function_id = normalise_id(function_id)
        function = self.library.function(function_id)
        if function is None:
            context.game_error("arguments.function.unknown", function_id)
            return CommandResult.failure()
        if context.depth >= self.MAX_DEPTH:
            context.note_key("emulator.depth", self.MAX_DEPTH, function_id)
            return CommandResult.failure()

        self.profiler.call(function_id)
        self.profiler.enter(function_id)
        try:
            return self._run_function_body(function, function_id, context, macro_arguments)
        finally:
            self.profiler.leave()

    def _run_function_body(
        self,
        function,
        function_id: str,
        context: ExecutionContext,
        macro_arguments: dict[str, Any] | None,
    ) -> CommandResult:
        inner = context.branch(function_id=function_id, depth=context.depth + 1)
        start = self.profiler.total_us
        executed = 0
        result = CommandResult(success=False, value=0)

        for command in function.content:
            if self.commands_run >= self.MAX_COMMANDS_PER_TICK:
                inner.note_key("emulator.chain_length", self.MAX_COMMANDS_PER_TICK)
                break
            if command.is_macro:
                expanded, missing = command.expand_macro(macro_arguments or {})
                if expanded is None:
                    inner.line = command.line
                    if missing:
                        inner.game_error(
                            "commands.function.error.missing_argument",
                            function_id,
                            ", ".join(missing),
                        )
                    break  # vanilla aborts the function on an unresolved macro
                command = expanded
            result = self.run_command(command, inner)
            executed += 1
            if result.returned:
                self.profiler.charge_total(function_id, self.profiler.total_us - start)
                return CommandResult(success=result.success, value=result.value, has_return=True)

        self.profiler.charge_total(function_id, self.profiler.total_us - start)
        return CommandResult(success=executed > 0, value=executed)

    # -- analysis ---------------------------------------------------------

    def call_graph(self):
        from datapack_emulator.emulator.analysis.graph import CallGraph

        return CallGraph.from_pack(self.pack)

    def __repr__(self) -> str:
        return f"<Emulator {self.datapack.name} @ {self.version.id}>"
