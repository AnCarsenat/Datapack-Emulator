"""The function debugger on the command line: the shell's ``.break``,
``.watch`` and ``.eval``, the prompt a stop opens, and ``--break`` /
``--watch`` for ``shell`` (which prompts) and ``run`` (which prints each stop
and goes on)."""

from __future__ import annotations

import argparse

from datapack_emulator.cli.common import CliError
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.runtime.debugger import (
    Action,
    Debugger,
    Pause,
    Trace,
    command_line_at,
    parse_location,
    watch_value,
)

DEBUG_HELP = """\
  debug    .break FUNC:LINE [if|unless CONDITION]   stop before that line
           .break                list the breakpoints
           .unbreak FUNC:LINE|all
           .watch EXPR           show EXPR at every stop; .watch lists them
           .unwatch N|all
           .eval EXPR            the value of EXPR now (or where it stopped)
           EXPR: score HOLDER OBJ | storage ID [PATH] | entity SELECTOR [PATH]
                 | block X Y Z [PATH] | if/unless CONDITION
                 | executor | position | rotation | dimension
"""

PAUSED_HELP = """\
Stopped before a function line. Answer with:
  step (s)        run this line, stop at the next function line (into calls)
  next (n)        run this line, stop at the next line of this function
  out (o)         stop at the next line of the caller
  continue (c)    run until the next breakpoint
  stop (q)        abandon the tick
  where (bt)      the call stack
  context (ctx)   executor, position, rotation, dimension
  list (l) [N]    the source around the line
  COMMAND         run a command as the stopped function would (same executor
                  and position); its function calls do not stop
  .scores .storage .entities .nbt .blocks .state .world .json .history .tick
  .break .unbreak .watch .unwatch .eval .help   as in the shell
"""

ACTIONS = {
    "step": Action.STEP_INTO,
    "s": Action.STEP_INTO,
    "next": Action.STEP_OVER,
    "n": Action.STEP_OVER,
    "out": Action.STEP_OUT,
    "o": Action.STEP_OUT,
    "continue": Action.CONTINUE,
    "c": Action.CONTINUE,
    "stop": Action.STOP,
    "q": Action.STOP,
}

#: dot-commands that only read, so they are allowed while stopped
PAUSED_DOTS = {
    "scores", "entities", "nbt", "storage", "blocks", "state", "world", "json",
    "history", "tick", "break", "unbreak", "watch", "unwatch", "eval", "help",
}  # fmt: skip


def add_debug_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--break",
        dest="breakpoints",
        action="append",
        default=[],
        metavar="FUNC:LINE[ if COND]",
        help="stop before that function line (repeatable); a condition such as "
        "'ns:f:3 if score @s x matches 5' stops only when it passes",
    )
    parser.add_argument(
        "--watch",
        dest="watches",
        action="append",
        default=[],
        metavar="EXPR",
        help="show EXPR at every stop (repeatable), e.g. 'score @s x', 'storage ns:s path'",
    )


def breakpoint_argument(text: str) -> tuple[str, int, str]:
    """``FUNC:LINE [if|unless COND]`` -> (function, line, condition)."""
    location, _, condition = text.strip().partition(" ")
    try:
        function_id, line = parse_location(location)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    condition = condition.strip()
    if condition and not condition.startswith(("if ", "unless ")):
        raise CliError(f"a breakpoint condition starts with if or unless: {condition!r}")
    return function_id, line, condition


def add_breakpoint(debugger: Debugger, emulator, text: str) -> str:
    """Add a breakpoint, moved to the next command line; a note about it."""
    function_id, line, condition = breakpoint_argument(text)
    function = emulator.library.function(function_id)
    if function is None:
        failed = function_id in emulator.library.function_failures
        why = "failed to load" if failed else "does not exist"
        raise CliError(f"function {function_id} {why} in {emulator.version.id}")
    stop_line = command_line_at(function, line)
    if stop_line is None:
        raise CliError(f"{function_id} has no command on or after line {line}")
    point = debugger.add(function_id, stop_line, condition)
    moved = f" (line {line} has no command)" if stop_line != line else ""
    return f"breakpoint {point}{moved}"


def setup_debugger(debugger: Debugger, emulator, arguments: argparse.Namespace) -> None:
    for text in getattr(arguments, "breakpoints", []) or []:
        print(add_breakpoint(debugger, emulator, text))
    debugger.watches.extend(getattr(arguments, "watches", []) or [])


def trace_debugger(arguments: argparse.Namespace, emulator) -> Trace | None:
    """For ``run``: a debugger that prints each stop, when breakpoints were given."""
    if not getattr(arguments, "breakpoints", None):
        return None
    debugger = Debugger()
    setup_debugger(debugger, emulator, arguments)
    trace = Trace(debugger=debugger, printer=lambda line: print(f"stop: {line}"))
    debugger.on_pause = trace
    emulator.debugger = debugger
    return trace


