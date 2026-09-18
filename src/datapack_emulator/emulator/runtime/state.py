"""Server state beyond entities and blocks: time of day, weather, difficulty,
the world border, random sequences, force-loaded chunks, spawn points and
teams — what ``time``, ``weather``, ``difficulty``, ``worldborder``,
``random``, ``forceload``, ``setworldspawn`` and ``team`` read and change.

Nothing here simulates the world: the time of day advances one tick per
server tick while ``doDaylightCycle`` is true, the world border does not
move over time (a timed change is applied at once), and weather never
changes by itself.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Any

#: default world border: 59 999 968 blocks wide, centered on 0 0
BORDER_SIZE = 59_999_968.0
BORDER_MAX = 59_999_968.0
#: how far out a border center may be
BORDER_CENTER_LIMIT = 29_999_984.0

DIFFICULTIES = ("peaceful", "easy", "normal", "hard")
GAME_MODES = ("survival", "creative", "adventure", "spectator")
#: the color names team colors accept
TEAM_COLORS = (
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple", "gold",
    "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple", "yellow", "white",
    "reset",
)  # fmt: skip
VISIBILITY = ("always", "never", "hideForOtherTeams", "hideForOwnTeam")
COLLISION = ("always", "never", "pushOtherTeams", "pushOwnTeam")


@dataclass
class Border:
    center: tuple[float, float] = (0.0, 0.0)
    size: float = BORDER_SIZE
    damage_amount: float = 0.2
    damage_buffer: float = 5.0
    warning_distance: int = 5
    warning_time: int = 15


@dataclass
class Team:
    name: str
    display_name: str = ""
    color: str = "reset"
    friendly_fire: bool = True
    see_friendly_invisibles: bool = True
    nametag_visibility: str = "always"
    death_message_visibility: str = "always"
    collision_rule: str = "always"
    prefix: str = ""
    suffix: str = ""
    #: score holder names: players by name, other entities by UUID
    members: list[str] = field(default_factory=list)

    @property
    def shown(self) -> str:
        """How feedback names the team: its display name in brackets."""
        return f"[{self.display_name or self.name}]"


@dataclass
class ServerState:
    #: the time of day (not wrapped), separate from the game time
    day_time: int = 0
    weather: str = "clear"
    #: ticks the weather lasts; 0 = until changed (the command's default is random)
    weather_duration: int = 0
    difficulty: str = "easy"
    default_game_mode: str = "survival"
    border: Border = field(default_factory=Border)
    spawn: tuple[int, int, int] = (0, 64, 0)
    spawn_angle: float = 0.0
    #: dimension -> force-loaded chunk positions
    forced_chunks: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    tick_rate: float = 20.0
    frozen: bool = False
    seed: int = 0
    #: random sequences in use, by id
    sequences: dict[str, random.Random] = field(default_factory=dict)
    #: custom boss bars by id (commands.bossbar.Bossbar)
    bossbars: dict[str, Any] = field(default_factory=dict)

    # -- teams ------------------------------------------------------------

    def team_of(self, holder: str) -> Team | None:
        for team in self.teams.values():
            if holder in team.members:
                return team
        return None

    def join(self, holder: str, team: Team) -> None:
        self.leave(holder)
        team.members.append(holder)

    def leave(self, holder: str) -> bool:
        current = self.team_of(holder)
        if current is None:
            return False
        current.members.remove(holder)
        return True

    # -- random sequences -------------------------------------------------

    #: (seed, include the world seed, include the id) for new sequences
    sequence_defaults: tuple[int, bool, bool] = (0, True, True)

    def sequence(self, name: str) -> random.Random:
        """A random sequence, seeded from the world seed and its id, like vanilla's."""
        if name not in self.sequences:
            self.reset_sequence(name, *self.sequence_defaults)
        return self.sequences[name]

    def reset_sequence(
        self, name: str, seed: int = 0, include_world_seed: bool = True, include_id: bool = True
    ) -> None:
        value = seed
        if include_world_seed:
            value ^= self.seed
        if include_id:
            value ^= zlib.crc32(name.encode())
        self.sequences[name] = random.Random(value)


def xp_for_next_level(level: int) -> int:
    """Points from ``level`` to the next one, as vanilla counts them."""
    if level >= 30:
        return 112 + (level - 30) * 9
    if level >= 15:
        return 37 + (level - 15) * 5
    return 7 + level * 2
