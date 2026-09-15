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
