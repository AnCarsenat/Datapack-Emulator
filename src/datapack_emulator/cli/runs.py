"""``run``, ``matrix`` and ``test``: emulating packs from a terminal or CI."""

from __future__ import annotations

import argparse
from pathlib import Path

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    USAGE,
    CliError,
    Inputs,
    add_output_filter,
    add_source_arguments,
    add_vanilla_arguments,
    add_version_selection,
    chosen_versions,
    err,
    load_inputs,
    parse_version,
    printing_bus,
    report_path,
    vanilla_for,
)
from datapack_emulator.cli.junit import write_junit
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.engine import TestEngine, VersionRun
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.testing import CommandTest
from datapack_emulator.emulator.vanilla import default_library
from datapack_emulator.settings import EMULATION

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def add_world_arguments(parser: argparse.ArgumentParser) -> None:
    """Ticks, players and seed; a project's values are the defaults."""
    parser.add_argument("--ticks", type=int, default=None, help="default: 20, or the project's")
    parser.add_argument("--players", type=int, default=None, help="default: 1, or the project's")
    parser.add_argument("--seed", type=int, default=None, help="default: 0, or the project's")


def ticks_setting(arguments: argparse.Namespace, inputs: Inputs) -> int:
    """``--ticks``, else the project's (its endless ``-1`` means the default)."""
    if arguments.ticks is not None:
        if arguments.ticks < 0:
            raise CliError("--ticks must be 0 or more")
        return arguments.ticks
    ticks = inputs.setting(arguments, "ticks", EMULATION.DEFAULT_TICKS)
    return ticks if ticks >= 0 else EMULATION.DEFAULT_TICKS


def build_emulator(arguments: argparse.Namespace, inputs: Inputs, bus) -> Emulator:
    version = inputs.version(arguments)
    assets = vanilla_for(arguments, version, inputs)
    if assets is not None:
        err(f"vanilla assets: {assets.summary}")
    return Emulator(
        inputs.datapack,
        version=version,
        players=inputs.setting(arguments, "players", EMULATION.DEFAULT_PLAYERS),
        output=bus,
        seed=inputs.setting(arguments, "seed", EMULATION.DEFAULT_SEED),
        vanilla=assets,
    )


