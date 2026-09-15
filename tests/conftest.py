"""Shared fixtures: synthetic datapacks and a synthetic client jar.

Nothing here touches the network or a real Minecraft install, so the suite
runs the same on a laptop and in CI.
"""

from __future__ import annotations

import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PackFactory = Callable[..., Path]


def _write(root: Path, files: dict[str, object]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            target.write_text(json.dumps(content), encoding="utf-8")
        else:
            target.write_text(str(content), encoding="utf-8")


@pytest.fixture
def make_pack(tmp_path: Path) -> PackFactory:
    """Build a datapack folder from ``{relative path: text or JSON}``.

    ``pack.mcmeta`` defaults to pack_format 61 (1.21.4) and a tick tag pointing
    at ``test:tick`` is added unless the caller provides tags.
    """
    counter = {"n": 0}

    def factory(files: dict[str, object], mcmeta: dict | None = None, name: str = "") -> Path:
        counter["n"] += 1
        root = tmp_path / (name or f"pack{counter['n']}")
        meta = (
            mcmeta if mcmeta is not None else {"pack": {"description": "test", "pack_format": 61}}
        )
        content = {"pack.mcmeta": meta, **files}
        if not any("tags/function" in key for key in content):
            content["data/minecraft/tags/function/tick.json"] = {"values": ["test:tick"]}
        _write(root, content)
        return root

    return factory


@pytest.fixture
def fake_jar(tmp_path: Path) -> Path:
    """A tiny client jar with the entries vanilla.py reads."""
    path = tmp_path / "minecraft-9.9-client.jar"
    lang = {
        "commands.summon.success": "Summoned new %s",
        "argument.id.unknown": "Unknown ID: %s",
        "argument.block.id.invalid": "Unknown block type '%s'",
        "command.unknown.command": "Unknown or incomplete command. See below for error",
        "entity.minecraft.pig": "Pig",
        "entity.minecraft.skeleton": "Skeleton",
        "entity.minecraft.stray": "Stray",
        "entity.minecraft.villager.farmer": "Farmer",
        "effect.minecraft.speed": "Speed",
        "arguments.objective.notFound": "No such objective: %s",
    }
    entries = {
        "version.json": {
            "id": "9.9",
            "world_version": 9999,
            "pack_version": {"data_major": 107, "data_minor": 1},
        },
        "assets/minecraft/lang/en_us.json": lang,
        "assets/minecraft/blockstates/stone.json": {},
        "assets/minecraft/items/diamond.json": {},
        "assets/minecraft/particles/flame.json": {},
        "data/minecraft/tags/entity_type/skeletons.json": {
            "values": ["skeleton", "#minecraft:cold"]
        },
        "data/minecraft/tags/entity_type/cold.json": {"values": ["minecraft:stray"]},
        "data/minecraft/recipe/torch.json": {},
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, json.dumps(content))
    return path


def game_errors(records) -> list[str]:
    from src.emulator.runtime.output import LogLevel, LogSource

    return [r.message for r in records if r.source is LogSource.GAME and r.level >= LogLevel.ERROR]


def chat(records) -> list[str]:
    from src.emulator.runtime.output import LogLevel, LogSource

    return [
        r.message
        for r in records
        if r.source is LogSource.GAME and LogLevel.INFO <= r.level < LogLevel.ERROR
    ]
