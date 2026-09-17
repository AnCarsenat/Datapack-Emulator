"""Function debugger: breakpoints on function lines, stepping and watches.

The emulator asks the debugger before each command of a function
(``Debugger.before``). When a breakpoint is hit, a step ends or a pause was
asked for, the debugger calls ``on_pause`` with a ``Pause`` — where it
stopped, the call stack, the command source — and waits for its answer, an
``Action``. The callback decides how to wait: the command line reads the
next line from the terminal, the window runs a nested event loop until a
button is pressed. Nothing here knows about either.

Only function lines stop: a typed command runs through, and the commands an
``execute … run`` starts are part of their line. Stepping follows the call
stack's depth: *over* stops at the next function line no deeper than the
current one, *out* at the next shallower one, so past the end of a tick
function they stop in whatever runs next at that depth (the next function of
the tag, a schedule). A step never carries into the next tick.

Conditions and watches only read the world: ``if function`` is refused, and
the records a condition produces are dropped.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from datapack_emulator.emulator.common import nbt_get, normalise_id, to_snbt

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.commands.parser import Command
    from datapack_emulator.emulator.runtime.context import ExecutionContext


class Action(enum.Enum):
    CONTINUE = "continue"
    #: stop at the next function line, wherever it is
    STEP_INTO = "step"
    #: stop at the next line of this function (or of a caller)
    STEP_OVER = "next"
    #: stop at the next line of a caller
    STEP_OUT = "out"
    #: abandon the run (``DebugStopped`` is raised)
    STOP = "stop"


class DebugStopped(BaseException):
    """The run was stopped from the debugger. A ``BaseException``, so the
    emulator's guard around each command does not swallow it."""


@dataclass
class Breakpoint:
    function_id: str
    line: int
    #: an ``execute`` condition (``if score @s x matches 5``); empty: always
    condition: str = ""
    enabled: bool = True
    hits: int = 0

    @property
    def key(self) -> tuple[str, int]:
        return (self.function_id, self.line)

    def __str__(self) -> str:
        text = f"{self.function_id}:{self.line}"
        if self.condition:
            text += f" {self.condition}"
        if not self.enabled:
            text += " (disabled)"
        return text


@dataclass
class Frame:
    """A function on the call stack and the line it is at."""

    function_id: str
    line: int
    context: ExecutionContext


@dataclass
class Pause:
    reason: str  # "breakpoint", "step" or "pause"
    command: Command
    context: ExecutionContext
    #: outermost first; the last frame is the function that stopped
    stack: list[Frame]
    tick: int
    breakpoint: Breakpoint | None = None

    @property
    def function_id(self) -> str:
        return self.context.function_id

    @property
    def line(self) -> int:
        return self.command.line

    def describe(self) -> str:
        why = f"breakpoint {self.breakpoint}" if self.breakpoint else self.reason
        return f"{self.function_id}:{self.line} ({why}) at tick {self.tick}: {self.command.raw}"


def condition_problem(condition: str) -> str:
    """Why a breakpoint condition or watch cannot be used ("" when it can):
    it must only read the world."""
    from datapack_emulator.emulator.commands.parser import Command

    command = Command.parse("execute " + condition.strip())
    if command is None:
        return f"not a condition: {condition!r}"
    words = command.arguments
    if not words or words[0] not in ("if", "unless"):
        return f"a condition starts with if or unless: {condition!r}"
    for index, word in enumerate(words):
        if word == "run":
            return "a condition cannot run a command"
        if word in ("if", "unless") and index + 1 < len(words) and words[index + 1] == "function":
            return "if function runs the function, so it cannot be a condition"
    return ""


def split_breakpoint(text: str) -> tuple[str, str]:
    """``FUNC:LINE [if|unless …]`` -> (location, condition); ``if`` is added
    to a condition that has neither."""
    parts = text.strip().split(None, 1)
    location = parts[0] if parts else ""
    condition = parts[1].strip() if len(parts) > 1 else ""
    if condition and condition.split(None, 1)[0] not in ("if", "unless"):
        condition = "if " + condition
    return location, condition


def parse_location(text: str) -> tuple[str, int]:
    """``ns:path:LINE`` (or ``path:LINE`` in ``minecraft``) -> (function id, line)."""
    function, sep, line = text.strip().rpartition(":")
    if not sep or not function or not line.isdigit() or int(line) < 1:
        raise ValueError(f"expected FUNCTION:LINE, got {text!r}")
    return normalise_id(function), int(line)


def command_line_at(function, line: int) -> int | None:
    """The first line at or after ``line`` of a function (a ``Function``
    resource) that holds a command: where a breakpoint set there stops."""
    lines = [command.line for command in function.content if command.line >= line]
    return lines[0] if lines else None