def command_run(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    bus = printing_bus(arguments)
    emulator = build_emulator(arguments, inputs, bus)
    ticks = ticks_setting(arguments, inputs)
    profiler = emulator.run(ticks=ticks)
    datapack = inputs.datapack

    print(
        f"\n{'function':40} {'calls':>6} {'cmds':>7} {'self ms':>9} {'total ms':>9} {'ms/tick':>9}"
    )
    for function_id, entry in profiler.sorted_entries():
        one = profiler.per_tick(entry)
        print(
            f"{function_id:40} {int(entry['calls']):6d} {int(entry['commands']):7d} "
            f"{entry['self_us'] / 1000:9.3f} {entry['total_us'] / 1000:9.3f} "
            f"{one['total_us'] / 1000:9.4f}"
        )
    print(
        f"\n{emulator.version.id}: {ticks} tick(s), "
        f"{profiler.total_us / 1000:.2f} ms estimated, "
        f"worst tick {profiler.worst_tick_us / 1000:.2f} ms"
    )

    report = profiler.write_html(
        report_path(arguments, "index.html"),
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
        dot_name = datapack.name.replace(" + ", "+")
        print(f"dot: {graph.write_dot(report.parent / f'{dot_name}.dot')}")
    return OK


def register_run(subparsers) -> None:
    run = subparsers.add_parser(
        "run",
        help="emulate the pack for one version",
        description="Run #minecraft:load and #minecraft:tick for some ticks, print every "
        "record, then the profiler table and call-graph findings.",
    )
    add_source_arguments(run)
    run.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )
    add_world_arguments(run)
    run.add_argument("--html", type=Path, default=None, help="default: generated/index.html")
    run.add_argument("--dot", action="store_true", help="also write the call graph (Graphviz)")
    add_output_filter(run)
    add_vanilla_arguments(run)
    run.set_defaults(handler=command_run)


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------


def engine_for(arguments: argparse.Namespace, inputs: Inputs, tests: list[CommandTest]):
    return TestEngine(
        inputs.datapack,
        ticks=ticks_setting(arguments, inputs),
        players=inputs.setting(arguments, "players", EMULATION.DEFAULT_PLAYERS),
        seed=inputs.setting(arguments, "seed", EMULATION.DEFAULT_SEED),
        library=default_library() if arguments.vanilla else None,
        allow_download=arguments.download,
        tests=tests,
    )


def engine_default(inputs: Inputs) -> list:
    """What a project ticked in the engine window, else every known version."""
    if inputs.project is not None and inputs.project.engine_versions:
        return [parse_version(item) for item in inputs.project.engine_versions]
    return list(versions.VERSIONS)


def progress(index: int, total: int, version) -> None:
    err(f"[{index}/{total}] {version.id}")


def command_matrix(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    chosen = chosen_versions(arguments, inputs, engine_default(inputs))
    tests = [test for test in inputs.tests if test.enabled] if arguments.tests else []
    engine = engine_for(arguments, inputs, tests)
    results = engine.run(chosen, progress=progress)

    print(
        f"\n{'version':10} {'format':7} {'status':12} {'cmds':>6} {'total ms':>9} "
        f"{'worst ms':>9} {'warn':>5} {'err':>5} {'tests':>6}  notes"
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
            f"{run.warnings:5d} {run.errors:5d} {run.tests_summary:>6}  {'; '.join(notes)}"
        )

    report = TestEngine.write_html(
        results, report_path(arguments, "matrix.html"), inputs.datapack.name
    )
    print(f"\nmatrix report: {report}")
    if arguments.junit and tests:
        print(f"junit report: {write_junit(results, arguments.junit, inputs.datapack.name)}")
    if arguments.strict and any(run.status in ("errors", "tests failed") for run in results):
        return FAILED
    return OK


def register_matrix(subparsers) -> None:
    matrix = subparsers.add_parser(
        "matrix",
        help="run the pack across versions",
        description="One fresh world per version; one row per version. Without a version "
        "option: the versions a project ticked in the engine window, else every release.",
    )
    add_source_arguments(matrix)
    add_version_selection(matrix)
    add_world_arguments(matrix)
    matrix.add_argument("--tests", action="store_true", help="also run the project's tests")
    matrix.add_argument("--junit", type=Path, default=None, help="with --tests: JUnit XML report")
    matrix.add_argument(
        "--strict",
        action="store_true",
        help="exit with 1 when a version has errors or failed tests",
    )
    matrix.add_argument("--html", type=Path, default=None, help="default: generated/matrix.html")
    add_vanilla_arguments(matrix, per_version=True)
    matrix.set_defaults(handler=command_matrix)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


def parse_test(text: str) -> CommandTest:
    """``[TICK:]COMMAND`` — ``5:scoreboard players get #g t``."""
    head, sep, rest = text.partition(":")
    if sep and head.strip().isdigit():
        return CommandTest(rest.strip(), at_tick=int(head))
    return CommandTest(text.strip())


def command_test(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    tests = [test for test in inputs.tests if test.enabled or arguments.include_disabled]
    for test in tests:
        test.enabled = True
    extra = [parse_test(text) for text in arguments.test or []]
    if extra and arguments.only:
        tests = extra
    else:
        tests += extra
    if arguments.expect or arguments.expect_value:
        if len(extra) != 1:
            raise CliError("--expect and --expect-value go with exactly one --test")
        extra[0].expect = arguments.expect or ""
        extra[0].expect_value = arguments.expect_value or ""
    if not tests:
        err("no tests: the project has none enabled and no --test was given")
        return USAGE

    if arguments.versions or arguments.start or arguments.end or arguments.declared:
        chosen = chosen_versions(arguments, inputs, [])
    elif arguments.all_versions or arguments.engine:
        chosen = chosen_versions(arguments, inputs, engine_default(inputs))
    else:
        chosen = chosen_versions(arguments, inputs, [inputs.version(arguments)])

    last = max(test.at_tick for test in tests)
    if arguments.ticks is None:
        arguments.ticks = last + 1
    engine = engine_for(arguments, inputs, tests)
    if engine.ticks <= last:
        err(f"note: {engine.ticks} tick(s) do not reach the test at tick {last}")
    bus = printing_bus(arguments) if arguments.verbose else None
    results: list[VersionRun] = []
    for index, version in enumerate(chosen, start=1):
        if len(chosen) > 1:
            progress(index, len(chosen), version)
        results.append(engine.run_version(version, output=bus))

    failed = 0
    for run in results:
        for result in run.tests:
            mark = "PASS" if result.passed else "FAIL"
            failed += not result.passed
            print(
                f"{mark} {run.version.id:10} tick {result.test.at_tick:<4} "
                f"{result.test.command}  — {result.reason}"
            )
            if not result.passed and arguments.show_records:
                for record in result.records:
                    print(f"       {record.format()}")
    total = sum(len(run.tests) for run in results)
    where = f"across {len(results)} versions" if len(results) > 1 else f"in {results[0].version.id}"
    print(f"\n{total - failed}/{total} passed {where}")
    if arguments.junit:
        print(f"junit report: {write_junit(results, arguments.junit, inputs.datapack.name)}")
    return FAILED if failed else OK


def register_test(subparsers) -> None:
    test = subparsers.add_parser(
        "test",
        help="run a project's tests; exit 1 when one fails",
        description="Run the tests saved in a project (the environment tab's) and/or tests "
        "given with --test, in one fresh world per version. Without a version option the "
        "project's version is used. Exit status: 0 all passed, 1 a test failed, 2 no tests.",
    )
    add_source_arguments(test)
    test.add_argument("--version", default=None, help="one version (default: the project's)")
    add_version_selection(test)
    test.add_argument(
        "--engine",
        action="store_true",
        help="the versions the project ticked in the engine window",
    )
    add_world_arguments(test)
    test.add_argument(
        "--test",
        action="append",
        metavar="[TICK:]COMMAND",
        help="a test to add (repeatable), e.g. '5:scoreboard players get #g t'",
    )
    test.add_argument(
        "--expect", default=None, help="with one --test: text the output must contain"
    )
    test.add_argument(
        "--expect-value", default=None, help="with one --test: range the result must be in (1..)"
    )
    test.add_argument(
        "--only", action="store_true", help="run only the --test tests, not the project's"
    )
    test.add_argument(
        "--include-disabled", action="store_true", help="also run tests unticked in the project"
    )
    test.add_argument("--junit", type=Path, default=None, help="write a JUnit XML report")
    test.add_argument(
        "--show-records", action="store_true", help="print the records of failed tests"
    )
    test.add_argument(
        "--verbose", action="store_true", help="print every record while the tests run"
    )
    add_output_filter(test)
    add_vanilla_arguments(test, per_version=True)
    test.set_defaults(handler=command_test)
