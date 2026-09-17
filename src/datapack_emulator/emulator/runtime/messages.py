"""Vanilla command feedback, word for word.

The baked-in strings are the real ones from
``assets/minecraft/lang/en_us.json`` (via https://github.com/misode/mcmeta), so
what the log shows is what a Minecraft server would send back.  Each entry
keeps its translation key, which the UI shows on the record and which makes
the transcription auditable.

When a client jar is loaded (see :mod:`datapack_emulator.emulator.vanilla`), a
:class:`MessageCatalogue` built from *that version's* ``en_us.json`` takes
priority, so the wording follows the version being emulated instead of the
snapshot below.

``%s`` placeholders are filled positionally by :func:`message`.
"""

from __future__ import annotations

from typing import Any

#: translation key -> en_us string
VANILLA: dict[str, str] = {
    # parsing / dispatch
    "command.unknown.command": "Unknown or incomplete command. See below for error",
    "command.unknown.argument": "Incorrect argument for command",
    "command.exception": "Could not parse command: %s",
    "command.failed": "An unexpected error occurred while trying to execute that command",
    "command.context.here": "<--[HERE]",
    "command.forkLimit": "Maximum number of contexts (%s) reached",
    # selectors and score holders
    "argument.entity.notfound.entity": "No entity was found",
    "argument.entity.notfound.player": "No player was found",
    "argument.entity.options.unknown": "Unknown option '%s'",
    "argument.entity.selector.unknown": "Unknown selector type '%s'",
    "argument.scoreHolder.empty": "No relevant score holders could be found",
    "argument.entity.toomany": (
        "Only one entity is allowed, but the provided selector allows more than one"
    ),
    "arguments.objective.notFound": "Unknown scoreboard objective '%s'",
    "arguments.operation.div0": "Cannot divide by zero",
    # registry ids (checked against the client jar, when one is loaded)
    "argument.id.unknown": "Unknown ID: %s",
    "argument.block.id.invalid": "Unknown block type '%s'",
    "argument.item.id.invalid": "Unknown item '%s'",
    "argument.entity.options.type.invalid": "Invalid or unknown entity type '%s'",
    "argument.resource_or_id.no_such_element": "Can't find element '%s' in registry '%s'",
    # functions and macros
    "arguments.function.unknown": "Unknown function %s",
    "arguments.function.tag.unknown": "Unknown function tag '%s'",
    "commands.function.error.missing_argument": "Missing argument %2$s to function %1$s",
    "commands.function.error.missing_arguments": "Missing arguments to function %s",
    "commands.function.error.argument_not_compound": "Invalid argument type: %s. Expected Compound",
    "commands.function.instantiationFailure": "Failed to instantiate function %s: %s",
    "commands.function.success.single": "Executed %s command(s) from function '%s'",
    "commands.function.scheduled.no_functions": "Can't find any functions for name %s",
    # scoreboard
    "commands.scoreboard.objectives.add.success": "Created new objective %s",
    "commands.scoreboard.objectives.add.duplicate": "An objective already exists by that name",
    "commands.scoreboard.objectives.remove.success": "Removed objective %s",
    "commands.scoreboard.objectives.list.empty": "There are no objectives",
    "commands.scoreboard.objectives.list.success": "There are %s objective(s): %s",
    "commands.scoreboard.objectives.display.set": "Set display slot %s to show objective %s",
    "commands.scoreboard.objectives.display.cleared": "Cleared any objectives in display slot %s",
    "commands.scoreboard.objectives.display.alreadySet": (
        "Nothing changed. That display slot is already showing that objective"
    ),
    "commands.scoreboard.objectives.display.alreadyEmpty": (
        "Nothing changed. That display slot is already empty"
    ),
    "commands.scoreboard.objectives.modify.displayname": "Changed the display name of %s to %s",
    "commands.scoreboard.objectives.modify.rendertype": "Changed the render type of objective %s",
    "commands.scoreboard.players.get.null": "Can't get value of %s for %s; none is set",
    "commands.scoreboard.players.get.success": "%s has %s %s",
    "commands.scoreboard.players.set.success.single": "Set %s for %s to %s",
    "commands.scoreboard.players.set.success.multiple": "Set %s for %s entities to %s",
    "commands.scoreboard.players.add.success.single": "Added %s to %s for %s (now %s)",
    "commands.scoreboard.players.add.success.multiple": "Added %s to %s for %s entities",
    "commands.scoreboard.players.remove.success.single": "Removed %s from %s for %s (now %s)",
    "commands.scoreboard.players.remove.success.multiple": "Removed %s from %s for %s entities",
    "commands.scoreboard.players.reset.all.single": "Reset scores for %s",
    "commands.scoreboard.players.reset.all.multiple": "Reset scores for %s entities",
    "commands.scoreboard.players.reset.specific.single": "Reset %s for %s",
    "commands.scoreboard.players.reset.specific.multiple": "Reset %s for %s entities",
    "commands.scoreboard.players.enable.success.single": "Enabled trigger %s for %s",
    "commands.scoreboard.players.enable.success.multiple": "Enabled trigger %s for %s entities",
    "commands.scoreboard.players.enable.failed": "Nothing changed. That trigger is already enabled",
    "commands.scoreboard.players.enable.invalid": "Enable only works on trigger-objectives",
    "commands.scoreboard.players.operation.success.single": "Set %s for %s to %s",
    "commands.scoreboard.players.operation.success.multiple": "Updated %s for %s entities",
    "commands.scoreboard.players.list.empty": "There are no tracked entities",
    "commands.scoreboard.players.list.success": "There are %s tracked entity/entities: %s",
    "commands.scoreboard.players.list.entity.empty": "%s has no scores to show",
    "commands.scoreboard.players.list.entity.success": "%s has %s score(s):",
    "commands.scoreboard.players.list.entity.entry": "%s: %s",
    "arguments.objective.readonly": "Scoreboard objective '%s' is read-only",
    "arguments.operation.invalid": "Invalid operation",
    "argument.criteria.invalid": "Unknown criterion '%s'",
    "argument.integer.low": "Integer must not be less than %s, found %s",
    # trigger
    "commands.trigger.failed.unprimed": "You cannot trigger this objective yet",
    "commands.trigger.failed.invalid": "You can only trigger objectives that are 'trigger' type",
    "commands.trigger.simple.success": "Triggered %s",
    "commands.trigger.set.success": "Triggered %s (set value to %s)",
    "commands.trigger.add.success": "Triggered %s (added %s to value)",
    "permissions.requires.player": "A player is required to run this command here",
    "parsing.int.invalid": "Invalid integer '%s'",
    # tags
    "commands.tag.add.success.single": "Added tag '%s' to %s",
    "commands.tag.add.failed": "Target either already has the tag or has too many tags",
    "commands.tag.remove.success.single": "Removed tag '%s' from %s",
    "commands.tag.remove.failed": "Target does not have this tag",
    # entities
    "commands.summon.success": "Summoned new %s",
    "commands.summon.failed": "Unable to summon entity",
    "commands.give.success.single": "Gave %s %s to %s",
    "commands.give.success.multiple": "Gave %s %s to %s players",
    "commands.give.failed.toomanyitems": "Can't give more than %s of %s",
    "commands.clear.success.single": "Removed %s item(s) from player %s",
    "commands.clear.success.multiple": "Removed %s item(s) from %s players",
    "commands.clear.failed.single": "No items were found on player %s",
    "commands.clear.failed.multiple": "No items were found on %s players",
    "commands.clear.test.single": "Found %s matching item(s) on player %s",
    "commands.clear.test.multiple": "Found %s matching item(s) on %s players",
    "commands.item.entity.set.success.single": "Replaced a slot on %s with %s",
    "commands.item.entity.set.success.multiple": "Replaced a slot on %s entities with %s",
    "commands.item.target.no_changes": "No targets accepted item into slot %s",
    "commands.item.source.no_such_slot": "The source does not have slot %s",
    "commands.enchant.success.single": "Applied enchantment %s to %s's item",
    "commands.enchant.success.multiple": "Applied enchantment %s to %s entities",
    "commands.enchant.failed": (
        "Nothing changed. Targets either have no item in their hands or the enchantment "
        "could not be applied"
    ),
    "commands.enchant.failed.itemless": "%s is not holding any item",
    "commands.enchant.failed.incompatible": "%s cannot support that enchantment",
    "arguments.item.overstacked": "%s can only stack up to %s",
    "argument.integer.big": "Integer must not be more than %s, found %s",
    "commands.drop.success.single": "Dropped %s %s",
    "commands.drop.success.multiple": "Dropped %s items",
    "argument.player.entities": (
        "Only players may be affected by this command, but the provided selector includes entities"
    ),
    "slot.unknown": "Unknown slot '%s'",
    "enchantment.unknown": "Unknown enchantment: %s",
    "commands.summon.failed.uuid": "Unable to summon entity due to duplicate UUIDs",
    "commands.kill.success.single": "Killed %s",
    "commands.kill.success.multiple": "Killed %s entities",
    "commands.teleport.success.entity.single": "Teleported %s to %s",
    # data
    "commands.data.get.unknown": "Can't get %s; tag doesn't exist",
    "arguments.nbtpath.nothing_found": "Found no elements matching %s",
    "argument.scoreboardDisplaySlot.invalid": "Unknown display slot '%s'",
    "commands.data.entity.query": "%s has the following entity data: %s",
    "commands.data.entity.get": "%s on %s after scale factor of %s is %s",
    "commands.data.entity.modified": "Modified entity data of %s",
    "commands.data.entity.invalid": "Unable to modify player data",
    "commands.data.storage.query": "Storage %s has the following contents: %s",
    "commands.data.storage.get": "%s in storage %s after scale factor of %s is %s",
    "commands.data.storage.modified": "Modified storage %s",
    "commands.data.get.invalid": "Can't get %s; only numeric tags are allowed",
    "commands.data.merge.failed": (
        "Nothing changed. The specified properties already have these values"
    ),
    "commands.data.modify.expected_list": "Expected a list: got %s",
    "commands.data.modify.expected_object": "Expected an object: got %s",
    "commands.data.modify.expected_value": "Expected a value: got %s",
    # blocks
    "argument.pos.outofworld": "That position is out of this world!",
    "argument.pos.unloaded": "That position is not loaded",
    "argument.pos.mixed": "Cannot mix world & local coordinates (everything must either use ^ or not)",
    "argument.block.property.unknown": "Block %s does not have property '%s'",
    "argument.block.property.invalid": "Block %s does not accept '%s' for %s property",
    "argument.block.tag.disallowed": "Tags aren't allowed here, only actual blocks",
    "commands.setblock.success": "Changed the block at %s, %s, %s",
    "commands.setblock.failed": "Could not set the block",
    "commands.fill.success": "Successfully filled %s block(s)",
    "commands.fill.failed": "No blocks were filled",
    "commands.fill.toobig": "Too many blocks in the specified area (maximum %s, specified %s)",
    "commands.clone.success": "Successfully cloned %s block(s)",
    "commands.clone.failed": "No blocks were cloned",
    "commands.clone.overlap": "The source and destination areas cannot overlap",
    "commands.clone.toobig": "Too many blocks in the specified area (maximum %s, specified %s)",
    "commands.execute.blocks.toobig": (
        "Too many blocks in the specified area (maximum %s, specified %s)"
    ),
    "commands.data.block.query": "%s, %s, %s has the following block data: %s",
    "commands.data.block.get": "%s in block %s, %s, %s after scale factor of %s is %s",
    "commands.data.block.modified": "Modified block data of %s, %s, %s",
    "commands.data.block.invalid": "The target block is not a block entity",
    "commands.item.block.set.success": "Replaced a slot at %s, %s, %s with %s",
    "commands.item.target.not_a_container": "Target position %s, %s, %s is not a container",
    "commands.item.source.not_a_container": "Source position %s, %s, %s is not a container",
    "commands.item.target.no_such_slot": "The target does not have slot %s",
    # server state, players and teams (from the 26.2 en_us.json)
    "argument.color.invalid": "Unknown color '%s'",
    "argument.float.big": "Float must not be more than %s: found %s",
    "argument.float.low": "Float must not be less than %s: found %s",
    "argument.player.toomany": "Only one player is allowed, but the provided selector allows more than one",
    "argument.time.invalid_tick_count": "The tick count must be non-negative",
    "argument.time.invalid_unit": "Invalid unit",
    "commands.defaultgamemode.success": "The default game mode is now %s",
    "commands.difficulty.failure": "The difficulty did not change; it is already set to %s",
    "commands.difficulty.query": "The difficulty is %s",
    "commands.difficulty.success": "The difficulty has been set to %s",
    "commands.experience.add.levels.success.multiple": "Gave %s experience levels to %s players",
    "commands.experience.add.levels.success.single": "Gave %s experience levels to %s",
    "commands.experience.add.points.success.multiple": "Gave %s experience points to %s players",
    "commands.experience.add.points.success.single": "Gave %s experience points to %s",
    "commands.experience.query.levels": "%s has %s experience levels",
    "commands.experience.query.points": "%s has %s experience points",
    "commands.experience.set.levels.success.multiple": "Set %s experience levels on %s players",
    "commands.experience.set.levels.success.single": "Set %s experience levels on %s",
    "commands.experience.set.points.invalid": "Cannot set experience points above the maximum points for the player's current level",
    "commands.experience.set.points.success.multiple": "Set %s experience points on %s players",
    "commands.experience.set.points.success.single": "Set %s experience points on %s",
    "commands.forceload.added.failure": "No chunks were marked for force loading",
    "commands.forceload.added.multiple": "Marked %s chunks in %s from %s to %s to be force loaded",
    "commands.forceload.added.none": "No force loaded chunks were found in %s",
    "commands.forceload.added.single": "Marked chunk %s in %s to be force loaded",
    "commands.forceload.list.multiple": "%s force loaded chunks were found in %s at: %s",
    "commands.forceload.list.single": "A force loaded chunk was found in %s at: %s",
    "commands.forceload.query.failure": "Chunk at %s in %s is not marked for force loading",
    "commands.forceload.query.success": "Chunk at %s in %s is marked for force loading",
    "commands.forceload.removed.all": "Unmarked all force loaded chunks in %s",
    "commands.forceload.removed.failure": "No chunks were removed from force loading",
    "commands.forceload.removed.multiple": "Unmarked %s chunks in %s from %s to %s for force loading",
    "commands.forceload.removed.single": "Unmarked chunk %s in %s for force loading",
    "commands.forceload.toobig": "Too many chunks in the specified area (maximum %s, but specified %s)",
    "commands.gamemode.success.other": "Set %s's game mode to %s",
    "commands.gamemode.success.self": "Set own game mode to %s",
    "commands.list.nameAndId": "%s (%s)",
    "commands.list.players": "There are %s of a max of %s players online: %s",
    "commands.random.error.range_too_large": "The range of the random value must be at most 2147483646",
    "commands.random.error.range_too_small": "The range of the random value must be at least 2",
    "commands.random.reset.all.success": "Reset %s random sequence(s)",
    "commands.random.reset.success": "Reset random sequence %s",
    "commands.random.roll": "%s rolled %s (from %s to %s)",
    "commands.random.sample.success": "Randomized value: %s",
    "commands.seed.success": "Seed: %s",
    "commands.setworldspawn.success": "Set the world spawn point to %s, %s, %s [%s]",
    "commands.spawnpoint.success.multiple": "Set spawn point to %s, %s, %s [%s] in %s for %s players",
    "commands.spawnpoint.success.single": "Set spawn point to %s, %s, %s [%s] in %s for %s",
    "commands.team.add.duplicate": "A team already exists by that name",
    "commands.team.add.success": "Created team %s",
    "commands.team.empty.success": "Removed %s member(s) from team %s",
    "commands.team.empty.unchanged": "Nothing changed. That team is already empty",
    "commands.team.join.success.multiple": "Added %s members to team %s",
    "commands.team.join.success.single": "Added %s to team %s",
    "commands.team.leave.success.multiple": "Removed %s members from any team",
    "commands.team.leave.success.single": "Removed %s from any team",
    "commands.team.list.members.empty": "There are no members on team %s",
    "commands.team.list.members.success": "Team %s has %s member(s): %s",
    "commands.team.list.teams.empty": "There are no teams",
    "commands.team.list.teams.success": "There are %s team(s): %s",
    "commands.team.option.collisionRule.success": 'Collision rule for team %s is now "%s"',
    "commands.team.option.collisionRule.unchanged": "Nothing changed. Collision rule is already that value",
    "commands.team.option.color.clear.success": "Cleared the color for team %s",
    "commands.team.option.color.success": "Updated the color for team %s to %s",
    "commands.team.option.color.unchanged": "Nothing changed. That team already has that color",
    "commands.team.option.deathMessageVisibility.success": 'Death message visibility for team %s is now "%s"',
    "commands.team.option.deathMessageVisibility.unchanged": "Nothing changed. Death message visibility is already that value",
    "commands.team.option.friendlyfire.alreadyDisabled": "Nothing changed. Friendly fire is already disabled for that team",
    "commands.team.option.friendlyfire.alreadyEnabled": "Nothing changed. Friendly fire is already enabled for that team",
    "commands.team.option.friendlyfire.disabled": "Disabled friendly fire for team %s",
    "commands.team.option.friendlyfire.enabled": "Enabled friendly fire for team %s",
    "commands.team.option.name.success": "Updated the name of team %s",
    "commands.team.option.name.unchanged": "Nothing changed. That team already has that name",
    "commands.team.option.nametagVisibility.success": 'Nametag visibility for team %s is now "%s"',
    "commands.team.option.nametagVisibility.unchanged": "Nothing changed. Nametag visibility is already that value",
    "commands.team.option.prefix.success": "Team prefix set to %s",
    "commands.team.option.seeFriendlyInvisibles.alreadyDisabled": "Nothing changed. That team already can't see invisible teammates",
    "commands.team.option.seeFriendlyInvisibles.alreadyEnabled": "Nothing changed. That team can already see invisible teammates",
    "commands.team.option.seeFriendlyInvisibles.disabled": "Team %s can no longer see invisible teammates",
    "commands.team.option.seeFriendlyInvisibles.enabled": "Team %s can now see invisible teammates",
    "commands.team.option.suffix.success": "Team suffix set to %s",
    "commands.team.remove.success": "Removed team %s",
    "commands.teammsg.failed.noteam": "You must be on a team to message your team",
    "commands.tick.rate.success": "Set the target tick rate to %s per second",
    "commands.tick.status.frozen": "The game is frozen",
    "commands.tick.status.running": "The game is running normally",
    "commands.time.query": "The time is %s",
    "commands.time.set": "Set the time to %s",
    "commands.weather.set.clear": "Set the weather to clear",
    "commands.weather.set.rain": "Set the weather to rain",
    "commands.weather.set.thunder": "Set the weather to rain & thunder",
    "commands.worldborder.center.failed": "Nothing changed. The world border is already centered there",
    "commands.worldborder.center.success": "Set the center of the world border to %s, %s",
    "commands.worldborder.damage.amount.failed": "Nothing changed. The world border damage is already that amount",
    "commands.worldborder.damage.amount.success": "Set the world border damage to %s per block each second",
    "commands.worldborder.damage.buffer.failed": "Nothing changed. The world border damage buffer is already that distance",
    "commands.worldborder.damage.buffer.success": "Set the world border damage buffer to %s block(s)",
    "commands.worldborder.get": "The world border is currently %s block(s) wide",
    "commands.worldborder.set.failed.big": "World border cannot be bigger than %s blocks wide",
    "commands.worldborder.set.failed.far": "World border cannot be further out than %s blocks",
    "commands.worldborder.set.failed.nochange": "Nothing changed. The world border is already that size",
    "commands.worldborder.set.failed.small": "World border cannot be smaller than 1 block wide",
    "commands.worldborder.set.grow": "Growing the world border to %s blocks wide over %s seconds",
    "commands.worldborder.set.immediate": "Set the world border to %s block(s) wide",
    "commands.worldborder.set.shrink": "Shrinking the world border to %s block(s) wide over %s second(s)",
    "commands.worldborder.warning.distance.failed": "Nothing changed. The world border warning is already that distance",
    "commands.worldborder.warning.distance.success": "Set the world border warning distance to %s block(s)",
    "commands.worldborder.warning.time.failed": "Nothing changed. The world border warning is already that amount of time",
    "commands.worldborder.warning.time.success": "Set the world border warning time to %s second(s)",
    "gameMode.adventure": "Adventure Mode",
    "gameMode.creative": "Creative Mode",
    "gameMode.spectator": "Spectator Mode",
    "gameMode.survival": "Survival Mode",
    "options.difficulty.easy": "Easy",
    "options.difficulty.hard": "Hard",
    "options.difficulty.normal": "Normal",
    "options.difficulty.peaceful": "Peaceful",
    "parsing.bool.invalid": "Invalid boolean: expected 'true' or 'false' but found '%s'",
    "parsing.float.invalid": "Invalid float '%s'",
    "permissions.requires.entity": "An entity is required to run this command here",
    "team.notFound": "Unknown team '%s'",
    # schedule
    "commands.schedule.created.function": "Scheduled function '%s' in %s tick(s) at gametime %s",
    "commands.schedule.cleared.success": "Removed %s schedule(s) with ID %s",
    "commands.schedule.cleared.failure": "No schedules with ID %s",
    "commands.schedule.same_tick": "Can't schedule for current tick",
    # execute
    "commands.execute.conditional.pass": "Test passed",
    "commands.execute.conditional.fail": "Test failed",
    "commands.execute.conditional.pass_count": "Test passed. Count: %s",
    "commands.execute.conditional.fail_count": "Test failed. Count: %s",
}

