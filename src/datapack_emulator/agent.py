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
    lists capped by ``max_records`` / ``max_problems`` /
    ``max_output_per_command`` say so, with the full count beside them, so a
    partial list is never mistaken for a whole one.
``trimmed``
    ``run`` keeps its result under ``max_result_chars``: when it would be
    larger, the biggest lists are cut further and each cut is listed here.

Bad input (a missing folder, an unknown version, a malformed test) raises
:class:`AgentError` with a message meant to be shown to whoever asked.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from datapack_emulator import __version__
from datapack_emulator.emulator import versions as _versions
from datapack_emulator.emulator.analysis.problems import (
    SEVERITIES,
    find_problems,
    ignored_folders,
    load_blockers,
)
from datapack_emulator.emulator.analysis.problems import count as count_problems
from datapack_emulator.emulator.analysis.world_view import world_to_dict
from datapack_emulator.emulator.commands import Command
from datapack_emulator.emulator.datapack import DatapackSet, preferred_version
from datapack_emulator.emulator.engine import TestEngine, VersionRun
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, OutputBus
from datapack_emulator.emulator.runtime.world import parse_chunks
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
#: lines kept per console command of ``run`` (errors first, then the newest)
MAX_OUTPUT_PER_COMMAND = 20
#: the size ``run`` keeps its JSON under (characters of compact JSON, ~40 KB)
MAX_RESULT_CHARS = 40_000
#: the function id console commands run as (``ExecutionContext`` of the server)
CONSOLE = "<server>"


class AgentError(ValueError):
    """The request cannot be carried out as given (bad path, version, test)."""


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


def load_packs(packs: Packs) -> DatapackSet:
    """One datapack folder (or zip), or several in load order."""
    from datapack_emulator.emulator import costs

    costs.ensure_user_calibration()
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


def _schema_of(assets: VanillaAssets | None):
    """The field schema of a client jar (learned once, kept in the cache)."""
    if assets is None:
        return None
    from datapack_emulator.emulator.analysis.schema import schema_for
    from datapack_emulator.settings.main import PATHS

    return schema_for(assets, PATHS.CACHE)


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


def _keep(items: list, limit: int, *first: Callable[[Any], bool]) -> list:
    """At most ``limit`` of ``items`` in their order: those matching each of
    ``first`` in turn (oldest of them first), then the newest of the rest."""
    limit = max(limit, 0)
    if len(items) <= limit:
        return list(items)
    kept: set[int] = set()
    for wanted in first:
        for index, item in enumerate(items):
            if len(kept) >= limit:
                break
            if index not in kept and wanted(item):
                kept.add(index)
    for index in range(len(items) - 1, -1, -1):
        if len(kept) >= limit:
            break
        kept.add(index)
    return [item for index, item in enumerate(items) if index in kept]


def records_to_list(
    records: list[LogRecord], level: str = "info", limit: int = MAX_RECORDS
) -> dict[str, Any]:
    """Records at ``level`` or above, newest kept when capped (errors first)."""
    minimum = _level(level)
    wanted = [record for record in records if record.level >= minimum]
    shown = _keep(wanted, limit, lambda r: r.level >= LogLevel.ERROR)
    return {
        "records": [record_to_dict(record) for record in shown],
        "records_total": len(wanted),
        "truncated": len(shown) < len(wanted),
    }


def command_output(
    records: list[LogRecord], level: str = "info", limit: int = MAX_OUTPUT_PER_COMMAND
) -> dict[str, Any]:
    """What one console command printed, filtered like ``records``: lines at
    ``level`` or above, plus the command's own answer whatever its level (the
    reason it was typed). Capped at ``limit``: errors first, then the answer,
    then the newest of the rest."""
    minimum = _level(level)
    wanted = [r for r in records if r.level >= minimum or _is_answer(r)]
    shown = _keep(wanted, limit, lambda r: r.level >= LogLevel.ERROR, _is_answer)
    return {
        "output": [record_to_dict(record) for record in shown],
        "output_total": len(wanted),
        "truncated": len(shown) < len(wanted),
    }


def _is_answer(record: LogRecord) -> bool:
    """Said by the console command itself, not by a function it ran."""
    return record.function == CONSOLE and record.source.value == "game"


# ---------------------------------------------------------------------------
# keeping a result small
# ---------------------------------------------------------------------------

#: a part of a result smaller than this is not worth cutting
_SMALLEST_CUT = 256


