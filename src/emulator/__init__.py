"""Datapack model, command emulator and version test engine.

    from src.emulator import Datapack, Emulator, TestEngine

    pack = Datapack.load("samples/hat")
    Emulator(pack, version="1.21.4").run(ticks=20)
    TestEngine(pack).run(["1.20.4", "1.21.4"])
"""

from src.emulator.analysis.graph import CallEdge, CallGraph
from src.emulator.analysis.profiler import Profiler
from src.emulator.commands import (
    Command,
    CommandResult,
    CommandSet,
    Selector,
    Subcommand,
    command_set,
)
from src.emulator.datapack import (
    Datapack,
    Layer,
    OverlayEntry,
    PackMCMETA,
    PackPNG,
    PackView,
)
from src.emulator.engine import TestEngine, VersionRun
from src.emulator.namespace import DirectoryNode, Namespace
from src.emulator.resources import (
    Advancement,
    Function,
    ItemModifier,
    JsonResource,
    LootTable,
    Predicate,
    Recipe,
    Resource,
    Tag,
)
from src.emulator.runtime.context import ExecutionContext
from src.emulator.runtime.emulator import Emulator
from src.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus
from src.emulator.runtime.world import Entity, Scoreboard, World
from src.emulator.versions import VERSIONS, Version

__all__ = [
    "Advancement",
    "CallEdge",
    "CallGraph",
    "Command",
    "CommandResult",
    "CommandSet",
    "Datapack",
    "DirectoryNode",
    "Emulator",
    "Entity",
    "ExecutionContext",
    "Function",
    "ItemModifier",
    "JsonResource",
    "Layer",
    "LogLevel",
    "LogRecord",
    "LogSource",
    "LootTable",
    "Namespace",
    "OutputBus",
    "OverlayEntry",
    "PackMCMETA",
    "PackPNG",
    "PackView",
    "Predicate",
    "Profiler",
    "Recipe",
    "Resource",
    "Scoreboard",
    "Selector",
    "Subcommand",
    "Tag",
    "TestEngine",
    "VERSIONS",
    "Version",
    "VersionRun",
    "World",
    "command_set",
]
