"""The world as people read it: score holders, histories, entity summaries,
blocks and whole-world dumps — shared by the window's world dock and
``datapack-emulator-cli world``.
"""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator.common import to_snbt
from datapack_emulator.emulator.runtime.world import Entity, Scoreboard, World, uuid_to_ints

#: changes listed in a score's history text
HISTORY_SHOWN = 20


def holder_label(entities: dict[str, Entity], holder: str) -> str:
    """A score holder as a row header: players and fake names as they are, other
    entities by name and a short UUID. ``entities`` maps holder ids to entities."""
    entity = entities.get(holder)
    if entity is None or entity.is_player:
        return holder
    return f"{entity.display} ({holder[:8]}…)"


def history_text(board: Scoreboard, holder: str, objective: str, shown: int = HISTORY_SHOWN) -> str:
    changes = list(board.history.get((holder, objective), ()))
    if not changes:
        return f"{holder} · {objective}: no changes recorded"
    taken = sorted({value for _, value in changes if value is not None})
    lines = [
        f"{holder} · {objective}",
        "values taken: " + (", ".join(str(value) for value in taken) or "-"),
        "",
    ]
    for tick, value in changes[-shown:]:
        lines.append(f"tick {tick}: {'reset' if value is None else value}")
    if len(changes) > shown:
        lines.append(f"(last {shown} of {len(changes)} changes)")
    return "\n".join(lines)


def entity_summary(entity: Entity) -> str:
    x, y, z = entity.position
    parts = [entity.type, f"{x:g} {y:g} {z:g}"]
    if entity.tags:
        parts.append("tags " + ", ".join(sorted(entity.tags)))
    stacks = list(entity.inventory.items())
    if stacks:
        parts.append(f"{sum(stack.count for _, stack in stacks)} item(s)")
    return " · ".join(parts)


def entity_selector(entity: Entity) -> str:
    """A selector for exactly this entity that also works in game: a player's
    name, otherwise ``@e[nbt={UUID:[I;…]},limit=1]``."""
    if entity.is_player and entity.name:
        return entity.name
    ints = ",".join(str(part) for part in uuid_to_ints(entity.uuid))
    return f"@e[nbt={{UUID:[I;{ints}]}},limit=1]"


def join_path(path: str, key: str) -> str:
    """An NBT path segment; keys with unusual characters are quoted."""
    bare = key.replace("_", "").replace("-", "").isalnum()
    segment = key if bare else '"' + key.replace('"', '\\"') + '"'
    return f"{path}.{segment}" if path else segment


def sorted_holders(world: World) -> list[str]:
    """Players first, then everyone else, by name."""
    players = {entity.id for entity in world.players}
    return sorted(
        world.scoreboard.tracked(), key=lambda holder: (holder not in players, holder.lower())
    )


