"""Vanilla command feedback, word for word.

The baked-in strings are the real ones from
``assets/minecraft/lang/en_us.json`` (via https://github.com/misode/mcmeta), so
what the log shows is what a Minecraft server would send back.  Each entry
keeps its translation key, which the UI shows on the record and which makes
the transcription auditable.

When a client jar is loaded (see :mod:`src.emulator.vanilla`), a
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
    "commands.scoreboard.players.get.null": "Can't get value of %s for %s; none is set",
    "commands.scoreboard.players.get.success": "%s has %s %s",
    "commands.scoreboard.players.set.success.single": "Set %s for %s to %s",
    "commands.scoreboard.players.add.success.single": "Added %s to %s for %s (now %s)",
    # trigger
    "commands.trigger.failed.unprimed": "You cannot trigger this objective yet",
    "commands.trigger.failed.invalid": "You can only trigger objectives that are 'trigger' type",
    "commands.trigger.simple.success": "Triggered %s",
    # tags
    "commands.tag.add.success.single": "Added tag '%s' to %s",
    "commands.tag.add.failed": "Target either already has the tag or has too many tags",
    "commands.tag.remove.success.single": "Removed tag '%s' from %s",
    "commands.tag.remove.failed": "Target does not have this tag",
    # entities
    "commands.summon.success": "Summoned new %s",
    "commands.summon.failed": "Unable to summon entity",
    "commands.kill.success.single": "Killed %s",
    "commands.kill.success.multiple": "Killed %s entities",
    "commands.teleport.success.entity.single": "Teleported %s to %s",
    # data
    "commands.data.get.unknown": "Can't get %s; tag doesn't exist",
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
