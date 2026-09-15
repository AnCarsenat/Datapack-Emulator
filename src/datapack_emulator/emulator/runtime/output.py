"""The output bus: everything the app, the emulator and the game have to say.

Three sources are kept apart on purpose:

``LogSource.APP``
    what this program does — loading a pack, writing a report, running a
    version matrix.
``LogSource.EMULATOR``
    what the emulation engine notices — an unimplemented command, a macro with
    no argument, a tick over budget.  These are *our* diagnostics.
``LogSource.GAME``
    what Minecraft itself would print: chat from ``say``/``tellraw``, command
    feedback, and the red error text a real server would send back.

Every record carries the tick, the function and the line it came from, so the
UI can filter and sort without re-parsing text.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, IntEnum


class LogSource(str, Enum):
    APP = "app"
    EMULATOR = "emulator"
    GAME = "game"

    def __str__(self) -> str:
        return self.value


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class LogRecord:
    source: LogSource
    level: LogLevel
    message: str
    tick: int | None = None
    function: str = ""
    line: int = 0
    command: str = ""
    version: str = ""
    #: vanilla translation key, when the message mirrors one (see messages.py)
    key: str = ""
    #: a command failed; set whatever the level (silent failures inside a
    #: function are DEBUG, typed commands ERROR)
    failure: bool = False
    #: who reads it in game: a player name, "*" for everyone (say, me), or ""
    #: for records that are not chat
    recipient: str = ""

    @property
    def origin(self) -> str:
        if self.function and self.line:
            return f"{self.function}:{self.line}"
        return self.function

    def format(self) -> str:
        head = f"[{self.tick if self.tick is not None else '-'}]"
        where = f" {self.origin}" if self.origin else ""
        return f"{head} {self.source}/{self.level.label}{where}: {self.message}"


def trim_records(records: list[LogRecord], keep: int) -> list[LogRecord]:
    """Drop records until ``keep`` remain: the oldest of the lowest level first.

    Long runs log mostly debug records (feedback functions send nowhere,
    silent failures); dropping those before anything else keeps the chat,
    warnings and errors that were visible from disappearing.
    """
    excess = len(records) - keep
    if excess <= 0:
        return records
    dropped: set[int] = set()
    for level in sorted({record.level for record in records}):
        for index, record in enumerate(records):
            if excess <= 0:
                break
            if record.level == level:
                dropped.add(index)
                excess -= 1
        if excess <= 0:
            break
    return [record for index, record in enumerate(records) if index not in dropped]


class OutputBus:
    """Collects :class:`LogRecord` objects and fans them out to listeners."""

    def __init__(self, limit: int = 200_000):
        self.records: list[LogRecord] = []
        self.listeners: list[Callable[[LogRecord], None]] = []
        self.limit = limit
        self.counts: dict[tuple[LogSource, LogLevel], int] = {}
        #: identical messages past this count are dropped within one tick
        self.repeat_limit = 5
        self._repeats: dict[str, int] = {}
        self._tick: int | None = None

    # -- emitting ---------------------------------------------------------

    def set_tick(self, tick: int | None) -> None:
        if tick != self._tick:
            self._repeats.clear()
        self._tick = tick

    def emit(self, record: LogRecord) -> LogRecord:
        seen = self._repeats.get(record.message, 0) + 1
        self._repeats[record.message] = seen
        if seen > self.repeat_limit:
            if seen != self.repeat_limit + 1:
                return record
            record = dataclasses.replace(
                record,
                message=f"{record.message}  (repeated; further copies suppressed this tick)",
            )

        self.records.append(record)
        if len(self.records) > self.limit:
            self.records[:] = trim_records(self.records, self.limit * 3 // 4)
        counter = (record.source, record.level)
        self.counts[counter] = self.counts.get(counter, 0) + 1
        for listener in list(self.listeners):
            listener(record)
        return record

    def log(
        self,
        source: LogSource,
        level: LogLevel,
        message: str,
        **fields,
    ) -> LogRecord:
        fields.setdefault("tick", self._tick)
        return self.emit(LogRecord(source=source, level=level, message=message, **fields))

    # convenience wrappers -------------------------------------------------

    def app(self, message: str, level: LogLevel = LogLevel.INFO, **fields) -> LogRecord:
        return self.log(LogSource.APP, level, message, **fields)

    def emulator(self, message: str, level: LogLevel = LogLevel.INFO, **fields) -> LogRecord:
        return self.log(LogSource.EMULATOR, level, message, **fields)

    def game(self, message: str, level: LogLevel = LogLevel.INFO, **fields) -> LogRecord:
        return self.log(LogSource.GAME, level, message, **fields)

    def game_error(self, message: str, **fields) -> LogRecord:
        return self.log(LogSource.GAME, LogLevel.ERROR, message, **fields)

    # -- reading ----------------------------------------------------------

    def filtered(
        self,
        sources: Iterable[LogSource] | None = None,
        min_level: LogLevel = LogLevel.DEBUG,
        text: str = "",
    ) -> list[LogRecord]:
        wanted = set(sources) if sources is not None else set(LogSource)
        needle = text.lower()
        return [
            record
            for record in self.records
            if record.source in wanted
            and record.level >= min_level
            and (not needle or needle in record.message.lower())
        ]

    def count(self, source: LogSource, min_level: LogLevel = LogLevel.DEBUG) -> int:
        return sum(
            value
            for (record_source, level), value in self.counts.items()
            if record_source == source and level >= min_level
        )

    @property
    def chat(self) -> list[str]:
        """Just what a player would have seen in chat."""
        return [
            record.message
            for record in self.records
            if record.source is LogSource.GAME and record.level < LogLevel.ERROR
        ]

    def clear(self) -> None:
        self.records.clear()
        self.counts.clear()
        self._repeats.clear()
