"""Running a datapack for one Minecraft version.

One :class:`Emulator` = one pack + one version + one world.  The version
decides which pack overlays apply and which commands exist, so running the
same pack against a range of versions is just a list of emulators — see
:mod:`datapack_emulator.emulator.engine`.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from datapack_emulator.emulator import costs, versions
from datapack_emulator.emulator.analysis.profiler import Profiler
from datapack_emulator.emulator.commands import Command, CommandResult, command_set
from datapack_emulator.emulator.commands.handlers import COSMETIC, UNMODELLED
from datapack_emulator.emulator.commands.registry import CommandSet
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.datapack import Datapack, DatapackSet, PackView
from datapack_emulator.emulator.runtime.advancements import (
    LOCATION_INTERVAL,
    LOCATION_TRIGGER,
    PLAYER_LOCATION_SINCE,
    TICK_TRIGGER,
    Advancement,
    AdvancementTree,
)
from datapack_emulator.emulator.runtime.context import ExecutionContext
from datapack_emulator.emulator.runtime.debugger import Debugger, DebugStopped
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


_RECURSION_LOCK = threading.Lock()


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
        datapack: Datapack | DatapackSet,
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
        self._advancement_tree = self._build_advancements()
        self._attach(self.world)
        self.profiler = Profiler()
        self.commands_run = 0
        self.schedules: list[tuple[int, str]] = []  # (absolute tick, function id)
        self.loaded = False
        self._pending_load = False
        #: diagnostics already reported this run (see ExecutionContext.note_once)
        self.noted: set[str] = set()
        #: asked before every function line when set (runtime/debugger.py)
        self.debugger: Debugger | None = None

    # -- advancements -----------------------------------------------------

    def _build_advancements(self) -> AdvancementTree:
        pack: dict[str, dict[str, Any]] = {}
        for resource_id, resource in self.pack.registries.get("advancement", {}).items():
            content = getattr(resource, "content", None)
            if isinstance(content, dict):
                pack[resource_id] = content
        vanilla = self.vanilla.advancements if self.vanilla is not None else None
        return AdvancementTree(pack, vanilla)

    def _attach(self, world: World) -> None:
        world.advancements.tree = self._advancement_tree
        world.advancements.on_complete = self._reward

    def _reward(self, holder: str, advancement: Advancement) -> None:
        """An advancement was completed: its function runs as the player,
        experience and loot are given."""
        player = self.world.entity_by_id(holder)
        if player is None:
            return
        rewards = advancement.rewards
        context = self.root_context().branch(
            executor=player, position=list(player.position), dimension=player.dimension
        )
        experience = rewards.get("experience")
        if isinstance(experience, int) and experience:
            from datapack_emulator.emulator.commands.players import give_points

            give_points(context, player, experience)
        tables = rewards.get("loot")
        for table_id in tables if isinstance(tables, list) else []:
            if isinstance(table_id, str):
                self._reward_loot(player, table_id, context)
        function = rewards.get("function")
        if isinstance(function, str):
            self.run_function(function, context)

    def _reward_loot(self, player, table_id: str, context) -> None:
        """A reward table's items go straight into the inventory, silently."""
        from datapack_emulator.emulator.commands.conditions import predicate_context
        from datapack_emulator.emulator.commands.items import loot_table, split_stacks
        from datapack_emulator.emulator.runtime.loot import evaluate

        table = loot_table(context, table_id)
        if table is None:
            context.note_once(f"advancement reward: no loot table {normalise_id(table_id)}")
            return
        result = evaluate(
            table,
            lambda name: loot_table(context, name),
            context=predicate_context(context, entity=player, position=player.position),
        )
        for stack in split_stacks(result.items):
            player.inventory.add(stack)

    def _advancement_triggers(self) -> None:
        """``minecraft:tick`` every tick, ``minecraft:location`` every 20."""
        from datapack_emulator.emulator.commands.conditions import predicate_context
        from datapack_emulator.emulator.runtime.predicates import (
            check,
            entity_matches,
            location_matches,
        )

        progress = self.world.advancements
        location_tick = self.world.tick % LOCATION_INTERVAL == 0
        old_location = self.version < versions.parse(PLAYER_LOCATION_SINCE)
        for advancement in progress.tree.pack_advancements():
            for name, criterion in advancement.criteria.items():
                trigger = normalise_id(str(criterion.get("trigger", "")))
                if trigger != TICK_TRIGGER and not (location_tick and trigger == LOCATION_TRIGGER):
                    continue
                conditions = criterion.get("conditions")
                if not isinstance(conditions, dict):
                    conditions = {}
                for player in self.world.players:
                    # vanilla stops listening once an advancement is done
                    if progress.done(player.id, advancement):
                        continue
                    if name in progress.criteria(player.id, advancement.id):
                        continue
                    context = predicate_context(
                        self.root_context(),
                        entity=player,
                        position=player.position,
                        dimension=player.dimension,
                    )
                    wanted = conditions.get("player")
                    if isinstance(wanted, list):
                        passed = check(wanted, context)
                    else:
                        passed = entity_matches(wanted, player, context)
                    if passed and old_location and trigger == LOCATION_TRIGGER:
                        passed = location_matches(
                            conditions.get("location"), player.position, player.dimension, context
                        )
                    if passed:
                        progress.grant(player.id, advancement, name)

    # -- lifecycle --------------------------------------------------------

    def reset(self, players: int | None = None) -> None:
        self.players = players if players is not None else self.players
        self.world = World(players=self.players, seed=self.seed)
        self._attach(self.world)
        self.profiler.reset()
        self.commands_run = 0
        self.schedules.clear()
        self.loaded = False
        self._pending_load = False
        self.noted.clear()
        self.output.set_tick(None)
        if self.debugger is not None:
            self.debugger.reset()

    @contextmanager
    def _stack_headroom(self) -> Iterator[None]:
        """Deep datapack recursion maps onto Python recursion; make room for it.

        The limit is process-wide and emulators may tick on several threads
        (the engine window), so it is raised once and never lowered: lowering
        it when one emulator finishes would break another one mid-tick."""
        needed = self.MAX_DEPTH * self.FRAMES_PER_DEPTH + 1000
        with _RECURSION_LOCK:
            if sys.getrecursionlimit() < needed:
                sys.setrecursionlimit(needed)
        yield

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
        game_time = self.world.tick + 1
        try:
            self._tick_body(game_time)
        except DebugStopped:
            # the debugger abandoned the tick: it still happened, so the next
            # one does not run the same game time again
            if self.world.tick < game_time:
                self.world.tick = game_time
            if self.debugger is not None:
                self.debugger.finished()
            raise

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

    def _tick_body(self, game_time: int) -> None:
        with self._stack_headroom():
            if self._pending_load and self.version >= versions.parse(self.LOAD_BEFORE_TICK_SINCE):
                self._pending_load = False
                self.run_load()
            self._run_tag("#minecraft:tick")
            if self._pending_load:  # 1.16.1–1.19.2: load after the first tick
                self._pending_load = False
                self.run_load()
            self.world.tick = game_time  # schedules made from here on count from here
            if self.world.rule_enabled(DAYLIGHT_RULES):
                self.world.state.day_time += 1
            due = [entry for entry in self.schedules if entry[0] <= game_time]
            self.schedules = [entry for entry in self.schedules if entry[0] > game_time]
            for _, target in due:
                self.run_scheduled(target)
            tick_entities(self.world, self.version, self._instant_effect)
            self._advancement_triggers()
        if self.debugger is not None:
            self.debugger.finished()

    def _instant_effect(self, entity, effect) -> None:
        from datapack_emulator.emulator.commands.living import apply_instant

        apply_instant(self.root_context(), entity, effect)

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
        try:
            with self._stack_headroom():
                result = self.run_command(command, self.root_context())
        finally:
            if self.debugger is not None:
                self.debugger.finished()
        return (result, started)

    def run_command(
        self, command: Command, context: ExecutionContext, nested: bool = False
    ) -> CommandResult:
        """Dispatch one command, after checking it exists in this version.
        ``nested`` is the command an ``execute … run`` or ``return run`` wraps:
        it is dispatched once per branch, but its line ran once."""
        inner = context.branch(line=command.line)
        self.profiler.charge(
            inner.function_id,
            # the wrapped command is dispatched, and charged, on its own
            command.estimate_cost(len(self.world.entities), include_child=False),
            line=command.line,
            raw=command.source_raw or command.raw,
            count_run=not nested,
        )
        self.commands_run += 1

        spec = self.commands.spec(command.name)
        if not spec.available:
            # exactly what the game answers for a command it does not know
            added = spec.since.id if spec.since else "a later version"
            inner.note(
                f"'{command.name}' does not exist in {self.version.id}"
                + (f" (added in {added})" if spec.since else "")
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
        debugger = self.debugger
        if debugger is not None:
            debugger.enter(function_id, context)
        try:
            return self._run_function_body(function, function_id, context, macro_arguments)
        finally:
            if debugger is not None:
                debugger.leave()
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
            if self.debugger is not None:
                self.debugger.before(command, inner)
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
