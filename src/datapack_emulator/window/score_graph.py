"""A score's recorded changes as a step plot: layout from ``score_graph.ui``."""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtWidgets import QDialog, QLabel, QWidget

from datapack_emulator.emulator.runtime.world import Scoreboard
from datapack_emulator.window.panels import load_ui_into

UI_FILE = Path(__file__).with_name("score_graph.ui")


def step_points(board: Scoreboard, holder: str, objective: str, now: int) -> tuple[list, list]:
    """``(ticks, values)`` for a step plot; a reset shows as a gap (no point)."""
    ticks: list[float] = []
    values: list[float] = []
    for tick, value in board.history.get((holder, objective), ()):
        if value is None:
            continue
        ticks.append(tick)
        values.append(value)
    if ticks:
        ticks.append(max(now, ticks[-1]))  # the last value holds until now
    return ticks, values


class ScoreGraphDialog(QDialog):
    def __init__(self, board: Scoreboard, holder: str, objective: str, now: int, parent=None):
        super().__init__(parent)
        load_ui_into(self, UI_FILE)
        container = self.findChild(QWidget, "scoreGraphContainer")
        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "game time")
        self.plot.setLabel("left", objective)
        container.layout().addWidget(self.plot)
        ticks, values = step_points(board, holder, objective, now)
        if values:
            self.plot.plot(ticks, values, stepMode="center", pen=pg.mkPen("#2c7be5", width=2))
        taken = sorted(set(values))
        self.findChild(QLabel, "labelScoreGraph").setText(
            f"{objective} of {holder}: {len(values)} change(s), values "
            + (", ".join(str(int(value)) for value in taken) or "-")
        )
