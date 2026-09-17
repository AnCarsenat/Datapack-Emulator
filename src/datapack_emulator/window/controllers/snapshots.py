"""The world dock's snapshots tab: keep a world aside, rewind to it, and see
what changed between two of them (``runtime/snapshot.py``; the shell's
``.snapshot``, ``.rewind`` and ``.diff`` do the same)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem

from datapack_emulator.emulator.runtime.snapshot import Snapshot, capture, compare, restore
from datapack_emulator.window.controllers.base import Controller

SNAPSHOT_ROLE = Qt.UserRole + 1
KIND_COLUMN, BEFORE_COLUMN, AFTER_COLUMN = range(3)


class SnapshotController(Controller):
    def __init__(self, window):
        super().__init__(window)
        find = window.findChild
        self.tree: QTreeWidget = find(QTreeWidget, "treeSnapshots")
        self.changes: QTreeWidget = find(QTreeWidget, "treeSnapshotChanges")
        self.label: QLabel = find(QLabel, "labelSnapshotChanges")
        self.take_button: QPushButton = find(QPushButton, "buttonSnapshotTake")
        self.rewind_button: QPushButton = find(QPushButton, "buttonSnapshotRewind")
        self.compare_button: QPushButton = find(QPushButton, "buttonSnapshotCompare")
        self.remove_button: QPushButton = find(QPushButton, "buttonSnapshotRemove")
        #: worlds kept aside, oldest first
        self.snapshots: list[Snapshot] = []
        self._filling = False

    def connect(self) -> None:
        self.take_button.clicked.connect(lambda: self.take())
        self.rewind_button.clicked.connect(self.rewind)
        self.compare_button.clicked.connect(self.compare_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemChanged.connect(self._renamed)
        self.fill()

    # -- keeping --------------------------------------------------------------

    def take(self, label: str = "") -> Snapshot | None:
        window = self.window
        if window.emulator is None:
            self.status("run, step or type a command first")
            return None
        snapshot = capture(window.emulator, label)
        self.snapshots.append(snapshot)
        self.fill()
        self.status(f"snapshot taken: {snapshot.describe()}")
        return snapshot

    def selected(self) -> list[Snapshot]:
        return [item.data(0, SNAPSHOT_ROLE) for item in self.tree.selectedItems()]

    def rewind(self) -> None:
        window = self.window
        chosen = self.selected()
        if window.emulator is None or len(chosen) != 1:
            self.status("select one snapshot to rewind to")
            return
        window.runs.stop(refresh=False)
        restore(window.emulator, chosen[0])
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        window.runs.run_profiler(switch_tab=False)
        self.status(f"rewound to {chosen[0].name}: game time {window.emulator.world.tick}")

    def remove_selected(self) -> None:
        chosen = self.selected()
        self.snapshots = [snapshot for snapshot in self.snapshots if snapshot not in chosen]
        self.fill()

    # -- comparing -------------------------------------------------------------

    def compare_selected(self) -> None:
        window = self.window
        chosen = self.selected()
        if not chosen:
            self.status("select a snapshot (and a second one, or compare with the world now)")
            return
        if len(chosen) > 2:
            self.status("select one or two snapshots")
            return
        if len(chosen) == 1:
            if window.emulator is None:
                self.status("no world to compare with")
                return
            before, after = chosen[0], capture(window.emulator)
            where = "the world now"
        else:
            order = {id(snapshot): index for index, snapshot in enumerate(self.snapshots)}
            before, after = sorted(chosen, key=lambda snapshot: order[id(snapshot)])
            where = after.name
        changes = compare(before, after, window.version)
        self.changes.clear()
        for change in changes:
            item = QTreeWidgetItem([f"{change.kind} {change.what}", change.before, change.after])
            item.setToolTip(KIND_COLUMN, change.format())
            self.changes.addTopLevelItem(item)
        for column in (KIND_COLUMN, BEFORE_COLUMN):
            self.changes.resizeColumnToContents(column)
        self.label.setText(f"{before.name} → {where}: {len(changes)} change(s)")
        self.status(self.label.text())

    # -- the list ---------------------------------------------------------------

    def fill(self) -> None:
        self._filling = True
        try:
            self.tree.clear()
            for snapshot in self.snapshots:
                world = snapshot.world
                item = QTreeWidgetItem(
                    [
                        snapshot.name,
                        str(snapshot.tick),
                        f"{len(world.entities)} entities, {len(world.storage)} storages, "
                        f"{len(world.blocks)} blocks",
                    ]
                )
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                item.setData(0, SNAPSHOT_ROLE, snapshot)
                item.setToolTip(0, "double-click to name it")
                self.tree.addTopLevelItem(item)
            for column in (0, 1):
                self.tree.resizeColumnToContents(column)
        finally:
            self._filling = False
        self.rewind_button.setEnabled(bool(self.snapshots))
        self.compare_button.setEnabled(bool(self.snapshots))
        self.remove_button.setEnabled(bool(self.snapshots))

    def _renamed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        snapshot = item.data(0, SNAPSHOT_ROLE)
        if snapshot is not None:
            snapshot.label = item.text(0).strip()
            self.fill()

    def forget(self) -> None:
        """A new world (another pack or version): the old snapshots are gone."""
        if self.snapshots:
            self.snapshots = []
            self.changes.clear()
            self.label.setText("take a snapshot, run, then compare")
            self.fill()
