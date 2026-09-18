"""``info``, ``explain``, ``search`` and ``graph``: looking at a pack without
running it (the window's inspector, analyze line, search and call graph)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    CliError,
    Inputs,
    add_source_arguments,
    add_vanilla_arguments,
    load_inputs,
    parse_version,
    vanilla_for,
)
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.analysis.completion import complete
from datapack_emulator.emulator.analysis.explain import explain_line
from datapack_emulator.emulator.analysis.graph import CallGraph
from datapack_emulator.emulator.analysis.inspector import (
    describe_datapack,
    describe_resource,
    describe_version,
    version_note,
)
from datapack_emulator.emulator.analysis.rename import RenameError, rename_function
from datapack_emulator.emulator.analysis.search import id_hits, text_hits
from datapack_emulator.emulator.common import normalise_tagged_id

Rows = list[tuple[str, str]]


def print_rows(rows: Rows, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([{"label": label, "text": text} for label, text in rows], indent=2))
        return
    width = min(max((len(label) for label, _ in rows), default=0), 32)
    for label, text in rows:
        lines = str(text).splitlines() or [""]
        print(f"{label:<{width}}  {lines[0]}")
        for extra in lines[1:]:
            print(f"{'':<{width}}  {extra}")


def add_version_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version", default=None, help="default: the project's, else the pack's newest"
    )


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")


def note_rows(inputs: Inputs, resource_id: str) -> Rows:
    note = inputs.project.function_notes.get(resource_id, "") if inputs.project else ""
    return [("your note", note)] if note else []


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def find_resource(view, resource_id: str):
    """A function, a function tag (``#ns:id``) or any other resource by id."""
    wanted = normalise_tagged_id(resource_id)
    if wanted.startswith("#"):
        tag = view.function_tags.get(wanted)
        if tag is not None:
            return tag
        return next(
            (
                resource
                for resource in view.resources()
                if resource.registry.startswith("tags/") and f"#{resource.id}" == wanted
            ),
            None,
        )
    function = view.functions.get(wanted)
    if function is not None:
        return function
    for resource in view.resources():
        if resource.id == wanted and not resource.registry.startswith("tags/"):
            return resource
    return None


def command_info(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    version = inputs.version(arguments)
    view = inputs.datapack.view_for(version)
    if arguments.resource:
        rows: Rows = []
        missing = []
        graph = CallGraph.from_pack(view)
        for resource_id in arguments.resource:
            resource = find_resource(view, resource_id)
            if resource is None:
                missing.append(resource_id)
                continue
            if rows:
                rows.append(("", ""))
            rows += describe_resource(resource, version)
            key = normalise_tagged_id(resource_id)
            if key in graph.nodes:
                rows += [
                    row
                    for row in graph.relations(key)
                    if row[0] not in ("function", "tag", "calls")
                ]
            rows += note_rows(inputs, key)
        print_rows(rows, arguments.json)
        if missing:
            raise CliError(f"not in the pack for {version.id}: {', '.join(missing)}")
        return OK
    rows = [("version", version_note(inputs.datapack, version))]
    rows += describe_datapack(inputs.datapack, version)
    rows += [(f"{version.id} › {label}", text) for label, text in describe_version(version)[1:]]
    if inputs.project is not None:
        project = inputs.project
        rows += [
            ("project", f"{project.name} ({project.path})"),
            (
                "tests",
                f"{sum(test.enabled for test in inputs.tests)} enabled of {len(inputs.tests)}",
            ),
        ]
        if project.notes.strip():
            rows.append(("project notes", project.notes.strip()))
    print_rows(rows, arguments.json)
    return OK


def register_info(subparsers) -> None:
    info = subparsers.add_parser(
        "info",
        help="what a pack declares and what a version makes of it",
        description="The inspector: the pack (formats, overlays, namespaces, compatibility) "
        "or, with --resource, functions, tags and other files (commands, cost, calls, callers, "
        "features the version lacks, your note).",
    )
    add_source_arguments(info)
    add_version_argument(info)
    info.add_argument(
        "--resource",
        "-r",
        action="append",
        metavar="ID",
        help="a function, #tag or other resource id (repeatable)",
    )
    add_json_argument(info)
    info.set_defaults(handler=command_info)


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def lines_at(view, location: str) -> list[tuple[str, str]]:
    """``ns:function`` (every line) or ``ns:function:12`` -> ``[(origin, text)]``."""
    head, sep, tail = location.rpartition(":")
    number = None
    function_id = location
    if sep and tail.isdecimal() and ":" in head:
        function_id, number = head, int(tail)
    function = view.functions.get(normalise_tagged_id(function_id))
    if function is None:
        raise CliError(f"no function {function_id}")
    try:
        text = function.path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CliError(f"cannot read {function.path}: {exc}") from exc
    if number is not None:
        if not 1 <= number <= len(text):
            raise CliError(f"{function.id} has {len(text)} line(s), not {number}")
        return [(f"{function.id}:{number}", text[number - 1])]
    return [
        (f"{function.id}:{index}", line)
        for index, line in enumerate(text, start=1)
        if line.strip() and not line.strip().startswith("#")
    ]


def command_explain(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    version = inputs.version(arguments)
    view = inputs.datapack.view_for(version)
    assets = vanilla_for(arguments, version, inputs)
    lines = [("", line) for line in arguments.line or []]
    for location in arguments.at or []:
        lines += lines_at(view, location)
    if not lines:
        raise CliError("give a command with --line or a function with --at")
    report = []
    for origin, text in lines:
        rows = explain_line(text, version, view=view, vanilla=assets)
        if origin:
            rows.insert(1, ("from", origin))
        report.append(rows)
    if arguments.json:
        print(json.dumps([[{"label": a, "text": b} for a, b in rows] for rows in report], indent=2))
        return OK
    for index, rows in enumerate(report):
        if index:
            print()
        print_rows(rows)
    return OK


def register_explain(subparsers) -> None:
    explain = subparsers.add_parser(
        "explain",
        help="what a command line does, without running it",
        description="Analyze line: each execute step, selectors, references, ids against the "
        "client jar, macro arguments, whether the emulator runs it, whether it loads in the "
        "version, and its cost.",
    )
    add_source_arguments(explain)
    add_version_argument(explain)
    explain.add_argument(
        "--line", "-l", action="append", metavar="COMMAND", help="a command line (repeatable)"
    )
    explain.add_argument(
        "--at",
        action="append",
        metavar="FUNCTION[:LINE]",
        help="a line of a function, or all its commands (repeatable)",
    )
    add_json_argument(explain)
    add_vanilla_arguments(explain)
    explain.set_defaults(handler=command_explain)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def command_search(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    version = inputs.version(arguments)
    view = inputs.datapack.view_for(version)
    if not arguments.text.strip():
        raise CliError("give some text to look for")
    hits = id_hits(view, arguments.text) if arguments.ids else text_hits(view, arguments.text)
    if arguments.json:
        print(
            json.dumps(
                [{"label": h.label, "path": str(h.path), "line": h.line} for h in hits], indent=2
            )
        )
    else:
        for hit in hits:
            where = f"{hit.path}:{hit.line}" if hit.line else str(hit.path)
            print(f"{hit.label}\n    {where}" if arguments.paths else hit.label)
        print(f"{len(hits)} hit(s) in {version.id}")
    return OK if hits else FAILED


def register_search(subparsers) -> None:
    search = subparsers.add_parser(
        "search",
        help="find text in the pack's functions, or functions and tags by id",
        description="Search in pack (Ctrl+Shift+F) and, with --ids, quick open (Ctrl+P). "
        "Searches the version's view: base pack and active overlays. Exit 1 when nothing "
        "matches.",
    )
    add_source_arguments(search)
    search.add_argument("text", help="what to look for (case-insensitive)")
    add_version_argument(search)
    search.add_argument("--ids", action="store_true", help="match function and tag ids instead")
    search.add_argument("--paths", action="store_true", help="also print file paths")
    add_json_argument(search)
    search.set_defaults(handler=command_search)


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def command_graph(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    version = inputs.version(arguments)
    graph = CallGraph.from_pack(inputs.datapack.view_for(version))
    if arguments.dot is not None:
        name = inputs.datapack.name.replace(" + ", "+")
        path = arguments.dot if arguments.dot != "" else f"generated/{name}-{version.id}.dot"
        try:
            print(f"dot: {graph.write_dot(path)}")
        except OSError as exc:
            raise CliError(f"cannot write {path}: {exc}") from exc
    if arguments.function:
        rows: Rows = []
        for function_id in arguments.function:
            key = normalise_tagged_id(function_id)
            if key not in graph.nodes:
                raise CliError(f"{function_id} is not in the call graph for {version.id}")
            rows += graph.relations(key) + note_rows(inputs, key)
        print_rows(rows, arguments.json)
        return OK
    if arguments.json:
        print(
            json.dumps(
                {
                    "version": version.id,
                    "nodes": graph.nodes,
                    "edges": [edge.__dict__ for edge in graph.edges],
                    "cycles": graph.cycles(),
                    "missing": graph.missing(),
                    "unreachable": graph.unreachable(),
                    "order": graph.topological_order(),
                },
                indent=2,
                default=str,
            )
        )
        return OK
    print(f"{version.id}: {graph}")
    depths = graph.depths()
    for node in graph.topological_order():
        kind = graph.nodes[node].get("kind", "")
        flags = [kind]
        if graph.nodes[node].get("missing"):
            flags.append("missing")
        if graph.nodes[node].get("macro"):
            flags.append("macro")
        if graph.nodes[node].get("overlay"):
            flags.append(f"overlay {graph.nodes[node]['overlay']}")
        calls = ", ".join(
            f"{edge.target} ({edge.kind})" for edge in graph.edges if edge.source == node
        )
        print(
            f"{'  ' * depths.get(node, 0)}{node}  [{', '.join(flags)}]"
            + (f" → {calls}" if calls else "")
        )
    for cycle in graph.cycles():
        print("recursion: " + " -> ".join(cycle))
    for name in graph.missing():
        print(f"missing function: {name}")
    for name in graph.unreachable():
        print(f"never called: {name}")
    return OK


def register_graph(subparsers) -> None:
    graph = subparsers.add_parser(
        "graph",
        help="the call graph: what calls what",
        description="The call graph tab: every function and tag with its calls (call, macro, "
        "schedule, condition, tag), recursion, missing and never-called functions; with "
        "--function, callers and calls of one function.",
    )
    add_source_arguments(graph)
    add_version_argument(graph)
    graph.add_argument(
        "--function", "-f", action="append", metavar="ID", help="show callers and calls of ID"
    )
    graph.add_argument(
        "--dot",
        nargs="?",
        const="",
        default=None,
        metavar="FILE",
        help="also write Graphviz (default file: generated/<pack>-<version>.dot)",
    )
    add_json_argument(graph)
    graph.set_defaults(handler=command_graph)


# ---------------------------------------------------------------------------
# complete / rename (the source view's completion popup and rename function…)
# ---------------------------------------------------------------------------


def command_complete(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.pack) if arguments.pack else None
    version = (
        inputs.version(arguments) if inputs else parse_version(arguments.version or versions.LATEST)
    )
    view = inputs.datapack.view_for(version) if inputs else None
    vanilla = vanilla_for(arguments, version, inputs) if inputs else None
    line = arguments.line
    cursor = arguments.cursor
    if cursor is not None and not 0 <= cursor <= len(line):
        raise CliError(f"--cursor must be between 0 and {len(line)}")
    found = complete(line, cursor, version, view=view, vanilla=vanilla, limit=arguments.limit)
    if arguments.json:
        print(
            json.dumps(
                [{"text": c.text, "kind": c.kind, "detail": c.detail} for c in found], indent=2
            )
        )
    else:
        for candidate in found:
            print(candidate.label if arguments.details else candidate.text)
        if not found:
            print("nothing to complete here")
    return OK if found else FAILED


def register_complete(subparsers) -> None:
    parser = subparsers.add_parser(
        "complete",
        help="what can be typed next on a command line (the source view's popup)",
        description="Print the commands, execute subcommands and conditions, selector options "
        "and ids that could come next. Without a pack, only what the version itself knows. "
        "Exit 1 when nothing fits.",
    )
    parser.add_argument("line", help="the line as typed so far, e.g. 'execute if '")
    parser.add_argument(
        "--pack",
        type=Path,
        action="append",
        default=None,
        metavar="SOURCE",
        help="a datapack folder or a project, for its function and tag ids (repeatable)",
    )
    parser.add_argument(
        "--cursor", type=int, default=None, metavar="N", help="where the cursor is (default: end)"
    )
    add_version_argument(parser)
    add_vanilla_arguments(parser)
    parser.add_argument("--limit", type=int, default=50, help="how many candidates (default: 50)")
    parser.add_argument("--details", action="store_true", help="also print the kind and the hint")
    add_json_argument(parser)
    parser.set_defaults(handler=command_complete)


def command_rename(arguments: argparse.Namespace) -> int:
    inputs = load_inputs(arguments.source)
    version = inputs.version(arguments)
    try:
        edits = rename_function(
            inputs.datapack, arguments.function, arguments.new_id, version, apply=arguments.apply
        )
    except RenameError as exc:
        raise CliError(str(exc)) from exc
    for edit in edits:
        print(edit.format())
    moves = sum(edit.kind == "move" for edit in edits)
    lines = len(edits) - moves
    done = "renamed" if arguments.apply else "would rename"
    print(f"{done} {arguments.function} -> {arguments.new_id}: {moves} file, {lines} line(s)")
    if not arguments.apply:
        print("nothing was written: --apply does it")
    return OK


def register_rename(subparsers) -> None:
    parser = subparsers.add_parser(
        "rename",
        help="rename a function and every reference to it (the source view's rename function…)",
        description="Move a function's file and follow its id through the pack's functions, "
        "function tags and JSON. Prints what would change; --apply writes it.",
    )
    add_source_arguments(parser)
    parser.add_argument("function", help="the function id to rename, e.g. hat:tick")
    parser.add_argument("new_id", metavar="NEW", help="its new id, e.g. hat:tick/main")
    add_version_argument(parser)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    parser.set_defaults(handler=command_rename)


def register(subparsers) -> None:
    register_info(subparsers)
    register_explain(subparsers)
    register_search(subparsers)
    register_graph(subparsers)
    register_complete(subparsers)
    register_rename(subparsers)
