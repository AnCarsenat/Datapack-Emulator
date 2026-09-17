"""``run``, ``matrix`` and ``test``: emulating packs from a terminal or CI."""

from __future__ import annotations

import argparse
import time
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
    count,
    err,
    interruptible,
    library_for,
    load_inputs,
    parse_version,
    printing_bus,
    report_path,
    vanilla_for,
    versions_given,
)
from datapack_emulator.cli.debug import add_debug_arguments, trace_debugger
from datapack_emulator.cli.junit import write_junit
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.engine import TestEngine, VersionRun
from datapack_emulator.emulator.runtime.emulator import Emulator
from datapack_emulator.emulator.testing import CommandTest, TestResult, TestSchedule, valid_range
from datapack_emulator.emulator.versions import Version
from datapack_emulator.settings import EMULATION

#: real time: 20 ticks per second
TICK_SECONDS = 0.05

# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def endless_count(text: str) -> int:
    """An argparse type: 0 or more, or -1 for "until Ctrl+C"."""
    value = count(text) if text != "-1" else -1
    return value


def add_world_arguments(parser: argparse.ArgumentParser, endless: bool = False) -> None:
    """Ticks, players and seed; a project's values are the defaults."""
    parser.add_argument(
        "--ticks",
        type=endless_count if endless else count,
        default=None,
        help="default: 20, or the project's" + ("; -1 runs until Ctrl+C" if endless else ""),
    )
    parser.add_argument("--players", type=count, default=None, help="default: 1, or the project's")
    parser.add_argument("--seed", type=int, default=None, help="default: 0, or the project's")


def ticks_setting(arguments: argparse.Namespace, inputs: Inputs) -> int:
    """``--ticks``, else the project's (its endless ``-1`` means the default)."""
    if arguments.ticks is not None:
        return arguments.ticks
    ticks = inputs.setting(arguments, "ticks", EMULATION.DEFAULT_TICKS)
    if ticks < 0:
        err(f"note: the project runs until stopped; running {EMULATION.DEFAULT_TICKS} ticks")
        return EMULATION.DEFAULT_TICKS
    return ticks


def print_result(version: Version, result: TestResult, show_records: bool | str) -> None:
    """One PASS/FAIL line; ``show_records`` "failed" (or True) or "all" adds the
    test's records."""
    mark = "PASS" if result.passed else "FAIL"
    print(
        f"{mark} {version.id:10} tick {result.test.at_tick:<4} "
        f"{result.test.command}  — {result.reason}"
    )
    if show_records == "all" or (show_records and not result.passed):
        for record in result.records:
            print(f"       {record.format()}")


def add_show_records(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--show-records",
        nargs="?",
        const="failed",
        default=None,
        choices=["failed", "all"],
        help="print the records of failed tests (or of all tests)",
    )


def warn_unreached(tests: list[CommandTest], ticks: int) -> None:
    if ticks < 0:
        return
    late = [test for test in tests if test.enabled and test.at_tick >= ticks]
    if late:
        last = max(test.at_tick for test in late)
        err(
            f"note: {len(late)} test(s) run after the last of the {ticks} tick(s) "
            f"(the latest at tick {last}) and will fail as not reached"
        )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


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
    with_tests = arguments.tests
    if with_tests is None:
        with_tests = bool(inputs.project and inputs.project.tests_during_runs)
    tests = [test for test in inputs.tests if test.enabled] if with_tests else []
    if with_tests and not tests:
        err("note: no enabled tests to run during the run")

    warn_unreached(tests, ticks)
    schedule = TestSchedule(tests) if tests else None
    realtime = arguments.realtime or bool(
        arguments.realtime is None and inputs.project and inputs.project.speed == "realtime"
    )
    trace = trace_debugger(arguments, emulator)
    emulator.start()
    if ticks == 0:
        emulator.run(ticks=0)
    done = 0
    try:
        while ticks < 0 or done < ticks:
            started = time.perf_counter()
            tick = emulator.world.tick
            emulator.run_tick()
            done += 1
            if schedule is not None:
                schedule.after_tick(emulator, tick)
            if realtime:
                time.sleep(max(0.0, TICK_SECONDS - (time.perf_counter() - started)))
    except KeyboardInterrupt:
        err(f"stopped after {done} tick(s)")
    ticks = done
    if trace is not None:
        err(f"debugger: {len(trace.lines)} stop(s)")
    results = schedule.results() if schedule is not None else []
    profiler = emulator.profiler
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

    if results:
        print()
        for result in results:
            print_result(emulator.version, result, arguments.show_records)
        passed = sum(result.passed for result in results)
        print(f"tests: {passed}/{len(results)} passed")
        if passed < len(results):
            return FAILED
    return OK


