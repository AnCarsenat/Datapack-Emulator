"""Per-function timings and the HTML report."""

from __future__ import annotations

from pathlib import Path


from src.emulator import costs


class Profiler:
    """Per-function accumulated estimated time and call counts."""

    def __init__(self) -> None:
        #: function id -> {"calls", "commands", "self_us", "total_us"}
        self.entries: dict[str, dict[str, float]] = {}
        self.tick_times: list[float] = []

    def _entry(self, function_id: str) -> dict[str, float]:
        return self.entries.setdefault(
            function_id, {"calls": 0.0, "commands": 0.0, "self_us": 0.0, "total_us": 0.0}
        )

    def call(self, function_id: str) -> None:
        self._entry(function_id)["calls"] += 1

    def charge(self, function_id: str, microseconds: float) -> None:
        entry = self._entry(function_id)
        entry["self_us"] += microseconds
        entry["commands"] += 1

    def charge_total(self, function_id: str, microseconds: float) -> None:
        self._entry(function_id)["total_us"] += microseconds

    def sorted_entries(self, key: str = "total_us") -> list[tuple[str, dict[str, float]]]:
        return sorted(self.entries.items(), key=lambda item: -item[1][key])

    @property
    def total_us(self) -> float:
        return sum(entry["self_us"] for entry in self.entries.values())

    @property
    def worst_tick_us(self) -> float:
        return max(self.tick_times, default=0.0)

    @property
    def average_tick_us(self) -> float:
        return sum(self.tick_times) / len(self.tick_times) if self.tick_times else 0.0

    def reset(self) -> None:
        self.entries.clear()
        self.tick_times.clear()

    # -- reporting --------------------------------------------------------

    def to_html(self, title: str = "Function profiler", subtitle: str = "") -> str:
        rows: list[str] = []
        total = self.total_us or 1.0
        for function_id, entry in self.sorted_entries():
            share = 100.0 * entry["total_us"] / total
            rows.append(
                "<tr>"
                f"<td class='id'>{function_id}</td>"
                f"<td>{int(entry['calls'])}</td>"
                f"<td>{int(entry['commands'])}</td>"
                f"<td>{entry['self_us'] / 1000:.3f}</td>"
                f"<td>{entry['total_us'] / 1000:.3f}</td>"
                f"<td><div class='bar' style='width:{min(share, 100):.1f}%'></div>"
                f"<span>{share:.1f}%</span></td>"
                "</tr>"
            )
        ticks = len(self.tick_times)
        worst = self.worst_tick_us
        body = "\n".join(rows) or "<tr><td colspan='6'>no data — run the emulator</td></tr>"
        over = "warn" if worst > costs.TICK_BUDGET_US else ""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 12px; color: #111; }}
 h2 {{ margin: 0 0 4px; font-size: 16px; }}
 p.summary {{ margin: 0 0 12px; color: #555; font-size: 12px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
 th, td {{ text-align: right; padding: 3px 6px; border-bottom: 1px solid #e3e3e3; }}
 th:first-child, td.id {{ text-align: left; font-family: monospace; }}
 tr:hover {{ background: #e4fbff; }}
 .bar {{ display: inline-block; height: 9px; background: #f5a623; vertical-align: middle;
         margin-right: 5px; min-width: 1px; }}
 .warn {{ color: #c0392b; font-weight: bold; }}
</style>
</head>
<body>
<h2>{title}</h2>
<p class="summary">
 {subtitle}{' &middot; ' if subtitle else ''}{ticks} tick(s) &middot;
 total {total / 1000:.2f} ms &middot;
 average {self.average_tick_us / 1000:.2f} ms/tick &middot;
 worst <span class="{over}">{worst / 1000:.2f} ms</span>
 (budget {costs.TICK_BUDGET_US / 1000:.0f} ms)
 <br>Times are estimates from a cost model, not measurements &mdash; see src/emulator/costs.py.
</p>
<table>
<thead><tr><th>function</th><th>calls</th><th>commands</th><th>self (ms)</th>
<th>total (ms)</th><th>share</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</body>
</html>
"""

    def write_html(
        self, path: Path | str, title: str = "Function profiler", subtitle: str = ""
    ) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_html(title, subtitle), encoding="utf-8")
        return path


def comparison_html(
    results: list[tuple[str, "Profiler"]], title: str = "Version comparison"
) -> str:
    """A small table comparing the same pack across versions."""
    rows = []
    for label, profiler in results:
        rows.append(
            "<tr>"
            f"<td class='id'>{label}</td>"
            f"<td>{len(profiler.tick_times)}</td>"
            f"<td>{profiler.total_us / 1000:.2f}</td>"
            f"<td>{profiler.average_tick_us / 1000:.2f}</td>"
            f"<td>{profiler.worst_tick_us / 1000:.2f}</td>"
            "</tr>"
        )
    return (
        "<h3>{}</h3><table><thead><tr><th>version</th><th>ticks</th><th>total ms</th>"
        "<th>avg ms</th><th>worst ms</th></tr></thead><tbody>{}</tbody></table>"
    ).format(title, "\n".join(rows))
