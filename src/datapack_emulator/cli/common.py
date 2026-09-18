"""What every subcommand shares: reading packs or a project, client jars,
choosing versions, printing records."""

from __future__ import annotations

import argparse
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.datapack import DatapackSet, preferred_version
from datapack_emulator.emulator.engine import TestEngine
from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus
from datapack_emulator.emulator.testing import CommandTest
from datapack_emulator.emulator.vanilla import VanillaAssets, VanillaLibrary, default_library
from datapack_emulator.emulator.versions import Version
from datapack_emulator.project import SUFFIXES, Project, recent_projects

LEVELS = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warn": LogLevel.WARNING,
    "error": LogLevel.ERROR,
}

#: exit codes
OK = 0
FAILED = 1
USAGE = 2
CRASHED = 3


class CliError(Exception):
    """A problem with what was asked; printed without a traceback."""


def err(text: str) -> None:
    print(text, file=sys.stderr)


@dataclass
class Inputs:
    """The packs to analyze and, when a project was given, the project."""

    datapack: DatapackSet
    project: Project | None = None
    #: tests from the project (enabled or not) and from the command line
    tests: list[CommandTest] = field(default_factory=list)

    def setting(self, arguments: argparse.Namespace, name: str, fallback):
        """An option given on the command line, else the project's, else ``fallback``."""
        value = getattr(arguments, name, None)
        if value is not None:
            return value
        if self.project is not None:
            return getattr(self.project, name, fallback)
        return fallback

    def version(self, arguments: argparse.Namespace) -> Version:
        requested = getattr(arguments, "version", None)
        if requested:
            return parse_version(requested)
        if self.project is not None and self.project.version:
            return parse_version(self.project.version)
        return preferred_version(self.datapack)


def parse_version(text: str | Version) -> Version:
    try:
        return versions.parse(text)
    except KeyError as exc:
        raise CliError(exc.args[0] if exc.args else f"unknown version {text!r}") from exc


def is_project(path: Path) -> bool:
    return path.is_file() and path.suffix in SUFFIXES


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "source",
        type=Path,
        nargs="+",
        help="datapack folders in load order, or one .dpemu / .json project "
        "(@last: the project the window opened last)",
    )


LAST_PROJECT = "@last"


def load_inputs(paths: list[Path]) -> Inputs:
    """Datapack folders, or one project whose packs and settings are used."""
    if [str(path) for path in paths] == [LAST_PROJECT]:
        recent = [path for path in recent_projects() if path.is_file()]
        if not recent:
            raise CliError("no recent project: open one in the window first")
        paths = [recent[0]]
    projects = [path for path in paths if is_project(path)]
    if projects and len(paths) > 1:
        raise CliError("give either one project or datapack folders, not both")
    project = None
    if projects:
        try:
            project = Project.load(projects[0])
        except (OSError, ValueError) as exc:
            raise CliError(f"cannot open project {projects[0]}: {exc}") from exc
        if not project.datapacks:
            raise CliError(f"project {projects[0]} has no datapack")
        paths = list(project.datapacks)
    for path in paths:
        if not path.exists():
            raise CliError(f"no such file or folder: {path}")
    datapack = DatapackSet.load(paths)
    for error in datapack.errors:
        err(f"error: {error}")
    if not datapack.namespaces:
        raise CliError("nothing to analyze: no data/ folder with namespaces")
    tests = [CommandTest.from_dict(test) for test in project.tests] if project else []
    return Inputs(datapack, project, tests)


def add_vanilla_arguments(parser: argparse.ArgumentParser, per_version: bool = False) -> None:
    """Client jars: like the window, the installed jar of the emulated version is
    used when there is one."""
    group = parser.add_argument_group("client jars")
    if per_version:
        group.add_argument(
            "--vanilla",
            action="store_true",
            help="use each version's installed client jar (the default)",
        )
    else:
        group.add_argument(
            "--vanilla",
            nargs="?",
            const="",
            default=None,
            metavar="VERSION|JAR",
            help="the client jar to check ids and message wording against (default: the "
            "emulated version's, if installed, else the project's)",
        )
    group.add_argument("--no-vanilla", action="store_true", help="use no client jar")
    group.add_argument("--download", action="store_true", help="fetch missing jars from Mojang")


def _read_jar(library: VanillaLibrary, path: Path) -> VanillaAssets:
    try:
        return library.load_jar(path)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise CliError(f"cannot read client jar {path}: {exc}") from exc


def _project_jar(inputs: Inputs | None) -> Path | None:
    saved = inputs.project.vanilla_jar if inputs and inputs.project else ""
    return Path(saved) if saved and Path(saved).is_file() else None


