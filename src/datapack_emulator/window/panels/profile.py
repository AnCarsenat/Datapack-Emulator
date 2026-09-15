"""The profiler tab's tree: the average cost of one tick, per call path."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from datapack_emulator.emulator import costs
from datapack_emulator.emulator.analysis.profiler import CallPath, Profiler

#: the function id of a row (empty for tags, schedules and "…")
FUNCTION_ROLE = Qt.UserRole
#: the numbers columns sort by
SORT_ROLE = Qt.UserRole + 1
COLUMN_HELP = (
    "the tag, schedule or function; its children are the functions it called",
    "average estimated time per tick, including the functions it calls",
    "share of an average tick's estimated time",
    "average time per tick of its own lines, without the functions it calls",
    "how many times per tick this path runs, on average",
    "its own command lines run per tick, on average",
)
#: levels expanded when the tree is filled
EXPANDED_DEPTH = 2


class _Row(QTreeWidgetItem):
    """Sorts numeric columns by value, not by text."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        mine, theirs = self.data(column, SORT_ROLE), other.data(column, SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text(column).lower() < other.text(column).lower()


def summary(profiler: Profiler) -> str:
    if not profiler.ticks:
        return "no ticks run yet: run or step the emulator"
    average = profiler.average_tick_us / 1000
    worst = profiler.worst_tick_us / 1000
    budget = costs.TICK_BUDGET_US / 1000
    return (
        f"average of {profiler.ticks} tick(s): {average:.3f} ms per tick "
        f"({average / budget:.1%} of the {budget:.0f} ms budget) · worst tick {worst:.3f} ms · "
        "estimates from a cost model, not measurements"
    )


def fill_profile_tree(tree: QTreeWidget, profiler: Profiler) -> None:
    children: dict[CallPath, list[CallPath]] = {}
    for path in profiler.tree:
        children.setdefault(path[:-1], []).append(path)
    tick_cost = profiler.total_us / max(profiler.ticks, 1) or 1.0

    tree.setUpdatesEnabled(False)
    tree.setSortingEnabled(False)
    tree.clear()
    for column, text in enumerate(COLUMN_HELP):
        tree.headerItem().setToolTip(column, text)

    def add(parent, path: CallPath) -> None:
        one = profiler.per_tick(profiler.tree[path])
        name = path[-1]
        is_function = not name.startswith(("#", "<", Profiler.DEEPER))
        values = (
            (one["total_us"] / 1000, f"{one['total_us'] / 1000:.4f}"),
            (one["total_us"] / tick_cost, f"{one['total_us'] / tick_cost:.1%}"),
            (one["self_us"] / 1000, f"{one['self_us'] / 1000:.4f}"),
            (one["calls"], f"{one['calls']:.2f}"),
            (one["commands"], f"{one['commands']:.1f}"),
        )
        item = _Row(parent, [name, *(text for _, text in values)])
        item.setData(0, FUNCTION_ROLE, name if is_function else "")
        for column, (number, _) in enumerate(values, start=1):
            item.setData(column, SORT_ROLE, number)
            item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
        item.setToolTip(0, " › ".join(path))
        for child in sorted(children.get(path, ()), key=lambda p: -profiler.tree[p]["total_us"]):
            add(item, child)
        item.setExpanded(len(path) < EXPANDED_DEPTH)

    for root in sorted(children.get((), ()), key=lambda p: -profiler.tree[p]["total_us"]):
        add(tree, root)
    tree.setSortingEnabled(True)
    tree.sortByColumn(1, Qt.DescendingOrder)
    tree.resizeColumnToContents(0)
    tree.setUpdatesEnabled(True)
