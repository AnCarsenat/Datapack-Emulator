"""The function call DAG."""

from __future__ import annotations

from dataclasses import dataclass
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
        self, x_spacing: float = 1.0, y_spacing: float = 1.0, passes: int = 4
    ) -> dict[str, tuple[float, float]]:
        """Layered (Sugiyama-lite) positions, ready to draw.

        Nodes sit on the layer given by their longest path from a root, then
        get reordered inside each layer by the barycentre of their neighbours
        to cut down on edge crossings.
        """
        depth = self.depths()
        layers: dict[int, list[str]] = {}
        for node, level in sorted(depth.items(), key=lambda item: (item[1], item[0])):
            layers.setdefault(level, []).append(node)

        order = {node: index for nodes in layers.values() for index, node in enumerate(nodes)}
        levels = sorted(layers)
        for step in range(passes):
            downwards = step % 2 == 0
            for level in levels if downwards else reversed(levels):
                neighbours = self.predecessors if downwards else self.successors
                nodes = layers[level]
                keys = {}
                for node in nodes:
                    around = [order[other] for other in neighbours(node) if other in order]
                    keys[node] = sum(around) / len(around) if around else order[node]
                nodes.sort(key=lambda node: (keys[node], node))
                for index, node in enumerate(nodes):
                    order[node] = index

        positions: dict[str, tuple[float, float]] = {}
        for level, nodes in layers.items():
            offset = (len(nodes) - 1) / 2.0
            for index, node in enumerate(nodes):
                positions[node] = ((index - offset) * x_spacing, -level * y_spacing)
        return positions

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
