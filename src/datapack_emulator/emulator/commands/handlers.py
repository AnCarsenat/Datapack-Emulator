"""Command implementations: the table of every emulated command.

Each handler takes ``(command, context)`` and returns a
:class:`~datapack_emulator.emulator.commands.result.CommandResult`.  Handlers speak to the
player through ``context.chat`` / ``context.feedback`` / ``context.game_error``
so that game output stays separate from emulator diagnostics.

The handlers live in one module per command group:

* :mod:`.helpers` — selectors, ids, score holders, integer arguments
* :mod:`.chat` — say, me, msg, tellraw, title
* :mod:`.scoreboard` — scoreboard, trigger
* :mod:`.entities` — tag, summon, kill, tp
* :mod:`.control` — function, schedule, return
* :mod:`.data` — data, gamerule
* :mod:`.execute` — execute and its conditions
* :mod:`.items` — give, clear, item, replaceitem, enchant, loot
* :mod:`.misc` — commands that only check their ids
"""

from __future__ import annotations

from datapack_emulator.emulator.commands.chat import (
    cmd_me,
    cmd_msg,
    cmd_say,
    cmd_tellraw,
    cmd_title,
)
from datapack_emulator.emulator.commands.control import cmd_function, cmd_return, cmd_schedule
from datapack_emulator.emulator.commands.data import cmd_data, cmd_gamerule
from datapack_emulator.emulator.commands.entities import (
    cmd_kill,
    cmd_summon,
    cmd_tag,
    cmd_teleport,
)
from datapack_emulator.emulator.commands.execute import (
    cmd_execute,
    condition_count,
    evaluate_condition,
)
from datapack_emulator.emulator.commands.helpers import Handler, checking
from datapack_emulator.emulator.commands.items import ITEM_HANDLERS
from datapack_emulator.emulator.commands.misc import cmd_effect, cmd_noop, cmd_setblock
from datapack_emulator.emulator.commands.scoreboard import cmd_scoreboard, cmd_trigger

#: commands that run without changing the emulated world, and why — for the
#: once-per-run emulator note and the line analysis
UNMODELLED: dict[str, str] = {
    "advancement": "advancements are not modelled",
    "attribute": "attributes are not modelled (queries return 1)",
    "bossbar": "boss bars are not modelled",
    "clone": "blocks are not modelled",
    "damage": "health and damage are not modelled",
    "difficulty": "the difficulty is not modelled",
    "effect": "status effects are not modelled (the effect id is still checked)",
    "experience": "experience is not modelled",
    "fill": "blocks are not modelled",
    "fillbiome": "biomes are not modelled",
    "forceload": "chunks are not modelled",
    "gamemode": "game modes are not modelled (gamemode= in selectors is not checked)",
    "particle": "particles have no effect on the world",
    "place": "blocks and structures are not modelled",
    "playsound": "sounds have no effect on the world",
    "random": "random draws are not modelled: the result is always 1",
    "recipe": "recipes are not modelled",
    "ride": "vehicles and passengers are not modelled",
    "rotate": "use tp or data to turn entities: rotate is not modelled",
    "setblock": "blocks are not modelled (the block id is still checked)",
    "setworldspawn": "the world spawn is not modelled",
    "spawnpoint": "spawn points are not modelled",
    "spectate": "spectating is not modelled",
    "spreadplayers": "spreadplayers does not move entities in the emulator",
    "stopsound": "sounds have no effect on the world",
    "team": "teams are not modelled (team= in selectors is not checked)",
    "tick": "the tick rate is not modelled",
    "time": "the time of day is not modelled (queries return 1)",
    "weather": "the weather is not modelled",
    "worldborder": "the world border is not modelled",
    "xp": "experience is not modelled",
}
#: unmodelled commands that cannot change what a pack's logic sees: no note
COSMETIC = frozenset({"particle", "playsound", "stopsound"})


#: command name -> handler.  Anything vanilla knows but this table does not is
#: reported as "exists in <version> but is not emulated".
HANDLERS: dict[str, Handler] = {
    "say": cmd_say,
    "me": cmd_me,
    "msg": cmd_msg,
    "tell": cmd_msg,
    "w": cmd_msg,
    "teammsg": cmd_say,
    "tm": cmd_say,
    "tellraw": cmd_tellraw,
    "title": cmd_title,
    "scoreboard": cmd_scoreboard,
    "trigger": cmd_trigger,
    "tag": cmd_tag,
    "summon": cmd_summon,
    "kill": cmd_kill,
    "tp": cmd_teleport,
    "teleport": cmd_teleport,
    "execute": cmd_execute,
    "function": cmd_function,
    "schedule": cmd_schedule,
    "return": cmd_return,
    "data": cmd_data,
    "gamerule": cmd_gamerule,
    # dispatched and costed, but no state change is modelled
    "advancement": cmd_noop,
    "attribute": cmd_noop,
    "bossbar": cmd_noop,
    "clone": cmd_noop,
    "damage": cmd_noop,
    "difficulty": cmd_noop,
    "effect": cmd_effect,
    "experience": cmd_noop,
    "fill": cmd_noop,
    "fillbiome": cmd_noop,
    "forceload": cmd_noop,
    "gamemode": cmd_noop,
    "particle": checking("particle", 0, "argument.id.unknown"),
    "place": cmd_noop,
    "playsound": cmd_noop,
    "random": cmd_noop,
    "recipe": cmd_noop,
    "ride": cmd_noop,
    "rotate": cmd_noop,
    "setblock": cmd_setblock,
    "setworldspawn": cmd_noop,
    "spawnpoint": cmd_noop,
    "spectate": cmd_noop,
    "spreadplayers": cmd_noop,
    "stopsound": cmd_noop,
    "team": cmd_noop,
    "tick": cmd_noop,
    "time": cmd_noop,
    "weather": cmd_noop,
    "worldborder": cmd_noop,
    "xp": cmd_noop,
}
HANDLERS.update(ITEM_HANDLERS)

__all__ = [
    "COSMETIC",
    "HANDLERS",
    "UNMODELLED",
    "Handler",
    "condition_count",
    "evaluate_condition",
]