def vanilla_for(
    arguments: argparse.Namespace, version: Version, inputs: Inputs | None = None
) -> VanillaAssets | None:
    """The client jar for a one-version command, picked like the window does.

    ``--vanilla`` names one (a version or a jar path; no value = the emulated
    version, downloaded with ``--download``). Otherwise the emulated version's
    installed jar, else the jar a project was saved with. ``--no-vanilla``: none.
    """
    if getattr(arguments, "no_vanilla", False):
        return None
    requested = getattr(arguments, "vanilla", None)
    download = getattr(arguments, "download", False)
    library = default_library()
    if isinstance(requested, str) and requested and Path(requested).is_file():
        return _read_jar(library, Path(requested))
    target = parse_version(requested).id if requested else version.id
    try:
        assets = library.load(target, allow_download=download, progress=err)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise CliError(f"cannot get the client jar for {target}: {exc}") from exc
    if assets is not None:
        return assets
    saved = _project_jar(inputs)
    if not requested and saved is not None:
        return _read_jar(library, saved)
    if requested is not None:
        err(f"no client jar for {target}; pass --download to fetch it")
    return None


def library_for(arguments: argparse.Namespace, inputs: Inputs) -> VanillaLibrary | None:
    """Installed jars, one per version, for the multi-version commands; a
    project's saved jar serves its own version."""
    if getattr(arguments, "no_vanilla", False):
        return None
    library = default_library()
    saved = _project_jar(inputs)
    if saved is not None:
        _read_jar(library, saved)  # cached under its own version id
    return library


def add_version_selection(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("versions (first match wins)")
    group.add_argument("--versions", nargs="+", default=None, metavar="VERSION")
    group.add_argument("--from", dest="start", default=None, metavar="VERSION")
    group.add_argument("--to", dest="end", default=None, metavar="VERSION")
    group.add_argument("--declared", action="store_true", help="the range pack.mcmeta declares")
    group.add_argument(
        "--all", dest="all_versions", action="store_true", help="every known version"
    )
    group.add_argument(
        "--boundaries", action="store_true", help="then keep the first release of each pack format"
    )


def versions_given(arguments: argparse.Namespace) -> bool:
    return bool(
        arguments.versions
        or arguments.start
        or arguments.end
        or arguments.declared
        or arguments.all_versions
    )


def chosen_versions(
    arguments: argparse.Namespace, inputs: Inputs, default: list[Version]
) -> list[Version]:
    """``--versions``, ``--from/--to``, ``--declared``, ``--all``, else ``default``."""
    if arguments.versions:
        chosen = [parse_version(item) for item in arguments.versions]
    elif arguments.start or arguments.end:
        chosen = versions.version_range(
            parse_version(arguments.start) if arguments.start else None,
            parse_version(arguments.end) if arguments.end else None,
        )
    elif arguments.declared:
        chosen = inputs.datapack.declared_versions()
    elif arguments.all_versions:
        chosen = list(versions.VERSIONS)
    else:
        chosen = list(default)
    if arguments.boundaries:
        chosen = TestEngine.format_boundaries(chosen)
    if not chosen:
        raise CliError("no versions selected")
    return chosen


def add_output_filter(parser: argparse.ArgumentParser, default_level: str = "info") -> None:
    parser.add_argument(
        "--level",
        choices=sorted(LEVELS),
        default=default_level,
        help="lowest level of the records printed",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=[source.value for source in LogSource],
        default=[source.value for source in LogSource],
        help="which records to print: what this program, the emulator or the game says",
    )
    parser.add_argument(
        "--seen-by",
        default="",
        metavar="PLAYER",
        help="only the chat PLAYER reads (the logs dock's 'seen by')",
    )
    parser.add_argument(
        "--grep", default="", metavar="TEXT", help="only records whose message contains TEXT"
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="print each record's command, translation key, version and reader",
    )


def record_filter(arguments: argparse.Namespace) -> Callable[[LogRecord], bool]:
    """The logs dock's filters, from ``add_output_filter``'s options."""
    minimum = LEVELS[arguments.level]
    wanted = {LogSource(name) for name in arguments.sources}
    needle = (getattr(arguments, "grep", "") or "").lower()
    reader = getattr(arguments, "seen_by", "") or ""
    return lambda record: (
        record.source in wanted
        and record.level >= minimum
        and (not needle or needle in record.message.lower())
        and record.seen_by(reader)
    )


def format_record(arguments: argparse.Namespace, record: LogRecord) -> str:
    return record.details() if getattr(arguments, "details", False) else record.format()


def printing_bus(
    arguments: argparse.Namespace, bus: OutputBus | None = None, stream: TextIO | None = None
) -> OutputBus:
    """A bus that prints the records the output options ask for (to ``stream``,
    standard output by default)."""
    bus = bus or OutputBus()
    passes = record_filter(arguments)

    def show(record: LogRecord) -> None:
        if passes(record):
            print(format_record(arguments, record), file=stream or sys.stdout)

    bus.listeners.append(show)
    return bus


def report_path(arguments: argparse.Namespace, name: str) -> Path:
    """``--html``, else ``generated/<name>`` in the working directory."""
    given = getattr(arguments, "html", None)
    return Path(given) if given else Path("generated") / name


def count(text: str) -> int:
    """An argparse type: a whole number, 0 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more: {text}")
    return value
