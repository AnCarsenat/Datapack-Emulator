"""Running a datapack for one Minecraft version.

One :class:`Emulator` = one pack + one version + one world.  The version
decides which pack overlays apply and which commands exist, so running the
same pack against a range of versions is just a list of emulators — see
:mod:`src.emulator.engine`.
"""

from __future__ import annotations

import logging
from typing import Any

from src.emulator import costs, versions
from src.emulator.analysis.profiler import Profiler
from src.emulator.commands import Command, CommandResult, command_set
from src.emulator.commands.registry import CommandSet
from src.emulator.common import normalise_id
from src.emulator.datapack import Datapack, PackView
from src.emulator.runtime.context import ExecutionContext
from src.emulator.runtime.messages import MessageCatalogue, unknown_command
from src.emulator.runtime.output import LogLevel, OutputBus
from src.emulator.runtime.world import World
from src.emulator.vanilla import VanillaAssets
from src.emulator.versions import Version

log = logging.getLogger(__name__)


class Emulator:
    """Runs ``#minecraft:load`` once, then ``#minecraft:tick`` every tick."""

    MAX_DEPTH = 64
    MAX_COMMANDS_PER_TICK = 65_536

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

    # -- lifecycle --------------------------------------------------------

    def reset(self, players: int | None = None) -> None:
        self.players = players if players is not None else self.players
        self.world = World(players=self.players, seed=self.seed)
        self.profiler.reset()
        self.commands_run = 0
        self.schedules.clear()
        self.loaded = False
        self.output.set_tick(None)

    def run_load(self) -> float:
        self.loaded = True
        self.output.set_tick(self.world.tick)
        return self._run_tag("#minecraft:load")

    def run_tick(self) -> float:
        """Run one tick: due schedules first, then ``#minecraft:tick``."""
        self.commands_run = 0
        self.output.set_tick(self.world.tick)
        start = self.profiler.total_us

        due = [entry for entry in self.schedules if entry[0] <= self.world.tick]
        self.schedules = [entry for entry in self.schedules if entry[0] > self.world.tick]
        for _, target in due:
            self.run_scheduled(target)

        self._run_tag("#minecraft:tick")

        elapsed = self.profiler.total_us - start
        self.profiler.tick_times.append(elapsed)
        if elapsed > costs.TICK_BUDGET_US:
            self.output.emulator(
                f"tick {self.world.tick} took an estimated {elapsed / 1000:.1f} ms, over the "
                f"{costs.TICK_BUDGET_US / 1000:.0f} ms budget",
                level=LogLevel.WARNING,
                version=self.version.id,
            )
        self.world.tick += 1
        return elapsed

    def run(self, ticks: int = 20) -> Profiler:
        if not self.loaded:
            self.run_load()
        for _ in range(ticks):
            self.run_tick()
        return self.profiler

    def _run_tag(self, tag_id: str) -> float:
        start = self.profiler.total_us
        targets = self.pack.resolve_function_tag(tag_id)
        if not targets:
            self.output.emulator(
                f"{tag_id} is empty or missing",
                level=LogLevel.WARNING,
                version=self.version.id,
            )
        for function_id in targets:
            self.run_function(function_id, self.root_context())
        return self.profiler.total_us - start

    def root_context(self) -> ExecutionContext:
        return ExecutionContext(emulator=self, function_id="<server>", executor=None)

    # -- schedules --------------------------------------------------------

    def run_scheduled(self, target: str) -> None:
        """Run a due schedule; ``#tag`` targets run every function in the tag."""
        if target.startswith("#"):
            for function_id in self.pack.resolve_function_tag(target):
                self.run_function(function_id, self.root_context())
        else:
            self.run_function(target, self.root_context())

    def add_schedule(self, function_id: str, tick: int, replace: bool = True) -> None:
        if replace:
            self.schedules = [entry for entry in self.schedules if entry[1] != function_id]
        self.schedules.append((tick, function_id))

    def clear_schedule(self, function_id: str) -> int:
        before = len(self.schedules)
        self.schedules = [entry for entry in self.schedules if entry[1] != function_id]
        return before - len(self.schedules)

    # -- execution --------------------------------------------------------

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
            self.output.game_error(
                unknown_command(command.name, self.messages),
                function=inner.function_id,
                line=command.line,
                command=command.raw,
                version=self.version.id,
                key="command.unknown.command",
            )
            return CommandResult.failure()

        missing = self.commands.missing_features(command.features())
        for feature, since in missing:
            if feature == f"command:{command.name}":
                continue
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
        function = self.pack.function(function_id)
        if function is None:
            context.game_error("arguments.function.unknown", function_id)
            return CommandResult.failure()
        if context.depth >= self.MAX_DEPTH:
            context.note_key("emulator.depth", self.MAX_DEPTH, function_id)
            return CommandResult.failure()

        self.profiler.call(function_id)
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
                return CommandResult(success=result.success, value=result.value)

        self.profiler.charge_total(function_id, self.profiler.total_us - start)
        return CommandResult(success=executed > 0, value=executed)

    # -- analysis ---------------------------------------------------------

    def call_graph(self):
        from src.emulator.analysis.graph import CallGraph

        return CallGraph.from_pack(self.pack)

    def __repr__(self) -> str:
        return f"<Emulator {self.datapack.name} @ {self.version.id}>"