class DebugCommands:
    """The shell's debugger commands (mixed into ``Shell``)."""

    session: object
    failed: bool

    # provided by Shell
    def dot(self, line: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def next_line(self, prompt: str) -> str | None:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def debugger(self) -> Debugger:
        return self.session.debugger

    def _context(self):
        paused = self.debugger.paused
        return paused.context if paused else self.session.emulator.root_context()

    def do_break(self, session, rest: str) -> None:
        if rest:
            print(add_breakpoint(self.debugger, session.emulator, rest))
            return
        points = sorted(self.debugger.breakpoints.values(), key=lambda p: p.key)
        if not points:
            print("no breakpoints")
        for point in points:
            print(f"{point}  (hit {point.hits}×)")

    def do_unbreak(self, session, rest: str) -> None:
        if rest == "all":
            self.debugger.clear()
            print("no breakpoints")
            return
        function_id, line, _ = breakpoint_argument(rest)
        if not self.debugger.remove(function_id, line):
            raise CliError(f"no breakpoint at {function_id}:{line}")
        print(f"removed {function_id}:{line}")

    def do_watch(self, session, rest: str) -> None:
        if rest:
            self.debugger.watches.append(rest)
        watches = self.debugger.watches
        if not watches:
            print("no watches")
        context = self._context()
        for number, text in enumerate(watches, start=1):
            print(f"{number}. {text} = {watch_value(text, context, self.debugger)}")

    def do_unwatch(self, session, rest: str) -> None:
        watches = self.debugger.watches
        if rest == "all":
            watches.clear()
        else:
            try:
                index = int(rest) - 1
            except ValueError:
                raise CliError(f"not a watch number: {rest!r}") from None
            if not 0 <= index < len(watches):
                raise CliError(f"no watch {rest} (there are {len(watches)})")
            del watches[index]
        self.do_watch(session, "")

    def do_eval(self, session, rest: str) -> None:
        if not rest:
            raise CliError("usage: .eval EXPR")
        print(watch_value(rest, self._context(), self.debugger))

    # -- stopped -----------------------------------------------------------

    def on_pause(self, pause: Pause) -> Action:
        print(f"stopped: {pause.describe()}")
        for text, value in self.debugger.watch_values(pause.context):
            print(f"  {text} = {value}")
        while True:
            line = self.next_line(f"(debug {pause.function_id}:{pause.line})> ")
            if line is None:
                print("(no more input: breakpoints cleared, running on)")
                self.debugger.clear()
                return Action.CONTINUE
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            word, _, rest = line.partition(" ")
            if word in ACTIONS and not rest:
                return ACTIONS[word]
            try:
                self._paused_line(pause, word, rest.strip(), line)
            except CliError as exc:
                print(f"error: {exc}")
                self.failed = True

    def _paused_line(self, pause: Pause, word: str, rest: str, line: str) -> None:
        if word in ("where", "bt"):
            for depth, frame in enumerate(reversed(pause.stack)):
                print(f"#{depth} {frame.function_id}:{frame.line}")
        elif word in ("context", "ctx"):
            for name in ("executor", "position", "rotation", "dimension"):
                print(f"{name}: {watch_value(name, pause.context)}")
        elif word in ("list", "l"):
            self._list(pause, int(rest) if rest.isdigit() else 3)
        elif word == "help":
            print(PAUSED_HELP)
        elif line.startswith("."):
            name = line[1:].split(" ", 1)[0].replace("-", "_")
            if name not in PAUSED_DOTS:
                raise CliError(f".{name} is not available while stopped (continue or stop first)")
            if name == "help":
                print(PAUSED_HELP)
            else:
                self.dot(line)
        else:
            self._run_here(pause, line)

    def _list(self, pause: Pause, around: int) -> None:
        function = self.session.emulator.library.function(pause.function_id)
        lines = function.lines if function is not None else []
        marks = self.debugger.lines_of(pause.function_id)
        first = max(1, pause.line - around)
        for number in range(first, min(len(lines), pause.line + around) + 1):
            arrow = "->" if number == pause.line else "  "
            dot = "●" if number in marks else " "
            print(f"{dot}{arrow}{number:4} {lines[number - 1]}")

    def _run_here(self, pause: Pause, line: str) -> None:
        command = Command.parse(line.removeprefix("/"), source="<debug>")
        if command is None:
            return
        emulator = self.session.emulator
        with self.debugger.aside():
            # typed, so the game's answers are shown (depth 0)
            result = emulator.run_command(command, pause.context.branch(depth=0))
        outcome = "succeeded" if result.success else "failed"
        print(f"→ {outcome} (value {result.value})")
