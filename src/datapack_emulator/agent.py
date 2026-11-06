"""A JSON-in, JSON-out API for scripts and AI agents.

Every function here takes plain values (paths, version strings, lists of
command strings) and returns a plain ``dict`` that ``json.dumps`` accepts as
is. Nothing prints, nothing asks a question, nothing needs Qt. This is what
``datapack-emulator-cli ... --json`` prints and what MCP servers wrap::

    from datapack_emulator import agent

    agent.check("path/to/pack", version="1.21.4")
    agent.run("path/to/pack", version="1.21.4", ticks=40,
              commands=["scoreboard players get @p hat"])
    agent.test("path/to/pack", ["5:scoreboard players get #game timer"],
               checks=["score #game timer = 5"])
    agent.matrix("path/to/pack", declared=True, boundaries=True)

Conventions, the same in every result:

``ok``
    ``True`` when nothing went wrong: no error-level problem (``check``), no
    error record (``run``), every test passed (``test``), no version with
    errors or failed tests (``matrix``).
``summary``
    one line a person (or a model) can read without the rest.
``version`` / ``versions``
    the Minecraft version ids actually emulated, after resolving ``"latest"``
    or a line like ``"1.21"``.
``truncated``
    lists capped by ``max_records`` / ``max_problems`` say so, with the full
    count beside them, so a partial list is never mistaken for a whole one.

Bad input (a missing folder, an unknown version, a malformed test) raises
:class:`AgentError` with a message meant to be shown to whoever asked.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from datapack_emulator import __version__
from datapack_emulator.emulator import versions as _versions
from datapack_emulator.emulator.analysis.problems import SEVERITIES, find_problems, ignored_folders
from datapack_emulator.emulator.analysis.problems import count as count_problems
from datapack_emulator.emulator.analysis.world_view import world_to_dict
from datapack_emulator.emulator.commands import Command
from datapack_emulator.emulator.datapack import DatapackSet, preferred_version
from datapack_emulator.emulator.engine import TestEngine, VersionRun
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, OutputBus
from datapack_emulator.emulator.testing import (
    CommandTest,
    TestResult,
    valid_check,
    valid_range,
)
from datapack_emulator.emulator.vanilla import VanillaAssets, default_library
from datapack_emulator.emulator.versions import Version

PathLike = str | Path
Packs = PathLike | Sequence[PathLike]

LEVELS = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warning": LogLevel.WARNING,
    "warn": LogLevel.WARNING,
    "error": LogLevel.ERROR,
}

#: default caps; a model's context is the scarce resource, not the emulator
MAX_RECORDS = 200
MAX_PROBLEMS = 200


class AgentError(ValueError):
    """The request cannot be carried out as given (bad path, version, test)."""


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


def load_packs(packs: Packs) -> DatapackSet:
    """One datapack folder (or zip), or several in load order."""
    paths = [Path(packs)] if isinstance(packs, (str, Path)) else [Path(p) for p in packs]
    if not paths:
        raise AgentError("no datapack given")
    for path in paths:
        if not path.exists():
            raise AgentError(f"no such file or folder: {path}")
    datapack = DatapackSet.load(paths)
    if not datapack.namespaces:
        raise AgentError(
            f"nothing to emulate in {', '.join(map(str, paths))}: no data/<namespace>/ folder "
            "(point at the folder that holds pack.mcmeta)"
        )
    return datapack


def resolve_version(spec: str | Version | None, datapack: DatapackSet | None = None) -> Version:
    """``"1.21.4"``, ``"latest"``, a line such as ``"1.21"``; ``None`` = the
    version the pack's ``pack.mcmeta`` points at."""
    if spec is None or spec == "":
        if datapack is None:
            return _versions.LATEST
        return preferred_version(datapack)
    try:
        return _versions.parse(spec)
    except KeyError as exc:
        raise AgentError(
            f"unknown Minecraft version {spec!r}; versions() lists the known ones"
        ) from exc


