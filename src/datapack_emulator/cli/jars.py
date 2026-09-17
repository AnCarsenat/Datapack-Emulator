"""``versions`` and ``vanilla``: what the emulator knows about the base game."""

from __future__ import annotations

import argparse
from pathlib import Path

from datapack_emulator.cli.common import FAILED, OK, err, parse_version
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.vanilla import VanillaAssets, default_library


def command_versions(arguments: argparse.Namespace) -> int:
    print(f"{'version':10} {'format':8} {'data version':>12}")
    for version in versions.VERSIONS:
        print(f"{version.id:10} {version.format_string:8} {version.data_version:12d}")
    prereleases = [version.id for version in versions.VERSIONS if not version.stable]
    print(
        f"\n{len(versions.VERSIONS) - len(prereleases)} releases, newest {versions.LATEST.id}"
        + (f"; upcoming: {', '.join(prereleases)}" if prereleases else "")
    )
    return OK


def command_vanilla(arguments: argparse.Namespace) -> int:
    """List, download and inspect client jars."""
    library = default_library()
    if arguments.download:
        path = library.download(parse_version(arguments.download).id, progress=err)
        print(f"downloaded {path}")
    if arguments.inspect:
        path = Path(arguments.inspect)
        assets = (
            VanillaAssets.from_jar(path)
            if path.is_file()
            else library.load(parse_version(arguments.inspect).id)
        )
        if assets is None:
            err(f"no client jar for {arguments.inspect}")
            return FAILED
        print(assets.summary)
        for registry, ids in sorted(assets.registries.items()):
            print(f"  {registry:16} {len(ids):5d}  e.g. {', '.join(sorted(ids)[:3])}")
        return OK

    jars = library.local_jars()
    print(f"{len(jars)} client jar(s) found")
    for version_id, path in sorted(jars.items()):
        print(f"  {version_id:10} {path}")
    if not jars:
        print("  (pass --download <version> to fetch one)")
    return OK


def register(subparsers) -> None:
    listing = subparsers.add_parser("versions", help="list known versions")
    listing.set_defaults(handler=command_versions)

    vanilla = subparsers.add_parser("vanilla", help="list, download and inspect client jars")
    vanilla.add_argument("--download", metavar="VERSION", default=None)
    vanilla.add_argument("--inspect", metavar="VERSION|JAR", default=None)
    vanilla.set_defaults(handler=command_vanilla)
