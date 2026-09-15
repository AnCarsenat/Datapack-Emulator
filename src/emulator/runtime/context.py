"""The command source: who runs a command, where, and how deep in the stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from src.emulator.runtime.messages import message
from src.emulator.runtime.output import LogLevel, LogRecord, LogSource
from src.emulator.runtime.world import Entity, World

if TYPE_CHECKING:  # pragma: no cover
    from src.emulator.runtime.emulator import Emulator


@dataclass
class ExecutionContext:
    """Vanilla's command source: executor, position, rotation, dimension."""

    emulator: "Emulator"
    function_id: str = ""
    line: int = 0
    executor: Optional[Entity] = None
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0])
    dimension: str = "minecraft:overworld"
    depth: int = 0

    @property
    def world(self) -> World:
        return self.emulator.world

    def branch(self, **overrides: Any) -> "ExecutionContext":
        return ExecutionContext(
            emulator=self.emulator,
            function_id=overrides.get("function_id", self.function_id),
            line=overrides.get("line", self.line),
            executor=overrides.get("executor", self.executor),
            position=list(overrides.get("position", self.position)),
            rotation=list(overrides.get("rotation", self.rotation)),
            dimension=overrides.get("dimension", self.dimension),
            depth=overrides.get("depth", self.depth),
        )

    # -- output -----------------------------------------------------------

    def _fields(self, **extra: Any) -> dict[str, Any]:
        fields = {
            "function": self.function_id,
            "line": self.line,
            "version": self.emulator.version.id,
        }
        fields.update(extra)
        return fields

    def chat(self, text: str, **extra: Any) -> LogRecord:
        """Something a player would read in chat."""
        return self.emulator.output.game(text, **self._fields(**extra))

    def feedback(self, key: str, *arguments: Any) -> LogRecord:
        """Vanilla command feedback, shown when sendCommandFeedback is on."""
        level = (
            LogLevel.DEBUG
            if self.world.gamerules.get("sendCommandFeedback", "true") == "true"
            else LogLevel.DEBUG
        )
        return self.emulator.output.log(
            LogSource.GAME, level, message(key, *arguments), **self._fields(key=key)
        )

    def game_error(self, key: str, *arguments: Any) -> LogRecord:
        """The red text a server sends back when a command fails."""
        return self.emulator.output.game_error(
            message(key, *arguments), **self._fields(key=key)
        )

    def note(self, text: str, level: LogLevel = LogLevel.WARNING) -> LogRecord:
        """A diagnostic from the emulator itself, not from the game."""
        return self.emulator.output.emulator(text, level=level, **self._fields())

    def note_key(
        self, key: str, *arguments: Any, level: LogLevel = LogLevel.WARNING
    ) -> LogRecord:
        """Same, but rendered from the message catalogue."""
        return self.emulator.output.emulator(
            message(key, *arguments), level=level, **self._fields(key=key)
        )