def _chars(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _record_lists(result: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """``(label, owner, key)`` of every list of records in a ``run`` result:
    cut keeping errors and the newest, and flagged ``truncated`` on the owner."""
    places = [("records", result, "records")]
    for index, answer in enumerate(result.get("commands") or []):
        if isinstance(answer, dict) and isinstance(answer.get("output"), list):
            places.append((f"commands[{index}].output", answer, "output"))
    return places


def _other_parts(result: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """``(label, owner, key)`` of the other parts that can grow without bound."""
    places: list[tuple[str, Any, Any]] = [
        ("chat", result, "chat"),
        ("failed_functions", result, "failed_functions"),
        ("failed_tags", result, "failed_tags"),
        ("load_blockers", result, "load_blockers"),
        ("ignored_folders", result, "ignored_folders"),
        ("load_errors", result, "load_errors"),
    ]
    for owner_key in ("world", "call_graph", "profile"):
        owner = result.get(owner_key)
        if isinstance(owner, dict):
            places += [
                (f"{owner_key}.{key}", owner, key)
                for key, value in owner.items()
                if isinstance(value, (list, dict, str))
            ]
    tests = result.get("tests")
    if isinstance(tests, list):
        places.append(("tests", result, "tests"))
    return [place for place in places if isinstance(place[1].get(place[2]), (list, dict, str))]


def _cut(container: Any, key: Any, label: str, newest: bool) -> tuple[str, int, int, str] | None:
    """Halve the biggest piece under ``container[key]``: go down while one
    child holds most of the size, then keep half of that list, dict or
    string (the newest half of a list with ``newest``). Returns ``(label,
    count before, count after, unit)``; None when nothing is worth cutting."""
    node = container[key]
    while isinstance(node, (list, dict)) and node:
        children = list(node.items()) if isinstance(node, dict) else list(enumerate(node))
        sizes = [(_chars(child), name) for name, child in children]
        biggest, name = max(sizes, key=lambda pair: pair[0])
        child = node[name]
        if len(children) > 1 and biggest * 2 <= sum(size for size, _ in sizes):
            break  # no single child dominates: cut this one
        if not isinstance(child, (list, dict, str)) or biggest < _SMALLEST_CUT:
            break
        container, key, node = node, name, child
        label += f"[{name}]" if isinstance(name, int) else f".{name}"
        newest = False
    if isinstance(node, str):
        if len(node) < _SMALLEST_CUT:
            return None
        keep = len(node) // 2
        container[key] = node[:keep] + f"… [{len(node) - keep} more characters]"
        return (label, len(node), keep, "characters")
    if isinstance(node, list) and node:
        keep = len(node) // 2
        container[key] = node[len(node) - keep :] if newest else node[:keep]
        return (label, len(node), keep, "items")
    if isinstance(node, dict) and node:
        keep = len(node) // 2
        container[key] = dict(list(node.items())[:keep])
        return (label, len(node), keep, "keys")
    return None


def _halve_records(owner: dict[str, Any], key: str, label: str) -> tuple[str, int, int, str]:
    items = owner[key]
    kept = _keep(items, len(items) // 2, lambda record: record.get("level") == "error")
    owner[key] = kept
    owner["truncated"] = True
    return (label, len(items), len(kept), "items")


def _cut_commands(result: dict[str, Any], sizes: list[int]) -> tuple[str, int, int, str] | None:
    """One cut across every console command at once, so that many commands
    cost a few cuts (and a few ``trimmed`` notes), not one each: halve every
    output of more than one line; failing that, halve each long message of
    the outputs; failing that, drop the newer half of the commands. When one
    output alone is most of their size, None: that one is cut on its own."""
    answers = result["commands"]
    outputs = [
        answer
        for answer in answers
        if isinstance(answer, dict) and isinstance(answer.get("output"), list)
    ]
    if sizes and max(sizes) * 2 > sum(sizes) and len(outputs) > 1:
        return None
    if any(len(answer["output"]) > 1 for answer in outputs):
        before = sum(len(answer["output"]) for answer in outputs)
        for answer in outputs:
            if len(answer["output"]) > 1:
                _halve_records(answer, "output", "")
        return ("commands[].output", before, sum(len(a["output"]) for a in outputs), "items")
    long = [answer for answer in outputs if _chars(answer["output"]) >= 2 * _SMALLEST_CUT]
    if long:
        before = sum(_chars(answer["output"]) for answer in long)
        for answer in long:
            _cut(answer, "output", "", newest=False)
            answer["truncated"] = True
        after = sum(_chars(answer["output"]) for answer in long)
        return ("commands[].output messages", before, after, "characters")
    if len(answers) > 1:
        keep = len(answers) // 2
        result["commands"] = answers[:keep]
        result["commands_truncated"] = True
        return ("commands", len(answers), keep, "items")
    return None


def _cut_biggest(result: dict[str, Any]) -> tuple[str, int, int, str] | None:
    """One cut in the biggest part of ``result`` that can lose something."""
    places: list[tuple[int, str, dict[str, Any], Any, bool]] = []
    output_sizes: list[int] = []
    for label, owner, key in _record_lists(result):
        size = _chars(owner[key])
        if owner is not result:
            output_sizes.append(size)
        places.append((size, label, owner, key, owner is result and len(owner[key]) > 1))
    answers = result.get("commands")
    if isinstance(answers, list) and answers:
        places.append((_chars(answers), "commands", result, "commands", False))
    for label, owner, key in _other_parts(result):
        places.append((_chars(owner[key]), label, owner, key, False))
    places.sort(key=lambda place: place[0], reverse=True)
    for size, label, owner, key, records in places:
        if size < _SMALLEST_CUT:
            break
        if records:
            return _halve_records(owner, key, label)
        if label == "commands":
            done = _cut_commands(result, output_sizes)
        else:
            done = _cut(owner, key, label, newest=label == "chat")
        if done is not None:
            if label == "chat":
                result["chat_truncated"] = True
            return done
    return None


def fit_result(result: dict[str, Any], max_chars: int | None = MAX_RESULT_CHARS) -> dict[str, Any]:
    """Keep a ``run`` result under ``max_chars`` characters of JSON (a soft
    limit; ``None`` or 0: no limit), in place. The biggest part is halved
    until the whole fits: lists of records keep their errors and newest lines
    and say ``truncated``; anything else (chat, scores, deep storage, a long
    message) is cut from its end. Every cut is listed under ``trimmed`` (and
    the result says ``trimmed: []`` when nothing was), so a partial result is
    never mistaken for a whole one."""
    if not max_chars or max_chars <= 0:
        return result
    original = _chars(result)
    cuts: dict[str, list[Any]] = {}
    while True:
        # the notes count too: they are part of what has to fit
        result["trimmed"] = [
            f"{label}: {before} -> {after} {unit}" for label, (before, after, unit) in cuts.items()
        ]
        if cuts:
            result["trimmed"].append(
                f"the result had {original} characters of JSON; max_result_chars is {max_chars}"
            )
        if _chars(result) <= max_chars:
            break
        done = _cut_biggest(result)
        if done is None:
            break
        label, before, after, unit = done
        cuts.setdefault(label, [before, after, unit])[1] = after
    return result


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
    server: bool = False,
    accept_eula: bool = False,
    server_jar: PathLike | None = None,
    java: str = "java",
) -> dict[str, Any]:
    """Static problems: what a version refuses to load or cannot run. Nothing runs.

    One entry per version under ``results``; ``ok`` is false if any version
    has an error-level problem. With a client jar (``vanilla``) ids and JSON
    fields are checked against the real game files of that version.

    ``server=True`` also runs the pack on a real dedicated server of each
    version (Java; the jar is downloaded once) and adds what its log says,
    under ``results[].server``: everything the server refuses, exactly. It
    needs ``accept_eula=True``, which only the user can say: running a
    Minecraft server means accepting the Minecraft EULA.
    """
    if server and not accept_eula:
        raise AgentError(
            "server=True runs a Minecraft server, which means accepting the Minecraft EULA "
            "(https://aka.ms/MinecraftEULA): pass accept_eula=True only if the user agreed"
        )
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
    fatal = 0
    blockers: list[dict[str, Any]] = []
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
        fatal += totals["fatal"]
        blockers += [
            {"version": target.id, **problem.to_dict()} for problem in load_blockers(problems)
        ]
        tallies.append(
            f"{target.id}: "
            + (f"{totals['fatal']} FATAL/" if totals["fatal"] else "")
            + f"{totals['error']}E/{totals['warning']}W"
        )
        entry: dict[str, Any] = {
            "version": target.id,
            "client_jar": assets.version_id if assets else None,
            "counts": totals,
            "problems": [problem.to_dict() for problem in problems[:max_problems]],
            "truncated": len(problems) > max_problems,
        }
        if server:
            on_server = _on_server(datapack, target, server_jar, java)
            entry["server"] = on_server.to_dict()
            server_totals = count_problems(on_server.problems)
            fatal += server_totals["fatal"]
            errors += server_totals["error"]
            blockers += [
                {"version": target.id, **problem.to_dict()}
                for problem in load_blockers(on_server.problems)
            ]
            tallies[-1] += (
                " (server: " + ("started" if on_server.started else "WORLD DID NOT LOAD") + ")"
            )
        results.append(entry)
    load_errors = list(datapack.errors)
    ok = errors == 0 and fatal == 0 and not load_errors
    worst = ", ".join(tallies)
    if fatal:
        headline = f"{fatal} problem(s) stop the world or the pack from loading"
    else:
        headline = "no errors" if ok else f"{errors} error(s)"
    return {
        "ok": ok,
        "summary": f"{headline} — {worst}",
        "load_blockers": blockers,
        "pack": datapack.name,
        "versions": [target.id for target in chosen],
        "load_errors": load_errors,
        "results": results,
    }


def _on_server(datapack: DatapackSet, target: Version, jar: PathLike | None, java: str):
    from datapack_emulator.emulator.server_check import check_on_server
    from datapack_emulator.settings.main import PATHS

    try:
        return check_on_server(
            [pack.path for pack in datapack.packs],
            datapack.namespaces,
            target,
            PATHS.CACHE / "server-check" / target.id,
            accept_eula=True,
            jar=Path(jar) if jar else None,
            java=java,
        )
    except (OSError, KeyError, TimeoutError, ValueError) as exc:
        raise AgentError(f"cannot run the {target.id} server: {exc}") from exc


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
    max_output_per_command: int = MAX_OUTPUT_PER_COMMAND,
    max_result_chars: int | None = MAX_RESULT_CHARS,
    unloaded_chunks: Sequence[Sequence[Any] | str] = (),
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

    Size: ``level`` filters ``records`` and each command's ``output`` alike
    (a command's own answer is always kept); ``max_records`` and
    ``max_output_per_command`` cap them (errors first, then the newest), and
    ``max_result_chars`` (``None``/0: off) trims the biggest parts further
    when the whole result would still be larger, listing each cut under
    ``trimmed``.

    ``unloaded_chunks``: chunks (``[cx, cz]`` or ``[cx, cz, dimension]``,
    chunk coordinates = block coordinates // 16, overworld by default) that
    count as unloaded for the whole run: ``execute if loaded`` fails there,
    their entities (not players) are out of reach of selectors and do not
    tick, and block commands there fail with "That position is not loaded".
    Every other chunk is loaded.
    """
    if ticks < 0:
        raise AgentError("ticks must be 0 or more")
    if max_output_per_command < 0:
        raise AgentError("max_output_per_command must be 0 or more")
    datapack = load_packs(packs)
    target = resolve_version(version, datapack)
    _level(level)  # a bad level is refused before the run, not after it
    bus = OutputBus()
    emulator = Emulator(
        datapack,
        version=target,
        players=players,
        output=bus,
        seed=seed,
        vanilla=vanilla_assets(vanilla, target, download),
        unloaded_chunks=_chunks(unloaded_chunks),
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
                **command_output(bus.records[before:], level, max_output_per_command),
            }
        )
    errors = [r for r in tick_records if r.level >= LogLevel.ERROR]
    graph = graph_summary(emulator.call_graph())
    failed_functions = sorted(emulator.library.function_failures)
    failed_tags = sorted(emulator.library.tag_failures)
    ignored = [p.to_dict() for p in ignored_folders(datapack, target) if p.severity == "error"]
    blockers = [
        p.to_dict()
        for p in load_blockers(
            find_problems(datapack, target, emulator.vanilla, _schema_of(emulator.vanilla))
        )
    ]
    broken = bool(
        errors
        or failed_functions
        or failed_tags
        or graph["missing_functions"]
        or ignored
        or blockers
    )
    chat = [
        r.message
        for r in tick_records
        if r.source.value == "game" and r.level < LogLevel.ERROR and r.recipient
    ]
    out: dict[str, Any] = {
        "ok": not broken,
        "summary": (
            (
                f"WILL NOT LOAD ({len(blockers)} fatal problem(s)); emulated anyway: "
                if blockers
                else ""
            )
            + f"{target.id}: {ran_ticks} tick(s), {len(errors)} error(s), "
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
        "load_blockers": blockers,
        "chat": chat[-50:],
        "chat_total": len(chat),
        **records_to_list(tick_records, level, max_records),
        "commands": answers,
        "call_graph": graph,
        "profile": profile_summary(emulator.profiler),
    }
    if emulator.unloaded_chunks:
        out["unloaded_chunks"] = chunks_to_list(emulator.unloaded_chunks)
    if world:
        out["world"] = world_summary(emulator.world, target, entities=entities)
    return fit_result(out, max_result_chars)


def _chunks(chunks: Iterable[Sequence[Any] | str]) -> list[Sequence[Any] | str]:
    """``unloaded_chunks`` checked before the run."""
    if isinstance(chunks, str):
        raise AgentError("unloaded_chunks is a list of [x, z] or [x, z, dimension] chunks")
    chunks = list(chunks)
    try:
        parse_chunks(chunks)
    except (TypeError, ValueError) as exc:
        raise AgentError(f"unloaded_chunks: {exc}") from None
    return chunks


def chunks_to_list(chunks: dict[str, set[tuple[int, int]]]) -> list[list[Any]]:
    """Chunks by dimension -> ``[[cx, cz, dimension], …]``, sorted."""
    return [
        [x, z, dimension] for dimension, found in sorted(chunks.items()) for x, z in sorted(found)
    ]


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
    unloaded_chunks: Sequence[Sequence[Any] | str] = (),
) -> dict[str, Any]:
    """Run command tests in a fresh world per version; ``ok`` = all passed.

    A test is ``"[TICK:]COMMAND"`` (run after that tick's functions) or a dict
    ``{"command", "at_tick", "expect", "expect_value", "checks"}``.
    ``expect``/``expect_value``/``checks`` given here apply to the single
    test passed. Check lines: ``score #g t = 3``, ``score @p t >= 1``,
    ``storage ns:s path = 1b``, ``@e[type=pig] = 2``,
    ``block 0 64 0 = stone``, ``if entity @a[tag=won]``.
    ``unloaded_chunks`` is the same as :func:`run`'s.
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
        unloaded_chunks=_chunks(unloaded_chunks),
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


def server_profile(
    report: PathLike,
    packs: Packs | None = None,
    version: str | None = None,
    *,
    ticks: int = 200,
    players: int = 1,
    top_lines: int = 10,
) -> dict[str, Any]:
    """Real timings from a server's ``/perf`` report (the zip in
    ``debug/profiling/``, or its ``server/profiling.txt``).

    Every function the server ran, with its measured ms per tick and its
    dearest lines. With ``packs``, the pack is also emulated for ``ticks``
    and each function gets the emulator's estimate and the ratio
    (measured / estimated) under ``compared``. ``ok`` is false when the
    server was lagging (more than 50 ms per tick on average).
    """
    from datapack_emulator.emulator.server_profile import ServerProfile, compare

    try:
        profile = ServerProfile.read(report)
    except (OSError, ValueError) as exc:
        raise AgentError(f"cannot read {report}: {exc}") from exc
    data = profile.to_dict(top_lines=top_lines)
    top = data["functions"][0] if data["functions"] else None
    out: dict[str, Any] = {
        "ok": data["mspt"] <= 50.0,
        "summary": (
            f"{profile.version}: {data['mspt']:.3f} ms per tick, datapacks "
            f"{data['datapack_ms_per_tick']:.3f} ms"
            + (f"; dearest {top['function']} {top['ms_per_tick']:.4f} ms" if top else "")
        ),
        **data,
    }
    if packs is not None:
        datapack = load_packs(packs)
        target = resolve_version(version or profile.version, datapack)
        emulator = Emulator(datapack, version=target, players=players, output=OutputBus())
        emulator.run(ticks=ticks)
        out["compared"] = {
            "version": target.id,
            "ticks": ticks,
            "note": "estimates are warm-loop costs: a lower bound, read the ratio as a ranking",
            "functions": compare(profile, emulator.profiler),
        }
    return out


__all__ = [
    "AgentError",
    "check",
    "explain",
    "load_packs",
    "matrix",
    "parse_test",
    "resolve_version",
    "run",
    "server_profile",
    "test",
    "versions",
]
