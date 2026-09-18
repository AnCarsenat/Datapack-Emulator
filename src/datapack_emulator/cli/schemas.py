"""``datapack-emulator-cli schema`` — what a version's own JSON files hold.

The window shows these fields in the problems dock (a field no vanilla file
uses is a warning there); this prints them, and writes a schema file a later
``check --schema`` can read on a machine with no client jar.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datapack_emulator.cli.common import (
    FAILED,
    OK,
    CliError,
    add_vanilla_arguments,
    err,
    parse_version,
    vanilla_for,
)
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.analysis.schema import (
    ENOUGH,
    Schema,
    Shape,
    load_schema,
    save_schema,
)
from datapack_emulator.emulator.analysis.schema import schema_for as learned_schema
from datapack_emulator.settings.main import PATHS


def _schema(arguments: argparse.Namespace) -> Schema:
    if arguments.read is not None:
        try:
            return load_schema(arguments.read)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    version = parse_version(arguments.version or versions.LATEST)
    assets = vanilla_for(arguments, version)
    if assets is None:
        raise CliError(
            f"no client jar for {version.id}: pass --download, --vanilla JAR or --read FILE"
        )
    schema = learned_schema(assets, PATHS.CACHE)
    if schema is None:
        raise CliError(f"cannot read the client jar {assets.jar_path}")
    return schema


def _fields(shape: Shape, folder: str, type_id: str) -> list[tuple[str, bool, str, int]]:
    """``(field, every file sets it, what it holds, how many files)``."""
    inner = shape
    if type_id:
        found = shape.for_type(type_id)
        if found is None:
            known = ", ".join(sorted(shape.variants)[:20]) or "none"
            raise CliError(f"{folder} has no {shape.dispatch or 'type'} {type_id}; known: {known}")
        inner = found
    always = inner.required()
    return [
        (name, name in always, child.describe(), child.seen)
        for name, child in sorted(inner.fields.items())
    ]


def command_schema(arguments: argparse.Namespace) -> int:
    schema = _schema(arguments)
    if arguments.write is not None:
        save_schema(schema, arguments.write)
        print(f"schema: {arguments.write}")
        return OK
    if arguments.folder is None:
        return _print_folders(schema, arguments.json)
    shape = schema.shape(arguments.folder)
    if shape is None:
        known = ", ".join(sorted(schema.folders)) or "none"
        raise CliError(f"{schema.version_id} has no {arguments.folder} files read; known: {known}")
    if arguments.type is None and shape.variants:
        return _print_types(shape, arguments.folder, arguments.json)
    return _print_fields(schema, shape, arguments.folder, arguments.type or "", arguments.json)


def _print_folders(schema: Schema, as_json: bool) -> int:
    """Every folder read, and how many files each was learned from."""
    folders = [
        (name, shape.seen, sorted(shape.variants)) for name, shape in sorted(schema.folders.items())
    ]
    if as_json:
        rows = [
            {"folder": name, "files": seen, "types": variants} for name, seen, variants in folders
        ]
        print(json.dumps({"version": schema.version_id, "folders": rows}, indent=2))
    else:
        print(f"{schema.version_id}: the fields of its own files")
        for name, seen, variants in folders:
            kinds = f", {len(variants)} type(s)" if variants else ""
            print(f"{name:32} {seen:5d} file(s){kinds}")
    return OK if folders else FAILED


def _print_types(shape: Shape, folder: str, as_json: bool) -> int:
    """The types of one folder: recipes by `type`, conditions by `condition`…"""
    variants = [
        (name, held.seen, sorted(held.fields)) for name, held in sorted(shape.variants.items())
    ]
    if as_json:
        rows = [{"type": name, "files": seen, "fields": names} for name, seen, names in variants]
        print(json.dumps({"folder": folder, "dispatch": shape.dispatch, "types": rows}, indent=2))
    else:
        print(f"{folder}: {shape.seen} file(s), by {shape.dispatch}")
        for name, seen, names in variants:
            print(f"{name:44} {seen:5d} file(s), {len(names)} field(s)")
    return OK if variants else FAILED


def _print_fields(schema: Schema, shape: Shape, folder: str, type_id: str, as_json: bool) -> int:
    """What one kind of file holds, field by field."""
    fields = _fields(shape, folder, type_id)
    if as_json:
        rows = [
            {"field": name, "always": always, "holds": holds, "files": seen}
            for name, always, holds, seen in fields
        ]
        print(json.dumps({"folder": folder, "type": type_id, "fields": rows}, indent=2))
        return OK if fields else FAILED
    print(f"{folder} {type_id}".strip() + f" in {schema.version_id}")
    if shape.seen < ENOUGH:
        err(f"only {shape.seen} file(s) read: nothing is claimed about what they always hold")
    for name, always, holds, seen in fields:
        mark = "required" if always else "optional"
        print(f"{name:28} {mark:9} {holds} ({seen} file(s))")
    if not fields:
        print("no fields")
    return OK if fields else FAILED


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "schema",
        help="what a version's own JSON files hold (the fields `check` reads)",
        description="The game ships its vanilla data pack inside client.jar. This reads it "
        "and says, for that version, which fields each kind of recipe, loot table, predicate, "
        "item modifier, advancement and worldgen file has, which are always there and what "
        "they hold. `check` uses the same schema to report fields the version's own files "
        "never use. Exit 1 when there is nothing to show.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="a resource folder, e.g. recipe, loot_table, worldgen/biome",
    )
    parser.add_argument(
        "type",
        nargs="?",
        default=None,
        help="one type of that folder, e.g. minecraft:crafting_shaped",
    )
    parser.add_argument(
        "--version", default=None, help="which version's files (default: the newest)"
    )
    parser.add_argument(
        "--read", type=Path, default=None, metavar="FILE", help="a written schema, not a jar"
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        metavar="FILE",
        help="write the schema, for `check --schema FILE` where there is no jar",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    add_vanilla_arguments(parser)
    parser.set_defaults(handler=command_schema)
