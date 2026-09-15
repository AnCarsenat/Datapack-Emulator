"""Explain one command line: what it does, step by step, and what may go wrong.

Used by the window's *analyze line* (source view, log records) and usable on
its own::

    from datapack_emulator.emulator.analysis.explain import explain_line
    for label, text in explain_line("execute as @a run say hi", "1.21.4"):
        print(label, text)

Everything here is static: nothing runs. The emulator's own coverage is
included, so a line that only half runs in the emulator says so.
"""

from __future__ import annotations

from typing import Any

from datapack_emulator.emulator import costs, versions
from datapack_emulator.emulator.commands.handlers import HANDLERS, UNMODELLED
from datapack_emulator.emulator.commands.parser import Command, Selector, Subcommand
from datapack_emulator.emulator.commands.registry import command_set
from datapack_emulator.emulator.common import (
    load_text_component,
    normalise_id,
    normalise_tagged_id,
    parse_snbt,
    split_arguments,
)
from datapack_emulator.emulator.runtime.world import UNMODELLED_SELECTOR_ARGUMENTS
from datapack_emulator.emulator.testing import describe_range
from datapack_emulator.emulator.versions import Version

Rows = list[tuple[str, str]]

#: what each vanilla command does, in one line
COMMAND_SUMMARIES: dict[str, str] = {
    "advancement": "grants or revokes advancements",
    "attribute": "reads or changes an entity attribute (max health, speed, …)",
    "ban": "bans a player from the server",
    "bossbar": "creates, edits or removes a boss bar",
    "clear": "removes items from player inventories",
    "clone": "copies a region of blocks",
    "damage": "damages entities",
    "data": "reads or changes the NBT of an entity, block or command storage",
    "datapack": "enables, disables or lists data packs",
    "debug": "starts or stops the debug profiler",
    "defaultgamemode": "sets the game mode new players get",
    "difficulty": "sets the difficulty",
    "effect": "gives or clears status effects",
    "enchant": "enchants the item a player holds",
    "execute": "runs a command with a changed executor, position or condition",
    "experience": "adds, sets or queries experience",
    "fill": "fills a region with a block",
    "fillbiome": "changes the biome of a region",
    "forceload": "keeps chunks loaded",
    "function": "runs a function, or every function in a tag",
    "gamemode": "sets a player's game mode",
    "gamerule": "reads or sets a game rule",
    "give": "gives an item to players",
    "item": "replaces or modifies items in inventories and containers",
    "kill": "removes entities (players die and respawn)",
    "list": "lists the players online",
    "locate": "finds the nearest structure, biome or point of interest",
    "loot": "drops or gives the items a loot table produces",
    "me": "sends an action message in chat",
    "msg": "whispers a private message",
    "particle": "shows particles",
    "place": "places a feature, jigsaw, structure or template",
    "playsound": "plays a sound",
    "random": "draws a random number (and can announce it)",
    "recipe": "gives or takes recipes",
    "reload": "reloads data packs",
    "replaceitem": "replaces an item in an inventory slot (before 1.17)",
    "return": "ends the function, optionally with a value",
    "ride": "makes an entity ride or dismount another",
    "rotate": "turns an entity",
    "say": "sends a message in chat",
    "schedule": "runs a function later, or clears scheduled ones",
    "scoreboard": "manages objectives and scores",
    "seed": "shows the world seed",
    "setblock": "places a block",
    "setworldspawn": "sets the world spawn point",
    "spawnpoint": "sets a player's spawn point",
    "spectate": "makes a spectator follow an entity",
    "spreadplayers": "teleports entities to random spots in an area",
    "stopsound": "stops sounds",
    "summon": "creates an entity",
    "tag": "adds, removes or lists entity tags",
    "team": "manages teams",
    "teammsg": "sends a message to the team",
    "tell": "whispers a private message",
    "tellraw": "sends a JSON/SNBT text message to players",
    "teleport": "moves entities",
    "tick": "freezes, steps or changes the tick rate",
    "time": "reads or changes the time of day",
    "title": "shows a title, subtitle or action bar",
    "tm": "sends a message to the team",
    "tp": "moves entities",
    "trigger": "lets a player change a trigger objective they are allowed to use",
    "w": "whispers a private message",
    "weather": "sets the weather",
    "worldborder": "manages the world border",
    "xp": "adds, sets or queries experience",
}