def register_run(subparsers) -> None:
    run = subparsers.add_parser(
        "run",
        help="emulate the pack for one version",
        description="Run #minecraft:load and #minecraft:tick for some ticks, print every "
        "record, then the profiler table and call-graph findings. With --tests, the "
        "project's tests run in their ticks during the run (exit 1 when one fails). With "
        "--break, each stop is printed with the --watch values and the run goes on "
        "(the shell stops and prompts instead).",
    )
    add_source_arguments(run)
    run.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )
    add_world_arguments(run, endless=True)
    run.add_argument(
        "--tests",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run the project's tests during the run (default: the project's "
        "'run tests during runs')",
    )
    add_show_records(run)
    run.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="20 ticks per second instead of as fast as possible (default: the project's speed)",
    )
    run.add_argument("--html", type=Path, default=None, help="default: generated/index.html")
    run.add_argument("--dot", action="store_true", help="also write the call graph (Graphviz)")
    add_debug_arguments(run)
    add_output_filter(run)
    add_vanilla_arguments(run)
    run.set_defaults(handler=command_run)


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------


def engine_for(
    arguments: argparse.Namespace,
    inputs: Inputs,
    tests: list[CommandTest],
    ticks: int,
    vanilla=None,
) -> TestEngine:
    return TestEngine(
        inputs.datapack,
        ticks=ticks,
        players=inputs.setting(arguments, "players", EMULATION.DEFAULT_PLAYERS),
        seed=inputs.setting(arguments, "seed", EMULATION.DEFAULT_SEED),
        library=None if vanilla is not None else library_for(arguments, inputs),
        allow_download=arguments.download,
        tests=tests,
        vanilla=vanilla,
    )


def engine_versions(inputs: Inputs) -> list[Version]:
    """What a project ticked in the engine window."""
    if inputs.project is None or not inputs.project.engine_versions:
        return []
    return [parse_version(item) for item in inputs.project.engine_versions]


def progress(index: int, total: int, version) -> None:
    err(f"[{index}/{total}] {version.id}")


