"""The pyqtgraph canvas that draws the function call DAG."""

from __future__ import annotations

import math
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QPoint, Signal

from datapack_emulator.emulator.analysis.graph import CallGraph

pg.setConfigOptions(antialias=True, background="w", foreground="k")

NODE_COLOURS = {
    "tag": (142, 68, 173),
    "function": (52, 73, 94),
    "missing": (192, 57, 43),
    "macro": (230, 126, 34),
    "overlay": (41, 128, 185),
}

FOCUS_COLOUR = (231, 76, 60)

EDGE_COLOURS = {
    "call": (120, 120, 120),
    "tag": (142, 68, 173),
    "schedule": (41, 128, 185),
    "condition": (160, 160, 160),
    "macro": (230, 126, 34),
}


class FunctionGraphWidget(pg.GraphicsLayoutWidget):
    """Draws a :class:`~datapack_emulator.emulator.analysis.graph.CallGraph` as a layered DAG."""

    #: right-click on a node: ``(node id, global position)``
    node_menu_requested = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.plot = self.addPlot()
        self.plot.hideAxis("bottom")
        self.plot.hideAxis("left")
        self.plot.setMenuEnabled(False)
        self.graph_item = pg.GraphItem()
        self.plot.addItem(self.graph_item)
        self._decorations: list[Any] = []
        self._positions: dict[str, tuple[float, float]] = {}
        self.graph: CallGraph | None = None
        #: the node shown by ``focus``, ringed
        self.focused: str | None = None
        self._focus_items: list[Any] = []

    def clear_graph(self) -> None:
        self._clear_focus()
        for item in self._decorations:
            self.plot.removeItem(item)
        self._decorations.clear()
        self.graph_item.setData()
        self._positions = {}

    def _clear_focus(self) -> None:
        for item in self._focus_items:
            self.plot.removeItem(item)
        self._focus_items.clear()
        self.focused = None

    def focus(self, node_id: str) -> bool:
        """Ring a node and its direct calls and callers, and centre the view on
        it; whether the graph has it."""
        self._clear_focus()
        if self.graph is None or node_id not in self._positions:
            return False
        x, y = self._positions[node_id]
        neighbours = [
            name
            for name in (*self.graph.predecessors(node_id), *self.graph.successors(node_id))
            if name in self._positions
        ]
        near = pg.ScatterPlotItem(
            pos=_as_array([self._positions[name] for name in neighbours]).reshape(-1, 2),
            size=24,
            symbol="o",
            brush=None,
            pen=pg.mkPen(FOCUS_COLOUR, width=2),
        )
        ring = pg.ScatterPlotItem(
            pos=_as_array([(x, y)]),
            size=30,
            symbol="o",
            brush=None,
            pen=pg.mkPen(FOCUS_COLOUR, width=4),
        )
        for item in (near, ring):
            self.plot.addItem(item)
            self._focus_items.append(item)
        xs = [x, *(self._positions[name][0] for name in neighbours)]
        ys = [y, *(self._positions[name][1] for name in neighbours)]
        self.plot.setXRange(min(xs) - 3, max(xs) + 3, padding=0.05)
        self.plot.setYRange(min(ys) - 2, max(ys) + 2, padding=0.05)
        self.focused = node_id
        return True

    def set_graph(self, graph: CallGraph) -> None:
        self.clear_graph()
        self.graph = graph
        if not graph.nodes:
            return

        positions = graph.layout(x_spacing=3.0, y_spacing=1.6)
        self._positions = positions
        names = list(graph.nodes)
        index_of = {name: index for index, name in enumerate(names)}
        points = [positions[name] for name in names]
        brushes = [pg.mkBrush(*_node_colour(graph.nodes[name])) for name in names]

        drawn = [
            edge for edge in graph.edges if edge.source in index_of and edge.target in index_of
        ]
        adjacency = [(index_of[edge.source], index_of[edge.target]) for edge in drawn]

        self.graph_item.setData(
            pos=_as_array(points),
            adj=_as_array(adjacency, dtype=int).reshape(-1, 2),
            size=14,
            symbol="o",
            symbolBrush=brushes,
            pen=_edge_pens(drawn),
            pxMode=True,
        )

        for name in names:
            x, y = positions[name]
            attributes = graph.nodes[name]
            cost = attributes.get("cost_us")
            label = name if cost is None else f"{name}\n{cost / 1000:.2f} ms"
            if attributes.get("overlay"):
                label += f"\n({attributes['overlay']})"
            text = pg.TextItem(label, anchor=(0.5, -0.4), color=(20, 20, 20))
            text.setPos(x, y)
            self.plot.addItem(text)
            self._decorations.append(text)

        for edge in drawn:
            start, end = positions[edge.source], positions[edge.target]
            angle = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
            arrow = pg.ArrowItem(
                angle=180 - angle,
                headLen=10,
                tipAngle=28,
                brush=pg.mkBrush(*EDGE_COLOURS.get(edge.kind, (120, 120, 120))),
                pen=None,
            )
            arrow.setPos(*end)
            self.plot.addItem(arrow)
            self._decorations.append(arrow)

        self.plot.enableAutoRange()
        self.plot.autoRange(padding=0.2)

    # -- interaction ------------------------------------------------------

    def node_at(self, widget_position) -> str | None:
        """The node under a widget-space point, if the click is close enough."""
        if not self._positions:
            return None
        scene_point = self.mapToScene(widget_position)
        data_point = self.plot.vb.mapSceneToView(scene_point)
        x, y = data_point.x(), data_point.y()
        best, distance = None, None
        for name, (node_x, node_y) in self._positions.items():
            current = (node_x - x) ** 2 + (node_y - y) ** 2
            if distance is None or current < distance:
                best, distance = name, current
        if best is None or distance is None:
            return None
        # generous but not global: within one layer's spacing
        return best if distance <= 0.8**2 + 0.8**2 else None

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt API)
        node = self.node_at(event.pos())
        if node is None:
            super().contextMenuEvent(event)
            return
        event.accept()
        self.node_menu_requested.emit(node, event.globalPos())


def _node_colour(attributes: dict[str, Any]) -> tuple[int, int, int]:
    if attributes.get("missing"):
        return NODE_COLOURS["missing"]
    if attributes.get("kind") == "tag":
        return NODE_COLOURS["tag"]
    if attributes.get("overlay"):
        return NODE_COLOURS["overlay"]
    if attributes.get("macro"):
        return NODE_COLOURS["macro"]
    return NODE_COLOURS["function"]


def _as_array(values, dtype=float):
    import numpy

    return numpy.array(values, dtype=dtype)


def _edge_pens(edges):
    """One pen per edge, in the record array ``GraphItem`` expects.

    A plain list of ``QPen`` is not a supported ``pen=`` value and makes the
    scene crash while painting, so the colours go in as a structured array.
    """
    import numpy

    pens = numpy.empty(
        len(edges),
        dtype=[
            ("red", numpy.ubyte),
            ("green", numpy.ubyte),
            ("blue", numpy.ubyte),
            ("alpha", numpy.ubyte),
            ("width", float),
        ],
    )
    for index, edge in enumerate(edges):
        red, green, blue = EDGE_COLOURS.get(edge.kind, (120, 120, 120))
        pens[index] = (red, green, blue, 255, 1.0 if edge.kind == "condition" else 2.0)
    return pens
