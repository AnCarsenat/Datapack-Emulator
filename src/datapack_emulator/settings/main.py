"""Defaults for the window and the emulator."""

from __future__ import annotations

from pathlib import Path


def _find_root() -> Path:
    """The checkout holding pyproject.toml, samples/ and projects/.

    Walks up from this file (an editable install or a plain checkout); an
    installed copy has no checkout around it, so it uses the working directory.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src").is_dir():
            return candidate
    return Path.cwd()


#: project root (the folder holding src/, samples/, generated/)
ROOT = _find_root()


class WINDOW:
    WINDOW_WIDTH = 1280
    WINDOW_HEIGHT = 800


class EMULATION:
    DEFAULT_TICKS = 20
    DEFAULT_PLAYERS = 1
    DEFAULT_SEED = 0


class PATHS:
    ROOT = ROOT
    SAMPLES = ROOT / "samples"
    PROJECTS = ROOT / "projects"
    GENERATED = ROOT / "generated"
    CACHE = ROOT / ".cache"
    VANILLA_CACHE = ROOT / ".cache" / "vanilla"