def scoreboard_rows(
    world: World, holder_filter: str = "", objective_filter: str = ""
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """``(objectives, [(holder label, [value per objective])])`` — the grid.

    Empty cells are ``""``; an enabled trigger with no score is ``"(enabled)"``,
    and an enabled one with a score gets a ``*``.
    """
    board = world.scoreboard
    wanted_objective = objective_filter.strip().lower()
    objectives = [name for name in board.objectives if wanted_objective in name.lower()]
    by_id = {entity.id: entity for entity in world.entities}
    wanted = holder_filter.strip().lower()
    rows = []
    for holder in sorted_holders(world):
        label = holder_label(by_id, holder)
        if wanted and wanted not in holder.lower() and wanted not in label.lower():
            continue
        cells = []
        for objective in objectives:
            value = board.get(holder, objective)
            enabled = (holder, objective) in board.enabled_triggers
            if value is None:
                cells.append("(enabled)" if enabled else "")
            else:
                cells.append(f"{value}*" if enabled else str(value))
        rows.append((label, cells))
    return objectives, rows


def format_table(header: list[str], rows: list[list[str]]) -> str:
    """Plain aligned columns; the first one left-aligned, the others right."""
    widths = [len(text) for text in header]
    for row in rows:
        for index, text in enumerate(row):
            widths[index] = max(widths[index], len(text))

    def line(cells: list[str]) -> str:
        parts = [
            cell.ljust(widths[index]) if index == 0 else cell.rjust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    return "\n".join([line(header), *(line(row) for row in rows)])


def scoreboard_text(world: World, holder_filter: str = "", objective_filter: str = "") -> str:
    board = world.scoreboard
    objectives, rows = scoreboard_rows(world, holder_filter, objective_filter)
    if not objectives:
        return "no objectives"
    slots = {objective: slot for slot, objective in board.display_slots.items()}
    header = ["holder"]
    for objective in objectives:
        label = f"{objective} ({board.objectives[objective]}"
        label += f", {slots[objective]})" if objective in slots else ")"
        header.append(label)
    if not rows:
        return format_table(header, []) + "\n(no scores)"
    return format_table(header, [[label, *cells] for label, cells in rows])


def entities_text(world: World, text_filter: str = "", version=None, nbt: bool = False) -> str:
    wanted = text_filter.strip().lower()
    lines = []
    for entity in world.entities:
        summary = entity_summary(entity)
        if wanted and not any(
            wanted in text.lower() for text in (entity.display, entity.uuid, summary)
        ):
            continue
        lines.append(f"{entity.display}  {summary}  (UUID {entity.uuid}, tick {entity.born})")
        if nbt:
            lines.append(f"  {to_snbt(entity.data(version))}")
    return "\n".join(lines) or "no entities"


def storage_text(world: World, text_filter: str = "") -> str:
    wanted = text_filter.strip().lower()
    lines = []
    for storage_id, contents in sorted(world.storage.items()):
        snbt = to_snbt(contents)
        if wanted and wanted not in storage_id.lower() and wanted not in snbt.lower():
            continue
        lines.append(f"{storage_id}  {snbt}")
    return "\n".join(lines) or "no storage"


def blocks_text(world: World, text_filter: str = "", version=None) -> str:
    wanted = text_filter.strip().lower()
    lines = []
    for (dimension, x, y, z), block in world.blocks.items():
        where = f"{x} {y} {z}" + ("" if dimension == "minecraft:overworld" else f" in {dimension}")
        if wanted and wanted not in where.lower() and wanted not in block.state.lower():
            continue
        line = f"{where}  {block.state}"
        data = {key: value for key, value in block.data(version).items() if key != "id"}
        if data:
            line += f"  {to_snbt(data)}"
        lines.append(line)
    return "\n".join(lines) or "no blocks (everything is air)"


def blocks_to_list(world: World, version=None) -> list[dict[str, Any]]:
    return [
        {
            "dimension": dimension,
            "pos": [x, y, z],
            "block": block.id,
            "properties": dict(block.properties),
            "nbt": block.data(version, (x, y, z)),
        }
        for (dimension, x, y, z), block in world.blocks.items()
    ]


def world_to_dict(world: World, version=None) -> dict[str, Any]:
    """Everything the world dock shows, as plain JSON-ready data."""
    board = world.scoreboard
    return {
        "tick": world.tick,
        "objectives": {
            name: {
                "criterion": criterion,
                "display_name": board.display_names.get(name, name),
                "display_slots": sorted(s for s, o in board.display_slots.items() if o == name),
            }
            for name, criterion in board.objectives.items()
        },
        "scores": {holder: dict(board.scores[holder]) for holder in sorted_holders(world)},
        "enabled_triggers": sorted(map(list, board.enabled_triggers)),
        "entities": [
            {
                "uuid": entity.uuid,
                "type": entity.type,
                "name": entity.display,
                "player": entity.is_player,
                "born": entity.born,
                "nbt": entity.data(version),
            }
            for entity in world.entities
        ],
        "storage": {key: world.storage[key] for key in sorted(world.storage)},
        "blocks": blocks_to_list(world, version),
        "gamerules": dict(world.gamerules),
    }