#: execute subcommands
STEP_SUMMARIES: dict[str, str] = {
    "as": "for each entity matched: runs the rest as that entity (@s), position unchanged",
    "at": "for each entity matched: runs the rest at its position, rotation and dimension",
    "positioned": "moves the position the rest runs at",
    "rotated": "changes the rotation the rest runs with",
    "facing": "turns to face a point or an entity",
    "anchored": "measures ^ ^ ^ and facing from the feet or the eyes",
    "align": "rounds the position down on the given axes",
    "in": "runs the rest in another dimension",
    "on": "switches to a related entity (vehicle, passengers, owner, attacker, …)",
    "summon": "summons an entity and runs the rest as it",
    "store": "stores the result or success of the command into a score, NBT or bossbar",
    "if": "continues only when the condition holds",
    "unless": "continues only when the condition does not hold",
}

#: conditions of if / unless
CONDITION_SUMMARIES: dict[str, str] = {
    "score": "compares a score",
    "entity": "at least one entity matches",
    "data": "the NBT path exists",
    "block": "the block at a position matches",
    "blocks": "two block regions are identical",
    "biome": "the biome at a position matches",
    "dimension": "the command runs in that dimension",
    "loaded": "the chunk at a position is loaded",
    "predicate": "a predicate passes",
    "function": "a function returns a non-zero value",
    "items": "an inventory slot holds matching items",
}

#: conditions the emulator evaluates (the others are noted and fail)
EMULATED_CONDITIONS = frozenset({"score", "entity", "data", "dimension", "loaded", "function"})

SELECTOR_KINDS = {
    "@a": "every player",
    "@p": "the nearest player",
    "@r": "a random player",
    "@e": "every entity",
    "@s": "the entity running the command (none on the console)",
    "@n": "the nearest entity",
}


def describe_selector(selector: Selector) -> str:
    if selector.kind == "literal":
        return f"the player or entity named or with UUID {selector.raw}"
    parts = [SELECTOR_KINDS.get(selector.kind, selector.kind)]
    for key, values in selector.arguments.items():
        for raw in values:
            parts.append(_describe_argument(key, raw))
    return ", ".join(part for part in parts if part)


def _describe_argument(key: str, raw: str) -> str:
    negated = raw.startswith("!")
    value = raw.lstrip("!").strip()
    not_ = "not " if negated else ""
    if key == "type":
        return f"{not_}of type {value}"
    if key == "tag":
        if not value:
            return "with some tag" if negated else "with no tags"
        return f"{'without' if negated else 'with'} tag {value}"
    if key == "name":
        return f"{not_}named {value}"
    if key == "scores":
        checks = [
            f"{objective} {describe_range(condition)}"
            for objective, condition in split_arguments(value.strip("{}"))
        ]
        return "whose scores are " + ", ".join(checks)
    if key == "distance":
        return f"at a distance {describe_range(value)} blocks"
    if key in ("x", "y", "z"):
        return f"measured from {key}={value}"
    if key in ("dx", "dy", "dz"):
        return f"inside the box {key}={value}"
    if key == "limit" or key == "c":
        return f"at most {value}"
    if key == "sort":
        return f"sorted {value}"
    if key == "nbt":
        return f"whose NBT {'does not contain' if negated else 'contains'} {value}"
    if key in UNMODELLED_SELECTOR_ARGUMENTS:
        return f"{key}={raw} (not checked by the emulator: every entity passes)"
    return f"{key}={raw}"


