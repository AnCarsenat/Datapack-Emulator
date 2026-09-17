"""World snapshots: keep a world as it is at a tick, put it back later, and
say what changed between two of them.

A snapshot is a deep copy of the world (entities, blocks, scores, storage,
server state, advancement progress) plus what the emulator keeps beside it
(the schedules and whether ``#minecraft:load`` has run). The pack, the
version and the profiler are not part of it: rewinding replays the same
pack, it does not reload it.

Two things in a world point outside it and are re-attached instead of
copied: the scoreboard's clock (it reads the world's tick) and the
advancement tree and reward callback (they belong to the emulator).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.analysis.world_view import world_to_dict
from datapack_emulator.emulator.runtime.advancements import AdvancementTree

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.emulator import Emulator
    from datapack_emulator.emulator.runtime.world import World


@dataclass
class Snapshot:
    """One world, kept aside."""

    tick: int
    label: str = ""
    world: Any = None
    schedules: list[tuple[int, str]] = field(default_factory=list)
    loaded: bool = False
    pending_load: bool = False
    #: game time of the run it was taken from, for the list
    commands_run: int = 0

    @property
    def name(self) -> str:
        return self.label or f"tick {self.tick}"

    def describe(self) -> str:
        world = self.world
        return (
            f"{self.name}: tick {self.tick}, {len(world.entities)} entities, "
            f"{len(world.storage)} storages, {len(world.blocks)} blocks"
        )


def _detached(world: World):
    """Take the emulator's own objects off a world while it is copied."""
    progress = world.advancements
    tree, on_complete = progress.tree, progress.on_complete
    progress.tree = AdvancementTree()  # the emulator's own tree is re-attached
    progress.on_complete = None
    try:
        copied = copy.deepcopy(world)
    finally:
        progress.tree, progress.on_complete = tree, on_complete
    copied.scoreboard._clock = lambda: copied.tick  # the copy reads its own tick
    return copied


def capture(emulator: Emulator, label: str = "") -> Snapshot:
    """The world as it is now, kept aside."""
    return Snapshot(
        tick=emulator.world.tick,
        label=label,
        world=_detached(emulator.world),
        schedules=list(emulator.schedules),
        loaded=emulator.loaded,
        pending_load=emulator._pending_load,
        commands_run=emulator.commands_run,
    )


def restore(emulator: Emulator, snapshot: Snapshot) -> None:
    """Put a snapshot back; it can be restored again afterwards."""
    emulator.world = _detached(snapshot.world)
    emulator._attach(emulator.world)
    emulator.schedules = list(snapshot.schedules)
    emulator.loaded = snapshot.loaded
    emulator._pending_load = snapshot.pending_load
    emulator.commands_run = snapshot.commands_run
    emulator.output.set_tick(emulator.world.tick)
    if emulator.debugger is not None:
        emulator.debugger.reset()


# ---------------------------------------------------------------------------
# comparing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One difference between two snapshots."""

    #: score, entity, storage, block, objective, gamerule or state
    kind: str
    what: str
    before: str = ""
    after: str = ""

    def format(self) -> str:
        if not self.before:
            return f"+ {self.kind} {self.what}: {self.after}"
        if not self.after:
            return f"- {self.kind} {self.what}: {self.before}"
        return f"~ {self.kind} {self.what}: {self.before} -> {self.after}"


def _entities(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["uuid"]: entity for entity in data["entities"]}


def _blocks(data: dict[str, Any]) -> dict[str, str]:
    def shown(block: dict[str, Any]) -> str:
        properties = ",".join(f"{k}={v}" for k, v in sorted(block["properties"].items()))
        nbt = block.get("nbt") or {}
        return block["block"] + (f"[{properties}]" if properties else "") + (" +nbt" if nbt else "")

    return {
        f"{block['dimension']} " + " ".join(str(value) for value in block["pos"]): shown(block)
        for block in data["blocks"]
    }


def _objective(value: dict[str, Any]) -> str:
    slots = ", ".join(value["display_slots"])
    return value["criterion"] + (f" shown in {slots}" if slots else "")


def _pairs(before: dict[str, Any], after: dict[str, Any], kind: str) -> list[Change]:
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        changes.append(
            Change(kind, key, "" if old is None else str(old), "" if new is None else str(new))
        )
    return changes


def compare(before: Snapshot, after: Snapshot, version=None) -> list[Change]:
    """What changed between two snapshots, in reading order."""
    old = world_to_dict(before.world, version)
    new = world_to_dict(after.world, version)
    changes: list[Change] = []

    for holder in sorted(set(old["scores"]) | set(new["scores"])):
        changes += _pairs(
            {f"{holder} {name}": value for name, value in old["scores"].get(holder, {}).items()},
            {f"{holder} {name}": value for name, value in new["scores"].get(holder, {}).items()},
            "score",
        )
    changes += _pairs(
        {name: _objective(value) for name, value in old["objectives"].items()},
        {name: _objective(value) for name, value in new["objectives"].items()},
        "objective",
    )

    old_entities, new_entities = _entities(old), _entities(new)
    for uuid in sorted(set(old_entities) | set(new_entities)):
        one, other = old_entities.get(uuid), new_entities.get(uuid)
        if one is None or other is None:
            entity = other if other is not None else one
            assert entity is not None
            shown = f"{entity['name']} ({entity['type']})"
            changes.append(
                Change("entity", uuid, "" if one is None else shown, "" if other is None else shown)
            )
        elif one["nbt"] != other["nbt"]:
            changes += _pairs(
                {f"{one['name']} {key}": value for key, value in one["nbt"].items()},
                {f"{other['name']} {key}": value for key, value in other["nbt"].items()},
                "entity",
            )

    for storage in sorted(set(old["storage"]) | set(new["storage"])):
        changes += _pairs(
            {f"{storage} {key}": value for key, value in old["storage"].get(storage, {}).items()},
            {f"{storage} {key}": value for key, value in new["storage"].get(storage, {}).items()},
            "storage",
        )
    changes += _pairs(_blocks(old), _blocks(new), "block")
    changes += _pairs(old["gamerules"], new["gamerules"], "gamerule")
    changes += _pairs(old["state"], new["state"], "state")
    if old["tick"] != new["tick"]:
        changes.insert(0, Change("state", "game time", str(old["tick"]), str(new["tick"])))
    return changes
