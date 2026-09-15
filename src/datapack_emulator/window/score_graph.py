"""A score's recorded changes as a step plot: layout from ``score_graph.ui``."""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtWidgets import QDialog, QLabel, QWidget

from datapack_emulator.emulator.runtime.world import Scoreboard
from datapack_emulator.window.panels import load_ui_into

UI_FILE = Path(__file__).with_name("score_graph.ui")


def step_segments(
    board: Scoreboard, holder: str, objective: str, now: int
) -> list[tuple[list[float], list[float]]]:
    """Step-plot segments ``(tick edges, values)`` with ``len(edges) == len(values) + 1``.

    Each value holds until the next change; a reset ends the segment, so the
    time the score was unset shows as a gap. The last value holds until ``now``.
    """
    segments: list[tuple[list[float], list[float]]] = []
    edges: list[float] = []
    values: list[float] = []
    for tick, value in board.history.get((holder, objective), ()):
        if values:
            edges.append(tick)  # the previous value ends here
        if value is None:
            if values:
                segments.append((edges, values))
            edges, values = [], []
            continue
        if not values:
            edges = [tick]
        values.append(value)
    if values:
        edges.append(max(now, edges[-1]))
        segments.append((edges, values))
    return segments


class ScoreGraphDialog(QDialog):
    def __init__(self, board: Scoreboard, holder: str, objective: str, now: int, parent=None):
        super().__init__(parent)
        load_ui_into(self, UI_FILE)
        container = self.findChild(QWidget, "scoreGraphContainer")
        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "game time")
        self.plot.setLabel("left", objective)
        container.layout().addWidget(self.plot)
        segments = step_segments(board, holder, objective, now)
        pen = pg.mkPen("#2c7be5", width=2)
        values = [value for _, segment in segments for value in segment]
        for edges, segment in segments:
            self.plot.plot(edges, segment, stepMode="center", pen=pen)
        taken = sorted(set(values))
        self.findChild(QLabel, "labelScoreGraph").setText(
            f"{objective} of {holder}: {len(values)} change(s), values "
            + (", ".join(str(int(value)) for value in taken) or "-")
        )
