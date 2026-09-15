"""Projects: what you were working on, saved next to ``samples/``.

A project is one small JSON file in ``projects/``:

    {
      "name": "hat",
      "datapack": "samples/hat",
      "version": "1.21.4",
      "ticks": 20, "players": 1, "seed": 0,
      "engine_versions": ["1.20.4", "1.21.4"],
      "vanilla_jar": ""
    }

Paths inside the repository are stored relative to it, so a project folder
can be copied to another machine and still open.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from datapack_emulator.settings import EMULATION, PATHS

log = logging.getLogger(__name__)

SUFFIX = ".json"


def projects_dir() -> Path:
    PATHS.PROJECTS.mkdir(parents=True, exist_ok=True)
    return PATHS.PROJECTS


def list_projects() -> list[Path]:
    if not PATHS.PROJECTS.is_dir():
        return []
    return sorted(PATHS.PROJECTS.glob(f"*{SUFFIX}"))


def _relative(path: Path | None) -> str:
    """Inside the repo -> relative; anywhere else -> absolute."""
    if path is None:
        return ""
    path = Path(path)
    try:
        return path.resolve().relative_to(PATHS.ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _absolute(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (PATHS.ROOT / path)


@dataclass
class Project:
    """Everything the window needs to pick up where it left off."""

    name: str = "untitled"
    datapack: Path | None = None
    version: str = ""
    ticks: int = EMULATION.DEFAULT_TICKS
    players: int = EMULATION.DEFAULT_PLAYERS
    seed: int = EMULATION.DEFAULT_SEED
    engine_versions: list[str] = field(default_factory=list)
    vanilla_jar: str = ""
    #: where it was saved; ``None`` until the first save
    path: Path | None = None

    # -- serialising ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datapack": _relative(self.datapack),
            "version": self.version,
            "ticks": self.ticks,
            "players": self.players,
            "seed": self.seed,
            "engine_versions": list(self.engine_versions),
            "vanilla_jar": self.vanilla_jar,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> Project:
        return cls(
            name=str(data.get("name") or (path.stem if path else "untitled")),
            datapack=_absolute(str(data.get("datapack", ""))),
            version=str(data.get("version", "")),
            ticks=int(data.get("ticks", EMULATION.DEFAULT_TICKS)),
            players=int(data.get("players", EMULATION.DEFAULT_PLAYERS)),
            seed=int(data.get("seed", EMULATION.DEFAULT_SEED)),
            engine_versions=[str(item) for item in data.get("engine_versions", [])],
            vanilla_jar=str(data.get("vanilla_jar", "")),
            path=path,
        )

    # -- files ------------------------------------------------------------

    def default_path(self) -> Path:
        return projects_dir() / f"{_safe_name(self.name)}{SUFFIX}"

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path else (self.path or self.default_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.path = target
        log.info("saved project %s", target)
        return target

    @classmethod
    def load(cls, path: Path | str) -> Project:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data, path=path)

    def renamed(self, name: str) -> Project:
        """A copy under a new name, not yet written anywhere."""
        return replace(self, name=name, path=None)

    @property
    def title(self) -> str:
        return self.name if self.path else f"{self.name} (unsaved)"

    def __repr__(self) -> str:
        return f"<Project {self.name} datapack={self.datapack}>"


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ ." else "-" for char in name)
    return cleaned.strip().strip(".") or "untitled"


def default_sample() -> Path | None:
    """The datapack to open on a cold start: the first one in ``samples/``."""
    if not PATHS.SAMPLES.is_dir():
        return None
    for candidate in sorted(PATHS.SAMPLES.iterdir()):
        if candidate.is_dir() and (candidate / "pack.mcmeta").is_file():
            return candidate
    return None
