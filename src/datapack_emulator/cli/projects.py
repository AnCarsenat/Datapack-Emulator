"""``project``: create, read and edit ``.dpemu`` projects without the window
(file › new/save project, the environment tab's settings and tests, notes)."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from datapack_emulator.cli.common import OK, CliError, count, err, parse_version
from datapack_emulator.cli.runs import parse_test
from datapack_emulator.emulator.common import normalise_tagged_id
from datapack_emulator.emulator.datapack import DatapackSet
from datapack_emulator.emulator.testing import CommandTest, valid_check, valid_range
from datapack_emulator.project import (
    SUFFIX,
    Project,
    list_projects,
    recent_datapacks,
    recent_projects,
)
from datapack_emulator.settings import EMULATION, PATHS


def _load(path: Path) -> Project:
    if not path.is_file():
        raise CliError(f"no project {path}")
    try:
        return Project.load(path)
    except (OSError, ValueError) as exc:
        raise CliError(f"cannot open project {path}: {exc}") from exc


def _save(project: Project, path: Path) -> None:
    saved = project.save(path)
    print(f"saved {saved}")


def _on_off(text: str) -> bool:
    lowered = text.lower()
    if lowered in ("on", "yes", "true", "1"):
        return True
    if lowered in ("off", "no", "false", "0"):
        return False
    raise argparse.ArgumentTypeError(f"expected on or off, not {text!r}")


def _pack(text: str) -> Path:
    path = Path(text)
    if not (path / "pack.mcmeta").is_file():
        raise CliError(f"not a datapack folder (no pack.mcmeta): {text}")
    return path


def _pack_argument(text: str) -> Path:
    try:
        return _pack(text)
    except CliError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _position(project_items: list, text: str, what: str) -> int:
    try:
        index = int(text) - 1
    except ValueError:
        raise CliError(f"not a {what} number: {text!r}") from None
    if not 0 <= index < len(project_items):
        raise CliError(f"no {what} {text} (there are {len(project_items)})")
    return index


def _move(items: list, index: int, direction: str) -> None:
    direction = direction.lower()
    if direction not in ("up", "down"):
        raise CliError(f"move up or down, not {direction!r}")
    target = index + (-1 if direction == "up" else 1)
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]


# ---------------------------------------------------------------------------
# show / list
# ---------------------------------------------------------------------------


def describe(project: Project) -> list[tuple[str, str]]:
    rows = [
        ("name", project.name),
        ("file", str(project.path)),
        ("datapacks", ", ".join(path.name for path in project.datapacks) or "-"),
        ("version", project.version or "(the pack's newest)"),
        ("ticks", "∞ until stopped" if project.ticks < 0 else str(project.ticks)),
        ("players", str(project.players)),
        ("seed", str(project.seed)),
        ("speed", project.speed),
        ("engine versions", ", ".join(project.engine_versions) or "-"),
        ("client jar", project.vanilla_jar or "-"),
        ("tests during runs", "on" if project.tests_during_runs else "off"),
        ("step on command", "on" if project.step_on_command else "off"),
        ("tests", str(len(project.tests))),
    ]
    for number, data in enumerate(project.tests, start=1):
        test = CommandTest.from_dict(data)
        rows.append(
            (f"  {number}. [{'x' if test.enabled else ' '}] tick {test.at_tick}", test.describe())
        )
    if project.notes.strip():
        rows.append(("notes", project.notes.strip()))
    for function_id, note in sorted(project.function_notes.items()):
        rows.append((f"note on {function_id}", note))
    for entry in project.breakpoints:
        rows.append(
            (
                "breakpoint",
                entry.removeprefix("!") + (" (disabled)" if entry.startswith("!") else ""),
            )
        )
    for entry in project.watches:
        rows.append(("watch", entry))
    return rows


def command_show(arguments: argparse.Namespace) -> int:
    from datapack_emulator.cli.inspect import print_rows

    project = _load(arguments.project)
    if arguments.json:
        data = project.to_dict()
        data["datapacks"] = [str(path) for path in project.datapacks]
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print_rows(describe(project))
    return OK


def command_list(arguments: argparse.Namespace) -> int:
    projects = list_projects()
    for path in projects:
        print(path)
    if not projects:
        print(f"no saved projects in {PATHS.PROJECTS}")
    return OK


def command_recent(arguments: argparse.Namespace) -> int:
    """The window's open recent project / add a recent datapack lists."""
    for title, paths in (("projects", recent_projects()), ("datapacks", recent_datapacks())):
        print(f"recent {title}:")
        for path in paths:
            print(f"  {path}" + ("" if path.exists() else "  (missing)"))
        if not paths:
            print("  none")
    return OK