class Debugger:
    def __init__(self, on_pause: Callable[[Pause], Action] | None = None):
        self.on_pause = on_pause
        self.breakpoints: dict[tuple[str, int], Breakpoint] = {}
        #: expressions shown at every pause (see ``watch_value``)
        self.watches: list[str] = []
        self.stack: list[Frame] = []
        self.mode = Action.CONTINUE
        #: stack depth when the current step started
        self._step_depth = 0
        self._pause_requested = threading.Event()
        #: above 0 while commands run aside (conditions, commands typed while
        #: stopped), which never stop
        self._aside = 0
        #: the last pause, while it lasts (for a UI to read)
        self.paused: Pause | None = None

    # -- breakpoints -------------------------------------------------------

    def add(self, function_id: str, line: int, condition: str = "") -> Breakpoint:
        point = Breakpoint(normalise_id(function_id), line, condition.strip())
        self.breakpoints[point.key] = point
        return point

    def remove(self, function_id: str, line: int) -> bool:
        return self.breakpoints.pop((normalise_id(function_id), line), None) is not None

    def toggle(self, function_id: str, line: int) -> bool:
        """Add or remove; whether there is one now."""
        if self.remove(function_id, line):
            return False
        self.add(function_id, line)
        return True

    def to_strings(self) -> list[str]:
        """The breakpoints as a project stores them (see ``load_strings``)."""
        return [
            ("" if point.enabled else "!")
            + f"{point.function_id}:{point.line}"
            + (f" {point.condition}" if point.condition else "")
            for point in sorted(self.breakpoints.values(), key=lambda p: p.key)
        ]

    def load_strings(self, entries: list[str]) -> list[str]:
        """Replace the breakpoints with ``[!]ns:function:LINE [if|unless …]``
        entries; the ones that cannot be read are returned."""
        self.clear()
        bad = []
        for entry in entries:
            text = entry.strip()
            enabled = not text.startswith("!")
            location, condition = split_breakpoint(text.lstrip("!"))
            try:
                function_id, line = parse_location(location)
            except ValueError:
                bad.append(entry)
                continue
            self.add(function_id, line, condition).enabled = enabled
        return bad

    def resolve(self, function_of: Callable[[str], object | None]) -> list[str]:
        """Check the breakpoints against the functions a version loads
        (``function_of`` returns a ``Function`` or None): each moves to the
        first command on or after its line. Returns what is wrong."""
        problems = []
        for point in list(self.breakpoints.values()):
            function = function_of(point.function_id)
            if function is None:
                problems.append(f"breakpoint {point}: no function {point.function_id}")
                continue
            line = command_line_at(function, point.line)
            if line is None:
                problems.append(f"breakpoint {point}: no command on or after line {point.line}")
            elif line != point.line and (point.function_id, line) not in self.breakpoints:
                del self.breakpoints[point.key]
                point.line = line
                self.breakpoints[point.key] = point
        return problems

    def lines_of(self, function_id: str) -> set[int]:
        return {line for (function, line) in self.breakpoints if function == function_id}

    def clear(self) -> None:
        self.breakpoints.clear()

    # -- control -----------------------------------------------------------

    def request_pause(self) -> None:
        """Stop at the next function line (safe from another thread)."""
        self._pause_requested.set()

    def cancel_pause(self) -> None:
        """Forget a pause request nothing reached."""
        self._pause_requested.clear()

    def reset(self) -> None:
        """Forget the stack and any stepping (a new run)."""
        self.stack.clear()
        self.mode = Action.CONTINUE
        self.paused = None
        self._pause_requested.clear()

    # -- called by the emulator ------------------------------------------

    def finished(self) -> None:
        """A tick (or a typed command) is over: a step does not carry over."""
        self.mode = Action.CONTINUE

    def enter(self, function_id: str, context: ExecutionContext) -> None:
        self.stack.append(Frame(function_id, 0, context))

    def leave(self) -> None:
        if self.stack:
            self.stack.pop()

    def before(self, command: Command, context: ExecutionContext) -> None:
        """Before a function line runs: stop here if a breakpoint, a step or a
        pause asks for it."""
        if self._aside or not self.stack:
            return
        frame = self.stack[-1]
        frame.line = command.line
        frame.context = context
        depth = len(self.stack)
        reason = ""
        point = self.breakpoints.get((frame.function_id, command.line))
        if point is not None and point.enabled and self._condition(point, context):
            point.hits += 1
            reason = "breakpoint"
        elif self._pause_requested.is_set():
            reason = "pause"
        elif (
            self.mode is Action.STEP_INTO
            or (self.mode is Action.STEP_OVER and depth <= self._step_depth)
            or (self.mode is Action.STEP_OUT and depth < self._step_depth)
        ):
            reason = "step"
        if not reason:
            return
        self._pause_requested.clear()
        pause = Pause(
            reason,
            command,
            context,
            list(self.stack),
            context.world.tick,
            point if reason == "breakpoint" else None,
        )
        self.paused = pause
        try:
            action = self.on_pause(pause) if self.on_pause else Action.CONTINUE
        finally:
            self.paused = None
        if action is Action.STOP:
            self.mode = Action.CONTINUE
            raise DebugStopped(pause.describe())
        self.mode = action
        self._step_depth = depth

    def _condition(self, point: Breakpoint, context: ExecutionContext) -> bool:
        if not point.condition:
            return True
        passed, _ = self.test(point.condition, context)
        return passed

    def test(self, condition: str, context: ExecutionContext) -> tuple[bool, str]:
        """Run an ``execute`` condition (``if …``/``unless …``, ``if`` may be
        left out) in ``context``: (passed, error)."""
        from datapack_emulator.emulator.commands.execute import cmd_execute
        from datapack_emulator.emulator.commands.parser import Command
        from datapack_emulator.emulator.runtime.output import OutputBus

        _, text = split_breakpoint("_ " + condition)
        problem = condition_problem(text)
        if problem:
            return False, problem
        command = Command.parse(f"execute {text}")
        assert command is not None  # condition_problem parsed it
        emulator = context.emulator
        counted = emulator.commands_run
        output = emulator.output
        emulator.output = OutputBus()  # what the check says is not part of the run
        try:
            with self.aside():
                result = cmd_execute(command, context.branch(depth=max(1, context.depth)))
        except Exception as exc:  # a broken condition must not end the run
            return False, f"{condition}: {exc}"
        finally:
            emulator.output = output
            emulator.commands_run = counted
        return result.success, ""

    @contextmanager
    def aside(self) -> Iterator[None]:
        """Commands run inside this never stop."""
        self._aside += 1
        try:
            yield
        finally:
            self._aside -= 1

    # -- watches -----------------------------------------------------------

    def watch_values(self, context: ExecutionContext) -> list[tuple[str, str]]:
        return [(text, watch_value(text, context, self)) for text in self.watches]


