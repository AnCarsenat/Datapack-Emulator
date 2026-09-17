"""Version-independent command parsing: selectors, ``execute`` chains, macros.

The parser accepts anything; deciding whether a command *exists* in a given
version is :mod:`datapack_emulator.emulator.commands.registry`'s job.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from datapack_emulator.emulator import costs
from datapack_emulator.emulator.common import (
    normalise_id,
    normalise_tagged_id,
    parse_number,
    split_arguments,
    tokenize,
    volume_of,
)


@dataclass
class Selector:
    """A target selector, e.g. ``@e[type=armor_stand,tag=hat,limit=1]``."""

    raw: str
    kind: str = "@s"  # @s @p @a @r @e @n, or "literal" for a name/UUID
    arguments: dict[str, list[str]] = field(default_factory=dict)

    SELECTOR_RE = re.compile(r"^@([sparen])(?:\[(.*)\])?$", re.DOTALL)

    @classmethod
    def parse(cls, raw: str) -> Selector:
        raw = raw.strip()
        match = cls.SELECTOR_RE.match(raw)
        if match is None:
            return cls(raw=raw, kind="literal")
        arguments: dict[str, list[str]] = {}
        for key, value in split_arguments(match.group(2) or ""):
            arguments.setdefault(key, []).append(value)
        return cls(raw=raw, kind="@" + match.group(1), arguments=arguments)

    def first(self, key: str, default: str | None = None) -> str | None:
        values = self.arguments.get(key)
        return values[0] if values else default

    @property
    def limit(self) -> int | None:
        raw = self.first("limit") or self.first("c")
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None

    @property
    def is_player_only(self) -> bool:
        return self.kind in ("@a", "@p", "@r")

    @property
    def is_single(self) -> bool:
        return self.kind in ("@s", "@p", "@r", "@n", "literal") or self.limit == 1

    def estimate_cost(self, entity_count: int = 30) -> float:
        """Cost of *resolving* this selector, in microseconds."""
        if self.kind == "literal":
            return 1.0
        cost = costs.SELECTOR_BASE_COST_US.get(self.kind, 10.0)
        scanned = entity_count if self.kind in ("@e", "@n") else max(1, entity_count // 10)
        scan = scanned * costs.SELECTOR_PER_ENTITY_COST_US
        if "type" in self.arguments:
            scan *= costs.TYPE_FILTER_DISCOUNT
        if self.limit is not None:
            scan *= costs.LIMIT_DISCOUNT
        cost += scan
        if "nbt" in self.arguments:
            cost += costs.NBT_FILTER_COST_US * max(1, scanned // 4)
        if "predicate" in self.arguments:
            cost += costs.CONDITION_COST_US["predicate"]
        return cost

    def __str__(self) -> str:
        return self.raw


#: how many tokens each ``execute`` subcommand consumes after its own name
SUBCOMMAND_ARITY: dict[str, int] = {
    "align": 1,
    "anchored": 1,
    "as": 1,
    "at": 1,
    "in": 1,
    "on": 1,
    "summon": 1,
    "rotated": 2,  # or "as <selector>", resolved while parsing
    "positioned": 3,  # or "as <selector>" / "over <heightmap>"
    "facing": 3,  # or "entity <selector> <anchor>"
}

#: how many tokens each ``if``/``unless`` condition consumes after its own name
CONDITION_ARITY: dict[str, int] = {
    "biome": 4,
    "block": 4,
    "blocks": 10,  # <start xyz> <end xyz> <destination xyz> all|masked
    "dimension": 1,
    "entity": 1,
    "function": 1,
    "loaded": 3,
    "predicate": 1,
}


@dataclass
class Subcommand:
    name: str
    arguments: list[str] = field(default_factory=list)

    @property
    def selectors(self) -> list[Selector]:
        return [Selector.parse(arg) for arg in self.arguments if arg.startswith("@")]

    @property
    def condition(self) -> str:
        """For ``if``/``unless``: the condition name (``score``, ``entity``…)."""
        return self.arguments[0] if self.arguments else ""

    def __str__(self) -> str:
        return " ".join([self.name, *self.arguments])


class Command:
    """One line of a ``.mcfunction`` file, parsed."""

    MACRO_RE = re.compile(r"\$\(([A-Za-z0-9_.+-]+)\)")

    def __init__(
        self,
        raw: str,
        name: str = "",
        arguments: list[str] | None = None,
        source: str = "",
        line: int = 0,
    ):
        self.raw = raw
        self.name = name
        self.arguments: list[str] = arguments or []
        self.source = source  # id of the function this line came from
        self.line = line
        self.is_macro = raw.lstrip().startswith("$")
        #: for ``execute``: the chain before ``run``
        self.subcommands: list[Subcommand] = []
        #: for ``execute … run <command>`` and ``return run <command>``: the wrapped command
        self.child: Command | None = None

    # -- parsing ----------------------------------------------------------

    @classmethod
    def parse(cls, raw_line: str, source: str = "", line: int = 0) -> Command | None:
        """Parse one line.  Returns ``None`` for blank lines and comments."""
        text = raw_line.strip()
        if not text or text.startswith("#"):
            return None
        body = text[1:].strip() if text.startswith("$") else text
        body = body.removeprefix("/")
        tokens = tokenize(body)
        if not tokens:
            return None
        command = cls(raw=text, name=tokens[0], arguments=tokens[1:], source=source, line=line)
        if command.name == "execute":
            command._parse_execute(tokens[1:])
        elif command.name == "return" and len(tokens) > 2 and tokens[1] == "run":
            # the wrapped command is parsed (and checked) with the line
            command.child = Command.parse(" ".join(tokens[2:]), source=source, line=line)
        return command

    def _parse_execute(self, tokens: list[str]) -> None:
        index = 0
        while index < len(tokens):
            name = tokens[index]
            index += 1
            if name == "run":
                rest = " ".join(tokens[index:])
                self.child = Command.parse(rest, source=self.source, line=self.line)
                return
            if name in ("if", "unless"):
                arity, arguments = self._condition_arity(tokens[index:])
                self.subcommands.append(Subcommand(name, list(arguments)))
                index += arity
                continue
            if name == "store":
                consumed = self._store_arity(tokens[index : index + 6])
                self.subcommands.append(Subcommand(name, tokens[index : index + consumed]))
                index += consumed
                continue
            arity = SUBCOMMAND_ARITY.get(name, 1)
            if name in ("positioned", "rotated", "facing") and index < len(tokens):
                lookahead = tokens[index]
                if lookahead == "as" or name == "positioned" and lookahead == "over":
                    arity = 2
                elif name == "facing" and lookahead == "entity":
                    arity = 3
            self.subcommands.append(Subcommand(name, tokens[index : index + arity]))
            index += arity

    @staticmethod
    def _condition_arity(tokens: list[str]) -> tuple[int, list[str]]:
        """Return ``(tokens consumed, the condition tokens)``."""
        if not tokens:
            return (0, [])
        kind = tokens[0]
        if kind == "score":
            # score <t> <o> matches <range> | score <t> <o> <op> <t2> <o2>
            consumed = 5 if len(tokens) > 3 and tokens[3] == "matches" else 6
        elif kind == "data":
            consumed = 6 if len(tokens) > 1 and tokens[1] == "block" else 4
        elif kind == "items":
            consumed = 7 if len(tokens) > 1 and tokens[1] == "block" else 5
        else:
            consumed = 1 + CONDITION_ARITY.get(kind, 1)
        consumed = min(consumed, len(tokens))
        return (consumed, tokens[:consumed])

    @staticmethod
    def _store_arity(tokens: list[str]) -> int:
        # store (result|success) <target> ...
        if len(tokens) < 2:
            return len(tokens)
        return {
            "score": 4,
            "bossbar": 4,
            "storage": 6,
            "entity": 6,
            "block": 8,
        }.get(tokens[1], 4)

    # -- static analysis --------------------------------------------------

    def walk(self) -> Iterator[Command]:
        yield self
        if self.child is not None:
            yield from self.child.walk()

    def calls(self) -> list[tuple[str, str]]:
        """Static outgoing call edges: ``(target id or #tag, kind)``."""
        out: list[tuple[str, str]] = []
        for command in self.walk():
            if command.name == "function" and command.arguments:
                kind = "macro" if command.is_macro or "with" in command.arguments else "call"
                out.append((normalise_tagged_id(command.arguments[0]), kind))
            elif command.name == "schedule" and len(command.arguments) >= 2:
                if command.arguments[0] == "function":
                    out.append((normalise_tagged_id(command.arguments[1]), "schedule"))
            elif command.name == "execute":
                for subcommand in command.subcommands:
                    if subcommand.condition == "function" and len(subcommand.arguments) > 1:
                        out.append((normalise_tagged_id(subcommand.arguments[1]), "condition"))
        return out

    @property
    def selectors(self) -> list[Selector]:
        out: list[Selector] = []
        for command in self.walk():
            for subcommand in command.subcommands:
                out.extend(subcommand.selectors)
            for argument in command.arguments:
                if argument.startswith("@"):
                    out.append(Selector.parse(argument))
        return out

    def features(self) -> set[str]:
        """Feature keys this line needs, for the version checker."""
        needed: set[str] = set()
        for command in self.walk():
            needed.add(f"command:{command.name}")
            if command.is_macro:
                needed.add("function:with")
            first = command.arguments[0] if command.arguments else ""
            if command.name == "schedule" and first in ("function", "clear"):
                needed.add(f"schedule:{first}")
            if command.name == "return" and first in ("run", "fail"):
                needed.add(f"return:{first}")
            if command.name == "function" and "with" in command.arguments:
                needed.add("function:with")
            for subcommand in command.subcommands:
                needed.add(f"execute:{subcommand.name}")
                if subcommand.name in ("if", "unless") and subcommand.condition:
                    needed.add(f"condition:{subcommand.condition}")
                if subcommand.name == "store" and len(subcommand.arguments) > 1:
                    needed.add(f"store:{subcommand.arguments[1]}")
        return needed

    def estimate_cost(self, entity_count: int = 30) -> float:
        """Estimated wall-clock cost of this line, in microseconds.

        Excludes the body of any function it calls: the emulator charges those
        to the callee.  See :mod:`datapack_emulator.emulator.costs` for the model.
        """
        total = costs.COMMAND_COST_US.get(self.name, costs.DEFAULT_COMMAND_COST_US)
        if self.is_macro:
            total += costs.MACRO_CALL_OVERHEAD_US

        for subcommand in self.subcommands:
            total += costs.SUBCOMMAND_COST_US.get(subcommand.name, 1.0)
            if subcommand.name in ("if", "unless") and subcommand.condition:
                total += costs.CONDITION_COST_US.get(subcommand.condition, 5.0)
            for selector in subcommand.selectors:
                total += selector.estimate_cost(entity_count)

        for argument in self.arguments:
            if argument.startswith("@"):
                total += Selector.parse(argument).estimate_cost(entity_count)

        if self.name in ("fill", "clone", "fillbiome"):
            total += volume_of(self.arguments) * costs.VOLUME_COST_US_PER_BLOCK

        if self.child is not None:
            total += self.child.estimate_cost(entity_count)
        return total

    # -- macros -----------------------------------------------------------

    def macro_keys(self) -> list[str]:
        return self.MACRO_RE.findall(self.raw)

    def expand_macro(self, arguments: dict[str, Any]) -> tuple[Command | None, list[str]]:
        """Substitute ``$(key)`` placeholders.  Returns ``(command, missing)``."""
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in arguments:
                missing.append(key)
                return match.group(0)
            value = arguments[key]
            return json.dumps(value) if isinstance(value, (dict, list)) else str(value)

        text = self.MACRO_RE.sub(replace, self.raw)
        if missing:
            return (None, sorted(set(missing)))
        expanded = Command.parse(text, source=self.source, line=self.line)
        if expanded is not None:
            expanded.is_macro = True  # keep the macro overhead in the cost model
        return (expanded, [])

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return f"<Command {self.name} @{self.source}:{self.line}>"


def resolve_position(tokens: list[str], origin: list[float]) -> list[float]:
    """Absolute, ``~`` relative and (approximated) ``^`` local coordinates."""
    out: list[float] = list(origin)
    for index, token in enumerate(tokens[:3]):
        token = token.strip()
        try:
            if token.startswith(("~", "^")):
                offset = float(token[1:]) if len(token) > 1 else 0.0
                out[index] = origin[index] + offset
            else:
                out[index] = float(token)
        except ValueError:
            pass
    return out


#: ticks per time unit suffix
TIME_UNITS = {"": 1, "t": 1, "s": 20, "d": 24000}


def parse_ticks(token: str) -> int | None:
    """A time argument in ticks (``5``, ``5t``, ``2.5s``, ``1d``), rounded like
    vanilla; None when it is not one."""
    token = token.strip()
    unit = token[-1:] if token[-1:] in ("t", "s", "d") else ""
    value = parse_number(token[: len(token) - len(unit)])
    if value is None:
        return None
    return math.floor(value * TIME_UNITS[unit] + 0.5)


def parse_duration(token: str) -> int:
    """``5``/``5t`` ticks, ``5s`` seconds, ``5d`` Minecraft days (1 when unreadable)."""
    ticks = parse_ticks(token)
    return 1 if ticks is None else ticks


__all__ = [
    "Command",
    "Selector",
    "Subcommand",
    "normalise_id",
    "parse_duration",
    "resolve_position",
]
