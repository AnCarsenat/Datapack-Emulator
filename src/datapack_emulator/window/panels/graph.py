"""The pyqtgraph canvas that draws the function call DAG.

Everything is drawn in layout units (``PX_PER_UNIT`` pixels at the starting
zoom) with the aspect ratio locked, so boxes, labels and arrowheads zoom
together like a picture and never run into each other.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
)

from datapack_emulator.emulator.analysis.graph import CallEdge, CallGraph, short_name

pg.setConfigOptions(antialias=True, background="w", foreground="k")

NODE_COLOURS = {
    "tag": (142, 68, 173),
    "function": (52, 73, 94),
    "missing": (192, 57, 43),
    "macro": (230, 126, 34),
    "overlay": (41, 128, 185),
}
#: legend wording for each node colour
NODE_LEGEND = {
    "function": "function",
    "tag": "tag",
    "macro": "macro",
    "overlay": "from an overlay",
    "missing": "missing",
}

FOCUS_COLOUR = (231, 76, 60)
#: how far ``focus`` zooms out around a node, in layout units
MAX_FOCUS_HALF_WIDTH = 12.0
MAX_FOCUS_HALF_HEIGHT = 5.0

EDGE_COLOURS = {
    "call": (120, 120, 120),
    "tag": (142, 68, 173),
    "schedule": (41, 128, 185),
    "condition": (110, 110, 110),
    "macro": (230, 126, 34),
}
EDGE_STYLES = {
    "schedule": Qt.PenStyle.DashLine,
    "condition": Qt.PenStyle.DotLine,
}
#: legend wording and sample stroke for each edge kind
EDGE_LEGEND = {
    "call": ("function", "━━"),
    "schedule": ("schedule", "╍╍"),
    "tag": ("tag entry", "━━"),
    "condition": ("execute if function", "┈┈"),
    "macro": ("macro call", "━━"),
}

#: screen pixels per layout unit at the starting zoom
PX_PER_UNIT = 40.0
#: below this many pixels per unit the starting view stops fitting the whole
#: graph and shows its top at ``PART_PX_PER_UNIT`` instead, where the labels
#: read well (the rest is a pan or a scroll away)
MIN_START_PX_PER_UNIT = 24.0
PART_PX_PER_UNIT = 34.0
#: box padding, gaps between boxes and layers, arrowheads: in pixels
PAD_X, PAD_Y = 8.0, 4.0
NODE_GAP = 18.0
LAYER_GAP = 44.0
ARROW_LENGTH, ARROW_WIDTH = 9.0, 7.0


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
        self.plot.hideButtons()
        self.plot.vb.setAspectLocked(True, ratio=1.0)
        self.plot.vb.sigRangeChangedManually.connect(self._left_home)
        self._items: list[Any] = []
        self._positions: dict[str, tuple[float, float]] = {}
        #: node -> (half width, half height) of its box, in layout units
        self._half_sizes: dict[str, tuple[float, float]] = {}
        self.graph: CallGraph | None = None
        #: the node shown by ``focus``, ringed
        self.focused: str | None = None
        self._focus_items: list[Any] = []
        #: whether the view is still the one ``set_graph`` picked (kept on resize)
        self._at_home = False
        #: what the colours and strokes mean, in a row under the canvas
        self.legend = pg.LabelItem("", justify="left", color=(34, 34, 34), size="8pt")
        self.addItem(self.legend, row=1, col=0)

    # -- building ---------------------------------------------------------

    def clear_graph(self) -> None:
        self._clear_focus()
        for item in self._items:
            self.plot.removeItem(item)
        self._items.clear()
        self._positions = {}
        self._half_sizes = {}
        self.graph = None
        self._at_home = False
        self.legend.setText("")

    def set_graph(self, graph: CallGraph) -> None:
        self.clear_graph()
        self.graph = graph
        if not graph.nodes:
            return

        name_font = QFont(self.font())
        cost_font = QFont(self.font())
        cost_font.setPointSizeF(max(name_font.pointSizeF() - 1.5, 6.0))
        name_metrics, cost_metrics = QFontMetricsF(name_font), QFontMetricsF(cost_font)

        namespace = graph.main_namespace()
        unreachable = set(graph.unreachable())
        labels = {name: _label_lines(name, graph.nodes[name], namespace) for name in graph.nodes}
        sizes: dict[str, tuple[float, float]] = {}
        for name, (title, details) in labels.items():
            width = max(
                [name_metrics.horizontalAdvance(title)]
                + [cost_metrics.horizontalAdvance(line) for line in details]
            )
            height = name_metrics.height() + cost_metrics.height() * len(details)
            sizes[name] = (width + 2 * PAD_X, height + 2 * PAD_Y)
        tallest = max(height for _, height in sizes.values())

        layout = graph.layered_layout(
            widths={name: width / PX_PER_UNIT for name, (width, _) in sizes.items()},
            gap=NODE_GAP / PX_PER_UNIT,
            y_spacing=(tallest + LAYER_GAP) / PX_PER_UNIT,
        )
        self._positions = layout.positions
        self._half_sizes = {
            name: (width / PX_PER_UNIT / 2, height / PX_PER_UNIT / 2)
            for name, (width, height) in sizes.items()
        }

        for edge_kind, (lines, heads) in self._edge_paths(graph, layout.routes).items():
            colour = QColor(*EDGE_COLOURS.get(edge_kind, EDGE_COLOURS["call"]))
            pen = QPen(colour, 1.6 if edge_kind == "tag" else 1.3)
            pen.setCosmetic(True)
            pen.setStyle(EDGE_STYLES.get(edge_kind, Qt.PenStyle.SolidLine))
            line_item = QGraphicsPathItem(lines)
            line_item.setPen(pen)
            line_item.setZValue(-2)
            head_item = QGraphicsPathItem(heads)
            head_item.setPen(QPen(Qt.PenStyle.NoPen))
            head_item.setBrush(QBrush(colour))
            head_item.setZValue(-1)
            self._add(line_item, head_item)

        for name in graph.nodes:
            self._add(
                self._node_item(
                    name,
                    graph.nodes[name],
                    labels[name],
                    (name_font, cost_font),
                    dashed=name in unreachable,
                )
            )

        self._fill_legend(graph, namespace, bool(unreachable))
        self._go_home()

    def _add(self, *items: QGraphicsItem) -> None:
        for item in items:
            self.plot.addItem(item)
            self._items.append(item)

    def _node_item(self, name, attributes, label, fonts, dashed: bool) -> QGraphicsPathItem:
        name_font, cost_font = fonts
        x, y = self._positions[name]
        half_width, half_height = self._half_sizes[name]
        colour = QColor(*_node_colour(attributes))
        path = QPainterPath()
        radius = 4 / PX_PER_UNIT
        path.addRoundedRect(
            QRectF(-half_width, -half_height, 2 * half_width, 2 * half_height), radius, radius
        )
        box = QGraphicsPathItem(path)
        box.setPos(x, y)
        pen = QPen(colour, 1.8)
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        box.setPen(pen)
        box.setBrush(QBrush(_tint(colour)))
        tooltip = name
        if attributes.get("path"):
            tooltip += f"\n{attributes['path']}"
        if dashed:
            tooltip += "\nnever called from #minecraft:load/tick"
        box.setToolTip(tooltip)

        # text is laid out in pixels, then scaled (and flipped: y points up here)
        title, details = label
        flip = QTransform.fromScale(1 / PX_PER_UNIT, -1 / PX_PER_UNIT)
        top = half_height * PX_PER_UNIT - PAD_Y
        rows = [(title, name_font, QColor(25, 25, 25))]
        missing = bool(attributes.get("missing"))
        rows += [
            (line, cost_font, QColor(*NODE_COLOURS["missing"]) if missing else QColor(95, 95, 95))
            for line in details
        ]
        for text, font, text_colour in rows:
            item = QGraphicsSimpleTextItem(text, box)
            item.setFont(font)
            item.setBrush(QBrush(text_colour))
            width = QFontMetricsF(font).horizontalAdvance(text)
            item.setTransform(flip)
            item.setPos(-width / 2 / PX_PER_UNIT, top / PX_PER_UNIT)
            top -= QFontMetricsF(font).height()
        return box

    def _edge_paths(
        self, graph: CallGraph, routes: dict[CallEdge, list[tuple[float, float]]]
    ) -> dict[str, tuple[QPainterPath, QPainterPath]]:
        """Per edge kind, the path of its lines and the path of its arrowheads."""
        # edges leaving a box's bottom (or reaching its top) fan out along it
        # in the order of where they go, instead of all meeting at one point
        leaving: dict[str, list[tuple[float, CallEdge]]] = {}
        arriving: dict[str, list[tuple[float, CallEdge]]] = {}
        for edge, points in routes.items():
            if len(points) > 1 and points[1][1] < points[0][1]:
                leaving.setdefault(edge.source, []).append((points[1][0], edge))
            if len(points) > 1 and points[-2][1] > points[-1][1]:
                arriving.setdefault(edge.target, []).append((points[-2][0], edge))
        start_x = self._spread(leaving)
        end_x = self._spread(arriving)

        paths: dict[str, tuple[QPainterPath, QPainterPath]] = {}
        arrow_length = ARROW_LENGTH / PX_PER_UNIT
        for edge in graph.edges:
            points = routes.get(edge)
            if not points:
                continue
            lines, heads = paths.setdefault(edge.kind, (QPainterPath(), QPainterPath()))
            if edge.source == edge.target:
                self._self_loop(lines, heads, edge.source)
                continue
            (sx, sy), (tx, ty) = points[0], points[-1]
            s_half, t_half = self._half_sizes[edge.source], self._half_sizes[edge.target]
            downward = edge in start_x and edge in end_x
            if downward:
                start = QPointF(start_x[edge], sy - s_half[1])
                tip = QPointF(end_x[edge], ty + t_half[1])
                middle = [QPointF(px, py) for px, py in points[1:-1]]
                stops = [start, *middle, QPointF(tip.x(), tip.y() + arrow_length)]
                # curved out of the source and into the target, straight
                # through the bend points in between
                lines.moveTo(stops[0])
                last = len(stops) - 2
                for index, (here, there) in enumerate(pairwise(stops)):
                    if index in (0, last):
                        bend = (here.y() + there.y()) / 2
                        lines.cubicTo(QPointF(here.x(), bend), QPointF(there.x(), bend), there)
                    else:
                        lines.lineTo(there)
                _arrowhead(heads, tip, 0.0, -1.0)
            else:
                # the back edge of a cycle, or two nodes on one layer
                start = _border(sx, sy, s_half, tx, ty)
                tip = _border(tx, ty, t_half, sx, sy)
                dx, dy = tip.x() - start.x(), tip.y() - start.y()
                length = math.hypot(dx, dy) or 1.0
                ux, uy = dx / length, dy / length
                lines.moveTo(start)
                lines.lineTo(QPointF(tip.x() - ux * arrow_length, tip.y() - uy * arrow_length))
                _arrowhead(heads, tip, ux, uy)
        return paths

    def _spread(self, ends: dict[str, list[tuple[float, CallEdge]]]) -> dict[CallEdge, float]:
        where: dict[CallEdge, float] = {}
        for node, items in ends.items():
            items.sort(key=lambda item: item[0])
            x = self._positions[node][0]
            span = 1.2 * self._half_sizes[node][0]  # 60 % of the box width
            for index, (_, edge) in enumerate(items):
                where[edge] = x + span * ((index + 1) / (len(items) + 1) - 0.5)
        return where

    def _self_loop(self, lines: QPainterPath, heads: QPainterPath, node: str) -> None:
        """A function that calls or schedules itself: a loop on the box's right."""
        x, y = self._positions[node]
        half_width, half_height = self._half_sizes[node]
        right = x + half_width
        reach = 16 / PX_PER_UNIT
        lines.moveTo(right, y + half_height * 0.5)
        lines.cubicTo(
            QPointF(right + reach, y + half_height * 0.9),
            QPointF(right + reach, y - half_height * 0.9),
            QPointF(right + ARROW_LENGTH / PX_PER_UNIT, y - half_height * 0.5),
        )
        _arrowhead(heads, QPointF(right, y - half_height * 0.5), -1.0, 0.0)

    def _fill_legend(self, graph: CallGraph, namespace: str | None, unreachable: bool) -> None:
        node_kinds = []
        for attributes in graph.nodes.values():
            kind = _node_kind(attributes)
            if kind not in node_kinds:
                node_kinds.append(kind)
        edge_kinds = {edge.kind for edge in graph.edges}
        nodes = [
            f'<span style="color:{_hex(NODE_COLOURS[kind])}">■</span> {NODE_LEGEND[kind]}'
            for kind in NODE_LEGEND
            if kind in node_kinds
        ]
        if unreachable:
            nodes.append("┆ dashed: never called from load/tick")
        edges = [
            f'<span style="color:{_hex(EDGE_COLOURS[kind])}">{stroke}</span> {text}'
            for kind, (text, stroke) in EDGE_LEGEND.items()
            if kind in edge_kinds
        ]
        rows = ["&nbsp;&nbsp;".join(nodes)]
        if edges:
            rows.append("&nbsp;&nbsp;".join(edges) + "&nbsp;&nbsp;(arrows point at the callee)")
        if namespace:
            rows.append(f"names without <b>{namespace}:</b>")
        self.legend.setText("<br>".join(rows))

    # -- the view ---------------------------------------------------------

    def _bounds(self) -> QRectF:
        left = min(x - self._half_sizes[n][0] for n, (x, _) in self._positions.items())
        right = max(x + self._half_sizes[n][0] for n, (x, _) in self._positions.items())
        bottom = min(y - self._half_sizes[n][1] for n, (_, y) in self._positions.items())
        top = max(y + self._half_sizes[n][1] for n, (_, y) in self._positions.items())
        # a little room around, and for self-loops on the right
        margin = 20 / PX_PER_UNIT
        # (y points up: the rect's "bottom" is the top of the picture)
        return QRectF(
            left - margin,
            bottom - margin,
            right - left + 3 * margin,
            top - bottom + 2 * margin,
        )

    def _view_size(self) -> tuple[float, float]:
        rect = self.plot.vb.boundingRect()
        return rect.width(), rect.height()

    def _go_home(self) -> None:
        """The whole graph if it fits at a readable size, else its top."""
        if not self._positions:
            return
        bounds = self._bounds()
        view_width, view_height = self._view_size()
        if view_width < 50 or view_height < 50:  # not laid out yet: fit, redo on resize
            self.plot.vb.setRange(bounds, padding=0)
            self._at_home = True
            return
        scale = min(view_width / bounds.width(), view_height / bounds.height())
        if scale >= PX_PER_UNIT:
            # small graphs at their natural size, centred, not blown up
            width, height = view_width / PX_PER_UNIT, view_height / PX_PER_UNIT
            centre = bounds.center()
            self.plot.vb.setRange(
                xRange=(centre.x() - width / 2, centre.x() + width / 2),
                yRange=(centre.y() - height / 2, centre.y() + height / 2),
                padding=0,
            )
        elif scale >= MIN_START_PX_PER_UNIT:
            self.plot.vb.setRange(bounds, padding=0)
        else:
            width = view_width / PART_PX_PER_UNIT
            height = view_height / PART_PX_PER_UNIT
            anchor = next(
                (
                    self._positions[node][0]
                    for node in ("#minecraft:tick", "#minecraft:load")
                    if node in self._positions
                ),
                bounds.left() + width / 2,
            )
            left = min(max(bounds.left(), anchor - width / 2), bounds.right() - width)
            left = max(left, bounds.left())
            self.plot.vb.setRange(
                xRange=(left, left + width),
                yRange=(bounds.bottom() - height, bounds.bottom()),
                padding=0,
            )
        self._at_home = True

    def _left_home(self, *_args) -> None:
        self._at_home = False

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        # pyqtgraph resizes once from its own __init__, before ours has run
        if getattr(self, "_at_home", False):
            self._go_home()

    # -- focus --------------------------------------------------------------

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
            for name in dict.fromkeys(
                (*self.graph.predecessors(node_id), *self.graph.successors(node_id))
            )
            if name in self._positions and name != node_id
        ]
        for name, width in [(name, 2.0) for name in neighbours] + [(node_id, 4.0)]:
            ring = self._ring(name, width)
            self.plot.addItem(ring)
            self._focus_items.append(ring)
        # centred on the node; wide enough for its neighbours, but never so
        # wide that the labels get too small to read (the rest is a pan away)
        dx = max((abs(self._positions[name][0] - x) for name in neighbours), default=0.0)
        dy = max((abs(self._positions[name][1] - y) for name in neighbours), default=0.0)
        half_width = min(dx + 3.0, MAX_FOCUS_HALF_WIDTH)
        half_height = min(dy + 2.0, MAX_FOCUS_HALF_HEIGHT)
        view_width, view_height = self._view_size()
        if view_width > 0 and view_height > 0:
            # match the view's shape so the locked aspect does not widen it
            units_per_px = max(2 * half_width / view_width, 2 * half_height / view_height)
            units_per_px = min(
                units_per_px,
                2 * MAX_FOCUS_HALF_WIDTH / view_width,
                2 * MAX_FOCUS_HALF_HEIGHT / view_height,
            )
            # and no closer than the natural size
            units_per_px = max(units_per_px, 1 / PX_PER_UNIT)
            half_width = view_width * units_per_px / 2
            half_height = view_height * units_per_px / 2
        self.plot.vb.setRange(
            xRange=(x - half_width, x + half_width),
            yRange=(y - half_height, y + half_height),
            padding=0,
        )
        self._at_home = False
        self.focused = node_id
        return True

    def _ring(self, name: str, width: float) -> QGraphicsPathItem:
        x, y = self._positions[name]
        half_width, half_height = self._half_sizes[name]
        grow = (3 + width) / PX_PER_UNIT
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(
                x - half_width - grow,
                y - half_height - grow,
                2 * (half_width + grow),
                2 * (half_height + grow),
            ),
            6 / PX_PER_UNIT,
            6 / PX_PER_UNIT,
        )
        ring = QGraphicsPathItem(path)
        pen = QPen(QColor(*FOCUS_COLOUR), width)
        pen.setCosmetic(True)
        ring.setPen(pen)
        ring.setZValue(1)
        return ring

    # -- interaction ------------------------------------------------------

    def node_at(self, widget_position) -> str | None:
        """The node whose box is under a widget-space point (a few pixels of slack)."""
        if not self._positions:
            return None
        scene_point = self.mapToScene(widget_position)
        data_point = self.plot.vb.mapSceneToView(scene_point)
        x, y = data_point.x(), data_point.y()
        view_width, _ = self._view_size()
        x_range = self.plot.vb.viewRange()[0]
        slack = 4 * (x_range[1] - x_range[0]) / view_width if view_width else 0.0
        best, distance = None, None
        for name, (node_x, node_y) in self._positions.items():
            half_width, half_height = self._half_sizes[name]
            if abs(node_x - x) <= half_width + slack and abs(node_y - y) <= half_height + slack:
                current = (node_x - x) ** 2 + (node_y - y) ** 2
                if distance is None or current < distance:
                    best, distance = name, current
        return best

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt API)
        node = self.node_at(event.pos())
        if node is None:
            super().contextMenuEvent(event)
            return
        event.accept()
        self.node_menu_requested.emit(node, event.globalPos())


