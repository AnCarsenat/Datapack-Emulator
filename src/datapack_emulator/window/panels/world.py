"""What the world dock shows: the scoreboard grid and the entity and storage trees.

The widgets themselves are declared in window.ui; these functions only fill
them from a :class:`~datapack_emulator.emulator.runtime.world.World`.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem

from datapack_emulator.emulator.common import to_snbt
from datapack_emulator.emulator.runtime.world import Entity, Scoreboard, World

#: a score that changed since the grid was last filled
CHANGED_COLOUR = QColor("#fff2a8")
#: a trigger the holder may use right now
TRIGGER_COLOUR = QColor("#d6e9ff")
#: the path of a tree item, to keep what was expanded across refreshes
KEY_ROLE = Qt.UserRole
#: the UUID of an entity's top-level item
UUID_ROLE = Qt.UserRole + 1
#: an NBT item's value and path inside its entity or storage
VALUE_ROLE = Qt.UserRole + 2
PATH_ROLE = Qt.UserRole + 3
#: the storage id of a storage tree's top-level item
STORAGE_ROLE = Qt.UserRole + 4
#: changes listed in a score's tooltip
HISTORY_SHOWN = 20
#: entities listed at most; the filter narrows a bigger world down
MAX_ENTITIES_SHOWN = 1000
#: the placeholder child that makes an entity expandable before its NBT is built
PLACEHOLDER = "…"

Snapshot = dict[tuple[str, str], int]


def holder_label(entities: dict[str, Entity], holder: str) -> str:
    """A score holder as a row header: players and fake names as they are, other
    entities by name and a short UUID. ``entities`` maps holder ids to entities."""
    entity = entities.get(holder)
    if entity is None or entity.is_player:
        return holder
    return f"{entity.display} ({holder[:8]}…)"


def history_text(board: Scoreboard, holder: str, objective: str) -> str:
    changes = list(board.history.get((holder, objective), ()))
    if not changes:
        return f"{holder} · {objective}: no changes recorded"
    taken = sorted({value for _, value in changes if value is not None})
    lines = [
        f"{holder} · {objective}",
        "values taken: " + (", ".join(str(value) for value in taken) or "-"),
        "",
    ]
    for tick, value in changes[-HISTORY_SHOWN:]:
        lines.append(f"tick {tick}: {'reset' if value is None else value}")
    if len(changes) > HISTORY_SHOWN:
        lines.append(f"(last {HISTORY_SHOWN} of {len(changes)} changes)")
    return "\n".join(lines)


def fill_scoreboard(
    table: QTableWidget,
    world: World,
    previous: Snapshot,
    holder_filter: str = "",
    objective_filter: str = "",
) -> tuple[Snapshot, list[str], list[str]]:
    """Rows are holders, columns objectives. Returns the values shown (pass them
    back as ``previous`` so changed cells are highlighted), and the holders and
    objectives of the rows and columns."""
    board = world.scoreboard
    wanted_objective = objective_filter.strip().lower()
    objectives = [name for name in board.objectives if wanted_objective in name.lower()]
    by_id = {entity.id: entity for entity in world.entities}
    players = {entity.id for entity in world.players}
    holders = sorted(board.tracked(), key=lambda holder: (holder not in players, holder.lower()))
    labels = {holder: holder_label(by_id, holder) for holder in holders}
    wanted = holder_filter.strip().lower()
    if wanted:
        holders = [h for h in holders if wanted in h.lower() or wanted in labels[h].lower()]
    slots = {objective: slot for slot, objective in board.display_slots.items()}

    table.setUpdatesEnabled(False)
    table.clear()
    table.setColumnCount(len(objectives))
    table.setRowCount(len(holders))
    for column, objective in enumerate(objectives):
        header = f"{objective}\n{board.objectives[objective]}"
        if objective in slots:
            header += f" · {slots[objective]}"
        item = QTableWidgetItem(header)
        item.setToolTip(f"display name: {board.display_names.get(objective, objective)}")
        table.setHorizontalHeaderItem(column, item)
    table.setVerticalHeaderLabels([labels[holder] for holder in holders])

    snapshot: Snapshot = {}
    bold = QFont()
    bold.setBold(True)
    for row, holder in enumerate(holders):
        for column, objective in enumerate(objectives):
            value = board.get(holder, objective)
            enabled = (holder, objective) in board.enabled_triggers
            if value is None and not enabled:
                continue
            item = QTableWidgetItem("" if value is None else str(value))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setToolTip(
                history_text(board, holder, objective) + ("\n\ntrigger enabled" if enabled else "")
            )
            if value is not None:
                snapshot[(holder, objective)] = value
                if previous and previous.get((holder, objective)) != value:
                    item.setBackground(QBrush(CHANGED_COLOUR))
                    item.setFont(bold)
                elif enabled:
                    item.setBackground(QBrush(TRIGGER_COLOUR))
            table.setItem(row, column, item)
    table.resizeColumnsToContents()
    table.setUpdatesEnabled(True)
    return snapshot, holders, objectives


def _scalar(value: Any) -> str:
    return to_snbt(value)


def _add_nbt(parent: QTreeWidgetItem, key: str, value: Any, owner: str, nbt_path: str) -> None:
    """One NBT entry; ``owner`` (UUID or storage id) and ``nbt_path`` make its key."""
    item = QTreeWidgetItem(parent, [key, ""])
    item.setData(0, KEY_ROLE, f"{owner}/{nbt_path}")
    item.setData(0, PATH_ROLE, nbt_path)
    item.setData(0, VALUE_ROLE, value)
    if isinstance(value, dict):
        item.setText(1, f"{{{len(value)} entries}}")
        for child_key, child in value.items():
            _add_nbt(item, child_key, child, owner, _join(nbt_path, child_key))
    elif isinstance(value, list) and any(isinstance(entry, (dict, list)) for entry in value):
        item.setText(1, f"[{len(value)} entries]")
        for index, child in enumerate(value):
            _add_nbt(item, f"[{index}]", child, owner, f"{nbt_path}[{index}]")
    else:
        item.setText(1, _scalar(value))
        item.setToolTip(1, "double-click to change this value")


def _join(path: str, key: str) -> str:
    """An NBT path segment; keys with unusual characters are quoted."""
    bare = key.replace("_", "").replace("-", "").isalnum()
    segment = key if bare else '"' + key.replace('"', '\\"') + '"'
    return f"{path}.{segment}" if path else segment


def expanded_keys(tree: QTreeWidget) -> set[str]:
    keys: set[str] = set()
    stack = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.isExpanded():
            keys.add(str(item.data(0, KEY_ROLE)))
        stack.extend(item.child(index) for index in range(item.childCount()))
    return keys


def _restore_expanded(tree: QTreeWidget, keys: set[str]) -> None:
    stack = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if str(item.data(0, KEY_ROLE)) in keys:
            item.setExpanded(True)
        stack.extend(item.child(index) for index in range(item.childCount()))


def entity_summary(entity: Entity) -> str:
    x, y, z = entity.position
    parts = [entity.type, f"{x:g} {y:g} {z:g}"]
    if entity.tags:
        parts.append("tags " + ", ".join(sorted(entity.tags)))
    return " · ".join(parts)


def fill_entities(tree: QTreeWidget, world: World, text_filter: str = "", version=None) -> None:
    """One item per entity; its NBT is built when the item is expanded
    (:func:`expand_entity`), so a big world stays quick to refresh."""
    wanted = text_filter.strip().lower()
    keys = expanded_keys(tree)
    tree.setUpdatesEnabled(False)
    tree.clear()
    shown = 0
    matching = 0
    for entity in world.entities:
        summary = entity_summary(entity)
        if wanted and not any(
            wanted in text.lower() for text in (entity.display, entity.uuid, summary)
        ):
            continue
        matching += 1
        if shown >= MAX_ENTITIES_SHOWN:
            continue
        shown += 1
        item = QTreeWidgetItem(tree, [entity.display, summary])
        item.setData(0, KEY_ROLE, entity.uuid)
        item.setData(0, UUID_ROLE, entity.uuid)
        item.setToolTip(0, f"UUID {entity.uuid}\nsummoned at tick {entity.born}")
        QTreeWidgetItem(item, [PLACEHOLDER, ""])
        if entity.uuid in keys:
            expand_entity(item, entity, version)
    if matching > shown:
        QTreeWidgetItem(tree, [f"… {matching - shown} more", "narrow them down with the filter"])
    _restore_expanded(tree, keys)
    tree.resizeColumnToContents(0)
    tree.setUpdatesEnabled(True)


def expand_entity(item: QTreeWidgetItem, entity: Entity | None, version=None) -> None:
    """Replace the placeholder under an entity's item with its NBT."""
    if item.childCount() != 1 or item.child(0).text(0) != PLACEHOLDER:
        return
    item.takeChild(0)
    if entity is None:
        QTreeWidgetItem(item, ["(gone)", "the entity no longer exists"])
        return
    for key, value in entity.data(version).items():
        _add_nbt(item, key, value, entity.uuid, _join("", key))


def fill_storage(
    tree: QTreeWidget, storage: dict[str, dict[str, Any]], text_filter: str = ""
) -> None:
    wanted = text_filter.strip().lower()
    keys = expanded_keys(tree)
    tree.setUpdatesEnabled(False)
    tree.clear()
    for storage_id, contents in sorted(storage.items()):
        if wanted and wanted not in storage_id.lower() and wanted not in to_snbt(contents).lower():
            continue
        item = QTreeWidgetItem(tree, [storage_id, f"{{{len(contents)} entries}}"])
        item.setData(0, KEY_ROLE, storage_id)
        item.setData(0, STORAGE_ROLE, storage_id)
        for key, value in contents.items():
            _add_nbt(item, key, value, storage_id, _join("", key))
    _restore_expanded(tree, keys)
    tree.resizeColumnToContents(0)
    tree.setUpdatesEnabled(True)
