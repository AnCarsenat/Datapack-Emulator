"""Run one datapack against many Minecraft versions.

Pick a version, a range, or the range the pack itself declares, and the engine
builds one :class:`~src.emulator.runtime.emulator.Emulator` per version — each
with its own overlays, its own command set and its own world — then reports
what differs.

Two kinds of finding come out of a run:

* **static** — checks that need no ticks: does the pack claim to support this
  format, are the registry folders spelled the way this version reads them,
  are overlays or macros used before they existed, does a function use a
  command this version does not have.
* **runtime** — everything the emulation itself logs while the ticks run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from src.emulator import versions
from src.emulator.analysis.graph import CallGraph
from src.emulator.analysis.profiler import Profiler
from src.emulator.commands.registry import command_set
from src.emulator.datapack import Datapack, PackView
from src.emulator.namespace import PLURAL_REGISTRIES
from src.emulator.runtime.emulator import Emulator
from src.emulator.runtime.output import LogLevel, LogRecord, LogSource, OutputBus
from src.emulator.vanilla import VanillaLibrary
from src.emulator.versions import Version

Progress = Callable[[int, int, Version], None]


@dataclass
class VersionRun:
    """What happened when the pack ran against one version."""

    version: Version
    supported: bool
    overlays: list[str] = field(default_factory=list)
    vanilla: str = ""
    ticks: int = 0
    commands: int = 0
    total_us: float = 0.0
    worst_tick_us: float = 0.0
    records: list[LogRecord] = field(default_factory=list)
    unknown_commands: set[str] = field(default_factory=set)
    missing_features: set[str] = field(default_factory=set)
    #: functions and tags the server refused to load in this version
    failed_functions: list[str] = field(default_factory=list)
    failed_tags: list[str] = field(default_factory=list)
    missing_functions: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    profiler: Profiler | None = None
    graph: CallGraph | None = None

    # -- summaries --------------------------------------------------------

    def count(self, source: LogSource, min_level: LogLevel = LogLevel.DEBUG) -> int:
        return sum(
            1 for record in self.records if record.source is source and record.level >= min_level
        )

    @property
    def errors(self) -> int:
        return sum(1 for record in self.records if record.level >= LogLevel.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for record in self.records if record.level == LogLevel.WARNING)

    @property
    def chat(self) -> list[str]:
        return [
            record.message
            for record in self.records
            if record.source is LogSource.GAME and record.level < LogLevel.ERROR
        ]

    @property
    def status(self) -> str:
        """errors > warnings > unsupported > ok.

        "unsupported" only means the pack's metadata does not claim this version:
        the game still loads it, so real problems take precedence.
        """
        if self.errors:
            return "errors"
        if self.warnings:
            return "warnings"
        if not self.supported:
            return "unsupported"
        return "ok"

    def __repr__(self) -> str:
        return f"<VersionRun {self.version.id} {self.status}>"


class TestEngine:
    """Runs a pack across versions and collects one :class:`VersionRun` each."""

    #: not a pytest test class, despite the name
    __test__ = False

    def __init__(
        self,
        datapack: Datapack,
        ticks: int = 20,
        players: int = 1,
        seed: int = 0,
        library: VanillaLibrary | None = None,
        allow_download: bool = False,
    ):
        self.datapack = datapack
        self.ticks = ticks
        self.players = players
        self.seed = seed
        #: client jars to check ids and message wording against, per version
        self.library = library
        self.allow_download = allow_download

    # -- choosing versions ------------------------------------------------

    def declared_versions(self) -> list[Version]:
        """What the pack says it supports; empty if it says nothing useful."""
        return self.datapack.declared_versions()

    def default_versions(self) -> list[Version]:
        declared = self.declared_versions()
        if declared:
            return declared
        return [versions.closest_to_pack_format(self.datapack.pack_format)]

    @staticmethod
    def version_range(start: str | Version | None, end: str | Version | None) -> list[Version]:
        return versions.version_range(start, end)

    @staticmethod
    def format_boundaries(candidates: Iterable[Version]) -> list[Version]:
        """One version per pack format — the cheap way to cover a wide range."""
        seen: set[tuple[int, int]] = set()
        out: list[Version] = []
        for version in candidates:
            if version.format not in seen:
                seen.add(version.format)
                out.append(version)
        return out

    # -- running ----------------------------------------------------------

    def run_version(self, version: str | Version, output: OutputBus | None = None) -> VersionRun:
        version = versions.parse(version)
        bus = output or OutputBus()
        first_record = len(bus.records)

        view = self.datapack.view_for(version)
        assets = None
        if self.library is not None:
            try:
                assets = self.library.load(version.id, allow_download=self.allow_download)
            except Exception as exc:  # a missing jar must not stop the matrix
                bus.app(f"no client jar for {version.id}: {exc}", level=LogLevel.WARNING)
        run = VersionRun(
            version=version,
            supported=self.datapack.supports(version),
            overlays=view.active_overlays,
            vanilla=str(assets.jar_path) if assets else "",
        )

        bus.app(
            f"running {self.datapack.name} against {version.id} "
            f"(pack_format {version.format_string})",
            version=version.id,
        )
        if assets is not None:
            bus.app(f"vanilla assets: {assets.summary}", version=version.id)
        self.static_checks(view, version, bus, run)

        emulator = Emulator(
            self.datapack,
            version=version,
            players=self.players,
            output=bus,
            seed=self.seed,
            vanilla=assets,
        )
        emulator.run(ticks=self.ticks)

        # functions the version could not parse were logged by the emulator as
        # load failures; keep what they needed for the summary columns
        commands = command_set(version)
        for function_id in emulator.library.function_failures:
            for feature, _since in commands.missing_features(
                view.functions[function_id].features()
            ):
                run.missing_features.add(feature)
                kind, _, name = feature.partition(":")
                if kind == "command":
                    run.unknown_commands.add(name)
        run.failed_functions = sorted(emulator.library.function_failures)
        run.failed_tags = sorted(emulator.library.tag_failures)

        graph = CallGraph.from_pack(view)
        run.profiler = emulator.profiler
        run.graph = graph
        run.ticks = self.ticks
        run.commands = int(sum(entry["commands"] for entry in emulator.profiler.entries.values()))
        run.total_us = emulator.profiler.total_us
        run.worst_tick_us = emulator.profiler.worst_tick_us
        run.missing_functions = graph.missing()
        run.unreachable = graph.unreachable()
        run.cycles = graph.cycles()
        run.records = bus.records[first_record:]
        return run

    def run(
        self,
        version_list: Iterable[str | Version] | None = None,
        progress: Progress | None = None,
        output: OutputBus | None = None,
    ) -> list[VersionRun]:
        chosen = (
            [versions.parse(item) for item in version_list]
            if version_list is not None
            else self.default_versions()
        )
        results: list[VersionRun] = []
        for index, version in enumerate(chosen, start=1):
            if progress is not None:
                progress(index, len(chosen), version)
            results.append(self.run_version(version, output=output))
        return results

    # -- static checks ----------------------------------------------------

    def static_checks(
        self, view: PackView, version: Version, bus: OutputBus, run: VersionRun
    ) -> None:
        """Everything that can be decided without running a tick."""
        fields = {"version": version.id}

        compatibility = self.datapack.compatibility(version)
        for line in compatibility.server_log:
            bus.game(line, level=LogLevel.WARNING, **fields)
        if not compatibility.compatible:
            bus.emulator(
                f"{version.id} lists this pack as {compatibility.status.replace('_', ' ')} "
                f"({compatibility.reason}); the game still loads it",
                level=LogLevel.INFO,
                **fields,
            )

        # folder naming ---------------------------------------------------
        # A pack that declares versions on both sides of a change ships the old
        # form on purpose, so the files one version ignores are expected there.
        declared = self.datapack.declared_versions()

        def spans(boundary: str) -> bool:
            edge = versions.parse(boundary)
            return any(v < edge for v in declared) and any(v >= edge for v in declared)

        folder_level = (
            LogLevel.INFO if spans(versions.SINGULAR_REGISTRIES_SINCE) else LogLevel.WARNING
        )
        raw_folders: set[str] = set()
        for namespaces in view.namespaces.values():
            for namespace in namespaces:
                raw_folders |= namespace.raw_folders
        renamed = (
            PLURAL_REGISTRIES
            if versions.uses_singular_registries(version)
            else {singular: plural for plural, singular in PLURAL_REGISTRIES.items()}
        )
        for folder in sorted(raw_folders):
            wanted = renamed.get(folder)
            if wanted is not None and wanted not in raw_folders:
                bus.emulator(
                    f"{version.id} reads '{wanted}/', not '{folder}/' — those files are ignored",
                    level=folder_level,
                    **fields,
                )

        # pack.mcmeta features --------------------------------------------
        if self.datapack.overlays and not versions.supports_overlays(version):
            bus.emulator(
                f"the pack declares overlays, which {version.id} ignores "
                f"(added in {versions.OVERLAYS_SINCE})",
                level=LogLevel.INFO if spans(versions.OVERLAYS_SINCE) else LogLevel.WARNING,
                **fields,
            )
        if view.active_overlays:
            bus.app(
                "active overlay(s): " + ", ".join(view.active_overlays),
                **fields,
            )

    # -- reporting --------------------------------------------------------

    @staticmethod
    def to_html(results: list[VersionRun], pack_name: str = "") -> str:
        rows = []
        colours = {
            "ok": "#1e6f3d",
            "warnings": "#b9770e",
            "errors": "#c0392b",
            "unsupported": "#7f8c8d",
        }
        for run in results:
            rows.append(
                "<tr>"
                f"<td class='id'>{run.version.id}</td>"
                f"<td>{run.version.format_string}</td>"
                f"<td style='color:{colours[run.status]};font-weight:bold'>{run.status}</td>"
                f"<td>{run.commands}</td>"
                f"<td>{run.total_us / 1000:.2f}</td>"
                f"<td>{run.worst_tick_us / 1000:.2f}</td>"
                f"<td>{run.warnings}</td>"
                f"<td>{run.errors}</td>"
                f"<td class='id'>{escape(', '.join(sorted(run.unknown_commands))) or '-'}</td>"
                f"<td class='id'>{escape(', '.join(run.overlays)) or '-'}</td>"
                "</tr>"
            )
        title = escape(f"Version matrix — {pack_name}" if pack_name else "Version matrix")
        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>{title}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 12px; color: #111; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
 th, td {{ text-align: right; padding: 3px 6px; border-bottom: 1px solid #e3e3e3; }}
 th:first-child, td.id {{ text-align: left; font-family: monospace; }}
 tr:hover {{ background: #e4fbff; }}
</style></head>
<body>
<h2>{title}</h2>
<table>
<thead><tr><th>version</th><th>format</th><th>status</th><th>commands</th><th>total ms</th>
<th>worst ms</th><th>warnings</th><th>errors</th><th>unknown commands</th><th>overlays</th></tr>
</thead>
<tbody>
{chr(10).join(rows) or "<tr><td colspan='10'>no runs</td></tr>"}
</tbody>
</table>
</body>
</html>
"""

    @staticmethod
    def write_html(results: list[VersionRun], path: Path | str, pack_name: str = "") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TestEngine.to_html(results, pack_name), encoding="utf-8")
        return path