#: messages the vanilla client has no exact string for; ours, marked as ours.
OURS: dict[str, str] = {
    "emulator.depth": "Function call depth limit (%s) reached in %s",
    "emulator.chain_length": "maxCommandChainLength (%s) reached, stopping the tick",
    "emulator.unimplemented": "'%s' exists in %s but this emulator does not run it",
    "emulator.not_modelled": "'%s' runs, but %s",
    "emulator.unavailable": "'%s' does not exist in %s (added in %s)",
    "emulator.removed": "'%s' was removed in %s",
    "emulator.condition": "condition '%s' is not emulated, treated as false",
    "emulator.over_budget": "tick %s took an estimated %s ms, over the %s ms budget",
}


class MessageCatalogue:
    """The strings of one version: its ``en_us.json`` over the baked-in table."""

    def __init__(self, lang: dict[str, str] | None = None, source: str = "built-in"):
        self.lang: dict[str, str] = lang or {}
        self.source = source

    def template(self, key: str) -> str | None:
        return self.lang.get(key) or VANILLA.get(key) or OURS.get(key)

    def render(self, key: str, *arguments: Any) -> str:
        return _render(self.template(key), key, *arguments)

    def __repr__(self) -> str:
        return f"<MessageCatalogue {self.source} strings={len(self.lang)}>"


#: used when no client jar is loaded
DEFAULT = MessageCatalogue()


def message(key: str, *arguments: Any) -> str:
    """Render ``key`` with ``arguments`` from the built-in table."""
    return DEFAULT.render(key, *arguments)


def _render(template: str | None, key: str, *arguments: Any) -> str:
    if template is None:
        return f"{key} {' '.join(str(argument) for argument in arguments)}".strip()
    # vanilla uses both "%s" and positional "%1$s" forms
    if "$s" in template:
        for index, argument in enumerate(arguments, start=1):
            template = template.replace(f"%{index}$s", str(argument))
        return template
    try:
        return template % tuple(str(argument) for argument in arguments)
    except TypeError:
        return template


def unknown_command(command_name: str, catalogue: MessageCatalogue | None = None) -> str:
    """What the game prints for a command it cannot parse, ``<--[HERE]`` and all."""
    catalogue = catalogue or DEFAULT
    head = catalogue.render("command.unknown.command")
    return f"{head}\n{command_name}{catalogue.render('command.context.here')}"
