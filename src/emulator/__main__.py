"""Headless runner.

python -m src.emulator run      samples/hat --ticks 20 --version 1.21.4
python -m src.emulator matrix   samples/hat --from 1.20.4 --to 1.21.6
python -m src.emulator versions
python -m src.emulator vanilla  --download 1.21.4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.emulator import versions  # noqa: E402
from src.emulator.datapack import Datapack  # noqa: E402
from src.emulator.engine import TestEngine  # noqa: E402
from src.emulator.runtime.emulator import Emulator  # noqa: E402
from src.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus  # noqa: E402
from src.emulator.vanilla import VanillaAssets, default_library  # noqa: E402

LEVELS = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warn": LogLevel.WARNING,
    "error": LogLevel.ERROR,
}


def _print_record(record: LogRecord) -> None:
    print(record.format())


def _vanilla(arguments: argparse.Namespace):
    """Client-jar assets for this run, if the user asked for any."""
    requested = getattr(arguments, "vanilla", None)
    if requested is None and not getattr(arguments, "download", False):
        return None
    library = default_library()
    target = requested or ""
    if target and Path(target).is_file():
        return library.load_jar(Path(target))
    version = target or arguments.version or versions.LATEST.id
    assets = library.load(
        versions.parse(version).id,
        allow_download=getattr(arguments, "download", False),
        progress=lambda text: print(text, file=sys.stderr),
    )
    if assets is None:
        print(
            f"no client jar for {version}; pass --download to fetch it",
            file=sys.stderr,
        )
    return assets


def _load(path: Path) -> Datapack:
    datapack = Datapack.load(path)
    for error in datapack.errors:
        print(f"error: {error}", file=sys.stderr)
    return datapack


def command_run(arguments: argparse.Namespace) -> int:
    datapack = _load(arguments.datapack)
    if not datapack.namespaces:
        return 1
    bus = OutputBus()
    minimum = LEVELS[arguments.level]
    wanted = {LogSource(name) for name in arguments.sources}
    bus.listeners.append(
        lambda record: (
            _print_record(record) if record.source in wanted and record.level >= minimum else None
        )
    )
    assets = _vanilla(arguments)
    if assets is not None:
        print(f"vanilla assets: {assets.summary}", file=sys.stderr)
    emulator = Emulator(
        datapack,
        version=arguments.version,
        players=arguments.players,
        output=bus,
        vanilla=assets,
    )
    profiler = emulator.run(ticks=arguments.ticks)

    print(f"\n{'function':40} {'calls':>6} {'cmds':>7} {'self ms':>9} {'total ms':>9}")
    for function_id, entry in profiler.sorted_entries():
        print(
            f"{function_id:40} {int(entry['calls']):6d} {int(entry['commands']):7d} "
            f"{entry['self_us'] / 1000:9.3f} {entry['total_us'] / 1000:9.3f}"
        )
    print(
        f"\n{emulator.version.id}: {arguments.ticks} tick(s), "
        f"{profiler.total_us / 1000:.2f} ms estimated, "
        f"worst tick {profiler.worst_tick_us / 1000:.2f} ms"
    )

    report = profiler.write_html(
        arguments.html,
        f"Function profiler — {datapack.name}",
        f"{emulator.version.id} (pack_format {emulator.version.format_string})",
    )
    print(f"report: {report}")

    graph = emulator.call_graph()
    print(f"call graph: {graph}")
    for cycle in graph.cycles():
        print("  recursion: " + " -> ".join(cycle))
    for name in graph.missing():
        print(f"  missing function: {name}")
    for name in graph.unreachable():
        print(f"  never called: {name}")
    if arguments.dot:
        print(f"dot: {graph.write_dot(arguments.html.parent / f'{datapack.name}.dot')}")
    return 0


def command_matrix(arguments: argparse.Namespace) -> int:
    datapack = _load(arguments.datapack)
    if not datapack.namespaces:
        return 1
    engine = TestEngine(
        datapack,
        ticks=arguments.ticks,
        players=arguments.players,
        library=default_library() if arguments.vanilla else None,
        allow_download=arguments.download,
    )

    if arguments.versions:
        chosen = [versions.parse(item) for item in arguments.versions]
    elif arguments.start or arguments.end:
        chosen = versions.version_range(arguments.start, arguments.end)
    elif arguments.declared:
        chosen = engine.declared_versions()
    else:
        chosen = versions.VERSIONS
    if arguments.boundaries:
        chosen = TestEngine.format_boundaries(chosen)
    if not chosen:
        print("no versions selected", file=sys.stderr)
        return 1

    def progress(index: int, total: int, version) -> None:
        print(f"[{index}/{total}] {version.id}", file=sys.stderr)

    results = engine.run(chosen, progress=progress)

    print(
        f"\n{'version':10} {'format':7} {'status':12} {'cmds':>6} {'total ms':>9} "
        f"{'worst ms':>9} {'warn':>5} {'err':>5}  notes"
    )
    for run in results:
        notes = []
        if run.unknown_commands:
            notes.append("unknown: " + ", ".join(sorted(run.unknown_commands)))
        if run.overlays:
            notes.append("overlays: " + ", ".join(run.overlays))
        print(
            f"{run.version.id:10} {run.version.format_string:7} {run.status:12} "
            f"{run.commands:6d} {run.total_us / 1000:9.2f} {run.worst_tick_us / 1000:9.2f} "
            f"{run.warnings:5d} {run.errors:5d}  {'; '.join(notes)}"
        )

    report = TestEngine.write_html(results, arguments.html, datapack.name)
    print(f"\nmatrix report: {report}")
    return 0


def command_vanilla(arguments: argparse.Namespace) -> int:
    """List, download and inspect client jars."""
    library = default_library()
    if arguments.download:
        path = library.download(
            versions.parse(arguments.download).id,
            progress=lambda text: print(text, file=sys.stderr),
        )
        print(f"downloaded {path}")
    if arguments.inspect:
        path = Path(arguments.inspect)
        assets = (
            VanillaAssets.from_jar(path)
            if path.is_file()
            else library.load(versions.parse(arguments.inspect).id)
        )
        if assets is None:
            print(f"no client jar for {arguments.inspect}", file=sys.stderr)
            return 1
        print(assets.summary)
        for registry, ids in sorted(assets.registries.items()):
            print(f"  {registry:16} {len(ids):5d}  e.g. {', '.join(sorted(ids)[:3])}")
        return 0

    jars = library.local_jars()
    print(f"{len(jars)} client jar(s) found")
    for version_id, path in sorted(jars.items()):
        print(f"  {version_id:10} {path}")
    if not jars:
        print("  (pass --download <version> to fetch one)")
    return 0


def command_versions(arguments: argparse.Namespace) -> int:
    print(f"{'version':10} {'format':8} {'data version':>12}")
    for version in versions.VERSIONS:
        print(f"{version.id:10} {version.format_string:8} {version.data_version:12d}")
    print(f"\n{len(versions.VERSIONS)} releases, newest {versions.LATEST.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.emulator")
    parser.add_argument("--quiet", action="store_true")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    run = subparsers.add_parser("run", help="emulate the pack for one version")
    run.add_argument("datapack", type=Path)
    run.add_argument("--version", default=None, help="Minecraft version (default: latest)")
    run.add_argument("--ticks", type=int, default=20)
    run.add_argument("--players", type=int, default=1)
    run.add_argument("--html", type=Path, default=Path("generated/index.html"))
    run.add_argument("--dot", action="store_true")
    run.add_argument("--level", choices=sorted(LEVELS), default="info")
    run.add_argument(
        "--vanilla",
        nargs="?",
        const="",
        default=None,
        metavar="VERSION|JAR",
        help="check ids and message wording against a client jar",
    )
    run.add_argument("--download", action="store_true", help="fetch the jar if missing")
    run.add_argument(
        "--sources",
        nargs="+",
        choices=[source.value for source in LogSource],
        default=[source.value for source in LogSource],
    )
    run.set_defaults(handler=command_run)

    matrix = subparsers.add_parser("matrix", help="run the pack across versions")
    matrix.add_argument("datapack", type=Path)
    matrix.add_argument("--from", dest="start", default=None)
    matrix.add_argument("--to", dest="end", default=None)
    matrix.add_argument("--versions", nargs="+", default=None)
    matrix.add_argument(
        "--declared", action="store_true", help="use the range pack.mcmeta declares"
    )
    matrix.add_argument(
        "--boundaries",
        action="store_true",
        help="only the first release of each pack format",
    )
    matrix.add_argument("--ticks", type=int, default=20)
    matrix.add_argument("--players", type=int, default=1)
    matrix.add_argument("--html", type=Path, default=Path("generated/matrix.html"))
    matrix.add_argument("--vanilla", action="store_true", help="use client jars, one per version")
    matrix.add_argument("--download", action="store_true", help="fetch missing jars")
    matrix.set_defaults(handler=command_matrix)

    listing = subparsers.add_parser("versions", help="list known versions")
    listing.set_defaults(handler=command_versions)

    vanilla = subparsers.add_parser("vanilla", help="manage client jars")
    vanilla.add_argument("--download", metavar="VERSION", default=None)
    vanilla.add_argument("--inspect", metavar="VERSION|JAR", default=None)
    vanilla.set_defaults(handler=command_vanilla)

    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if arguments.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return arguments.handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
