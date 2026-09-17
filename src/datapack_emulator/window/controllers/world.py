"""The world dock: scoreboard grid, entities and storage of the current world."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QInputDialog, QMenu, QTreeWidgetItem

from datapack_emulator.emulator.analysis.world_view import entity_selector
from datapack_emulator.emulator.common import to_snbt
from datapack_emulator.window.controllers.base import Controller
from datapack_emulator.window.panels.world import (
    BLOCK_ROLE,
    DIMENSION_ROLE,
    PATH_ROLE,
    STORAGE_ROLE,
    UUID_ROLE,
    VALUE_ROLE,
    Snapshot,
    expand_entity,
    fill_blocks,
    fill_entities,
    fill_scoreboard,
    fill_storage,
)

TAB_SCORES, TAB_ENTITIES, TAB_STORAGE, TAB_BLOCKS = range(4)


class WorldController(Controller):
    #: while a run is going, the dock refreshes at most this often
    REFRESH_INTERVAL_S = 0.25

    def __init__(self, window):
        super().__init__(window)
        self._scores: Snapshot = {}
        #: the holders and objectives of the grid's rows and columns
        self._holders: list[str] = []
        self._objectives: list[str] = []
        self._last_refresh = 0.0
        #: how long the last fill took; a slow dock refreshes less often
        self._fill_seconds = 0.0

    def connect(self) -> None:
        window = self.window
        window.edit_world_filter.textChanged.connect(lambda _text: self.refresh())
        window.edit_objective_filter.textChanged.connect(lambda _text: self.refresh())
        window.table_scores.cellDoubleClicked.connect(self._on_score_double_click)
        window.table_scores.customContextMenuRequested.connect(self.score_menu)
        window.tree_entities.itemDoubleClicked.connect(self._on_nbt_double_click)
        window.tree_storage.itemDoubleClicked.connect(self._on_nbt_double_click)
        window.tree_storage.customContextMenuRequested.connect(self.storage_menu)
        console = window.console
        window.world_summon_button.clicked.connect(lambda: console.prefill("summon minecraft:"))
        window.world_objective_button.clicked.connect(
            lambda: console.prefill("scoreboard objectives add ")
        )
        window.env_summon_button.clicked.connect(lambda: console.prefill("summon minecraft:"))
        window.env_score_button.clicked.connect(lambda: console.prefill("scoreboard players set "))
        window.env_world_button.clicked.connect(self.show_dock)
        window.tabs_world.currentChanged.connect(lambda _index: self.refresh())
        window.tree_entities.customContextMenuRequested.connect(self.entity_menu)
        window.tree_entities.itemExpanded.connect(self._on_entity_expanded)
        window.tree_storage.itemExpanded.connect(
            lambda _item: window.tree_storage.resizeColumnToContents(0)
        )
        window.tree_blocks.itemDoubleClicked.connect(self._on_nbt_double_click)
        window.tree_blocks.customContextMenuRequested.connect(self.block_menu)
        window.tree_blocks.itemExpanded.connect(
            lambda _item: window.tree_blocks.resizeColumnToContents(0)
        )
        window.dock_world.visibilityChanged.connect(lambda visible: visible and self.refresh())

    # -- refreshing ---------------------------------------------------------

    def refresh_if_due(self) -> None:
        """During runs: refresh at most every REFRESH_INTERVAL_S, and never spend
        more than a fifth of the time filling the dock."""
        interval = max(self.REFRESH_INTERVAL_S, 4 * self._fill_seconds)
        if time.monotonic() - self._last_refresh >= interval:
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
            window.tree_blocks.clear()
            self._scores = {}
            return
        world = emulator.world
        window.world_label.setText(
            f"game time {world.tick} · {len(world.entities)} entities · "
            f"{len(world.scoreboard.objectives)} objectives · {len(world.storage)} storages · "
            f"{len(world.blocks)} blocks · time {world.state.day_time % 24000} · "
            f"{world.state.weather}"
        )
        if window.dock_world.isHidden():
            return
        text = window.edit_world_filter.text()
        tab = window.tabs_world.currentIndex()
        started = time.monotonic()
        if tab == TAB_SCORES:
            self._scores, self._holders, self._objectives = fill_scoreboard(
                window.table_scores,
                world,
                self._scores,
                text,
                window.edit_objective_filter.text(),
            )
        elif tab == TAB_ENTITIES:
            fill_entities(window.tree_entities, world, text, emulator.version)
        elif tab == TAB_BLOCKS:
            fill_blocks(window.tree_blocks, world, text, emulator.version)
        else:
            fill_storage(window.tree_storage, world.storage, text)
        self._fill_seconds = time.monotonic() - started
        self._last_refresh = time.monotonic()

    def _on_entity_expanded(self, item) -> None:
        window = self.window
        if item.parent() is None and window.emulator is not None:
            uuid = str(item.data(0, UUID_ROLE))
            emulator = window.emulator
            expand_entity(item, emulator.world.entity_by_id(uuid), emulator.version)
        window.tree_entities.resizeColumnToContents(0)

    def forget(self) -> None:
        """A new world: nothing counts as changed on its first refresh."""
        self._scores = {}
        self.refresh()

    def show_dock(self) -> None:
        self.window.dock_world.show()
        self.window.dock_world.raise_()
        self.refresh()

    # -- scores -------------------------------------------------------------

    def _cell(self, row: int, column: int) -> tuple[str, str] | None:
        if 0 <= row < len(self._holders) and 0 <= column < len(self._objectives):
            return self._holders[row], self._objectives[column]
        return None

    def _run(self, command: str) -> None:
        self.window.console.run(command)

    def _on_score_double_click(self, row: int, column: int) -> None:
        cell = self._cell(row, column)
        if cell is not None:
            self.set_score(*cell)

    def set_score(self, holder: str, objective: str) -> None:
        emulator = self.window.emulator
        current = emulator.world.scoreboard.get(holder, objective) if emulator else None
        value, accepted = QInputDialog.getInt(
            self.window,
            "set score",
            f"{objective} of {holder}:",
            current or 0,
            -(2**31),
            2**31 - 1,
        )
        if accepted:
            self._run(f"scoreboard players set {holder} {objective} {value}")

    def graph(self, holder: str, objective: str) -> None:
        from datapack_emulator.window.score_graph import ScoreGraphDialog

        emulator = self.window.emulator
        if emulator is None:
            return
        world = emulator.world
        ScoreGraphDialog(world.scoreboard, holder, objective, world.tick, self.window).exec()

    def score_menu(self, point: QPoint) -> None:
        window = self.window
        table = window.table_scores
        index = table.indexAt(point)
        cell = self._cell(index.row(), index.column()) if index.isValid() else None
        menu = QMenu(window)
        if cell is not None and window.emulator is not None:
            holder, objective = cell
            board = window.emulator.world.scoreboard
            value = board.get(holder, objective)
            menu.addAction(f"{objective} of {holder}").setEnabled(False)
            menu.addSeparator()
            menu.addAction("set…", lambda: self.set_score(holder, objective))
            menu.addAction(
                "add 1", lambda: self._run(f"scoreboard players add {holder} {objective} 1")
            )
            menu.addAction(
                "remove 1", lambda: self._run(f"scoreboard players remove {holder} {objective} 1")
            )
            reset = menu.addAction(
                "reset", lambda: self._run(f"scoreboard players reset {holder} {objective}")
            )
            reset.setEnabled(value is not None)
            if board.objectives.get(objective) == "trigger":
                menu.addAction(
                    "enable trigger",
                    lambda: self._run(f"scoreboard players enable {holder} {objective}"),
                )
            menu.addSeparator()
            menu.addAction("graph over time…", lambda: self.graph(holder, objective))
            copy = menu.addAction(
                "copy value", lambda: window.navigation.copy_text(str(value), "the value")
            )
            copy.setEnabled(value is not None)
            menu.addSeparator()
        menu.addAction(
            "new objective…", lambda: window.console.prefill("scoreboard objectives add ")
        )
        menu.exec(table.viewport().mapToGlobal(point))

    # -- NBT ------------------------------------------------------------------

    def _owner(self, item: QTreeWidgetItem) -> tuple[str, str] | None:
        """``("entity", selector)``, ``("storage", id)`` or ``("block", "x y z")``
        for an NBT item."""
        top = self._top(item)
        storage = top.data(0, STORAGE_ROLE)
        if storage:
            return ("storage", str(storage))
        block = top.data(0, BLOCK_ROLE)
        if block:
            return ("block", str(block))
        emulator = self.window.emulator
        entity = emulator.world.entity_by_id(str(top.data(0, UUID_ROLE))) if emulator else None
        return ("entity", entity_selector(entity)) if entity is not None else None

    @staticmethod
    def _top(item: QTreeWidgetItem) -> QTreeWidgetItem:
        while item.parent() is not None:
            item = item.parent()
        return item

    def _run_in(self, dimension: str, command: str) -> None:
        """A command at a block, in its dimension."""
        if dimension and dimension != "minecraft:overworld":
            command = f"execute in {dimension} run {command}"
        self._run(command)

    def block_menu(self, point: QPoint) -> None:
        window = self.window
        tree = window.tree_blocks
        clicked = tree.itemAt(point)
        menu = QMenu(window)
        if clicked is None or not self._top(clicked).data(0, BLOCK_ROLE):
            menu.addAction(
                "set block…", lambda: window.console.prefill("setblock ~ ~ ~ minecraft:")
            )
            menu.exec(tree.viewport().mapToGlobal(point))
            return
        top = self._top(clicked)
        where = str(top.data(0, BLOCK_ROLE))
        dimension = str(top.data(0, DIMENSION_ROLE))
        path = clicked.data(0, PATH_ROLE)
        if path:
            menu.addAction("change value…", lambda: self.edit_value(clicked))
            menu.addAction(
                "remove", lambda: self._run_in(dimension, f"data remove block {where} {path}")
            )
            menu.addSeparator()
        menu.addAction(top.text(1)).setEnabled(False)
        menu.addAction(
            "replace block…", lambda: window.console.prefill(f"setblock {where} minecraft:")
        )
        menu.addAction(
            "set item in slot…",
            lambda: window.console.prefill(
                f"item replace block {where} container.0 with minecraft:"
            ),
        )
        menu.addAction(
            "remove block", lambda: self._run_in(dimension, f"setblock {where} minecraft:air")
        )
        menu.addAction(
            "run a command here",
            lambda: window.console.prefill(f"execute positioned {where} run "),
        )
        menu.addAction(
            "copy block state",
            lambda: window.navigation.copy_text(top.text(1).split(" · ")[0], "the block"),
        )
        menu.exec(tree.viewport().mapToGlobal(point))

    def _on_nbt_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.edit_value(item)

    def edit_value(self, item: QTreeWidgetItem) -> None:
        owner = self._owner(item)
        path = item.data(0, PATH_ROLE)
        if owner is None or not path:
            return
        kind, target = owner
        text, accepted = QInputDialog.getText(
            self.window,
            "change value",
            f'new SNBT value for {path} (e.g. 5, 1.5d, "text", [1, 2], {{a: 1}}):',
            text=to_snbt(item.data(0, VALUE_ROLE)),
        )
        if accepted and text.strip():
            command = f"data modify {kind} {target} {path} set value {text.strip()}"
            if kind == "block":
                self._run_in(str(self._top(item).data(0, DIMENSION_ROLE)), command)
            else:
                self._run(command)

    def storage_menu(self, point: QPoint) -> None:
        window = self.window
        tree = window.tree_storage
        item = tree.itemAt(point)
        menu = QMenu(window)
        if item is not None and item.data(0, PATH_ROLE):
            owner = self._owner(item)
            path = item.data(0, PATH_ROLE)
            menu.addAction("change value…", lambda: self.edit_value(item))
            if owner is not None:
                menu.addAction(
                    "remove", lambda: self._run(f"data remove storage {owner[1]} {path}")
                )
            menu.addAction(
                "copy value",
                lambda: window.navigation.copy_text(to_snbt(item.data(0, VALUE_ROLE)), "the value"),
            )
        else:
            menu.addAction("right-click a value").setEnabled(False)
        menu.exec(tree.viewport().mapToGlobal(point))

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
        clicked = tree.itemAt(point)
        if clicked is not None and clicked.data(0, PATH_ROLE):
            menu.addAction("change value…", lambda: self.edit_value(clicked))
            menu.addSeparator()
        if entity is None:
            menu.addAction("right-click an entity").setEnabled(False)
        else:
            navigation = window.navigation
            selector = entity_selector(entity)
            menu.addAction(entity.display).setEnabled(False)
            menu.addSeparator()
            menu.addAction("teleport…", lambda: window.console.prefill(f"tp {selector} "))
            menu.addAction("add tag…", lambda: window.console.prefill(f"tag {selector} add "))
            if entity.is_player:
                menu.addAction(
                    "give item…", lambda: window.console.prefill(f"give {selector} minecraft:")
                )
                clear = menu.addAction("clear inventory", lambda: self._run(f"clear {selector}"))
                clear.setEnabled(any(True for _ in entity.inventory.items()))
            menu.addAction(
                "set item in slot…",
                lambda: window.console.prefill(
                    f"item replace entity {selector} weapon.mainhand with minecraft:"
                ),
            )
            kill = menu.addAction("kill", lambda: self._run(f"kill {selector}"))
            kill.setToolTip("players respawn")
            menu.addAction(
                "run a command as this entity",
                lambda: window.console.prefill(f"execute as {entity_selector(entity)} at @s run "),
            )
            menu.addAction("copy UUID", lambda: navigation.copy_text(entity.uuid, "the UUID"))
            menu.addAction(
                "copy data (SNBT)",
                lambda: navigation.copy_text(
                    to_snbt(entity.data(window.emulator.version)), "the entity data"
                ),
            )
        menu.exec(tree.viewport().mapToGlobal(point))
