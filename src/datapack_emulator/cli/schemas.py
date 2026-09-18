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
from datapack_emulator.emulator.analysis.schema import ENOUGH, Schema, load_schema, save_schema
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


def _fields(shape, folder: str, type_id: str) -> list[dict[str, object]]:
    inner = shape
    if type_id:
        found = shape.for_type(type_id)
        if found is None:
            known = ", ".join(sorted(shape.variants)[:20]) or "none"
            raise CliError(f"{folder} has no {shape.dispatch or 'type'} {type_id}; known: {known}")
        inner = found
    required = inner.required()
    return [
        {
            "field": name,
            "required": name in required,
            "holds": child.describe(),
            "files": child.seen,
        }
        for name, child in sorted(inner.fields.items())
    ]


def command_schema(arguments: argparse.Namespace) -> int:
    schema = _schema(arguments)
    if arguments.write is not None:
        save_schema(schema, arguments.write)
        print(f"schema: {arguments.write}")
        return OK

    folder = arguments.folder
    if folder is None:
        rows = [
            {"folder": name, "files": shape.seen, "types": sorted(shape.variants)}
            for name, shape in sorted(schema.folders.items())
        ]
        if arguments.json:
            print(json.dumps({"version": schema.version_id, "folders": rows}, indent=2))
        else:
            print(f"{schema.version_id}: the fields of its own files")
            for row in rows:
                kinds = f", {len(row['types'])} type(s)" if row["types"] else ""
                print(f"{row['folder']:32} {row['files']:5d} file(s){kinds}")
        return OK if rows else FAILED

    shape = schema.shape(folder)
    if shape is None:
        known = ", ".join(sorted(schema.folders)) or "none"
        raise CliError(f"{schema.version_id} has no {folder} files read; known: {known}")
    if arguments.type is None and shape.variants:
        rows = [
            {"type": name, "files": held.seen, "fields": sorted(held.fields)}
            for name, held in sorted(shape.variants.items())
        ]
        if arguments.json:
            print(
                json.dumps({"folder": folder, "dispatch": shape.dispatch, "types": rows}, indent=2)
            )
        else:
            print(f"{folder}: {shape.seen} file(s), by {shape.dispatch}")
            for row in rows:
                print(f"{row['type']:44} {row['files']:5d} file(s), {len(row['fields'])} field(s)")
        return OK if rows else FAILED

    rows = _fields(shape, folder, arguments.type or "")
    if arguments.json:
        print(
            json.dumps({"folder": folder, "type": arguments.type or "", "fields": rows}, indent=2)
        )
        return OK if rows else FAILED
    where = f"{folder} {arguments.type}" if arguments.type else folder
    print(f"{where} in {schema.version_id}")
    if shape.seen < ENOUGH:
        err(f"only {shape.seen} file(s) read: nothing is claimed about what is required")
    for row in rows:
        mark = "required" if row["required"] else "optional"
        print(f"{row['field']:28} {mark:9} {row['holds']} ({row['files']} file(s))")
    if not rows:
        print("no fields")
    return OK if rows else FAILED


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
