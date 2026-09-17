"""Per-function timings and the HTML report."""

from __future__ import annotations

from collections import deque
from html import escape
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import costs

#: a call path: the tag or schedule that started it, then each function called
CallPath = tuple[str, ...]


def _short(text: str, width: int = 90) -> str:
    """A command as one line of a table, with an ellipsis when it is cut."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _stats() -> dict[str, float]:
    return {"calls": 0.0, "commands": 0.0, "self_us": 0.0, "total_us": 0.0}


class Profiler:
    """Estimated time and call counts, per function and per call path.

    ``entries`` add up everything a function did wherever it was called from;
    ``tree`` keeps the same numbers per call path (``#minecraft:tick`` ›
    ``hat:tick`` › ``hat:swap``), which is what the profiler's tree shows.
    Divide by ``ticks`` for the cost of one tick.
    """

    #: recent tick durations kept for display
    TICK_HISTORY = 10_000
    #: calls deeper than this (recursion) are added up in one "…" node under the last one
    MAX_PATH = 32
    DEEPER = "…"

    def __init__(self) -> None:
        #: function id -> {"calls", "commands", "self_us", "total_us"}
        self.entries: dict[str, dict[str, float]] = {}
        #: call path -> the same statistics, for that path only
        self.tree: dict[CallPath, dict[str, float]] = {}
        #: (function id, line) -> {"raw", "runs", "self_us"}: what one line costs
        self.commands: dict[tuple[str, int], dict[str, Any]] = {}
        self._stack: list[str] = []
        self._starts: list[float] = []
        self._total_us = 0.0
        #: the most recent tick durations (endless runs would otherwise grow forever)
        self.tick_times: deque[float] = deque(maxlen=self.TICK_HISTORY)
        #: totals over every tick, not just the recent history
        self.ticks = 0
        self._tick_total_us = 0.0
        self._worst_tick_us = 0.0
        #: what was run, for a saved profile: the pack, the version and when
        self.pack = ""
        self.version = ""
        self.saved = ""

    def _entry(self, function_id: str) -> dict[str, float]:
        entry = self.entries.get(function_id)
        if entry is None:
            entry = self.entries[function_id] = _stats()
        return entry

    def _path(self) -> CallPath:
        if len(self._stack) <= self.MAX_PATH:
            return tuple(self._stack)
        return (*self._stack[: self.MAX_PATH], self.DEEPER)

    def call(self, function_id: str) -> None:
        self._entry(function_id)["calls"] += 1

    def enter(self, name: str) -> None:
        """A function (or the tag / schedule running functions) starts."""
        self._stack.append(name)
        path = self._path()
        node = self.tree.get(path)
        if node is None:
            node = self.tree[path] = _stats()
        node["calls"] += 1
        self._starts.append(self._total_us)

    def leave(self) -> None:
        """The innermost :meth:`enter` ends: its path gets the time spent inside."""
        if not self._stack:
            return
        start = self._starts.pop()
        # calls nested inside the "…" node are already inside its outermost call
        if len(self._stack) <= self.MAX_PATH + 1:
            self.tree[self._path()]["total_us"] += self._total_us - start
        self._stack.pop()

    def charge(
        self,
        function_id: str,
        microseconds: float,
        line: int = 0,
        raw: str = "",
        count_run: bool = True,
    ) -> None:
        """``count_run`` is false for the command an ``execute … run`` wraps:
        its cost belongs to the line, but the line itself ran once."""
        if line:
            key = (function_id, line)
            command = self.commands.get(key)
            if command is None:
                command = self.commands[key] = {"raw": raw, "runs": 0, "self_us": 0.0}
            if count_run:
                command["runs"] += 1
            command["self_us"] += microseconds
        entry = self._entry(function_id)
        entry["self_us"] += microseconds
        entry["commands"] += 1
        self._total_us += microseconds
        if self._stack:
            node = self.tree[self._path()]
            node["self_us"] += microseconds
            node["commands"] += 1

    def charge_total(self, function_id: str, microseconds: float) -> None:
        self._entry(function_id)["total_us"] += microseconds

    def sorted_entries(self, key: str = "total_us") -> list[tuple[str, dict[str, float]]]:
        return sorted(self.entries.items(), key=lambda item: -item[1][key])

    def hot_commands(self, limit: int = 20) -> list[tuple[str, int, dict[str, Any]]]:
        """The lines that cost the most, dearest first: ``(function, line, stats)``."""
        ordered = sorted(self.commands.items(), key=lambda item: -item[1]["self_us"])
        return [(function_id, line, stats) for (function_id, line), stats in ordered[:limit]]

    def per_tick(self, stats: dict[str, float]) -> dict[str, float]:
        """``stats`` divided by the ticks run: the cost of one average tick."""
        ticks = max(self.ticks, 1)
        return {key: value / ticks for key, value in stats.items()}

    def children_index(self) -> dict[CallPath, list[CallPath]]:
        """Parent path -> child paths (``()`` holds the roots), costliest first."""
        index: dict[CallPath, list[CallPath]] = {}
        for path in self.tree:
            index.setdefault(path[:-1], []).append(path)
        for paths in index.values():
            paths.sort(key=lambda path: -self.tree[path]["total_us"])
        return index

    def children(self, path: CallPath) -> list[tuple[CallPath, dict[str, float]]]:
        """The call paths one level below ``path`` (``()`` for the roots), costliest first."""
        return [(child, self.tree[child]) for child in self.children_index().get(path, [])]

    @property
    def total_us(self) -> float:
        return self._total_us

    @property
    def worst_tick_us(self) -> float:
        return self._worst_tick_us

    @property
    def average_tick_us(self) -> float:
        return self._tick_total_us / self.ticks if self.ticks else 0.0

    def record_tick(self, microseconds: float) -> None:
        self.tick_times.append(microseconds)
        self.ticks += 1
        self._tick_total_us += microseconds
        self._worst_tick_us = max(self._worst_tick_us, microseconds)

    # -- keeping and comparing runs ---------------------------------------

    #: what a saved profile says it is, and the shape of the file
    KIND = "datapack-emulator-profile"
    FORMAT = 1

    def to_dict(self) -> dict[str, Any]:
        """The run's numbers, for saving and comparing (JSON-ready)."""
        return {
            "kind": self.KIND,
            "format": self.FORMAT,
            "pack": self.pack,
            "version": self.version,
            "saved": self.saved,
            "ticks": self.ticks,
            "total_us": self.total_us,
            "tick_total_us": self._tick_total_us,
            "worst_tick_us": self._worst_tick_us,
            "entries": {name: dict(stats) for name, stats in self.entries.items()},
            "tree": {"\u0000".join(path): dict(stats) for path, stats in self.tree.items()},
            "commands": {
                f"{function_id}\u0000{line}": dict(stats)
                for (function_id, line), stats in self.commands.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Any) -> Profiler:
        """A profile read back. Anything that is not one raises ``ValueError``."""
        if not isinstance(data, dict) or data.get("kind") != cls.KIND:
            raise ValueError("not a profile written by this program")
        try:
            profiler = cls()
            profiler.pack = str(data.get("pack", ""))
            profiler.version = str(data.get("version", ""))
            profiler.saved = str(data.get("saved", ""))
            profiler.ticks = int(data.get("ticks", 0))
            profiler._total_us = float(data.get("total_us", 0.0))
            profiler._tick_total_us = float(data.get("tick_total_us", 0.0))
            profiler._worst_tick_us = float(data.get("worst_tick_us", 0.0))
            profiler.entries = {
                str(name): {**_stats(), **{key: float(value) for key, value in stats.items()}}
                for name, stats in (data.get("entries") or {}).items()
            }
            profiler.tree = {
                tuple(path.split("\u0000")): {
                    **_stats(),
                    **{key: float(value) for key, value in stats.items()},
                }
                for path, stats in (data.get("tree") or {}).items()
            }
            commands: dict[tuple[str, int], dict[str, Any]] = {}
            for key, stats in (data.get("commands") or {}).items():
                function_id, _, line = key.rpartition("\u0000")
                commands[(function_id, int(line))] = {
                    "raw": str(stats.get("raw", "")),
                    "runs": int(float(stats.get("runs", 0))),
                    "self_us": float(stats.get("self_us", 0.0)),
                }
            profiler.commands = commands
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"the profile is damaged: {exc}") from exc
        return profiler

    def snapshot(self) -> Profiler:
        """A copy of the run so far, to compare a later run against."""
        return Profiler.from_dict(self.to_dict())

    def compare(self, baseline: Profiler) -> list[dict[str, Any]]:
        """Per-tick cost of every function in this run and in ``baseline``,
        sorted by the biggest change."""
        rows = []
        for function_id in sorted(set(self.entries) | set(baseline.entries)):
            mine = self.per_tick(self.entries.get(function_id, _stats()))
            theirs = baseline.per_tick(baseline.entries.get(function_id, _stats()))
            rows.append(
                {
                    "function": function_id,
                    "before_us": theirs["total_us"],
                    "after_us": mine["total_us"],
                    "delta_us": mine["total_us"] - theirs["total_us"],
                    "before_commands": theirs["commands"],
                    "after_commands": mine["commands"],
                }
            )
        return sorted(rows, key=lambda row: -abs(row["delta_us"]))

    def compare_commands(self, baseline: Profiler) -> list[dict[str, Any]]:
        """The same, per line: what each line costs per tick in both runs."""
        rows = []
        for key in set(self.commands) | set(baseline.commands):
            mine = self.commands.get(key) or {"raw": "", "runs": 0, "self_us": 0.0}
            theirs = baseline.commands.get(key) or {"raw": "", "runs": 0, "self_us": 0.0}
            before = theirs["self_us"] / max(baseline.ticks, 1)
            after = mine["self_us"] / max(self.ticks, 1)
            rows.append(
                {
                    "function": key[0],
                    "line": key[1],
                    "raw": mine["raw"] or theirs["raw"],
                    "before_us": before,
                    "after_us": after,
                    "delta_us": after - before,
                    "before_runs": theirs["runs"] / max(baseline.ticks, 1),
                    "after_runs": mine["runs"] / max(self.ticks, 1),
                }
            )
        return sorted(rows, key=lambda row: (-abs(row["delta_us"]), row["function"], row["line"]))

    def reset(self) -> None:
        self.entries.clear()
        self.commands.clear()
        self.tree.clear()
        self._stack.clear()
        self._starts.clear()
        self._total_us = 0.0
        self.tick_times.clear()
        self.ticks = 0
        self._tick_total_us = 0.0
        self._worst_tick_us = 0.0

    # -- reporting --------------------------------------------------------

    def summary(self) -> str:
        """One sentence on the average and worst tick against the budget."""
        if not self.ticks:
            return (
                "no ticks run yet — the numbers below are totals (load, typed commands), "
                "not per tick: run or step the emulator"
            )
        average = self.average_tick_us / 1000
        worst = self.worst_tick_us / 1000
        budget = costs.TICK_BUDGET_US / 1000
        return (
            f"average of {self.ticks} tick(s): {average:.3f} ms per tick "
            f"({average / budget:.1%} of the {budget:.0f} ms budget) · worst tick {worst:.3f} ms · "
            "estimates from a cost model, not measurements"
        )

    def tree_rows(self) -> list[tuple[CallPath, dict[str, float]]]:
        """Every call path, depth first and costliest first, with its per-tick
        numbers plus ``share`` (of an average tick) — the profiler tab's tree."""
        index = self.children_index()
        tick_cost = self.total_us / max(self.ticks, 1) or 1.0
        rows: list[tuple[CallPath, dict[str, float]]] = []

        def walk(path: CallPath) -> None:
            for child in index.get(path, []):
                one = self.per_tick(self.tree[child])
                one["share"] = one["total_us"] / tick_cost
                rows.append((child, one))
                walk(child)

        walk(())
        return rows

    def to_html(
        self,
        title: str = "Function profiler",
        subtitle: str = "",
        baseline: Profiler | None = None,
    ) -> str:
        """The report: a flame graph of the call tree, the call tree itself,
        the dearest lines, every function, and — with ``baseline`` — what
        changed since that run."""
        # titles and function ids come from the pack: escape them, the report
        # is shown in the app's web view
        title, subtitle = escape(title), escape(subtitle)
        ticks = self.ticks
        tick_cost = self.total_us / max(ticks, 1) or 1.0
        rows: list[str] = []
        for function_id, entry in self.sorted_entries():
            one = self.per_tick(entry)
            share = 100.0 * one["total_us"] / tick_cost
            rows.append(
                f'<tr data-function="{escape(function_id)}" title="right-click to open the source">'
                f"<td class='id'>{escape(function_id)}</td>"
                f"<td>{one['calls']:.2f}</td>"
                f"<td>{one['commands']:.1f}</td>"
                f"<td>{one['self_us'] / 1000:.4f}</td>"
                f"<td>{one['total_us'] / 1000:.4f}</td>"
                f"<td><div class='bar' style='width:{min(share, 100):.1f}%'></div>"
                f"<span>{share:.1f}%</span></td>"
                f"<td>{int(entry['calls'])}</td>"
                f"<td>{entry['total_us'] / 1000:.3f}</td>"
                "</tr>"
            )
        worst = self.worst_tick_us
        body = "\n".join(rows) or "<tr><td colspan='8'>no data — run the emulator</td></tr>"
        index = self.children_index()
        tree = "\n".join(self._tree_html((), tick_cost, index)) or "<p>no calls recorded</p>"
        flame = self._flame_html((), index) or "<p>no calls recorded</p>"
        hot = (
            "\n".join(
                f'<tr data-function="{escape(function_id)}"'
                ' title="right-click to open the source">'
                f'<td class="id">{escape(function_id)}:{line}</td>'
                f"<td class='id'>{escape(_short(str(stats['raw'])))}</td>"
                f"<td>{stats['runs'] / max(ticks, 1):.2f}</td>"
                f"<td>{stats['self_us'] / max(ticks, 1) / 1000:.4f}</td>"
                f"<td>{stats['self_us'] / 1000:.3f}</td>"
                "</tr>"
                for function_id, line, stats in self.hot_commands()
            )
            or "<tr><td colspan='5'>no commands recorded</td></tr>"
        )
        comparison = self._comparison_html(baseline) if baseline is not None else ""
        over = "warn" if worst > costs.TICK_BUDGET_US else ""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 12px; color: #111; }}
 h2 {{ margin: 0 0 4px; font-size: 16px; }}
 h3 {{ margin: 16px 0 4px; font-size: 14px; }}
 p.summary {{ margin: 0 0 12px; color: #555; font-size: 12px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
 th, td {{ text-align: right; padding: 3px 6px; border-bottom: 1px solid #e3e3e3; }}
 th:first-child, td.id {{ text-align: left; font-family: monospace; }}
 tr:hover {{ background: #e4fbff; }}
 .bar {{ display: inline-block; height: 9px; background: #f5a623; vertical-align: middle;
         margin-right: 5px; min-width: 1px; }}
 .warn {{ color: #c0392b; font-weight: bold; }}
 details {{ margin-left: 16px; font-size: 12px; }}
 summary {{ font-family: monospace; cursor: pointer; padding: 1px 0; }}
 summary span {{ color: #555; font-family: system-ui, sans-serif; }}
 .leaf {{ margin-left: 30px; font-family: monospace; font-size: 12px; padding: 1px 0; }}
 .flame {{ font-family: monospace; font-size: 11px; }}
 /* no horizontal border or padding: a child's % is of its parent's content
    box, so either would shift every level of the graph */
 .frame {{ box-sizing: border-box; border-left: 1px solid #fff; background: #f5a623;
           color: #111; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
 .frame > span {{ padding: 1px 3px; display: block; overflow: hidden;
                  text-overflow: ellipsis; }}
 .frame.deep {{ background: #f7c66b; }}
 .row {{ display: flex; width: 100%; }}
 .rest {{ box-sizing: border-box; background: #f1f1f1; }}
 .up {{ color: #c0392b; }} .down {{ color: #1e6f3d; }}
</style>
</head>
<body>
<h2>{title}</h2>
<p class="summary">
 {subtitle}{" &middot; " if subtitle else ""}{ticks} tick(s) &middot;
 total {self.total_us / 1000:.2f} ms &middot;
 average {self.average_tick_us / 1000:.3f} ms/tick &middot;
 worst <span class="{over}">{worst / 1000:.2f} ms</span>
 (budget {costs.TICK_BUDGET_US / 1000:.0f} ms)
 <br>Per tick = the run's totals divided by its ticks. Times are estimates from a
 cost model, not measurements &mdash; see datapack_emulator/emulator/costs.py.
</p>
{comparison}
<h3>Flame graph, per tick</h3>
<p class="summary">Each bar is a call path; its width is the share of its caller
 (the roots share the whole run), so a bar 20% as wide as a tick cost a fifth of it.
 Paths under 0.2% of their caller stay in it.</p>
<div class="flame">{flame}</div>
<h3>Call tree, per tick</h3>
{tree}
<h3>Dearest lines</h3>
<table>
<thead><tr><th>line</th><th>command</th><th>runs/tick</th><th>self ms/tick</th>
<th>self ms</th></tr></thead>
<tbody>
{hot}
</tbody>
</table>
<h3>Functions</h3>
<table>
<thead><tr><th>function</th><th>calls/tick</th><th>commands/tick</th><th>self ms/tick</th>
<th>total ms/tick</th><th>share of a tick</th><th>calls</th><th>total ms</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</body>
</html>
"""

    def _tree_html(
        self, path: CallPath, tick_cost: float, index: dict[CallPath, list[CallPath]]
    ) -> list[str]:
        out = []
        for key in index.get(path, []):
            stats = self.tree[key]
            one = self.per_tick(stats)
            name = escape(key[-1])
            label = (
                f"{name} <span>{one['total_us'] / 1000:.4f} ms/tick "
                f"({100.0 * one['total_us'] / tick_cost:.1f}%) &middot; "
                f"self {one['self_us'] / 1000:.4f} ms &middot; {one['calls']:.2f} calls</span>"
            )
            is_function = not key[-1].startswith(("#", "<", self.DEEPER))
            attribute = f' data-function="{name}"' if is_function else ""
            inner = self._tree_html(key, tick_cost, index)
            if inner:
                out.append(f"<details open{attribute}><summary>{label}</summary>")
                out.extend(inner)
                out.append("</details>")
            else:
                out.append(f'<div class="leaf"{attribute}>{label}</div>')
        return out

    def _flame_html(
        self, path: CallPath, index: dict[CallPath, list[CallPath]], depth: int = 0
    ) -> str:
        """Nested bars. A bar's width is its share of what its caller costs, so
        the widths of a whole column still read as shares of a tick; what the
        roots leave out (typed commands) is the empty space after them."""
        children = index.get(path, [])
        if not children:
            return ""
        if path:
            parent = max(self.tree[path]["total_us"], 1e-9)
        else:  # the roots share the run, not only what they add up to
            roots = sum(self.tree[key]["total_us"] for key in children)
            parent = max(roots, self.total_us, 1e-9)
        bars = []
        for key in children:
            stats = self.tree[key]
            share = 100.0 * stats["total_us"] / parent
            if share < 0.2:  # too thin to read; its time stays in the caller
                continue
            one = self.per_tick(stats)
            name = escape(key[-1])
            title = (
                f"{name} — {one['total_us'] / 1000:.4f} ms/tick, "
                f"self {one['self_us'] / 1000:.4f} ms/tick, {one['calls']:.2f} calls/tick"
            )
            attribute = (
                f' data-function="{name}"'
                if not key[-1].startswith(("#", "<", self.DEEPER))
                else ""
            )
            inner = self._flame_html(key, index, depth + 1)
            bars.append(
                f'<div class="frame{" deep" if depth % 2 else ""}" '
                f'style="width:{share:.3f}%" title="{title}"{attribute}>'
                f"<span>{name}</span>{inner}</div>"
            )
        if not bars:
            return ""
        return f'<div class="row">{"".join(bars)}</div>'

    def _comparison_html(self, baseline: Profiler) -> str:
        rows = []
        for row in self.compare(baseline)[:30]:
            delta = row["delta_us"] / 1000
            if abs(delta) < 1e-6:
                continue
            style = "up" if delta > 0 else "down"
            rows.append(
                f'<tr data-function="{escape(row["function"])}"'
                ' title="right-click to open the source">'
                f"<td class='id'>{escape(row['function'])}</td>"
                f"<td>{row['before_us'] / 1000:.4f}</td>"
                f"<td>{row['after_us'] / 1000:.4f}</td>"
                f"<td class='{style}'>{delta:+.4f}</td>"
                f"<td>{row['before_commands']:.1f}</td>"
                f"<td>{row['after_commands']:.1f}</td>"
                "</tr>"
            )
        total = (
            self.total_us / max(self.ticks, 1) - baseline.total_us / max(baseline.ticks, 1)
        ) / 1000
        body = "\n".join(rows) or "<tr><td colspan='6'>nothing changed</td></tr>"
        lines = []
        for row in self.compare_commands(baseline)[:30]:
            delta = row["delta_us"] / 1000
            if abs(delta) < 1e-6:
                continue
            style = "up" if delta > 0 else "down"
            lines.append(
                f'<tr data-function="{escape(row["function"])}"'
                ' title="right-click to open the source">'
                f'<td class="id">{escape(row["function"])}:{row["line"]}</td>'
                f'<td class="id">{escape(_short(str(row["raw"])))}</td>'
                f"<td>{row['before_us'] / 1000:.4f}</td>"
                f"<td>{row['after_us'] / 1000:.4f}</td>"
                f"<td class='{style}'>{delta:+.4f}</td>"
                "</tr>"
            )
        line_body = "\n".join(lines) or "<tr><td colspan='5'>nothing changed</td></tr>"
        whose = ""
        if baseline.pack or baseline.version:
            whose = (
                f" The kept run is {escape(baseline.pack or 'a pack')}"
                f"{' on ' + escape(baseline.version) if baseline.version else ''}"
                f"{', saved ' + escape(baseline.saved) if baseline.saved else ''}."
            )
        return f"""<h3>Compared with the kept run</h3>
<p class="summary">Per tick: {baseline.total_us / max(baseline.ticks, 1) / 1000:.4f} ms then
 {self.total_us / max(self.ticks, 1) / 1000:.4f} ms
 (<span class="{"up" if total > 0 else "down"}">{total:+.4f} ms</span>),
 {baseline.ticks} then {self.ticks} tick(s).{whose}</p>
<table>
<thead><tr><th>function</th><th>before ms/tick</th><th>after ms/tick</th><th>change</th>
<th>before commands/tick</th><th>after commands/tick</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
<h4>The lines that changed</h4>
<table>
<thead><tr><th>line</th><th>command</th><th>before ms/tick</th><th>after ms/tick</th>
<th>change</th></tr></thead>
<tbody>
{line_body}
</tbody>
</table>"""

    def write_html(
        self,
        path: Path | str,
        title: str = "Function profiler",
        subtitle: str = "",
        baseline: Profiler | None = None,
    ) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_html(title, subtitle, baseline), encoding="utf-8")
        return path


def comparison_html(results: list[tuple[str, Profiler]], title: str = "Version comparison") -> str:
    """A small table comparing the same pack across versions."""
    rows = []
    for label, profiler in results:
        rows.append(
            "<tr>"
            f"<td class='id'>{escape(label)}</td>"
            f"<td>{profiler.ticks}</td>"
            f"<td>{profiler.total_us / 1000:.2f}</td>"
            f"<td>{profiler.average_tick_us / 1000:.2f}</td>"
            f"<td>{profiler.worst_tick_us / 1000:.2f}</td>"
            "</tr>"
        )
    return (
        "<h3>{}</h3><table><thead><tr><th>version</th><th>ticks</th><th>total ms</th>"
        "<th>avg ms</th><th>worst ms</th></tr></thead><tbody>{}</tbody></table>"
    ).format(escape(title), "\n".join(rows))
