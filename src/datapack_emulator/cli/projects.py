"""``project``: create, read and edit ``.dpemu`` projects without the window
(file › new/save project, the environment tab's settings and tests, notes)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datapack_emulator.cli.common import OK, CliError, count, parse_version
from datapack_emulator.cli.runs import parse_test
from datapack_emulator.emulator.testing import CommandTest, valid_range
from datapack_emulator.project import SUFFIX, Project, list_projects
from datapack_emulator.settings import EMULATION


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
        raise argparse.ArgumentTypeError(f"not a datapack folder (no pack.mcmeta): {text}")
    return path


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
        expect = []
        if test.expect:
            expect.append(f"output ~ {test.expect!r}")
        if test.expect_value:
            expect.append(f"value {test.expect_value}")
        rows.append(
            (
                f"  {number}. [{'x' if test.enabled else ' '}] tick {test.at_tick}",
                test.command + (f"  ({'; '.join(expect)})" if expect else ""),
            )
        )
    if project.notes.strip():
        rows.append(("notes", project.notes.strip()))
    for function_id, note in sorted(project.function_notes.items()):
        rows.append((f"note on {function_id}", note))
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
        print("no saved projects")
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
    group.add_argument("--notes", default=None, help="the project's notes")
    group.add_argument("--notes-file", type=Path, default=None, help="read the notes from a file")


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


def command_packs(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    changed = False
    for path in arguments.add or []:
        project.datapacks.append(path)
        changed = True
    if arguments.remove is not None:
        index = _position(project.datapacks, arguments.remove, "datapack")
        del project.datapacks[index]
        changed = True
    if arguments.move is not None:
        index = _position(project.datapacks, arguments.move[0], "datapack")
        _move(project.datapacks, index, arguments.move[1])
        changed = True
    for number, path in enumerate(project.datapacks, start=1):
        print(f"{number}. {path.name}  ({path})")
    if changed:
        _save(project, arguments.project)
    return OK


def command_tests(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    tests = project.tests
    changed = False
    for text in arguments.add or []:
        tests.append(parse_test(text).to_dict())
        changed = True
    for number, text in arguments.expect or []:
        tests[_position(tests, number, "test")]["expect"] = text
        changed = True
    for number, text in arguments.expect_value or []:
        if text and not valid_range(text):
            raise CliError(f"invalid expected value {text!r} (5, 1.., ..3, 1..4)")
        tests[_position(tests, number, "test")]["expect_value"] = text
        changed = True
    for number, tick in arguments.tick or []:
        if not tick.isdecimal():
            raise CliError(f"not a tick: {tick!r}")
        tests[_position(tests, number, "test")]["at_tick"] = int(tick)
        changed = True
    for key, numbers in (("enable", arguments.enable), ("disable", arguments.disable)):
        for number in numbers or []:
            tests[_position(tests, number, "test")]["enabled"] = key == "enable"
            changed = True
    if arguments.move is not None:
        _move(tests, _position(tests, arguments.move[0], "test"), arguments.move[1])
        changed = True
    if arguments.duplicate is not None:
        index = _position(tests, arguments.duplicate, "test")
        tests.insert(index + 1, dict(tests[index]))
        changed = True
    for number in sorted(
        {_position(tests, n, "test") for n in arguments.remove or []}, reverse=True
    ):
        del tests[number]
        changed = True
    for number, data in enumerate(tests, start=1):
        test = CommandTest.from_dict(data)
        extra = []
        if test.expect:
            extra.append(f"output ~ {test.expect!r}")
        if test.expect_value:
            extra.append(f"value {test.expect_value}")
        print(
            f"{number:3}. [{'x' if test.enabled else ' '}] tick {test.at_tick:<4} {test.command}"
            + (f"  ({'; '.join(extra)})" if extra else "")
        )
    if not tests:
        print("no tests")
    if changed:
        _save(project, arguments.project)
    return OK


def command_note(arguments: argparse.Namespace) -> int:
    project = _load(arguments.project)
    key = arguments.function
    if arguments.text is None:
        print(project.function_notes.get(key, "(no note)"))
        return OK
    if arguments.text.strip():
        project.function_notes[key] = arguments.text.strip()
    else:
        project.function_notes.pop(key, None)
    _save(project, arguments.project)
    return OK


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
    new.add_argument("pack", type=_pack, nargs="+", help="datapack folders, in load order")
    new.add_argument("--test", action="append", metavar="[TICK:]COMMAND", help="add a test")
    new.add_argument("--force", action="store_true", help="replace an existing file")
    add_settings(new)
    new.set_defaults(handler=command_new)

    show = actions.add_parser("show", help="everything a project holds")
    show.add_argument("project", type=Path)
    show.add_argument("--json", action="store_true", help="project.json as JSON")
    show.set_defaults(handler=command_show)

    listing = actions.add_parser("list", help="the projects saved in projects/")
    listing.set_defaults(handler=command_list)

    settings = actions.add_parser("set", help="change settings and notes")
    settings.add_argument("project", type=Path)
    add_settings(settings)
    settings.set_defaults(handler=command_set)

    packs = actions.add_parser("packs", help="list, add, remove or reorder datapacks")
    packs.add_argument("project", type=Path)
    packs.add_argument("--add", type=_pack, action="append", metavar="DIR", help="append a pack")
    packs.add_argument("--remove", metavar="N", default=None, help="remove pack N")
    packs.add_argument(
        "--move", nargs=2, metavar=("N", "up|down"), default=None, help="load pack N earlier/later"
    )
    packs.set_defaults(handler=command_packs)

    tests = actions.add_parser("tests", help="list and edit tests")
    tests.add_argument("project", type=Path)
    tests.add_argument("--add", action="append", metavar="[TICK:]COMMAND")
    tests.add_argument("--expect", nargs=2, action="append", metavar=("N", "TEXT"))
    tests.add_argument("--expect-value", nargs=2, action="append", metavar=("N", "RANGE"))
    tests.add_argument("--tick", nargs=2, action="append", metavar=("N", "TICK"))
    tests.add_argument("--enable", action="append", metavar="N")
    tests.add_argument("--disable", action="append", metavar="N")
    tests.add_argument("--move", nargs=2, metavar=("N", "up|down"), default=None)
    tests.add_argument("--duplicate", metavar="N", default=None)
    tests.add_argument("--remove", action="append", metavar="N")
    tests.set_defaults(handler=command_tests)

    note = actions.add_parser("note", help="read or write the note on a function")
    note.add_argument("project", type=Path)
    note.add_argument("function", help="a function id, or a #tag")
    note.add_argument("text", nargs="?", default=None, help="the note ('' removes it)")
    note.set_defaults(handler=command_note)
