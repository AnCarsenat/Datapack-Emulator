"""Defaults for the window and the emulator."""

from __future__ import annotations

from pathlib import Path

#: project root (the folder holding src/, samples/, generated/)
ROOT = Path(__file__).resolve().parents[2]


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