def describe_step(subcommand: Subcommand) -> str:
    summary = STEP_SUMMARIES.get(subcommand.name, "")
    arguments = subcommand.arguments
    if subcommand.name in ("if", "unless") and arguments:
        condition = arguments[0]
        detail = CONDITION_SUMMARIES.get(condition, condition)
        if condition == "score" and len(arguments) >= 5 and arguments[3] == "matches":
            detail = f"the score {arguments[2]} of {arguments[1]} is {describe_range(arguments[4])}"
        elif condition == "score" and len(arguments) >= 6:
            detail = (
                f"the score {arguments[2]} of {arguments[1]} {arguments[3]} "
                f"the score {arguments[5]} of {arguments[4]}"
            )
        elif condition == "entity" and len(arguments) >= 2:
            detail = "something matches " + describe_selector(Selector.parse(arguments[1]))
        summary = f"{summary}: {detail}"
        if condition not in EMULATED_CONDITIONS:
            summary += " (not emulated: treated as false)"
    elif subcommand.name == "on":
        summary += " (not emulated: the branch ends)"
    elif subcommand.name in ("as", "at") and arguments:
        summary = f"{summary} — {describe_selector(Selector.parse(arguments[0]))}"
    return summary


def emulation_status(name: str, version: Version) -> str:
    spec = command_set(version).spec(name)
    if not spec.available:
        if spec.since is None and spec.removed is None:
            return "not a command in any version (a typo?): the whole function fails to load"
        if spec.removed is not None and version >= spec.removed:
            return f"removed in {spec.removed.id}: the whole function fails to load"
        since = spec.since.id if spec.since else "a later version"
        return (
            f"does not exist in {version.id} (added in {since}): the whole function fails to load"
        )
    if name not in HANDLERS:
        return "exists, but the emulator does not run it"
    reason = UNMODELLED.get(name)
    if reason:
        return f"runs without changing the emulated world: {reason}"
    return "emulated"


def explain_line(
    line: str,
    version: str | Version,
    view: Any | None = None,
    vanilla: Any | None = None,
) -> Rows:
    """Rows of ``(label, text)`` describing one line of a function.

    ``view`` (a PackView) lets function and tag references be checked, and
    ``vanilla`` (VanillaAssets) lets entity, item and block ids be checked.
    """
    version = versions.parse(version)
    text = line.strip()
    rows: Rows = [("line", text or "(empty)")]
    if not text:
        rows.append(("does", "nothing: an empty line"))
        return rows
    if text.startswith("#"):
        rows.append(("does", "nothing: a comment"))
        return rows
    command = Command.parse(text)
    if command is None:
        rows.append(("does", "nothing"))
        return rows

    if command.is_macro:
        keys = command.macro_keys()
        rows.append(
            (
                "macro line",
                f"needs the argument(s) {', '.join(keys) or '-'}; the line is filled in "
                "and parsed when the function is called with them",
            )
        )
        if not versions.supports_macros(version):
            rows.append(
                (
                    "macro support",
                    f"macros exist from {versions.MACROS_SINCE}: in {version.id} this line "
                    "is an unknown command and the whole function fails to load",
                )
            )

    _explain_command(command, version, view, vanilla, rows, prefix="")

    missing = command_set(version).missing_features(command.features())
    if missing:
        rows.append(
            (
                f"loads in {version.id}",
                "no — the whole function fails to load; needs "
                + ", ".join(f"{f}" + (f" (since {s.id})" if s else "") for f, s in missing),
            )
        )
    else:
        rows.append((f"loads in {version.id}", "yes"))
    cost = command.estimate_cost()
    rows.append(
        (
            "estimated cost",
            f"{cost / 1000:.3f} ms per run ({cost / costs.TICK_BUDGET_US:.2%} of a tick), "
            "excluding functions it calls",
        )
    )
    return rows


