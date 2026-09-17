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
        if window.debug.busy():  # a half-run tick is not a world to come back to
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
        if window.debug.busy():  # the stopped tick would go on in the restored world
            return
        schedule = window.runs.schedule
        window.runs.stop(refresh=False)
        if schedule is not None:  # the tests of the abandoned run never ran
            window.environment.show_results(schedule.by_index())
        else:  # the results are of ticks that are being undone (the shell too)
            window.environment.show_results({})
        restore(window.emulator, chosen[0])
        shown = self._shown(chosen[0])
        window.output.app(f"rewound to {shown}: game time {window.emulator.world.tick}")
        window.log_view.flush()
        window.runs.show_tick()
        window.world_view.refresh()
        window.runs.run_profiler(switch_tab=False)
        self._forget_comparison()
        self.status(f"rewound to {shown}: game time {window.emulator.world.tick}")

    def remove_selected(self) -> None:
        chosen = self.selected()
        self.snapshots = [snapshot for snapshot in self.snapshots if snapshot not in chosen]
        self._forget_comparison()
        self.fill()

    def _shown(self, snapshot: Snapshot) -> str:
        """Its number and name: two snapshots may share a name."""
        return f"{self.snapshots.index(snapshot) + 1}. {snapshot.name}"

    def _forget_comparison(self) -> None:
        """The changes shown are of snapshots or a world that are gone."""
        self.changes.clear()
        self.label.setText("take a snapshot, run, then compare")

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
            # the world as it is: comparing it does not copy it
            before, after = chosen[0], window.emulator.world
            where = "the world now"
        else:
            order = {id(snapshot): index for index, snapshot in enumerate(self.snapshots)}
            before, after = sorted(chosen, key=lambda snapshot: order[id(snapshot)])
            where = self._shown(after)
        changes = compare(before, after, window.version)
        self.changes.clear()
        for change in changes:
            item = QTreeWidgetItem([f"{change.kind} {change.what}", change.before, change.after])
            item.setToolTip(KIND_COLUMN, change.format())
            self.changes.addTopLevelItem(item)
        for column in (KIND_COLUMN, BEFORE_COLUMN):
            self.changes.resizeColumnToContents(column)
        self.label.setText(f"{self._shown(before)} → {where}: {len(changes)} change(s)")
        self.status(self.label.text())

    # -- the list ---------------------------------------------------------------

    def fill(self) -> None:
        kept = self.selected()
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
                if snapshot in kept:  # a rename or a removal keeps the selection
                    item.setSelected(True)
            for column in (0, 1):
                self.tree.resizeColumnToContents(column)
            last = self.tree.topLevelItem(self.tree.topLevelItemCount() - 1)
            if last is not None:
                self.tree.scrollToItem(last)
        finally:
            self._filling = False
        self.rewind_button.setEnabled(bool(self.snapshots))
        self.compare_button.setEnabled(bool(self.snapshots))
        self.remove_button.setEnabled(bool(self.snapshots))

    def _renamed(self, item: QTreeWidgetItem, column: int) -> None:
        # every column is editable (Qt flags are per item), but only the name
        # is kept; filling again puts the other columns back
        if self._filling:
            return
        snapshot = item.data(0, SNAPSHOT_ROLE)
        if snapshot is not None:
            if column == 0:
                snapshot.label = item.text(0).strip()
            self.fill()

    def forget(self) -> None:
        """A new world (a run, another pack or another version): the old
        snapshots belong to a world that is gone (the shell says the same)."""
        if self.snapshots:
            count = len(self.snapshots)
            self.snapshots = []
            self._forget_comparison()
            self.fill()
            self.status(f"{count} snapshot(s) forgotten: this is a new world")
