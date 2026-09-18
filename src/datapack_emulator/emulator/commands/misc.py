"""Commands that change no emulated state but still check their ids."""

from __future__ import annotations

from datapack_emulator.emulator.commands.helpers import require_id
from datapack_emulator.emulator.commands.parser import Command
from datapack_emulator.emulator.commands.result import CommandResult
from datapack_emulator.emulator.runtime.context import ExecutionContext


def cmd_noop(command: Command, context: ExecutionContext) -> CommandResult:
    """Counted and costed, but no world state change is modelled."""
    return CommandResult(success=True, value=1)


def cmd_effect(command: Command, context: ExecutionContext) -> CommandResult:
    """``effect give <targets> <effect>`` / ``effect clear``."""
    arguments = command.arguments
    if (
        len(arguments) >= 3
        and arguments[0] == "give"
        and not require_id(context, "mob_effect", arguments[2])
    ):
        return CommandResult.failure()
    return CommandResult(success=True, value=1)
