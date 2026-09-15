"""The world dock: scoreboard grid, entities and storage of the current world."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

from datapack_emulator.emulator.common import to_snbt
from datapack_emulator.window.controllers.base import Controller
from datapack_emulator.window.panels.world import (
    UUID_ROLE,
    Snapshot,
    fill_entities,
    fill_scoreboard,
    fill_storage,
)

TAB_SCORES, TAB_ENTITIES, TAB_STORAGE = range(3)


class WorldController(Controller):
    #: while a run is going, the dock refreshes at most this often
    REFRESH_INTERVAL_S = 0.25

    def __init__(self, window):
        super().__init__(window)
        self._scores: Snapshot = {}
        self._last_refresh = 0.0

    def connect(self) -> None:
        window = self.window
        window.edit_world_filter.textChanged.connect(lambda _text: self.refresh())
        window.tabs_world.currentChanged.connect(lambda _index: self.refresh())
        window.tree_entities.customContextMenuRequested.connect(self.entity_menu)
        for tree in (window.tree_entities, window.tree_storage):
            tree.itemExpanded.connect(lambda _item, tree=tree: tree.resizeColumnToContents(0))
        window.dock_world.visibilityChanged.connect(lambda visible: visible and self.refresh())

    # -- refreshing ---------------------------------------------------------

    def refresh_if_due(self) -> None:
        if time.monotonic() - self._last_refresh >= self.REFRESH_INTERVAL_S:
            self.refresh()

    def refresh(self) -> None:
        """Fill the visible tab from the current world (the others fill when shown)."""
        window = self.window
        self._last_refresh = time.monotonic()
        emulator = window.emulator
        if emulator is None or not emulator.started:
            window.world_label.setText("no world yet — run, step or type a command")
            window.table_scores.clear()
            window.table_scores.setRowCount(0)
            window.table_scores.setColumnCount(0)
            window.tree_entities.clear()
            window.tree_storage.clear()
            self._scores = {}
            return
        world = emulator.world
        window.world_label.setText(
            f"game time {world.tick} · {len(world.entities)} entities · "
            f"{len(world.scoreboard.objectives)} objectives · {len(world.storage)} storages"
        )
        if window.dock_world.isHidden():
            return
        text = window.edit_world_filter.text()
        tab = window.tabs_world.currentIndex()
        if tab == TAB_SCORES:
            self._scores = fill_scoreboard(window.table_scores, world, self._scores, text)
        elif tab == TAB_ENTITIES:
            fill_entities(window.tree_entities, world, text)
        else:
            fill_storage(window.tree_storage, world.storage, text)

    def forget(self) -> None:
        """A new world: nothing counts as changed on its first refresh."""
        self._scores = {}
        self.refresh()

    # -- entities ---------------------------------------------------------

    def entity_menu(self, point: QPoint) -> None:
        window = self.window
        tree = window.tree_entities
        item = tree.itemAt(point)
        while item is not None and item.parent() is not None:
            item = item.parent()
        menu = QMenu(window)
        entity = None
        if item is not None and window.emulator is not None:
            entity = window.emulator.world.entity_by_id(str(item.data(0, UUID_ROLE)))
        if entity is None:
            menu.addAction("right-click an entity").setEnabled(False)
        else:
            navigation = window.navigation
            menu.addAction(entity.display).setEnabled(False)
            menu.addSeparator()
            menu.addAction(
                "run commands as this entity", lambda: window.console.use_executor(entity.id)
            )
            menu.addAction("copy UUID", lambda: navigation.copy_text(entity.uuid, "the UUID"))
            menu.addAction(
                "copy data (SNBT)",
                lambda: navigation.copy_text(to_snbt(entity.data()), "the entity data"),
            )
        menu.exec(tree.viewport().mapToGlobal(point))
