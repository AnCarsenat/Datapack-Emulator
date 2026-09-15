"""What commands exist in a given Minecraft version, and who runs them.

Three states a command name can be in for a version:

* **emulated** — vanilla has it and :mod:`handlers` implements it
* **known** — vanilla has it, we only dispatch and cost it
* **unavailable** — vanilla does not have it in that version, so the game would
  answer ``Unknown or incomplete command``

The vanilla side of that answer comes from the generated table in
:mod:`src.emulator.version_data`, so it is the real command list of the real
version, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from src.emulator import versions
from src.emulator.commands.handlers import HANDLERS, Handler
from src.emulator.versions import Version


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: Handler | None
    available: bool
    since: Version | None
    removed: Version | None

    @property
    def emulated(self) -> bool:
        return self.available and self.handler is not None


class CommandSet:
    """The commands of one version, resolved once and cached."""

    def __init__(self, version: Version):
        self.version = version
        self._vanilla = versions.vanilla_commands(version)
        self._specs: dict[str, CommandSpec] = {}

    def spec(self, name: str) -> CommandSpec:
        cached = self._specs.get(name)
        if cached is not None:
            return cached
        feature = f"command:{name}"
        available = name in self._vanilla
        spec = CommandSpec(
            name=name,
            handler=HANDLERS.get(name),
            available=available,
            since=versions.since_of(feature),
            removed=self._removed(feature),
        )
        self._specs[name] = spec
        return spec

    @staticmethod
    def _removed(feature: str) -> Version | None:
        from src.emulator.version_data import FEATURE_UNTIL

        until = FEATURE_UNTIL.get(feature)
        return versions.parse(until) if until else None

    # -- feature queries --------------------------------------------------

    def supports(self, feature: str) -> bool:
        return versions.supports(self.version, feature)

    def missing_features(self, needed: set[str]) -> list[tuple[str, Version | None]]:
        """``(feature, version that introduced it)`` for everything unsupported."""
        out: list[tuple[str, Version | None]] = []
        for feature in sorted(needed):
            if not self.supports(feature):
                out.append((feature, versions.since_of(feature)))
        return out

    @property
    def emulated_names(self) -> list[str]:
        return sorted(name for name in HANDLERS if name in self._vanilla)

    @property
    def known_names(self) -> list[str]:
        return sorted(self._vanilla)

    def __repr__(self) -> str:
        return f"<CommandSet {self.version.id} commands={len(self._vanilla)}>"


@cache
def command_set(version: Version) -> CommandSet:
    return CommandSet(version)