def select_versions(
    datapack: DatapackSet,
    versions: Iterable[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    declared: bool = False,
    boundaries: bool = False,
    default: list[Version] | None = None,
) -> list[Version]:
    """The same choice as the CLI's ``--versions/--from/--to/--declared/--boundaries``."""
    if versions:
        chosen = [resolve_version(item) for item in versions]
    elif start or end:
        chosen = _versions.version_range(
            resolve_version(start) if start else None, resolve_version(end) if end else None
        )
    elif declared:
        chosen = datapack.declared_versions()
        if not chosen:
            raise AgentError("pack.mcmeta declares no version range")
    else:
        chosen = list(default or [preferred_version(datapack)])
    if boundaries:
        chosen = TestEngine.format_boundaries(chosen)
    if not chosen:
        raise AgentError("no versions selected")
    return chosen


def vanilla_assets(
    vanilla: str | bool | None, version: Version, download: bool = False
) -> VanillaAssets | None:
    """``"auto"``/``True``: the version's client jar if installed (or fetched
    with ``download``); ``None``/``False``: none; a version id or a jar path:
    that jar."""
    if vanilla is None or vanilla is False:
        return None
    library = default_library()
    try:
        if isinstance(vanilla, str) and vanilla not in ("", "auto") and Path(vanilla).is_file():
            return library.load_jar(Path(vanilla))
        target = version.id
        if isinstance(vanilla, str) and vanilla not in ("", "auto"):
            target = resolve_version(vanilla).id
        return library.load(target, allow_download=download)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise AgentError(f"cannot read the client jar for {version.id}: {exc}") from exc


def _read_test(test: str | dict[str, Any] | CommandTest) -> CommandTest:
    if isinstance(test, CommandTest):
        parsed = test
    elif isinstance(test, dict):
        parsed = CommandTest.from_dict(test)
    else:
        text = str(test)
        head, sep, rest = text.partition(":")
        head = head.strip()
        if sep and head.isdecimal() and head.isascii():
            parsed = CommandTest(rest.strip(), at_tick=int(head))
        else:
            parsed = CommandTest(text.strip())
    return parsed


def parse_test(test: str | dict[str, Any] | CommandTest) -> CommandTest:
    """``"[TICK:]COMMAND"``, a dict with ``command``/``at_tick``/``expect``/
    ``expect_value``/``checks``, or a :class:`CommandTest`."""
    parsed = _read_test(test)
    for line in parsed.checks:
        problem = valid_check(line)
        if problem:
            raise AgentError(problem)
    if parsed.expect_value and not valid_range(parsed.expect_value):
        raise AgentError(f"invalid expect_value {parsed.expect_value!r} (5, 1.., ..3, 1..4)")
    if not parsed.command and not parsed.checks:
        raise AgentError(f"test at tick {parsed.at_tick} has neither a command nor checks")
    parsed.enabled = True
    return parsed


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------


def record_to_dict(record: LogRecord) -> dict[str, Any]:
    """One line of output: what the game, the emulator or the app said."""
    data: dict[str, Any] = {
        "tick": record.tick,
        "source": record.source.value,
        "level": record.level.label,
        "message": record.message,
    }
    if record.origin:
        data["at"] = record.origin
    if record.command:
        data["command"] = record.command
    if record.failure:
        data["failure"] = True
    if record.key:
        data["key"] = record.key
    return data


def records_to_list(
    records: list[LogRecord], level: str = "info", limit: int = MAX_RECORDS
) -> dict[str, Any]:
    """Records at ``level`` or above, newest kept when capped (errors first)."""
    minimum = _level(level)
    wanted = [record for record in records if record.level >= minimum]
    if len(wanted) > limit:
        # keep every error, then the newest of the rest
        errors = [r for r in wanted if r.level >= LogLevel.ERROR][:limit]
        rest = [r for r in wanted if r.level < LogLevel.ERROR]
        room = limit - len(errors)
        keep = set(map(id, errors + (rest[-room:] if room > 0 else [])))
        shown = [r for r in wanted if id(r) in keep]
    else:
        shown = wanted
    return {
        "records": [record_to_dict(record) for record in shown],
        "records_total": len(wanted),
        "truncated": len(shown) < len(wanted),
    }


def test_result_to_dict(version: Version, result: TestResult) -> dict[str, Any]:
    test = result.test
    status = "pass" if result.passed else "skip" if result.skipped else "fail"
    data: dict[str, Any] = {
        "version": version.id,
        "status": status,
        "at_tick": test.at_tick,
        "command": test.command,
        "reason": result.reason,
        "value": result.value,
    }
    if test.expect:
        data["expect"] = test.expect
    if test.expect_value:
        data["expect_value"] = test.expect_value
    if test.checks:
        data["checks"] = list(test.checks)
    if not result.passed:
        data["output"] = [record_to_dict(record) for record in result.records[:20]]
    return data


def profile_summary(profiler, top: int = 10) -> dict[str, Any]:
    """Estimated cost per function and the dearest lines (a model, not timings)."""
    ticks = max(profiler.ticks, 1)
    functions = [
        {
            "function": function_id,
            "calls": int(entry["calls"]),
            "commands": int(entry["commands"]),
            "self_ms": round(entry["self_us"] / 1000, 4),
            "total_ms": round(entry["total_us"] / 1000, 4),
            "ms_per_tick": round(entry["total_us"] / 1000 / ticks, 5),
        }
        for function_id, entry in profiler.sorted_entries()[:top]
    ]
    lines = [
        {
            "at": f"{function_id}:{line}",
            "self_ms": round(stats["self_us"] / 1000, 4),
            "runs": int(stats["runs"]),
        }
        for function_id, line, stats in profiler.hot_commands(top)
    ]
    return {
        "estimated": True,
        "total_ms": round(profiler.total_us / 1000, 4),
        "ms_per_tick": round(profiler.total_us / 1000 / ticks, 5),
        "worst_tick_ms": round(profiler.worst_tick_us / 1000, 4),
        "functions": functions,
        "dearest_lines": lines,
    }


def graph_summary(graph) -> dict[str, Any]:
    return {
        "functions": len(graph.nodes) if hasattr(graph, "nodes") else None,
        "missing_functions": list(graph.missing()),
        "never_called": list(graph.unreachable()),
        "recursion": [list(cycle) for cycle in graph.cycles()],
    }


def world_summary(world, version: Version, entities: bool = False) -> dict[str, Any]:
    """Scores, storage and what is in the world; full entity NBT only on request."""
    data = world_to_dict(world, version)
    counts: dict[str, int] = {}
    for entity in data["entities"]:
        counts[entity["type"]] = counts.get(entity["type"], 0) + 1
    out: dict[str, Any] = {
        "tick": data["tick"],
        "objectives": {name: info["criterion"] for name, info in data["objectives"].items()},
        "scores": data["scores"],
        "storage": data["storage"],
        "entity_counts": counts,
        "blocks_placed": len(data["blocks"]),
        "tags": {
            entity["name"]: sorted(entity["nbt"].get("Tags", []))
            for entity in data["entities"]
            if isinstance(entity.get("nbt"), dict) and entity["nbt"].get("Tags")
        },
    }
    if entities:
        out["entities"] = data["entities"]
        out["blocks"] = data["blocks"]
    return out


def version_run_to_dict(
    run: VersionRun, level: str = "warning", max_records: int = 50
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": run.version.id,
        "pack_format": run.version.format_string,
        "supported": run.supported,
        "status": run.status,
        "ticks": run.ticks,
        "commands": run.commands,
        "estimated_ms": round(run.total_us / 1000, 4),
        "worst_tick_ms": round(run.worst_tick_us / 1000, 4),
        "errors": run.errors,
        "warnings": run.warnings,
        "overlays": list(run.overlays),
        "unknown_commands": sorted(run.unknown_commands),
        "missing_features": sorted(run.missing_features),
        "failed_functions": list(run.failed_functions),
        "failed_tags": list(run.failed_tags),
        "missing_functions": list(run.missing_functions),
        "recursion": [list(cycle) for cycle in run.cycles],
    }
    if run.vanilla:
        data["client_jar"] = run.vanilla
    if run.cancelled:
        data["cancelled"] = True
    if run.tests:
        data["tests"] = {
            "passed": run.tests_passed,
            "failed": run.tests_failed,
            "skipped": run.tests_skipped,
        }
    data.update(records_to_list(run.records, level, max_records))
    return data


def _level(level: str) -> LogLevel:
    try:
        return LEVELS[level.lower()]
    except KeyError:
        raise AgentError(f"unknown level {level!r}: one of {', '.join(LEVELS)}") from None


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------


def versions(include_prereleases: bool = False) -> dict[str, Any]:
    """Every Minecraft version the emulator knows, with its data pack format."""
    known = [v for v in _versions.VERSIONS if include_prereleases or v.stable]
    return {
        "ok": True,
        "emulator": __version__,
        "latest": _versions.LATEST.id,
        "oldest": _versions.OLDEST.id,
        "versions": [
            {"id": v.id, "pack_format": v.format_string, "stable": v.stable} for v in known
        ],
        "summary": f"{len(known)} versions, {_versions.OLDEST.id} to {_versions.LATEST.id}",
    }


def check(
    packs: Packs,
    version: str | None = None,
    versions: Iterable[str] | None = None,
    *,
    declared: bool = False,
    boundaries: bool = False,
    severity: str = "info",
    vanilla: str | bool | None = "auto",
    download: bool = False,
    max_problems: int = MAX_PROBLEMS,
) -> dict[str, Any]:
    """Static problems: what a version refuses to load or cannot run. Nothing runs.

    One entry per version under ``results``; ``ok`` is false if any version
    has an error-level problem. With a client jar (``vanilla``) ids and JSON
    fields are checked against the real game files of that version.
    """
    if severity not in SEVERITIES:
        raise AgentError(f"unknown severity {severity!r}: one of {', '.join(SEVERITIES)}")
    datapack = load_packs(packs)
    if version and (versions or declared):
        raise AgentError("give version or versions/declared, not both")
    chosen = select_versions(
        datapack,
        versions,
        declared=declared,
        boundaries=boundaries,
        default=[resolve_version(version, datapack)],
    )
    lowest = SEVERITIES.index(severity)
    results: list[dict[str, Any]] = []
    tallies: list[str] = []
    errors = 0
    for target in chosen:
        assets = vanilla_assets(vanilla, target, download)
        schema = None
        if assets is not None:
            from datapack_emulator.emulator.analysis.schema import schema_for
            from datapack_emulator.settings.main import PATHS

            schema = schema_for(assets, PATHS.CACHE)
        problems = [
            problem
            for problem in find_problems(datapack, target, assets, schema)
            if SEVERITIES.index(problem.severity) <= lowest
        ]
        totals = count_problems(problems)
        errors += totals["error"]
        tallies.append(f"{target.id}: {totals['error']}E/{totals['warning']}W")
        results.append(
            {
                "version": target.id,
                "client_jar": assets.version_id if assets else None,
                "counts": totals,
                "problems": [problem.to_dict() for problem in problems[:max_problems]],
                "truncated": len(problems) > max_problems,
            }
        )
    load_errors = list(datapack.errors)
    ok = errors == 0 and not load_errors
    worst = ", ".join(tallies)
    return {
        "ok": ok,
        "summary": f"{'no errors' if ok else f'{errors} error(s)'} — {worst}",
        "pack": datapack.name,
        "versions": [target.id for target in chosen],
        "load_errors": load_errors,
        "results": results,
    }


def run(
    packs: Packs,
    version: str | None = None,
    *,
    ticks: int = 20,
    players: int = 1,
    seed: int = 0,
    commands: Sequence[str] = (),
    level: str = "info",
    max_records: int = MAX_RECORDS,
    world: bool = True,
    entities: bool = False,
    vanilla: str | bool | None = "auto",
    download: bool = False,
) -> dict[str, Any]:
    """Load the pack, run ``#minecraft:load`` and ``ticks`` ticks of
    ``#minecraft:tick``, then each of ``commands`` as if typed in the server
    console. Returns what was printed, the final world, the call graph and
    the estimated cost. A failing console command shows in its own
    ``commands`` entry only.

    ``commands`` is how to *ask* the world something afterwards:
    ``"scoreboard players get #game timer"``, ``"data get storage ns:s"``,
    ``"function ns:debug/dump"``, ``"execute if entity @e[tag=boss]"``.

    ``ok`` is false when a tick logged an error, a function or tag failed to
    load (the game only *warns* about those, which is why packs "do nothing"),
    a called function does not exist, or a whole kind of resource sits in
    folders this version does not read (``functions/`` on 1.21+,
    ``function/`` before) — the game says nothing about that at all.
    """
    if ticks < 0:
        raise AgentError("ticks must be 0 or more")
    datapack = load_packs(packs)
    target = resolve_version(version, datapack)
    bus = OutputBus()
    emulator = Emulator(
        datapack,
        version=target,
        players=players,
        output=bus,
        seed=seed,
        vanilla=vanilla_assets(vanilla, target, download),
    )
    emulator.run(ticks=ticks)
    ran_ticks = emulator.world.tick
    #: what the pack itself printed; console answers are kept with their command
    tick_records = list(bus.records)
    answers = []
    for line in commands:
        before = len(bus.records)
        try:
            result, _ = emulator.run_typed(line)
        except Exception as exc:  # a bad console line must not lose the run
            answers.append({"command": line, "success": False, "error": repr(exc)})
            continue
        answers.append(
            {
                "command": line,
                "success": bool(result.success) if result is not None else None,
                "value": result.value if result is not None else None,
                "output": [record_to_dict(r) for r in bus.records[before:]],
            }
        )
    errors = [r for r in tick_records if r.level >= LogLevel.ERROR]
    graph = graph_summary(emulator.call_graph())
    failed_functions = sorted(emulator.library.function_failures)
    failed_tags = sorted(emulator.library.tag_failures)
    ignored = [p.to_dict() for p in ignored_folders(datapack, target) if p.severity == "error"]
    broken = bool(
        errors or failed_functions or failed_tags or graph["missing_functions"] or ignored
    )
    chat = [
        r.message
        for r in tick_records
        if r.source.value == "game" and r.level < LogLevel.ERROR and r.recipient
    ]
    out: dict[str, Any] = {
        "ok": not broken,
        "summary": (
            f"{target.id}: {ran_ticks} tick(s), {len(errors)} error(s), "
            f"{len(failed_functions)} function(s) not loaded, "
            + (f"{len(ignored)} ignored folder(s), " if ignored else "")
            + f"{sum(1 for r in tick_records if r.level == LogLevel.WARNING)} warning(s), "
            f"{len(chat)} chat line(s)"
        ),
        "pack": datapack.name,
        "version": target.id,
        "pack_format": target.format_string,
        "client_jar": emulator.vanilla.version_id if emulator.vanilla else None,
        "ticks": ran_ticks,
        "load_errors": list(datapack.errors),
        "failed_functions": failed_functions,
        "failed_tags": failed_tags,
        "ignored_folders": ignored,
        "chat": chat[-50:],
        **records_to_list(tick_records, level, max_records),
        "commands": answers,
        "call_graph": graph,
        "profile": profile_summary(emulator.profiler),
    }
    if world:
        out["world"] = world_summary(emulator.world, target, entities=entities)
    return out


def test(
    packs: Packs,
    tests: Sequence[str | dict[str, Any] | CommandTest],
    version: str | None = None,
    versions: Iterable[str] | None = None,
    *,
    declared: bool = False,
    boundaries: bool = False,
    expect: str = "",
    expect_value: str = "",
    checks: Sequence[str] = (),
    ticks: int | None = None,
    players: int = 1,
    seed: int = 0,
    vanilla: str | bool | None = "auto",
    download: bool = False,
) -> dict[str, Any]:
    """Run command tests in a fresh world per version; ``ok`` = all passed.

    A test is ``"[TICK:]COMMAND"`` (run after that tick's functions) or a dict
    ``{"command", "at_tick", "expect", "expect_value", "checks"}``.
    ``expect``/``expect_value``/``checks`` given here apply to the single
    test passed. Check lines: ``score #g t = 3``, ``score @p t >= 1``,
    ``storage ns:s path = 1b``, ``@e[type=pig] = 2``,
    ``block 0 64 0 = stone``, ``if entity @a[tag=won]``.
    """
    if not tests:
        raise AgentError("no tests given")
    if expect or expect_value or checks:
        if len(tests) != 1:
            raise AgentError("expect, expect_value and checks go with exactly one test")
        parsed = [_read_test(tests[0])]
        parsed[0] = parse_test(
            {
                "command": parsed[0].command,
                "at_tick": parsed[0].at_tick,
                "expect": expect or parsed[0].expect,
                "expect_value": expect_value or parsed[0].expect_value,
                "checks": list(parsed[0].checks) + list(checks),
            }
        )
    else:
        parsed = [parse_test(item) for item in tests]
    datapack = load_packs(packs)
    if version and (versions or declared):
        raise AgentError("give version or versions/declared, not both")
    chosen = select_versions(
        datapack,
        versions,
        declared=declared,
        boundaries=boundaries,
        default=[resolve_version(version, datapack)],
    )
    last = max(item.at_tick for item in parsed)
    run_ticks = ticks if ticks is not None else last + 1
    single = vanilla_assets(vanilla, chosen[0], download) if len(chosen) == 1 else None
    engine = TestEngine(
        datapack,
        ticks=run_ticks,
        players=players,
        seed=seed,
        library=None if (single is not None or not vanilla) else default_library(),
        allow_download=download,
        tests=parsed,
        vanilla=single,
    )
    runs = engine.run(chosen)
    results = [test_result_to_dict(run.version, result) for run in runs for result in run.tests]
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skip")
    total = len(results) - skipped
    where = f"across {len(runs)} versions" if len(runs) > 1 else f"in {chosen[0].id}"
    return {
        "ok": failed == 0 and total > 0,
        "summary": f"{total - failed}/{total} passed {where}"
        + (f", {skipped} skipped" if skipped else ""),
        "pack": datapack.name,
        "versions": [run.version.id for run in runs],
        "ticks": run_ticks,
        "results": results,
        "unreached": [item.at_tick for item in parsed if item.at_tick >= run_ticks],
    }


def matrix(
    packs: Packs,
    versions: Iterable[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    declared: bool = False,
    boundaries: bool = False,
    ticks: int = 20,
    players: int = 1,
    seed: int = 0,
    tests: Sequence[str | dict[str, Any]] = (),
    vanilla: bool = True,
    download: bool = False,
    level: str = "warning",
    max_records: int = 20,
) -> dict[str, Any]:
    """Run the pack in a fresh world per version; one row per version.

    Without a selection: the range ``pack.mcmeta`` declares, else the pack's
    own version. ``boundaries`` keeps the first release of each pack format,
    which is usually all a compatibility check needs.
    """
    datapack = load_packs(packs)
    default = datapack.declared_versions() or [preferred_version(datapack)]
    chosen = select_versions(datapack, versions, start, end, declared, boundaries, default)
    parsed = [parse_test(item) for item in tests]
    engine = TestEngine(
        datapack,
        ticks=ticks,
        players=players,
        seed=seed,
        library=default_library() if vanilla else None,
        allow_download=download,
        tests=parsed,
    )
    runs = engine.run(chosen)
    rows = [version_run_to_dict(run, level, max_records) for run in runs]
    bad = [run.version.id for run in runs if run.status in ("errors", "tests failed")]
    unsupported = [run.version.id for run in runs if not run.supported]
    return {
        "ok": not bad,
        "summary": (
            f"{len(runs) - len(bad)}/{len(runs)} versions clean"
            + (f"; problems in {', '.join(bad)}" if bad else "")
            + (f"; pack.mcmeta does not claim {', '.join(unsupported)}" if unsupported else "")
        ),
        "pack": datapack.name,
        "versions": [run.version.id for run in runs],
        "results": rows,
    }


def explain(packs: Packs, command: str, version: str | None = None) -> dict[str, Any]:
    """Parse one command line the way the emulator would, without running it:
    whether this version has it, the features it uses and when they appeared."""
    datapack = load_packs(packs) if packs else None
    target = resolve_version(version, datapack)
    parsed = Command.parse(command.strip().removeprefix("/"), source="<agent>")
    if parsed is None:
        raise AgentError("empty command or comment")
    from datapack_emulator.emulator.commands import command_set

    commands = command_set(target)
    spec = commands.spec(parsed.name)
    missing = [
        {"feature": feature, "since": since.id if since else None}
        for feature, since in commands.missing_features(parsed.features())
    ]
    available = bool(spec.available) and not missing
    return {
        "ok": available,
        "summary": (
            f"{parsed.name} is available in {target.id}"
            if available
            else f"{parsed.name}: not fully available in {target.id}"
        ),
        "version": target.id,
        "command": parsed.name,
        "exists": bool(spec.available),
        "added_in": spec.since.id if spec.since else None,
        "removed_in": spec.removed.id if spec.removed else None,
        "emulated": spec.handler is not None,
        "features": sorted(parsed.features()),
        "missing_features": missing,
    }


__all__ = [
    "AgentError",
    "check",
    "explain",
    "load_packs",
    "matrix",
    "parse_test",
    "resolve_version",
    "run",
    "test",
    "versions",
]
