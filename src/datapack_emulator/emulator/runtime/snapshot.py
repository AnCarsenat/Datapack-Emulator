"""World snapshots: keep a world as it is at a tick, put it back later, and
say what changed between two of them.

A snapshot is a deep copy of the world (entities, blocks, scores, storage,
server state, advancement progress) plus what the emulator keeps beside it:
the schedules, whether ``#minecraft:load`` has run, the diagnostics already
printed once, and the profiler, so a rewind un-counts the ticks it undoes.
The pack and the version are not part of it: rewinding replays the same
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
from datapack_emulator.emulator.common import to_snbt
from datapack_emulator.emulator.runtime.advancements import AdvancementTree
from datapack_emulator.emulator.runtime.world import World

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.emulator import Emulator


@dataclass
class Snapshot:
    """One world, kept aside."""

    tick: int
    label: str = ""
    world: Any = None
    schedules: list[tuple[int, str]] = field(default_factory=list)
    loaded: bool = False
    pending_load: bool = False
    #: commands run in the tick it was taken in (the emulator's own counter)
    commands_run: int = 0
    #: diagnostics the run has already printed once
    noted: set[str] = field(default_factory=set)
    #: the profiler as it was, so a rewind un-counts the ticks it undoes
    profiler: Any = None

    @property
    def name(self) -> str:
        return self.label or f"tick {self.tick}"

    def describe(self) -> str:
        world = self.world
        # an unnamed snapshot is already called "tick N"
        said = f"{self.label}: tick {self.tick}" if self.label else f"tick {self.tick}"
        return (
            f"{said}, {len(world.entities)} entities, "
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
        noted=set(emulator.noted),
        profiler=copy.deepcopy(emulator.profiler),
    )


def restore(emulator: Emulator, snapshot: Snapshot) -> None:
    """Put a snapshot back; it can be restored again afterwards."""
    emulator.world = _detached(snapshot.world)
    emulator._attach(emulator.world)
    emulator.schedules = list(snapshot.schedules)
    emulator.loaded = snapshot.loaded
    emulator._pending_load = snapshot.pending_load
    emulator.commands_run = snapshot.commands_run
    emulator.noted = set(snapshot.noted)
    if snapshot.profiler is not None:
        emulator.profiler = copy.deepcopy(snapshot.profiler)
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
    #: whether each side had the value at all (an empty string is a value)
    had_before: bool = True
    has_after: bool = True

    def format(self) -> str:
        if not self.had_before:
            return f"+ {self.kind} {self.what}: {self.after}"
        if not self.has_after:
            return f"- {self.kind} {self.what}: {self.before}"
        return f"~ {self.kind} {self.what}: {self.before} -> {self.after}"


def _entities(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["uuid"]: entity for entity in data["entities"]}


def _blocks(data: dict[str, Any]) -> dict[str, str]:
    def shown(block: dict[str, Any]) -> str:
        properties = ",".join(f"{k}={v}" for k, v in sorted(block["properties"].items()))
        # the position is already the key and the id is already the name
        skip = ("x", "y", "z", "id")
        nbt = {k: v for k, v in (block.get("nbt") or {}).items() if k not in skip}
        return (
            block["block"]
            + (f"[{properties}]" if properties else "")
            + (f" {to_snbt(nbt)}" if nbt else "")
        )

    return {
        f"{block['dimension']} " + " ".join(str(value) for value in block["pos"]): shown(block)
        for block in data["blocks"]
    }


def _objective(name: str, value: dict[str, Any]) -> str:
    slots = ", ".join(value["display_slots"])
    shown = value.get("display_name") or ""
    return (
        value["criterion"]
        + (f" named {shown}" if shown and shown != name else "")
        + (f" shown in {slots}" if slots else "")
    )


def _pairs(before: dict[str, Any], after: dict[str, Any], kind: str) -> list[Change]:
    changes = []
    for key in sorted(set(before) | set(after)):
        had, has = key in before, key in after
        old, new = before.get(key), after.get(key)
        if had and has and old == new:
            continue
        changes.append(
            Change(
                kind,
                key,
                "" if not had else str(old),
                "" if not has else str(new),
                had_before=had,
                has_after=has,
            )
        )
    return changes


def _flat(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested dicts as ``a.b`` keys, so one changed field is one line."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            flat.update(_flat(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _as_dict(what: Snapshot | World, version) -> dict[str, Any]:
    """A snapshot or a live world, as data: comparing with the world as it is
    now does not copy it."""
    return world_to_dict(what.world if isinstance(what, Snapshot) else what, version)


def compare(before: Snapshot | World, after: Snapshot | World, version=None) -> list[Change]:
    """What changed between two snapshots (or a snapshot and a live world),
    in reading order."""
    old = _as_dict(before, version)
    new = _as_dict(after, version)
    changes: list[Change] = []

    for holder in sorted(set(old["scores"]) | set(new["scores"])):
        changes += _pairs(
            {f"{holder} {name}": value for name, value in old["scores"].get(holder, {}).items()},
            {f"{holder} {name}": value for name, value in new["scores"].get(holder, {}).items()},
            "score",
        )
    changes += _pairs(
        {name: _objective(name, value) for name, value in old["objectives"].items()},
        {name: _objective(name, value) for name, value in new["objectives"].items()},
        "objective",
    )
    changes += _pairs(
        {" ".join(trigger): "enabled" for trigger in old["enabled_triggers"]},
        {" ".join(trigger): "enabled" for trigger in new["enabled_triggers"]},
        "trigger",
    )

    old_entities, new_entities = _entities(old), _entities(new)
    for uuid in sorted(set(old_entities) | set(new_entities)):
        one, other = old_entities.get(uuid), new_entities.get(uuid)
        if one is None or other is None:
            entity = other if other is not None else one
            assert entity is not None
            shown = f"{entity['name']} ({entity['type']})"
            changes.append(
                Change(
                    "entity",
                    uuid,
                    "" if one is None else shown,
                    "" if other is None else shown,
                    had_before=one is not None,
                    has_after=other is not None,
                )
            )
        elif one["nbt"] != other["nbt"]:
            # two unnamed pigs would read the same without their uuid
            shown = f"{one['name']} ({uuid[:8]})"
            changes += _pairs(
                {f"{shown} {key}": value for key, value in one["nbt"].items()},
                {f"{shown} {key}": value for key, value in other["nbt"].items()},
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
    changes += _pairs(_flat(old["state"]), _flat(new["state"]), "state")
    if old["tick"] != new["tick"]:
        changes.insert(0, Change("state", "game time", str(old["tick"]), str(new["tick"])))
    return changes
