"""``check``: the problems dock in a terminal — everything a version refuses
or cannot run in a pack, without running it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    CliError,
    add_source_arguments,
    add_vanilla_arguments,
    add_version_selection,
    chosen_versions,
    err,
    load_inputs,
    vanilla_for,
    versions_given,
)
from datapack_emulator.emulator.analysis.problems import (
    CODES,
    SEVERITIES,
    count,
    find_problems,
    text_problems,
)

#: the suffixes the per-line check knows (the source view checks the same)
CHECKED_SUFFIXES = (".mcfunction", ".json", ".mcmeta")


def check_lines(arguments: argparse.Namespace, chosen: list) -> int:
    """``--lines FILE``: what the source view underlines, per file and line."""
    if arguments.code or arguments.severity != "info":
        raise CliError("--lines checks one file's lines: it takes no --code or --severity")
    rows = []
    for file in arguments.lines:
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CliError(f"cannot read {file}: {exc}") from exc
        if file.suffix.lower() not in CHECKED_SUFFIXES:
            err(f"{file}: not checked (only {', '.join(CHECKED_SUFFIXES)})")
            continue
        for version in chosen:
            for line, message in sorted(text_problems(text, file.suffix, version).items()):
                rows.append(
                    {"file": str(file), "line": line, "version": version.id, "message": message}
                )
    if arguments.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            # the path comes first, so an editor or grep can follow it
            prefix = f"[{row['version']}] " if len(chosen) > 1 else ""
            print(f"{row['file']}:{row['line']}: {prefix}{row['message']}")
        if not rows:
            print("no refused lines")
    return FAILED if rows else OK


def command_check(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    several = versions_given(arguments)
    if several and arguments.version:
        raise CliError("give --version or a list of versions, not both")
    if arguments.boundaries and not several:
        raise CliError("--boundaries needs a list of versions (--versions, --from/--to, …)")
    if several:
        chosen = chosen_versions(arguments, inputs, [inputs.version(arguments)])
    else:
        chosen = [inputs.version(arguments)]
    if arguments.lines:
        return check_lines(arguments, chosen)
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
        # a list whenever versions were chosen as a list, even if one matched
        print(json.dumps(report if several else report[0], indent=2))
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
        choices=CODES,
        metavar="CODE",
        help="only these kinds (repeatable): " + ", ".join(CODES),
    )
    check.add_argument(
        "--lines",
        action="append",
        type=Path,
        metavar="FILE",
        help="instead of the pack: the lines of a .mcfunction or JSON file the version would "
        "refuse, as the source view underlines them (repeatable; the pack gives the version)",
    )
    check.add_argument("--strict", action="store_true", help="exit with 1 on warnings too")
    check.add_argument("--json", action="store_true", help="print JSON")
    check.add_argument(
        "--verbose", action="store_true", help="name the version and each problem's file"
    )
    add_vanilla_arguments(check)
    check.set_defaults(handler=command_check)