# ---------------------------------------------------------------------------
# new / set
# ---------------------------------------------------------------------------


def add_settings(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("settings (the environment tab)")
    group.add_argument("--name", default=None)
    group.add_argument("--version", default=None, help="the emulated version ('' = the pack's)")
    group.add_argument("--ticks", type=int, default=None, help="-1 runs until stopped")
    group.add_argument("--players", type=count, default=None)
    group.add_argument("--seed", type=int, default=None)
    group.add_argument("--speed", choices=["fast", "realtime"], default=None)
    group.add_argument(
        "--engine-versions", nargs="*", default=None, metavar="VERSION", help="ticked in the engine"
    )
    group.add_argument("--vanilla-jar", default=None, metavar="JAR", help="'' for none")
    group.add_argument("--tests-during-runs", type=_on_off, default=None, metavar="on|off")
    group.add_argument("--step-on-command", type=_on_off, default=None, metavar="on|off")
    notes = group.add_mutually_exclusive_group()
    notes.add_argument("--notes", default=None, help="the project's notes")
    notes.add_argument("--notes-file", type=Path, default=None, help="read the notes from a file")


def apply_settings(project: Project, arguments: argparse.Namespace) -> None:
    if arguments.name is not None:
        project.name = arguments.name
    if arguments.version is not None:
        project.version = parse_version(arguments.version).id if arguments.version else ""
    if arguments.ticks is not None:
        if arguments.ticks < -1:
            raise CliError("--ticks must be -1 (until stopped) or more")
        project.ticks = arguments.ticks
    for name in ("players", "seed", "speed", "tests_during_runs", "step_on_command"):
        value = getattr(arguments, name)
        if value is not None:
            setattr(project, name, value)
    if arguments.engine_versions is not None:
        project.engine_versions = [parse_version(v).id for v in arguments.engine_versions]
    if arguments.vanilla_jar is not None:
        if arguments.vanilla_jar and not Path(arguments.vanilla_jar).is_file():
            raise CliError(f"no such client jar: {arguments.vanilla_jar}")
        project.vanilla_jar = (
            str(Path(arguments.vanilla_jar).resolve()) if arguments.vanilla_jar else ""
        )
    if arguments.notes_file is not None:
        try:
            project.notes = arguments.notes_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"cannot read {arguments.notes_file}: {exc}") from exc
    elif arguments.notes is not None:
        project.notes = arguments.notes


def target_path(path: Path) -> Path:
    return path if path.suffix == SUFFIX else path.with_name(path.name + SUFFIX)


def command_new(arguments: argparse.Namespace) -> int:
    path = target_path(arguments.project)
    if path.exists() and not arguments.force:
        raise CliError(f"{path} exists (use --force to replace it)")
    project = Project(
        name=path.stem,
        datapacks=list(arguments.pack),
        ticks=EMULATION.DEFAULT_TICKS,
    )
    apply_settings(project, arguments)
    for text in arguments.test or []:
        project.tests.append(parse_test(text).to_dict())
    _save(project, path)
    return OK


