"""Real timings from a Minecraft server: reading ``/perf`` reports.

A vanilla server (1.17+) profiles itself with ``/perf start``: ten seconds
later it writes ``debug/profiling/<date>-<world>-<version>.zip``, whose
``server/profiling.txt`` is the profiler tree. The function manager opens a
section per function it runs (``function ns:tick``) and, from 1.20.3, one per
command line inside it (``execute scoreboard players add #x t 1``), with how
often each ran. That is measured time, on a real server, per function and per
line -- what the emulator's cost model only estimates.

The tree is printed as::

    [03] |   |   |   function probe:sb(201/1) - 65.15%/1.53%
    [04] |   |   |   |   execute scoreboard players add #x p 1(402000/2000) - 45.36%/0.70%

``depth``, ``name(total calls/calls per tick)``, then the share of the parent
and of the whole run. The whole-run share is rounded to 0.01 % (one
millisecond of a ten-second run), so times are computed from the *parent*
shares multiplied down the path, which keep four significant digits.

No mod is needed. (spark profiles Java methods, which do not map back to
datapack functions; the vanilla profiler does.)
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HEADER = re.compile(r"^(Version|Time span|Tick span):\s*(.+?)\s*$")
_NODE = re.compile(
    r"^\[(?P<depth>\d+)\]\s[|\s]*(?P<name>.*?)\((?P<total>\d+)/(?P<per>\d+)\)"
    r"\s+-\s+(?P<local>[\d.]+)%/(?P<global>[\d.]+)%\s*$"
)
#: where a ``/perf`` zip keeps the tree
PROFILE_ENTRY = "server/profiling.txt"
FUNCTION_PREFIX = "function "
#: a command line's section; "prepare" is a forking ``execute`` being set up
COMMAND_PREFIXES = ("execute ", "prepare ")


@dataclass
class Section:
    """One profiler section and its children."""

    name: str
    depth: int
    calls: int
    calls_per_tick: int
    local_percent: float
    global_percent: float
    #: share of the whole run, from the parents' shares (more precise)
    share: float = 0.0
    children: list[Section] = field(default_factory=list)
    parent: Section | None = field(default=None, repr=False)

    def walk(self) -> Iterator[Section]:
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def path(self) -> str:
        names = []
        node: Section | None = self
        while node is not None and node.depth >= 0:
            names.append(node.name)
            node = node.parent
        return "/".join(reversed(names))


@dataclass
class ServerProfile:
    """A parsed ``profiling.txt``."""

    version: str
    time_span_ms: float
    ticks: int
    root: Section
    source: str = ""

    # -- reading ------------------------------------------------------------

    @classmethod
    def parse(cls, text: str, source: str = "") -> ServerProfile:
        header: dict[str, str] = {}
        root = Section("<root>", -1, 0, 0, 100.0, 100.0, share=1.0)
        stack = [root]
        in_dump = False
        for line in text.splitlines():
            if line.startswith("--- BEGIN PROFILE DUMP"):
                in_dump = True
                continue
            if line.startswith("--- END PROFILE DUMP"):
                break
            if not in_dump:
                match = _HEADER.match(line)
                if match:
                    header[match.group(1)] = match.group(2)
                continue
            match = _NODE.match(line)
            if not match:
                continue
            depth = int(match["depth"])
            while stack[-1].depth >= depth:
                stack.pop()
            parent = stack[-1]
            local = float(match["local"])
            node = Section(
                name=match["name"],
                depth=depth,
                calls=int(match["total"]),
                calls_per_tick=int(match["per"]),
                local_percent=local,
                global_percent=float(match["global"]),
                share=parent.share * local / 100.0,
                parent=parent,
            )
            parent.children.append(node)
            stack.append(node)
        if "Time span" not in header or "Tick span" not in header:
            raise ValueError("not a Minecraft profiler report (no time or tick span)")
        if not root.children:
            raise ValueError("the profiler report has no profile dump")
        return cls(
            version=header.get("Version", "?"),
            time_span_ms=float(header["Time span"].split()[0]),
            ticks=int(header["Tick span"].split()[0]),
            root=root,
            source=source,
        )

    @classmethod
    def read(cls, path: Path | str) -> ServerProfile:
        """A ``/perf`` zip, or a ``profiling.txt`` taken out of one."""
        path = Path(path)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith("profiling.txt")]
                if not names:
                    raise ValueError(f"{path} has no {PROFILE_ENTRY}: is it a /perf report?")
                name = PROFILE_ENTRY if PROFILE_ENTRY in names else names[0]
                text = archive.read(name).decode("utf-8", "replace")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        return cls.parse(text, source=str(path))

    # -- numbers ------------------------------------------------------------

    def us_per_tick(self, section: Section) -> float:
        """Microseconds this section took per tick, on average."""
        return section.share * self.time_span_ms * 1000.0 / max(self.ticks, 1)

    @property
    def mspt(self) -> float:
        """Average milliseconds per tick spent ticking (not waiting)."""
        tick = next((c for c in self.root.children if c.name == "tick"), None)
        return self.us_per_tick(tick) / 1000.0 if tick else 0.0

    def function_sections(self) -> list[Section]:
        return [s for s in self.root.walk() if s.name.startswith(FUNCTION_PREFIX) and s.depth >= 0]

    def functions(self) -> list[dict[str, Any]]:
        """One row per function the server ran, dearest first.

        ``us_per_tick`` is the function and everything it ran; a function run
        from several places (two tags, a schedule) gets one row per place,
        merged here by id. ``lines`` are the commands inside it (1.20.3+),
        each with its runs and the time of one run.
        """
        merged: dict[str, dict[str, Any]] = {}
        for section in self.function_sections():
            function_id = section.name[len(FUNCTION_PREFIX) :].strip()
            row = merged.setdefault(
                function_id,
                {"function": function_id, "calls": 0, "us_per_tick": 0.0, "lines": {}},
            )
            row["calls"] += section.calls
            row["us_per_tick"] += self.us_per_tick(section)
            for child in section.children:
                prefix = next((p for p in COMMAND_PREFIXES if child.name.startswith(p)), None)
                if prefix is None:
                    continue
                command = child.name[len(prefix) :]
                line = row["lines"].setdefault(command, {"command": command, "runs": 0, "us": 0.0})
                line["runs"] += child.calls
                line["us"] += self.us_per_tick(child) * self.ticks
        rows = []
        for row in merged.values():
            lines = sorted(row.pop("lines").values(), key=lambda line: -line["us"])
            row["calls_per_tick"] = row["calls"] / max(self.ticks, 1)
            row["lines"] = [
                {
                    "command": line["command"],
                    "runs": line["runs"],
                    "runs_per_tick": line["runs"] / max(self.ticks, 1),
                    "us_each": line["us"] / line["runs"] if line["runs"] else 0.0,
                    "us_per_tick": line["us"] / max(self.ticks, 1),
                }
                for line in lines
            ]
            rows.append(row)
        return sorted(rows, key=lambda row: -row["us_per_tick"])

    def to_dict(self, top_lines: int = 10) -> dict[str, Any]:
        functions = self.functions()
        return {
            "measured": True,
            "source": self.source,
            "version": self.version,
            "time_span_ms": self.time_span_ms,
            "ticks": self.ticks,
            "mspt": round(self.mspt, 4),
            "datapack_ms_per_tick": round(
                sum(self.us_per_tick(s) for s in self.root.walk() if s.name == "commandFunctions")
                / 1000.0,
                4,
            ),
            "functions": [
                {
                    "function": row["function"],
                    "calls_per_tick": round(row["calls_per_tick"], 3),
                    "ms_per_tick": round(row["us_per_tick"] / 1000.0, 5),
                    "lines": [
                        {
                            "command": line["command"],
                            "runs_per_tick": round(line["runs_per_tick"], 3),
                            "us_each": round(line["us_each"], 4),
                        }
                        for line in row["lines"][:top_lines]
                    ],
                }
                for row in functions
            ],
        }


def compare(profile: ServerProfile, estimated: Any) -> list[dict[str, Any]]:
    """Measured against estimated ms per tick, per function.

    ``estimated`` is the emulator's :class:`Profiler` after a run of the same
    pack. Functions the server ran from a tag or schedule are compared with
    the emulator's *total* for that function (it and what it called), which
    is what the server's section measures. ``ratio`` > 1: the estimate is too
    low.
    """
    ticks = max(getattr(estimated, "ticks", 0), 1)
    entries = dict(estimated.sorted_entries())
    rows = []
    for row in profile.functions():
        entry = entries.get(row["function"])
        estimate = entry["total_us"] / ticks / 1000.0 if entry else None
        measured = row["us_per_tick"] / 1000.0
        rows.append(
            {
                "function": row["function"],
                "measured_ms_per_tick": round(measured, 5),
                "estimated_ms_per_tick": round(estimate, 5) if estimate is not None else None,
                "ratio": round(measured / estimate, 3) if estimate else None,
            }
        )
    return rows


__all__ = ["Section", "ServerProfile", "compare"]