def watch_value(text: str, context: ExecutionContext, debugger: Debugger | None = None) -> str:
    """The value of a watch expression in ``context``:

    * ``score HOLDER OBJECTIVE`` (a name or a selector such as ``@s``)
    * ``storage ID [PATH]``
    * ``entity SELECTOR [PATH]`` (the first entity)
    * ``block X Y Z [PATH]`` (``~`` is relative to the position)
    * ``if …``/``unless …``: whether the ``execute`` condition passes
    * ``executor``, ``position``, ``rotation``, ``dimension``
    """
    from datapack_emulator.emulator.commands.blocks import parse_block_position
    from datapack_emulator.emulator.commands.helpers import find_holders, find_targets
    from datapack_emulator.emulator.commands.parser import Command

    words = text.split(None, 1)
    if not words:
        return ""
    kind, rest = words[0], (words[1] if len(words) > 1 else "")
    world = context.world
    version = context.emulator.version
    if kind == "executor":
        return context.executor.display if context.executor else "the server"
    if kind == "position":
        return " ".join(f"{value:g}" for value in context.position)
    if kind == "rotation":
        return " ".join(f"{value:g}" for value in context.rotation)
    if kind == "dimension":
        return context.dimension
    if kind in ("if", "unless"):
        tester = debugger or Debugger()
        passed, error = tester.test(text, context)
        return error or ("passes" if passed else "fails")
    parsed = Command.parse(f"watch {rest}")
    tokens = parsed.arguments if parsed else []
    if kind == "score" and len(tokens) == 2:
        holders = find_holders(context, tokens[0])
        if not holders:
            return "no holder"
        value = world.scoreboard.get(holders[0], tokens[1])
        return "unset" if value is None else str(value)
    if kind == "storage" and tokens:
        data = world.storage.get(normalise_id(tokens[0]), {})
        return _path(data, tokens[1:])
    if kind == "entity" and tokens:
        found = find_targets(context, tokens[0])
        if not found:
            return "no entity"
        return _path(found[0].data(version), tokens[1:])
    if kind == "block" and len(tokens) >= 3:
        position, error = parse_block_position(context, tokens[:3])
        if position is None:
            return context.render(error)
        block = world.blocks.get(context.dimension, position)
        if len(tokens) == 3:
            properties = ",".join(f"{k}={v}" for k, v in sorted(block.properties.items()))
            return block.id + (f"[{properties}]" if properties else "")
        return _path(block.data(version, position), tokens[3:])
    return (
        "unknown watch (score, storage, entity, block, if/unless, executor, position, "
        "rotation, dimension)"
    )


def _path(data, rest: list[str]) -> str:
    if not rest:
        return to_snbt(data)
    value = nbt_get(data, rest[0])
    return "nothing" if value is None else to_snbt(value)


@dataclass
class Trace:
    """A pause handler that never waits: it records where each pause was and
    goes on (``run --break …``)."""

    action: Action = Action.CONTINUE
    lines: list[str] = field(default_factory=list)
    debugger: Debugger | None = None
    printer: Callable[[str], None] | None = None

    def __call__(self, pause: Pause) -> Action:
        line = pause.describe()
        if self.debugger is not None:
            for text, value in self.debugger.watch_values(pause.context):
                line += f"\n    {text} = {value}"
        self.lines.append(line)
        if self.printer:
            self.printer(line)
        return self.action
