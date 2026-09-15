"""The explorer model: the datapack mirrored as it sits on disk."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from src.emulator.datapack import Datapack, Layer
from src.emulator.namespace import DirectoryNode, Namespace

#: role holding the filesystem path of a row
PATH_ROLE = int(Qt.UserRole) + 1
#: role holding the resource id (``ns:path``) of a row, when it has one
RESOURCE_ROLE = int(Qt.UserRole) + 2
#: role holding the overlay directory a row belongs to ("" for the base pack)
OVERLAY_ROLE = int(Qt.UserRole) + 3

OVERLAY_COLOUR = QColor("#8e44ad")
ERROR_COLOUR = QColor("#c0392b")


def build_explorer_model(datapack: Optional[Datapack]) -> QStandardItemModel:
    """Pack files, then ``data/``, then one subtree per overlay directory."""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["datapack"])
    if datapack is None:
        model.appendRow(QStandardItem("no datapack loaded"))
        return model

    root = _item(datapack.name, str(datapack.path))
    model.appendRow(root)

    if datapack.mcmeta is not None:
        root.appendRow(_item("pack.mcmeta", str(datapack.mcmeta.path)))
    if datapack.icon is not None:
        root.appendRow(_item("pack.png", str(datapack.icon.path)))

    root.appendRow(_layer_item(datapack.base, "data"))
    for layer in datapack.overlays:
        label = layer.directory
        if layer.entry is not None:
            label = layer.entry.describe()
        item = _layer_item(layer, label)
        item.setForeground(QBrush(OVERLAY_COLOUR))
        root.appendRow(item)
    return model


def _layer_item(layer: Layer, label: str) -> QStandardItem:
    item = _item(label, str(layer.path), overlay=layer.directory if layer.entry else "")
    for namespace in layer.namespaces.values():
        item.appendRow(_namespace_item(namespace))
    return item


def _namespace_item(namespace: Namespace) -> QStandardItem:
    item = _item(namespace.name, str(namespace.path), overlay=namespace.overlay)
    for child in namespace.tree.children:
        item.appendRow(_tree_item(child, namespace.overlay))
    return item


def _tree_item(node: DirectoryNode, overlay: str) -> QStandardItem:
    item = _item(node.name, str(node.path), overlay=overlay)
    if node.resource is not None:
        item.setData(node.resource.id, RESOURCE_ROLE)
        if node.resource.error:
            item.setToolTip(node.resource.error)
            item.setForeground(QBrush(ERROR_COLOUR))
    for child in node.children:
        item.appendRow(_tree_item(child, overlay))
    return item


def _item(text: str, path: str = "", overlay: str = "") -> QStandardItem:
    item = QStandardItem(text)
    item.setEditable(False)
    item.setData(path, PATH_ROLE)
    item.setData(overlay, OVERLAY_ROLE)
    return item