def command_set(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    apply_settings(project, arguments)
    _save(project, arguments.project)
    return OK


# ---------------------------------------------------------------------------
# packs / tests / note
# ---------------------------------------------------------------------------


def _edit_list(items: list, operations: list[tuple[str, list[str]]], what: str) -> bool:
    """Apply edits to ``items`` in command-line order; every number refers to the
    list as it was before the edits. Returns whether anything changed."""
    original = list(items)

    def item(number: str):
        return original[_position(original, number, what)]

    def where(entry) -> int:
        for index, candidate in enumerate(items):
            if candidate is entry:
                return index
        raise CliError(f"{what} was removed earlier on the command line")

    changed = False
    for action, values in operations:
        changed = True
        if action == "add":
            items.append(values[0])
        elif action == "remove":
            del items[where(item(values[0]))]
        elif action == "move":
            _move(items, where(item(values[0])), values[1])
        elif action == "duplicate":
            entry = item(values[0])
            items.insert(where(entry) + 1, dict(entry))
        else:  # a change to one entry, which must still be there
            entry = item(values[0])
            where(entry)
            values[1](entry)
    return changed


class _Ordered(argparse.Action):
    """Collects options in the order they were given, into ``operations``."""

    def __call__(self, parser, namespace, values, option_string=None):
        operations = getattr(namespace, "operations", None) or []
        operations.append((self.dest, values if isinstance(values, list) else [values]))
        namespace.operations = operations


def command_packs(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    operations = [
        (action, [_pack(values[0])] if action == "add" else values)
        for action, values in getattr(arguments, "operations", None) or []
    ]
    if _edit_list(project.datapacks, operations, "datapack"):
        if not project.datapacks:
            raise CliError("a project needs at least one datapack")
        _save(project, arguments.project)
    for number, path in enumerate(project.datapacks, start=1):
        print(f"{number}. {path.name}  ({path})")
    return OK


def _set(key: str, value):
    return lambda entry: entry.__setitem__(key, value)


def _add_check(line: str):
    def change(entry: dict) -> None:
        entry["checks"] = [*(entry.get("checks") or []), line]

    return change


def _remove_check(which: str):
    def change(entry: dict) -> None:
        checks = list(entry.get("checks") or [])
        if which == "all":
            checks = []
        else:
            del checks[_position(checks, which, "check")]
        entry["checks"] = checks

    return change


def command_tests(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    operations = []
    for action, values in getattr(arguments, "operations", None) or []:
        if action == "add":
            operations.append(("add", [parse_test(values[0]).to_dict()]))
        elif action == "expect":
            operations.append(("set", [values[0], _set("expect", values[1])]))
        elif action == "expect_value":
            if values[1] and not valid_range(values[1]):
                raise CliError(f"invalid expected value {values[1]!r} (5, 1.., ..3, 1..4)")
            operations.append(("set", [values[0], _set("expect_value", values[1])]))
        elif action == "tick":
            if not values[1].isdecimal():
                raise CliError(f"not a tick: {values[1]!r}")
            operations.append(("set", [values[0], _set("at_tick", int(values[1]))]))
        elif action in ("enable", "disable"):
            operations.append(("set", [values[0], _set("enabled", action == "enable")]))
        elif action == "check":
            problem = valid_check(values[1])
            if problem:
                raise CliError(f"--check {values[1]!r}: {problem}")
            operations.append(("set", [values[0], _add_check(values[1])]))
        elif action == "uncheck":
            operations.append(("set", [values[0], _remove_check(values[1])]))
        else:
            operations.append((action, values))
    if _edit_list(project.tests, operations, "test"):
        _save(project, arguments.project)
    for number, data in enumerate(project.tests, start=1):
        test = CommandTest.from_dict(data)
        box = "x" if test.enabled else " "
        print(f"{number:3}. [{box}] tick {test.at_tick:<4} {test.describe()}")
    if not project.tests:
        print("no tests")
    return OK


def command_note(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    key = normalise_tagged_id(arguments.function.strip())
    if not re.fullmatch(r"#?[a-z0-9_.-]+:[a-z0-9_./-]+", key):
        raise CliError(f"not a function id or #tag: {arguments.function!r}")
    if arguments.text is None:
        print(project.function_notes.get(key, "(no note)"))
        return OK
    text = arguments.text.strip()
    if not text and key not in project.function_notes:
        print(f"no note on {key}")
        return OK
    if text:
        known = _known_ids(project)
        if known is not None and key not in known:
            err(f"note: {key} is not in the project's datapacks")
        project.function_notes[key] = text
    else:
        project.function_notes.pop(key, None)
    _save(project, arguments.project)
    return OK


def command_debug(arguments: argparse.Namespace) -> int:
    """The debugger's breakpoints and watches, as the window's debugger dock
    and the shell's ``.break``/``.watch`` keep them."""
    from datapack_emulator.cli.debug import breakpoint_argument
    from datapack_emulator.emulator.runtime.debugger import Debugger

    project = _load(arguments.project)
    debugger = Debugger()
    for entry in debugger.load_strings(project.breakpoints):
        err(f"note: dropped an unreadable breakpoint {entry!r}")
    watches = list(project.watches)
    changed = False
    for action, values in getattr(arguments, "operations", None) or []:
        changed = True
        value = values[0]
        if action == "watch":
            watches.append(value)
        elif action == "unwatch":
            if value == "all":
                watches.clear()
            else:
                del watches[_position(watches, value, "watch")]
        elif action == "unbreak" and value == "all":
            debugger.clear()
        else:
            function_id, line, condition = breakpoint_argument(value)
            if action == "break":
                debugger.add(function_id, line, condition)
                continue
            point = debugger.breakpoints.get((function_id, line))
            if point is None:
                raise CliError(f"no breakpoint at {function_id}:{line}")
            if action == "unbreak":
                debugger.remove(function_id, line)
            else:
                point.enabled = action == "enable_break"
    if changed:
        project.breakpoints = debugger.to_strings()
        project.watches = watches
        _save(project, arguments.project)
    if not project.breakpoints:
        print("no breakpoints")
    for entry in project.breakpoints:
        print(
            f"break {entry.removeprefix('!')}" + ("  (disabled)" if entry.startswith("!") else "")
        )
    for number, text in enumerate(project.watches, start=1):
        print(f"watch {number}. {text}")
    return OK


def _known_ids(project: Project) -> set[str] | None:
    """Function and function tag ids of the project's packs (any version)."""
    try:
        view = DatapackSet.load(project.datapacks).view()
    except Exception:  # an unreadable pack must not block a note
        return None
    return set(view.functions) | set(view.function_tags)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    project = subparsers.add_parser(
        "project",
        help="create, show and edit .dpemu projects",
        description="Projects as the window saves them: datapacks, settings, tests and notes. "
        "Every change is written back to the file.",
    )
    actions = project.add_subparsers(dest="action", required=True, metavar="ACTION")

    new = actions.add_parser("new", help="create a project from datapack folders")
    new.add_argument("project", type=Path, help="the .dpemu file to write")
    new.add_argument("pack", type=_pack_argument, nargs="+", help="datapack folders, in load order")
    new.add_argument("--test", action="append", metavar="[TICK:]COMMAND", help="add a test")
    new.add_argument("--force", action="store_true", help="replace an existing file")
    add_settings(new)
    new.set_defaults(handler=command_new)

    show = actions.add_parser("show", help="everything a project holds")
    show.add_argument("project", type=Path)
    show.add_argument("--json", action="store_true", help="project.json as JSON")
    show.set_defaults(handler=command_show)

    listing = actions.add_parser(
        "list", help="the projects saved in the projects folder (the window's default)"
    )
    listing.set_defaults(handler=command_list)

    recent = actions.add_parser(
        "recent", help="the window's recent projects and datapacks (@last opens the first)"
    )
    recent.set_defaults(handler=command_recent)

    settings = actions.add_parser("set", help="change settings and notes")
    settings.add_argument("project", type=Path)
    add_settings(settings)
    settings.set_defaults(handler=command_set)

    packs = actions.add_parser("packs", help="list, add, remove or reorder datapacks")
    packs.add_argument("project", type=Path)
    packs.add_argument("--add", action=_Ordered, metavar="DIR", help="append a pack")
    packs.add_argument("--remove", action=_Ordered, metavar="N", help="remove pack N")
    packs.add_argument(
        "--move", action=_Ordered, nargs=2, metavar=("N", "up|down"), help="load N earlier/later"
    )
    packs.set_defaults(handler=command_packs)

    tests = actions.add_parser("tests", help="list and edit tests")
    tests.add_argument("project", type=Path)
    tests.add_argument("--add", action=_Ordered, metavar="[TICK:]COMMAND")
    tests.add_argument("--expect", action=_Ordered, nargs=2, metavar=("N", "TEXT"))
    tests.add_argument("--expect-value", action=_Ordered, nargs=2, metavar=("N", "RANGE"))
    tests.add_argument("--tick", action=_Ordered, nargs=2, metavar=("N", "TICK"))
    tests.add_argument(
        "--check",
        action=_Ordered,
        nargs=2,
        metavar=("N", "CHECK"),
        help="add a check to test N ('score #g t = 3', 'storage ns:s x = 1b', …)",
    )
    tests.add_argument(
        "--uncheck",
        action=_Ordered,
        nargs=2,
        metavar=("N", "M|all"),
        help="remove test N's check M",
    )
    tests.add_argument("--enable", action=_Ordered, metavar="N")
    tests.add_argument("--disable", action=_Ordered, metavar="N")
    tests.add_argument("--move", action=_Ordered, nargs=2, metavar=("N", "up|down"))
    tests.add_argument("--duplicate", action=_Ordered, metavar="N")
    tests.add_argument("--remove", action=_Ordered, metavar="N")
    tests.set_defaults(handler=command_tests)

    note = actions.add_parser("note", help="read or write the note on a function")
    note.add_argument("project", type=Path)
    note.add_argument("function", help="a function id, or a #tag")
    note.add_argument("text", nargs="?", default=None, help="the note ('' removes it)")
    note.set_defaults(handler=command_note)

    debug = actions.add_parser("debug", help="list and edit the debugger's breakpoints and watches")
    debug.add_argument("project", type=Path)
    debug.add_argument(
        "--break",
        dest="break",
        action=_Ordered,
        metavar="FUNC:LINE[ if COND]",
        help="add a breakpoint (a condition starts with if or unless)",
    )
    debug.add_argument("--unbreak", action=_Ordered, metavar="FUNC:LINE|all")
    debug.add_argument("--enable", dest="enable_break", action=_Ordered, metavar="FUNC:LINE")
    debug.add_argument("--disable", dest="disable_break", action=_Ordered, metavar="FUNC:LINE")
    debug.add_argument("--watch", action=_Ordered, metavar="EXPR", help="add a watch")
    debug.add_argument("--unwatch", action=_Ordered, metavar="N|all")
    debug.set_defaults(handler=command_debug)
