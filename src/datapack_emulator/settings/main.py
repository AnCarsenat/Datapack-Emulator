"""Defaults for the window and the emulator."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "datapack-emulator"


def find_checkout(start: Path | None = None) -> Path | None:
    """The source checkout this package runs from, if any.

    Only a folder with pyproject.toml *and* src/datapack_emulator counts, so an
    unrelated project around a regular install is never mistaken for it.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "datapack_emulator"
        ).is_dir():
            return candidate
    return None


def user_dirs(platform: str = sys.platform, environ=os.environ) -> tuple[Path, Path]:
    """``(data, cache)`` folders of an installed copy, per operating system."""
    home = Path.home()
    if platform.startswith("win"):
        data = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
        cache = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return data / APP_NAME, cache / APP_NAME / "cache"
    if platform == "darwin":
        return (
            home / "Library" / "Application Support" / APP_NAME,
            home / "Library" / "Caches" / APP_NAME,
        )
    data = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    cache = Path(environ.get("XDG_CACHE_HOME") or home / ".cache")
    return data / APP_NAME, cache / APP_NAME


_CHECKOUT = find_checkout()

#: project root: the checkout (holding src/, samples/, generated/), or the
#: per-user data folder when the package is installed on its own
ROOT = _CHECKOUT if _CHECKOUT is not None else user_dirs()[0]
_CACHE = ROOT / ".cache" if _CHECKOUT is not None else user_dirs()[1]


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
    CACHE = _CACHE
    VANILLA_CACHE = _CACHE / "vanilla"
