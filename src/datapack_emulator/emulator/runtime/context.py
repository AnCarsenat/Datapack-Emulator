"""The command source: who runs a command, where, and how deep in the stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource
from datapack_emulator.emulator.runtime.world import Entity, World

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.emulator import Emulator


@dataclass
class ExecutionContext:
    """Vanilla's command source: executor, position, rotation, dimension."""

    emulator: Emulator
    function_id: str = ""
    line: int = 0
    executor: Entity | None = None
    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0])
    dimension: str = "minecraft:overworld"
    depth: int = 0

    @property
    def world(self) -> World:
        return self.emulator.world

    def branch(self, **overrides: Any) -> ExecutionContext:
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

    def render(self, key: str, *arguments: Any) -> str:
        """Render a message with the catalogue of the version being emulated."""
        return self.emulator.messages.render(key, *arguments)

    def feedback(self, key: str, *arguments: Any) -> LogRecord:
        """Vanilla command feedback: shown to whoever typed the command (INFO);
        functions send it nowhere, so there it is kept at DEBUG."""
        return self.emulator.output.log(
            LogSource.GAME,
            LogLevel.DEBUG if self.silent else LogLevel.INFO,
            self.render(key, *arguments),
            **self._fields(key=key),
        )

    @property
    def silent(self) -> bool:
        """Inside a function, vanilla sends command errors nowhere: the line just
        fails and the function carries on. Typed commands (depth 0) show them."""
        return self.depth > 0

    def game_error(self, key: str, *arguments: Any) -> LogRecord:
        """The red text a server sends back when a command fails.

        Recorded at ERROR for a typed command; inside a function, where no player
        would see it, at DEBUG so it stays available without flagging the pack.
        """
        return self.emulator.output.log(
            LogSource.GAME,
            LogLevel.DEBUG if self.silent else LogLevel.ERROR,
            self.render(key, *arguments),
            **self._fields(key=key, failure=True),
        )

    def note_once(
        self, text: str, level: LogLevel = LogLevel.INFO, key: str = ""
    ) -> LogRecord | None:
        """A diagnostic reported once per run, however often the line executes."""
        if text in self.emulator.noted:
            return None
        self.emulator.noted.add(text)
        return self.note(text, level=level, key=key)

    def note(self, text: str, level: LogLevel = LogLevel.WARNING, key: str = "") -> LogRecord:
        """A diagnostic from the emulator itself, not from the game."""
        return self.emulator.output.emulator(text, level=level, **self._fields(key=key))

    def note_key_once(
        self, key: str, *arguments: Any, level: LogLevel = LogLevel.INFO
    ) -> LogRecord | None:
        """A catalogue note reported once per run — for emulator limitations."""
        return self.note_once(self.render(key, *arguments), level=level, key=key)

    def note_key(self, key: str, *arguments: Any, level: LogLevel = LogLevel.WARNING) -> LogRecord:
        """Same, but rendered from the message catalogue."""
        return self.emulator.output.emulator(
            self.render(key, *arguments), level=level, **self._fields(key=key)
        )