def _node_kind(attributes: dict[str, Any]) -> str:
    if attributes.get("missing"):
        return "missing"
    if attributes.get("kind") == "tag":
        return "tag"
    if attributes.get("overlay"):
        return "overlay"
    if attributes.get("macro"):
        return "macro"
    return "function"


def _node_colour(attributes: dict[str, Any]) -> tuple[int, int, int]:
    return NODE_COLOURS[_node_kind(attributes)]


def _label_lines(
    name: str, attributes: dict[str, Any], namespace: str | None
) -> tuple[str, list[str]]:
    """A node's title (its id, maybe without the pack's namespace) and detail lines."""
    details = []
    cost = attributes.get("cost_us")
    if cost is not None:
        details.append(f"{cost / 1000:.2f} ms")
    if attributes.get("overlay"):
        details.append(f"({attributes['overlay']})")
    if attributes.get("missing"):
        details.append("missing")
    return short_name(name, namespace), details


def _tint(colour: QColor) -> QColor:
    """The colour mixed with a lot of white: a box fill dark text reads on."""
    keep = 0.12
    return QColor(
        round(255 - (255 - colour.red()) * keep),
        round(255 - (255 - colour.green()) * keep),
        round(255 - (255 - colour.blue()) * keep),
    )


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _border(x: float, y: float, half: tuple[float, float], to_x: float, to_y: float) -> QPointF:
    """Where the line from a box's centre towards ``(to_x, to_y)`` leaves the box."""
    dx, dy = to_x - x, to_y - y
    if dx == 0 and dy == 0:
        return QPointF(x, y)
    scale = min(
        half[0] / abs(dx) if dx else math.inf,
        half[1] / abs(dy) if dy else math.inf,
    )
    return QPointF(x + dx * scale, y + dy * scale)


def _arrowhead(path: QPainterPath, tip: QPointF, ux: float, uy: float) -> None:
    """A filled triangle with its point at ``tip``, pointing along ``(ux, uy)``."""
    length, half = ARROW_LENGTH / PX_PER_UNIT, ARROW_WIDTH / 2 / PX_PER_UNIT
    base_x, base_y = tip.x() - ux * length, tip.y() - uy * length
    path.moveTo(tip)
    path.lineTo(base_x - uy * half, base_y + ux * half)
    path.lineTo(base_x + uy * half, base_y - ux * half)
    path.closeSubpath()
