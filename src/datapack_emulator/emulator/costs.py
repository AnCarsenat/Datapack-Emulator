"""Execution costs of Minecraft commands, measured on a real server.

The emulator charges every command it runs a cost from the constants below
and adds them up into the profiler's milliseconds. The constants are
*measured*: :mod:`datapack_emulator.emulator.calibration` runs a benchmark
datapack on a vanilla dedicated server, reads the server's own profiler
(``/perf``, which times every function and every command line), and fits
these constants to the measured times (``datapack-emulator-cli calibrate``;
``tools/apply_calibration.py`` writes the result here). The block between
the ``calibration:`` markers says where and when.

What that means, and what it does not:

* the numbers are real microseconds on one machine and one Minecraft version.
  Another CPU, JVM or version scales them; ``calibrate`` on your own machine
  writes ``costs.json`` in the data folder, which :func:`load_user_calibration`
  applies on top of these (``DATAPACK_EMULATOR_COSTS`` names another file);
* the model is a sum per command (base cost, ``execute`` subcommands,
  conditions, selector scans per entity, ``nbt=`` per candidate, fill volume),
  so it cannot see everything a server does: two ``data`` commands on storage
  share one constant, a ``kill`` costs the same however many it kills, and
  real servers also spend time on chunks, entities and players the emulator
  does not simulate. Measured against the benchmarks it was fitted on, the
  median error is about 9 %;
* constants no benchmark exercises (commands that need a real player, admin
  commands) are the earlier guesses scaled by the measured/guessed ratio.
* the benchmarks repeat one line hundreds of times, so they measure *warm*
  costs: the server's caches and branch predictors already know the line. A
  line run once per tick among many different lines is colder and can cost
  10-50x more (a one-line tick function measured 27 us on the same server,
  estimated 0.7 us). Treat the numbers as a lower bound and a ranking --
  they are right about what is dear (NBT, entity scans, ``fill`` volume)
  and cheap (scoreboards, storage) -- not as a prediction of a real tick.

Every value is read at call time, so patching them at runtime works. A tick
budget is 50000 us (50 ms); the server is lagging past that.

For real timings of a *specific* pack, take a ``/perf`` report on a server
running it and read it with :mod:`server_profile`
(``datapack-emulator-cli server-profile``).
"""

from __future__ import annotations

# calibration:begin
#: measured on Minecraft 26.2, 2026-11-06T22:08:03
#: Intel(R) Core(TM) 5 120U (12 threads), Linux 7.2.14-arch1-1
#: openjdk version "27.0.1.0.3" 2026-10-24
#: 35 constants fitted from 69 benchmark runs; the rest are the old guesses x 0.03399
CALIBRATED_ON: str = "26.2"
# calibration:end

TICK_BUDGET_US: float = 50_000.0
"""One Minecraft tick, in microseconds (20 TPS)."""

DEFAULT_COMMAND_COST_US: float = 0.1699
"""Fallback for a command that is not in :data:`COMMAND_COST_US`."""

COMMAND_COST_US: dict[str, float] = {
    # --- parsing / no-op -------------------------------------------------
    "#": 0.0,  # comment line
    "": 0.0,
    # --- chat / logging --------------------------------------------------
    "say": 0.102,
    "me": 0.102,
    "msg": 0.102,
    "tell": 0.102,
    "w": 0.102,
    "tellraw": 0.3808,  # JSON text component resolution
    "title": 0.2719,
    "titleraw": 0.2719,
    "bossbar": 0.3399,
    # --- scoreboard ------------------------------------------------------
    "scoreboard": 0.433,
    "trigger": 0.068,
    # --- control flow ----------------------------------------------------
    "execute": 0.238,  # the dispatch itself; subcommands add their own cost
    "function": 0.1184,  # call overhead only, body is timed separately
    "return": 0.017,
    "schedule": 0.5447,
    # --- tags / teams ----------------------------------------------------
    "tag": 0.3716,
    "team": 0.1699,
    # --- entities --------------------------------------------------------
    "summon": 3.3087,  # builds an entity, adds it to the chunk
    "kill": 3.621,
    "tp": 3.0062,
    "teleport": 0.5098,
    "ride": 0.6798,
    "attribute": 0.3509,
    "effect": 0.5305,
    "damage": 0.8497,
    "spawnpoint": 0.3399,
    "experience": 0.2719,
    "xp": 0.2719,
    "gamemode": 0.2719,
    # --- inventory / items (NBT heavy) -----------------------------------
    "item": 0.3852,
    "give": 3.0589,
    "clear": 2.0393,
    "loot": 4.0786,
    "enchant": 2.0393,
    "recipe": 0.5098,
    # --- NBT / storage ---------------------------------------------------
    "data": 0.5554,  # on storage; entity / block targets add DATA_ENTITY_EXTRA_US
    # --- world -----------------------------------------------------------
    "setblock": 3.2259,
    "fill": 15.8358,  # scales with volume, see VOLUME_COST_US_PER_BLOCK
    "clone": 20.393,
    "place": 16.9942,
    "fillbiome": 13.5953,
    "setworldspawn": 0.3399,
    "forceload": 1.6994,
    "worldborder": 0.1699,
    "time": 0.068,
    "weather": 0.1699,
    "difficulty": 0.068,
    "gamerule": 0.068,
    "seed": 0.034,
    # --- feedback / cosmetic ---------------------------------------------
    "particle": 0.6639,
    "playsound": 0.3399,
    "stopsound": 0.1699,
    "spectate": 0.1699,
    # --- admin (rare in datapacks) ---------------------------------------
    "advancement": 1.3595,
    "datapack": 3.3988,
    "reload": 1699.4161,
    "perf": 0.3399,
    "debug": 0.3399,
}