def command_matrix(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    chosen = chosen_versions(arguments, inputs, engine_versions(inputs) or list(versions.VERSIONS))
    with_tests = arguments.tests or arguments.junit is not None
    tests = [test for test in inputs.tests if test.enabled] if with_tests else []
    if with_tests and not tests:
        err("note: no enabled tests to run")
    ticks = ticks_setting(arguments, inputs)
    warn_unreached(tests, ticks)
    engine = engine_for(arguments, inputs, tests, ticks)
    bus = printing_bus(arguments) if arguments.verbose else None
    with interruptible() as interrupt:
        results = engine.run(chosen, progress=progress, output=bus, cancelled=interrupt)

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
    if arguments.junit is not None:
        print(f"junit report: {write_junit(results, arguments.junit, inputs.datapack.name)}")
    if cancelled_note(results, len(chosen)):
        return FAILED
    if arguments.strict and any(run.status in ("errors", "tests failed") for run in results):
        return FAILED
    return OK


def register_matrix(subparsers) -> None:
    matrix = subparsers.add_parser(
        "matrix",
        help="run the pack across versions",
        description="One fresh world per version; one row per version. Without a version "
        "option: the versions a project ticked in the engine window, else every known "
        "version (pre-releases included).",
    )
    add_source_arguments(matrix)
    add_version_selection(matrix)
    add_world_arguments(matrix)
    matrix.add_argument("--tests", action="store_true", help="also run the project's tests")
    matrix.add_argument(
        "--junit", type=Path, default=None, help="JUnit XML report of the tests (implies --tests)"
    )
    matrix.add_argument(
        "--strict",
        action="store_true",
        help="exit with 1 when a version has errors or failed tests",
    )
    matrix.add_argument("--html", type=Path, default=None, help="default: generated/matrix.html")
    matrix.add_argument(
        "--verbose",
        action="store_true",
        help="print every version's records (the engine window's lower pane)",
    )
    add_output_filter(matrix)
    add_vanilla_arguments(matrix, per_version=True)
    matrix.set_defaults(handler=command_matrix)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


def parse_test(text: str) -> CommandTest:
    """``[TICK:]COMMAND`` — ``5:scoreboard players get #g t``."""
    head, sep, rest = text.partition(":")
    head = head.strip()
    if sep and head.isdecimal() and head.isascii():
        return CommandTest(rest.strip(), at_tick=int(head))
    return CommandTest(text.strip())


def command_test(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    extra = [parse_test(text) for text in arguments.test or []]
    if arguments.only and not extra:
        raise CliError("--only needs at least one --test")
    if arguments.expect is not None or arguments.expect_value is not None:
        if len(extra) != 1:
            raise CliError("--expect and --expect-value go with exactly one --test")
        extra[0].expect = arguments.expect or ""
        extra[0].expect_value = (arguments.expect_value or "").strip()
        if extra[0].expect_value and not valid_range(extra[0].expect_value):
            raise CliError(f"invalid --expect-value {arguments.expect_value!r} (5, 1.., ..3, 1..4)")
    if arguments.engine and inputs.project is None:
        raise CliError("--engine needs a project")
    if arguments.version and (versions_given(arguments) or arguments.engine):
        raise CliError("give --version or a list of versions, not both")

    tests = (
        []
        if arguments.only
        else [test for test in inputs.tests if test.enabled or arguments.include_disabled]
    )
    for test in tests:
        test.enabled = True
    tests += extra
    if not tests:
        where = "the project has no enabled test" if inputs.project else "no project was given"
        err(f"no tests to run: {where}; add some with --test")
        return USAGE

    if arguments.engine:
        chosen = chosen_versions(arguments, inputs, engine_versions(inputs))
    else:
        chosen = chosen_versions(arguments, inputs, [inputs.version(arguments)])

    # like the window's run tests: the world ticks until the last test ran
    last = max(test.at_tick for test in tests)
    ticks = arguments.ticks if arguments.ticks is not None else last + 1
    warn_unreached(tests, ticks)
    single = vanilla_for(arguments, chosen[0], inputs) if len(chosen) == 1 else None
    engine = engine_for(arguments, inputs, tests, ticks, vanilla=single)
    bus = printing_bus(arguments) if arguments.verbose else None
    progress_of = progress if len(chosen) > 1 else None
    with interruptible() as interrupt:
        results = engine.run(chosen, progress=progress_of, output=bus, cancelled=interrupt)
    if not results:
        err("stopped before the first version ran")
        return FAILED
    stopped = cancelled_note(results, len(chosen))

    failed = 0
    for run in results:
        for result in run.tests:
            failed += not result.passed
            print_result(run.version, result, arguments.show_records)
    total = sum(len(run.tests) for run in results)
    where = f"across {len(results)} versions" if len(results) > 1 else f"in {results[0].version.id}"
    print(f"\n{total - failed}/{total} passed {where}")
    if arguments.junit:
        print(f"junit report: {write_junit(results, arguments.junit, inputs.datapack.name)}")
    return FAILED if failed or stopped else OK


def cancelled_note(results: list[VersionRun], chosen: int) -> bool:
    """Say when Ctrl+C cut the run short; whether it did."""
    if not results or not results[-1].cancelled:
        return False
    run = results[-1]
    err(
        f"stopped: {run.version.id} after {run.ticks} tick(s); "
        f"{chosen - len(results)} version(s) not run"
    )
    return True


def register_test(subparsers) -> None:
    test = subparsers.add_parser(
        "test",
        help="run a project's tests; exit 1 when one fails",
        description="Run the tests saved in a project (the environment tab's) and/or tests "
        "given with --test, in one fresh world per version, which ticks until the last test "
        "ran. Without a version option the project's version is used. Exit status: 0 all "
        "passed, 1 a test failed, 2 nothing to run or a usage error.",
    )
    add_source_arguments(test)
    test.add_argument(
        "--version", default=None, help="one version (default: the project's, else the pack's)"
    )
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
    add_show_records(test)
    test.add_argument(
        "--verbose", action="store_true", help="print every record while the tests run"
    )
    add_output_filter(test)
    add_vanilla_arguments(test)
    test.set_defaults(handler=command_test)
