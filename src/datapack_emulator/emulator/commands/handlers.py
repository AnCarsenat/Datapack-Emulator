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
* :mod:`.blocks` — setblock, fill, clone
* :mod:`.state` — time, weather, difficulty, worldborder, random, seed, list, tick,
  forceload, setworldspawn, spawnpoint
* :mod:`.players` — gamemode, defaultgamemode, experience, team, teammsg
* :mod:`.living` — effect, attribute, damage, ride
* :mod:`.bossbar` — bossbar
* :mod:`.misc` — commands that only check their ids
"""

from __future__ import annotations

from datapack_emulator.emulator.commands.blocks import cmd_clone, cmd_fill, cmd_setblock
from datapack_emulator.emulator.commands.bossbar import BOSSBAR_HANDLERS
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
from datapack_emulator.emulator.commands.living import LIVING_HANDLERS
from datapack_emulator.emulator.commands.misc import cmd_noop
from datapack_emulator.emulator.commands.players import PLAYER_HANDLERS
from datapack_emulator.emulator.commands.scoreboard import cmd_scoreboard, cmd_trigger
from datapack_emulator.emulator.commands.state import STATE_HANDLERS

#: commands that run without changing the emulated world, and why — for the
#: once-per-run emulator note and the line analysis
UNMODELLED: dict[str, str] = {
    "advancement": "advancements are not modelled",
    "fillbiome": "biomes are not modelled",
    "particle": "particles have no effect on the world",
    "place": "features and structures are not modelled",
    "playsound": "sounds have no effect on the world",
    "recipe": "recipes are not modelled",
    "rotate": "use tp or data to turn entities: rotate is not modelled",
    "spectate": "spectating is not modelled",
    "spreadplayers": "spreadplayers does not move entities in the emulator",
    "stopsound": "sounds have no effect on the world",
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
    "clone": cmd_clone,
    "fill": cmd_fill,
    "fillbiome": cmd_noop,
    "particle": checking("particle", 0, "argument.id.unknown"),
    "place": cmd_noop,
    "playsound": cmd_noop,
    "recipe": cmd_noop,
    "rotate": cmd_noop,
    "setblock": cmd_setblock,
    "spectate": cmd_noop,
    "spreadplayers": cmd_noop,
    "stopsound": cmd_noop,
}
HANDLERS.update(ITEM_HANDLERS)
HANDLERS.update(STATE_HANDLERS)
HANDLERS.update(PLAYER_HANDLERS)
HANDLERS.update(LIVING_HANDLERS)
HANDLERS.update(BOSSBAR_HANDLERS)

__all__ = [
    "COSMETIC",
    "HANDLERS",
    "UNMODELLED",
    "Handler",
    "condition_count",
    "evaluate_condition",
]
