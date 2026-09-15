"""Estimated execution costs for Minecraft commands.

There is no official, published table of per-command execution times: Mojang
does not document them, and the community benchmark packs only produce
*relative* numbers that are valid for one machine and one world state.  See:

* https://minecraft.wiki/w/Tutorial:Optimizing_a_data_pack
  (qualitative: NBT access is "really expensive", ``@e`` scans are expensive,
  ``type=`` filters are cheap, macro calls have an overhead)
* https://datapack.wiki/guide/performance/how-to-measure
  (measures iterations per 40 ms tick budget, not absolute per-command time)
* https://github.com/Silicon42/Function-Benchmark-Pack and
  https://github.com/mcenv/mch (nanosecond-scale relative benchmarks)

So the numbers below are a *model*, not measurements.  They are expressed in
microseconds and were picked so that the relative magnitudes match the
qualitative guidance above (cheap scoreboard maths ~1 us, entity scans tens of
us, NBT reads/writes hundreds of us).  Tune them for your reference hardware:
every value is read at call time, so patching this dict at runtime works.

A tick budget is 50000 us (50 ms); the server is considered to be lagging past
that.
"""

from __future__ import annotations

TICK_BUDGET_US: float = 50_000.0
"""One Minecraft tick, in microseconds (20 TPS)."""

DEFAULT_COMMAND_COST_US: float = 5.0
"""Fallback for a command that is not in :data:`COMMAND_COST_US`."""

COMMAND_COST_US: dict[str, float] = {
    # --- parsing / no-op -------------------------------------------------
    "#": 0.0,  # comment line
    "": 0.0,
    # --- chat / logging --------------------------------------------------
    "say": 3.0,
    "me": 3.0,
    "msg": 3.0,
    "tell": 3.0,
    "w": 3.0,
    "tellraw": 6.0,  # JSON text component resolution
    "title": 8.0,
    "titleraw": 8.0,
    "bossbar": 10.0,
    # --- scoreboard ------------------------------------------------------
    "scoreboard": 1.5,
    "trigger": 2.0,
    # --- control flow ----------------------------------------------------
    "execute": 1.0,  # the dispatch itself; subcommands add their own cost
    "function": 1.0,  # call overhead only, body is timed separately
    "return": 0.5,
    "schedule": 4.0,
    # --- tags / teams ----------------------------------------------------
    "tag": 3.0,
    "team": 5.0,
    # --- entities --------------------------------------------------------
    "summon": 120.0,  # builds an entity, adds it to the chunk
    "kill": 25.0,
    "tp": 15.0,
    "teleport": 15.0,
    "ride": 20.0,
    "attribute": 20.0,
    "effect": 20.0,
    "damage": 25.0,
    "spawnpoint": 10.0,
    "experience": 8.0,
    "xp": 8.0,
    "gamemode": 8.0,
    # --- inventory / items (NBT heavy) -----------------------------------
    "item": 90.0,
    "give": 90.0,
    "clear": 60.0,
    "loot": 120.0,
    "enchant": 60.0,
    "recipe": 15.0,
    # --- NBT / storage ---------------------------------------------------
    "data": 180.0,  # "really expensive"
    # --- world -----------------------------------------------------------
    "setblock": 40.0,
    "fill": 400.0,  # scales with volume, see VOLUME_COST_US_PER_BLOCK
    "clone": 600.0,
    "place": 500.0,
    "fillbiome": 400.0,
    "setworldspawn": 10.0,
    "forceload": 50.0,
    "worldborder": 5.0,
    "time": 2.0,
    "weather": 5.0,
    "difficulty": 2.0,
    "gamerule": 2.0,
    "seed": 1.0,
    # --- feedback / cosmetic ---------------------------------------------
    "particle": 12.0,
    "playsound": 10.0,
    "stopsound": 5.0,
    "spectate": 5.0,
    # --- admin (rare in datapacks) ---------------------------------------
    "advancement": 40.0,
    "datapack": 100.0,
    "reload": 50_000.0,
    "perf": 10.0,
    "debug": 10.0,
}

# Cost added by each ``execute`` subcommand, before selector costs.
SUBCOMMAND_COST_US: dict[str, float] = {
    "align": 1.0,
    "anchored": 0.5,
    "as": 1.0,
    "at": 1.0,
    "facing": 2.0,
    "in": 1.0,
    "on": 8.0,  # relation lookup (vehicle, passengers, owner, ...)
    "positioned": 1.0,
    "rotated": 1.0,
    "summon": 120.0,
    "if": 2.0,
    "unless": 2.0,
    "store": 3.0,
    "run": 0.0,
}

CONDITION_COST_US: dict[str, float] = {
    "score": 2.0,
    "entity": 6.0,  # plus the selector cost
    "block": 20.0,
    "blocks": 300.0,
    "biome": 25.0,
    "data": 180.0,  # NBT read
    "predicate": 30.0,
    "dimension": 1.0,
    "loaded": 5.0,
    "items": 90.0,
    "function": 1.0,
}

# --- selectors -----------------------------------------------------------

SELECTOR_BASE_COST_US: dict[str, float] = {
    "@s": 0.2,  # already resolved, free
    "@p": 4.0,  # scans players only
    "@r": 4.0,
    "@a": 4.0,
    "@e": 25.0,  # scans every loaded entity
    "@n": 25.0,
}

SELECTOR_PER_ENTITY_COST_US: float = 0.35
"""Added per entity the selector has to consider (``@e`` scans the world)."""

TYPE_FILTER_DISCOUNT: float = 0.25
"""``type=`` lets the game cheaply reject entities; keep 25% of the scan cost."""

NBT_FILTER_COST_US: float = 150.0
"""An ``nbt={...}`` selector argument deserialises every candidate entity."""

LIMIT_DISCOUNT: float = 0.6
"""``limit=1`` lets the scan stop early on average."""

MACRO_CALL_OVERHEAD_US: float = 30.0
"""Extra cost of calling a macro (``$``) function: the body is re-compiled."""

VOLUME_COST_US_PER_BLOCK: float = 0.8
"""``fill`` / ``clone`` / ``fillbiome`` cost per block in the affected volume."""

FUNCTION_CALL_OVERHEAD_US: float = 1.0
"""Pushing a function frame, independent of its body."""
