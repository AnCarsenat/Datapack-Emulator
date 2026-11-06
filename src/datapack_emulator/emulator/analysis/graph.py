"""The function call DAG."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.common import normalise_tagged_id

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.datapack import Datapack, PackView


@dataclass(frozen=True)
class CallEdge:
    source: str
    target: str
    kind: str = "call"  # call | schedule | tag | condition | macro


class CallGraph:
    """Directed graph of function calls.  A DAG unless the pack recurses."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[CallEdge] = []
        self._edge_set: set[CallEdge] = set()
        #: node -> targets and node -> sources, in edge order
        self._out: dict[str, list[str]] = {}
        self._in: dict[str, list[str]] = {}

    # -- construction -----------------------------------------------------

    @classmethod
    def from_pack(cls, pack: PackView) -> CallGraph:
        graph = cls()
        functions = pack.functions

        for tag_id, tag in pack.function_tags.items():
            graph.add_node(tag_id, kind="tag", missing=False)
            for value in tag.values:
                target = normalise_tagged_id(value)
                graph.add_node(
                    target,
                    kind="tag" if target.startswith("#") else "function",
                    missing=not target.startswith("#") and target not in functions,
                )
                graph.add_edge(tag_id, target, "tag")

        for function_id, function in functions.items():
            graph.add_node(
                function_id,
                kind="function",
                missing=False,
                commands=len(function.content),
                cost_us=function.estimate_cost(),
                path=str(function.path),
                macro=function.is_macro,
                overlay=function.overlay,
            )

        for function_id, function in functions.items():
            for target, kind in function.calls:
                if target not in graph.nodes:
                    graph.add_node(
                        target,
                        kind="tag" if target.startswith("#") else "function",
                        missing=not target.startswith("#") and target not in functions,
                    )
                graph.add_edge(function_id, target, kind)
        return graph

    @classmethod
    def from_datapack(cls, datapack: Datapack) -> CallGraph:
        return cls.from_pack(datapack.view())

    def add_node(self, node_id: str, **attributes: Any) -> None:
        self.nodes.setdefault(node_id, {}).update(attributes)

    def add_edge(self, source: str, target: str, kind: str = "call") -> None:
        edge = CallEdge(source, target, kind)
        if edge not in self._edge_set:
            self._edge_set.add(edge)
            self.edges.append(edge)
            self._out.setdefault(source, []).append(target)
            self._in.setdefault(target, []).append(source)

    # -- queries ----------------------------------------------------------

    def successors(self, node_id: str) -> list[str]:
        return list(self._out.get(node_id, []))

    def predecessors(self, node_id: str) -> list[str]:
        return list(self._in.get(node_id, []))

    def relations(self, node_id: str) -> list[tuple[str, str]]:
        """What calls ``node_id`` and what it calls, as ``(label, text)`` rows."""
        callers = self.predecessors(node_id)
        calls = self.successors(node_id)
        rows = [
            ("tag" if node_id.startswith("#") else "function", node_id),
            ("called by", ", ".join(callers) or "nothing (only #minecraft:load/tick or commands)"),
            ("calls", ", ".join(calls) or "nothing"),
        ]
        rows += [
            (f"edge {edge.source} → {edge.target}", edge.kind)
            for edge in self.edges
            if node_id in (edge.source, edge.target)
        ]
        return rows

    def roots(self) -> list[str]:
        """Entry points: the vanilla tags, plus anything nothing else calls."""
        entries: list[str] = [
            node for node in ("#minecraft:load", "#minecraft:tick") if node in self.nodes
        ]
        entries += [
            node for node in self.nodes if not self.predecessors(node) and node not in entries
        ]
        return entries

    def unreachable(self) -> list[str]:
        """Functions no entry point can reach (dead code in the pack)."""
        seen: set[str] = set()
        stack: list[str] = [
            node for node in ("#minecraft:load", "#minecraft:tick") if node in self.nodes
        ]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self.successors(node))
        return sorted(
            node
            for node, attributes in self.nodes.items()
            if node not in seen and attributes.get("kind") == "function"
        )

    def missing(self) -> list[str]:
        return sorted(node for node, data in self.nodes.items() if data.get("missing"))

    def main_namespace(self) -> str | None:
        """The namespace at least half the pack's functions share, if any."""
        counts: dict[str, int] = {}
        functions = 0
        for node, data in self.nodes.items():
            if data.get("kind") == "function" and ":" in node:
                functions += 1
                namespace = node.split(":", 1)[0]
                counts[namespace] = counts.get(namespace, 0) + 1
        if not counts:
            return None
        namespace, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
        return namespace if 2 * count >= functions else None

    def cycles(self) -> list[list[str]]:
        """Every back-edge cycle; a non-empty result means it is not a DAG."""
        found: list[list[str]] = []
        state: dict[str, int] = {}  # 0 = visiting, 1 = done
        # iterative depth-first search: packs chain thousands of functions deep
        for root in self.nodes:
            if root in state:
                continue
            path: list[str] = [root]
            on_path: dict[str, int] = {root: 0}
            state[root] = 0
            stack = [iter(self._out.get(root, []))]
            while stack:
                successor = next(stack[-1], None)
                if successor is None:
                    stack.pop()
                    done = path.pop()
                    del on_path[done]
                    state[done] = 1
                    continue
                if state.get(successor) == 0:
                    found.append(path[on_path[successor] :] + [successor])
                elif successor not in state:
                    state[successor] = 0
                    on_path[successor] = len(path)
                    path.append(successor)
                    stack.append(iter(self._out.get(successor, [])))
        return found

    @property
    def is_dag(self) -> bool:
        return not self.cycles()

    def topological_order(self) -> list[str]:
        """Kahn's algorithm; nodes inside a cycle are appended at the end."""
        indegree = {node: 0 for node in self.nodes}
        for edge in self.edges:
            if edge.target in indegree:
                indegree[edge.target] += 1
        queue = [node for node, degree in indegree.items() if degree == 0]
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for successor in self.successors(node):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        order.extend(node for node in self.nodes if node not in order)
        return order

    def depths(self) -> dict[str, int]:
        """Longest path from a root; the layer index used for layout."""
        depth = {node: 0 for node in self.nodes}
        for node in self.topological_order():
            for successor in self.successors(node):
                if successor != node:
                    depth[successor] = max(depth[successor], depth[node] + 1)
        return depth

    # -- layout / export --------------------------------------------------

    def layout(
        self, x_spacing: float = 1.0, y_spacing: float = 1.0, passes: int = 8
    ) -> dict[str, tuple[float, float]]:
        """Layered positions, ready to draw: ``node -> (x, y)``, roots on top.

        Neighbours in a layer sit at least ``x_spacing`` apart; see
        :meth:`layered_layout` for the rest (crossing reduction, edges that
        skip layers, separate parts) and for boxes of different widths.
        """
        widths = dict.fromkeys(self.nodes, x_spacing)
        return self.layered_layout(widths, gap=0.0, y_spacing=y_spacing, passes=passes).positions

    def layered_layout(
        self,
        widths: Mapping[str, float] | None = None,
        gap: float = 0.5,
        y_spacing: float = 1.0,
        passes: int = 8,
    ) -> GraphLayout:
        """A Sugiyama-style layout, deterministic for a given graph.

        * each node goes on the layer of its longest path from a root, and an
          edge that skips layers gets a bend point on every layer it crosses,
          so it is routed between the nodes instead of through them;
        * layers are reordered by the barycentre of their neighbours, sweeping
          down and up, keeping the order with the fewest edge crossings;
        * nodes then slide sideways towards their neighbours (above, then
          below) without overlapping: in a layer, centres are at least
          ``(width_a + width_b) / 2 + gap`` apart (``widths`` defaults to 1);
        * every connected part is laid out on its own and the parts are
          packed in rows, the one holding ``#minecraft:load``/``tick`` first;
          nodes with no edge at all go in a grid after them.
        """
        sizes = {node: float(widths.get(node, 1.0)) if widths else 1.0 for node in self.nodes}
        median = sorted(sizes.values())[len(sizes) // 2] if sizes else 1.0
        context = _LayoutContext(self, sizes, gap, y_spacing, passes, dummy=0.3 * median)
        return context.run(component_gap=max(2 * gap, 0.5 * median))

    def to_dot(self) -> str:
        style = {
            "call": "",
            "schedule": ' [style=dashed,color="#2980b9"]',
            "tag": ' [style=bold,color="#8e44ad"]',
            "condition": " [style=dotted]",
            "macro": ' [color="#e67e22"]',
        }
        lines = ["digraph functions {", "  rankdir=TB;", "  node [shape=box,fontname=monospace];"]
        for node, attributes in self.nodes.items():
            colour = (
                "#c0392b"
                if attributes.get("missing")
                else ("#8e44ad" if attributes.get("kind") == "tag" else "#333333")
            )
            cost = attributes.get("cost_us")
            label = node if cost is None else f"{node}\\n{cost / 1000:.2f} ms"
            lines.append(f'  "{node}" [label="{label}",color="{colour}"];')
        for edge in self.edges:
            lines.append(f'  "{edge.source}" -> "{edge.target}"{style.get(edge.kind, "")};')
        lines.append("}")
        return "\n".join(lines)

    def write_dot(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_dot(), encoding="utf-8")
        return path

    def __repr__(self) -> str:
        return f"<CallGraph nodes={len(self.nodes)} edges={len(self.edges)} dag={self.is_dag}>"


def short_name(node_id: str, namespace: str | None) -> str:
    """``node_id`` without ``namespace:`` (``#ns:tag`` keeps its ``#``)."""
    if namespace is None:
        return node_id
    tag = node_id.startswith("#")
    bare = node_id[1:] if tag else node_id
    if bare.startswith(namespace + ":"):
        bare = bare[len(namespace) + 1 :]
    return ("#" if tag else "") + bare


# -- layout ---------------------------------------------------------------

Point = tuple[float, float]


@dataclass
class GraphLayout:
    """Where :meth:`CallGraph.layered_layout` puts things.

    ``routes`` has every edge as a polyline from its source's centre to its
    target's centre, through one bend point per layer it skips (a self-call
    is just the node's centre).  ``crossings`` counts the crossings between
    neighbouring layers that the ordering left.
    """

    positions: dict[str, Point] = field(default_factory=dict)
    routes: dict[CallEdge, list[Point]] = field(default_factory=dict)
    widths: dict[str, float] = field(default_factory=dict)
    crossings: int = 0


def count_crossings(rows: list[list[str]], below: Mapping[str, list[str]]) -> int:
    """Edge crossings between each layer and the next, for these orders.

    ``below`` maps a node to its neighbours on the next layer down.
    """
    total = 0
    for upper, lower in pairwise(rows):
        index = {node: position for position, node in enumerate(lower)}
        ends = [index[other] for node in upper for other in below.get(node, ()) if other in index]
        total += _inversions(ends)
    return total


def _inversions(values: list[int]) -> int:
    """Pairs out of order (a Fenwick tree count, O(n log n))."""
    if not values:
        return 0
    size = max(values) + 1
    tree = [0] * (size + 1)
    count = 0
    for seen, value in enumerate(values):
        # how many earlier values are greater than this one
        smaller_or_equal, index = 0, value + 1
        while index > 0:
            smaller_or_equal += tree[index]
            index -= index & -index
        count += seen - smaller_or_equal
        index = value + 1
        while index <= size:
            tree[index] += 1
            index += index & -index
    return count


def _isotonic(targets: list[float]) -> list[float]:
    """The non-decreasing sequence closest (least squares) to ``targets``."""
    blocks: list[list[float]] = []  # [mean, count]
    for value in targets:
        blocks.append([value, 1.0])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            mean, count = blocks.pop()
            blocks[-1][0] = (blocks[-1][0] * blocks[-1][1] + mean * count) / (blocks[-1][1] + count)
            blocks[-1][1] += count
    result: list[float] = []
    for mean, count in blocks:
        result.extend([mean] * int(count))
    return result


class _LayoutContext:
    """The working state of one :meth:`CallGraph.layered_layout` call."""

    VANILLA = ("#minecraft:load", "#minecraft:tick")

    def __init__(
        self,
        graph: CallGraph,
        sizes: dict[str, float],
        gap: float,
        y_spacing: float,
        passes: int,
        dummy: float,
    ) -> None:
        self.graph = graph
        self.sizes = dict(sizes)
        self.gap = gap
        self.y_spacing = y_spacing
        self.passes = passes
        self.dummy = dummy
        self.depth = graph.depths()
        #: node (or bend point) -> neighbours one layer up / down
        self.above: dict[str, list[str]] = {}
        self.below: dict[str, list[str]] = {}
        #: edge -> the bend points it goes through, top to bottom
        self.bends: dict[CallEdge, list[str]] = {}
        self.bends_from: dict[str, list[str]] = {}

    # -- the whole graph --------------------------------------------------

    def run(self, component_gap: float) -> GraphLayout:
        result = GraphLayout(widths={node: self.sizes[node] for node in self.graph.nodes})
        if not self.graph.nodes:
            return result
        components, isolated = self._components()
        self._add_bends()

        blocks: list[tuple[dict[str, Point], float, float]] = []  # (positions, width, height)
        for nodes in components:
            rows = self._order(nodes)
            result.crossings += count_crossings(rows, self.below)
            blocks.append(self._place(rows))
        if isolated:
            blocks.append(self._grid(isolated))

        # pack the parts in rows about twice as wide as they are tall
        area = sum(width * height for _, width, height in blocks)
        target = max(max(width for _, width, _ in blocks), math.sqrt(2.0 * area))
        x = y = shelf = 0.0
        for positions, width, height in blocks:
            if x > 0 and x + width > target:
                x, y, shelf = 0.0, y - shelf - 0.5 * self.y_spacing, 0.0
            for node, (node_x, node_y) in positions.items():
                result.positions[node] = (node_x + x, node_y + y)
            x += width + component_gap
            shelf = max(shelf, height)

        for edge in self.graph.edges:
            if edge.source == edge.target:
                result.routes[edge] = [result.positions[edge.source]]
                continue
            points = [edge.source, *self.bends.get(edge, []), edge.target]
            result.routes[edge] = [result.positions[point] for point in points]
        for node in list(result.positions):
            if node not in self.graph.nodes:
                del result.positions[node]
        return result

    def _components(self) -> tuple[list[list[str]], list[str]]:
        """Connected parts (main one first, then by size) and lone nodes."""
        parent = {node: node for node in self.graph.nodes}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        linked: set[str] = set()
        for edge in self.graph.edges:
            if edge.source != edge.target:
                linked.update((edge.source, edge.target))
                parent[find(edge.source)] = find(edge.target)
        groups: dict[str, list[str]] = {}
        for node in self.graph.nodes:
            if node in linked:
                groups.setdefault(find(node), []).append(node)

        def rank(nodes: list[str]) -> tuple[int, int]:
            # the part holding #minecraft:load first, then #minecraft:tick, then by size
            entry = min(
                (index for index, tag in enumerate(self.VANILLA) if tag in nodes),
                default=len(self.VANILLA),
            )
            return entry, -len(nodes)

        ranked = sorted(groups.values(), key=rank)
        isolated = sorted(node for node in self.graph.nodes if node not in linked)
        return ranked, isolated

    def _add_bends(self) -> None:
        """Split edges that skip layers; link each layer to the next."""
        for index, edge in enumerate(self.graph.edges):
            top, bottom = self.depth[edge.source], self.depth[edge.target]
            if edge.source == edge.target or bottom <= top:
                continue  # self-calls and the back edges of a cycle stay straight
            chain = [edge.source]
            for level in range(top + 1, bottom):
                bend = f"\0{index}:{level}"
                self.depth[bend] = level
                self.sizes[bend] = self.dummy
                chain.append(bend)
            chain.append(edge.target)
            self.bends[edge] = chain[1:-1]
            self.bends_from.setdefault(edge.source, []).extend(chain[1:-1])
            for upper, lower in pairwise(chain):
                self.below.setdefault(upper, []).append(lower)
                self.above.setdefault(lower, []).append(upper)

    # -- one connected part -----------------------------------------------

    def _order(self, nodes: list[str]) -> list[list[str]]:
        """Layers of a part, ordered to cut crossings."""
        members = set(nodes)
        for node in nodes:
            members.update(self.bends_from.get(node, ()))
        levels = 1 + max(self.depth[node] for node in members)
        rows: list[list[str]] = [[] for _ in range(levels)]

        # start from a depth-first walk from the roots, in edge order
        seen: set[str] = set()
        starts = [node for node in self.VANILLA if node in members]
        starts += [node for node in nodes if self.depth[node] == 0 and node not in starts]
        starts += nodes
        for start in starts:
            stack = [start]
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                rows[self.depth[node]].append(node)
                stack.extend(reversed(self.below.get(node, [])))

        best = [list(row) for row in rows]
        fewest = count_crossings(rows, self.below)
        for step in range(self.passes):
            if fewest == 0:
                break
            downwards = step % 2 == 0
            sweep = range(1, levels) if downwards else range(levels - 2, -1, -1)
            for level in sweep:
                reference = rows[level - 1] if downwards else rows[level + 1]
                where = {
                    node: (index + 0.5) / len(reference) for index, node in enumerate(reference)
                }
                neighbours = self.above if downwards else self.below
                row = rows[level]
                keys = {}
                for index, node in enumerate(row):
                    around = [where[other] for other in neighbours.get(node, ()) if other in where]
                    keys[node] = (
                        sum(around) / len(around) if around else (index + 0.5) / len(row),
                        index,
                    )
                row.sort(key=keys.__getitem__)
            crossings = count_crossings(rows, self.below)
            if crossings < fewest:
                best, fewest = [list(row) for row in rows], crossings
        # a part whose top is a cycle can leave a layer empty
        return [row for row in best if row]

    def _place(self, rows: list[list[str]]) -> tuple[dict[str, Point], float, float]:
        """x for every node of a part (in order, no overlaps), y by layer."""
        x: dict[str, float] = {}
        for row in rows:
            cursor = 0.0
            for index, node in enumerate(row):
                if index:
                    cursor += self._separation(row[index - 1], node)
                x[node] = cursor

        def settle(row: list[str], neighbours: Mapping[str, list[str]]) -> None:
            desired = []
            for node in row:
                around = [x[other] for other in neighbours.get(node, ()) if other in x]
                desired.append(sum(around) / len(around) if around else x[node])
            offsets = [0.0]
            for left, right in pairwise(row):
                offsets.append(offsets[-1] + self._separation(left, right))
            shifted = [want - offset for want, offset in zip(desired, offsets, strict=True)]
            fitted = _isotonic(shifted)
            for node, value, offset in zip(row, fitted, offsets, strict=True):
                x[node] = value + offset

        for _ in range(3):
            for row in rows[1:]:
                settle(row, self.above)
            for row in reversed(rows[:-1]):
                settle(row, self.below)
        for row in rows[1:]:
            settle(row, self.above)

        left = min(x[node] - self.sizes[node] / 2 for node in x)
        right = max(x[node] + self.sizes[node] / 2 for node in x)
        positions = {
            node: (x[node] - left, -level * self.y_spacing)
            for level, row in enumerate(rows)
            for node in row
        }
        return positions, right - left, len(rows) * self.y_spacing

    def _separation(self, left: str, right: str) -> float:
        return (self.sizes[left] + self.sizes[right]) / 2 + self.gap

    def _grid(self, nodes: list[str]) -> tuple[dict[str, Point], float, float]:
        """Nodes with no edges, in a grid of equal cells."""
        columns = max(1, min(len(nodes), math.ceil(math.sqrt(2 * len(nodes)))))
        cell = max(self.sizes[node] for node in nodes) + self.gap
        positions = {
            node: ((index % columns + 0.5) * cell, -(index // columns) * self.y_spacing)
            for index, node in enumerate(nodes)
        }
        rows = math.ceil(len(nodes) / columns)
        return positions, columns * cell - self.gap, rows * self.y_spacing
