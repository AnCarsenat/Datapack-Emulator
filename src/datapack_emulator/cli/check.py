"""``check``: the problems dock in a terminal — everything a version refuses
or cannot run in a pack, without running it."""

from __future__ import annotations

import argparse
import json

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    add_source_arguments,
    add_vanilla_arguments,
    add_version_selection,
    chosen_versions,
    load_inputs,
    vanilla_for,
    versions_given,
)
from datapack_emulator.emulator.analysis.problems import SEVERITIES, count, find_problems


def command_check(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    if versions_given(arguments):
        chosen = chosen_versions(arguments, inputs, [inputs.version(arguments)])
    else:
        chosen = [inputs.version(arguments)]
    lowest = SEVERITIES.index(arguments.severity)
    codes = set(arguments.code or [])
    report = []
    failed = False
    for version in chosen:
        vanilla = vanilla_for(arguments, version, inputs)
        problems = [
            problem
            for problem in find_problems(inputs.datapack, version, vanilla)
            if SEVERITIES.index(problem.severity) <= lowest and (not codes or problem.code in codes)
        ]
        totals = count(problems)
        failed = failed or bool(totals["error"] or (arguments.strict and totals["warning"]))
        if arguments.json:
            report.append(
                {
                    "version": version.id,
                    "client_jar": vanilla.version_id if vanilla else "",
                    "counts": totals,
                    "problems": [problem.to_dict() for problem in problems],
                }
            )
            continue
        if len(chosen) > 1 or arguments.verbose:
            jar = f", client jar {vanilla.version_id}" if vanilla else ", no client jar"
            print(f"== {version.id}{jar}")
        for problem in problems:
            print(problem.format())
            if arguments.verbose and problem.path:
                print(f"        {problem.path}" + (f":{problem.line}" if problem.line else ""))
        print(
            f"{version.id}: {totals['error']} error(s), {totals['warning']} warning(s), "
            f"{totals['info']} note(s)"
        )
    if arguments.json:
        print(json.dumps(report if len(chosen) > 1 else report[0], indent=2))
    return FAILED if failed else OK


def register(subparsers) -> None:
    check = subparsers.add_parser(
        "check",
        help="static problems of a pack: what a version refuses or cannot run",
        description="The problems dock: functions and tags that do not load, unknown "
        "selector options, SNBT and ids the version does not have, calls to missing "
        "functions, JSON resources with unknown conditions, item functions or entries, "
        "advancements without criteria, and — as notes — unused functions and recursion. "
        "Nothing runs. Exit status 1 when there is an error (or, with --strict, a warning).",
    )
    add_source_arguments(check)
    check.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )
    add_version_selection(check)
    check.add_argument(
        "--severity",
        choices=SEVERITIES,
        default="info",
        help="the lowest severity shown (default: everything)",
    )
    check.add_argument(
        "--code",
        action="append",
        metavar="CODE",
        help="only these kinds (repeatable): function-not-loaded, tag-not-loaded, "
        "selector-option, snbt, unknown-id, missing-function, missing-tag, text-component, "
        "json, condition, item-function, loot-table, loot-entry, advancement, recipe, "
        "unused-function, recursion",
    )
    check.add_argument("--strict", action="store_true", help="exit with 1 on warnings too")
    check.add_argument("--json", action="store_true", help="print JSON")
    check.add_argument(
        "--verbose", action="store_true", help="name the version and each problem's file"
    )
    add_vanilla_arguments(check)
    check.set_defaults(handler=command_check)