def _explain_command(
    command: Command,
    version: Version,
    view: Any | None,
    vanilla: Any | None,
    rows: Rows,
    prefix: str,
) -> None:
    label = f"{prefix}command" if prefix else "command"
    summary = COMMAND_SUMMARIES.get(command.name, "not a vanilla command")
    rows.append((label, f"{command.name} — {summary}"))
    rows.append((f"{prefix}in the emulator", emulation_status(command.name, version)))

    if command.name == "execute":
        for index, subcommand in enumerate(command.subcommands, start=1):
            rows.append((f"{prefix}step {index}: {subcommand}", describe_step(subcommand)))
        if command.child is None:
            rows.append((f"{prefix}result", "no `run`: the command only tests its last condition"))
        else:
            _explain_command(command.child, version, view, vanilla, rows, prefix=f"{prefix}run › ")
    else:
        for argument in command.arguments:
            if argument.startswith("@"):
                rows.append(
                    (f"{prefix}targets {argument}", describe_selector(Selector.parse(argument)))
                )
    _check_references(command, view, vanilla, rows, prefix)
    if not command.is_macro:  # $(...) placeholders are not valid until filled in
        _check_payloads(command, rows, prefix)


def _check_references(
    command: Command, view: Any | None, vanilla: Any | None, rows: Rows, prefix: str
) -> None:
    arguments = command.arguments
    referenced: list[str] = []
    if command.name == "function" and arguments:
        referenced.append(normalise_tagged_id(arguments[0]))
    if command.name == "schedule" and len(arguments) >= 2 and arguments[0] in ("function", "clear"):
        referenced.append(normalise_tagged_id(arguments[1]))
    for subcommand in command.subcommands:
        if subcommand.condition == "function" and len(subcommand.arguments) > 1:
            referenced.append(normalise_tagged_id(subcommand.arguments[1]))
    if view is not None:
        for target in referenced:
            if target.startswith("#"):
                members = view.resolve_function_tag(target)
                found = (
                    f"tag with {len(members)} function(s): {', '.join(members)}"
                    if members
                    else "missing or empty tag"
                )
            else:
                found = (
                    "defined in the pack" if view.function(target) else "not defined in the pack"
                )
            rows.append((f"{prefix}references {target}", found))

    if vanilla is None:
        return
    checks: list[tuple[str, str]] = []
    if command.name == "summon" and arguments:
        checks.append(("entity_type", normalise_id(arguments[0])))
    if command.name in ("give", "clear") and len(arguments) > 1:
        checks.append(("item", normalise_id(arguments[1].split("[")[0].split("{")[0])))
    if command.name == "setblock" and len(arguments) > 3:
        checks.append(("block", normalise_id(arguments[3].split("[")[0].split("{")[0])))
    for registry, resource in checks:
        known = vanilla.knows(registry, resource)
        verdict = {True: "exists", False: "unknown in this version", None: "cannot be checked"}[
            known
        ]
        rows.append((f"{prefix}{registry} {resource}", verdict))


def _check_payloads(command: Command, rows: Rows, prefix: str) -> None:
    arguments = command.arguments
    for argument in arguments:
        if argument.startswith("{") and command.name in ("summon", "data", "function"):
            parsed = parse_snbt(argument)
            if parsed or argument.strip() == "{}":
                keys = ", ".join(parsed) or "(empty)"
                rows.append((f"{prefix}NBT", f"compound with {keys}"))
            else:
                rows.append((f"{prefix}NBT", "could not be parsed as SNBT"))
            break
    if command.name in ("tellraw", "title") and len(arguments) > 1:
        start = 1 if command.name == "tellraw" else 2
        payload = " ".join(arguments[start:])
        if payload and load_text_component(payload) is None:
            rows.append((f"{prefix}text", "could not be parsed as a text component"))
        elif payload:
            rows.append((f"{prefix}text", "valid text component"))