# Cost added by each ``execute`` subcommand, before selector costs.
SUBCOMMAND_COST_US: dict[str, float] = {
    "align": 0.034,
    "anchored": 0.017,
    "as": 0.0198,
    "at": 0.0198,
    "facing": 0.068,
    "in": 0.034,
    "on": 0.2719,  # relation lookup (vehicle, passengers, owner, ...)
    "positioned": 0.034,
    "rotated": 0.034,
    "summon": 4.0786,
    "if": 0.1407,
    "unless": 0.068,
    "store": 0.2011,
    "run": 0.0,
}

CONDITION_COST_US: dict[str, float] = {
    "score": 0.1319,
    "entity": 0.3782,  # plus the selector cost
    "block": 0.0348,
    "blocks": 10.1965,
    "biome": 0.8497,
    "data": 0.0362,  # storage; entity / block targets add DATA_ENTITY_EXTRA_US
    "predicate": 0.2486,
    "dimension": 0.034,
    "loaded": 0.1699,
    "items": 3.0589,
    "function": 0.034,
}

# --- selectors -----------------------------------------------------------

SELECTOR_BASE_COST_US: dict[str, float] = {
    "@s": 0.0198,  # the executor itself: no search
    "@p": 0.136,  # scans players only
    "@r": 0.136,
    "@a": 0.2145,
    "@e": 0.0,  # scans every loaded entity
    "@n": 0.8497,
}

SELECTOR_PER_ENTITY_COST_US: float = 0.0132
"""Added per entity the selector has to consider (``@e`` scans the world)."""

TYPE_FILTER_DISCOUNT: float = 1.0
"""Share of the per-entity scan cost kept with ``type=``. Measured on 26.2:
``type=`` does not make an ``@e`` scan cheaper (every entity is still
visited), so this is 1.0 -- it narrows what ``nbt=`` has to read, not the scan."""

NBT_FILTER_COST_US: float = 1.4318
"""``nbt={...}``: added per entity scanned, each one serialised to NBT to
compare (by far the dearest selector argument)."""

LIMIT_DISCOUNT: float = 0.5
"""Share of the scan kept with ``limit=``: the scan stops at the first match.
Measured with a match among the first entities, so it is an optimistic bound."""

MACRO_CALL_OVERHEAD_US: float = 0.0405
"""Extra cost of calling a macro (``$``) function: the body is re-compiled."""

VOLUME_COST_US_PER_BLOCK: float = 2.2996
"""``fill`` / ``clone`` / ``fillbiome`` cost per block in the affected volume."""

DATA_ENTITY_EXTRA_US: float = 2.7919
"""Added to ``data`` (and ``execute if data``) on an ``entity`` or ``block``:
the target is serialised to NBT first, which command storage is not."""

FUNCTION_CALL_OVERHEAD_US: float = 1.0
"""Unused. Measured: a function the server starts costs under 2 us beyond its
lines, so a call is charged as the ``function`` command's cost only."""


#: where the active numbers came from: "" for this file, else a calibration file
USER_CALIBRATION: str = ""


_LOADED = False


def ensure_user_calibration() -> str:
    """:func:`load_user_calibration` once per process (the entry points call
    it); a broken file is reported, never fatal."""
    global _LOADED
    if not _LOADED:
        _LOADED = True
        try:
            load_user_calibration()
        except (OSError, ValueError) as exc:
            import logging

            logging.getLogger(__name__).warning("ignoring the cost calibration file: %s", exc)
    return USER_CALIBRATION


def load_user_calibration(path=None) -> str:
    """Apply a ``calibrate`` result (``{"values": {...}}``) on top of the
    defaults: ``path``, else ``$DATAPACK_EMULATOR_COSTS``, else ``costs.json``
    in the data folder. Returns the file applied, or "" when there is none."""
    import json
    import os
    from pathlib import Path

    global USER_CALIBRATION
    if path is None:
        named = os.environ.get("DATAPACK_EMULATOR_COSTS")
        if named:
            path = Path(named)
        else:
            from datapack_emulator.settings.main import PATHS

            path = PATHS.DATA / "costs.json"
    path = Path(path)
    if not path.is_file():
        return ""
    document = json.loads(path.read_text(encoding="utf-8"))
    values = document.get("values", document)
    module = globals()
    for name, value in values.items():
        if name not in module or name.startswith("_") or callable(module[name]):
            continue
        if isinstance(value, dict) and isinstance(module[name], dict):
            module[name].update({key: float(v) for key, v in value.items()})
        elif isinstance(value, (int, float)):
            module[name] = float(value)
    USER_CALIBRATION = str(path)
    return USER_CALIBRATION
