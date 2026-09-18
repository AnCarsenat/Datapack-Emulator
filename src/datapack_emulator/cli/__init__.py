"""The command line: ``datapack-emulator-cli``.

Everything the window does has a subcommand here (only what needs a screen
does not), so packs can be checked from a terminal or CI::

    datapack-emulator-cli run    samples/hat --ticks 20 --version 1.21.4
    datapack-emulator-cli matrix samples/hat --from 1.20.4 --to 1.21.6
    datapack-emulator-cli test   projects/hat.dpemu --junit generated/tests.xml
    datapack-emulator-cli shell  projects/hat.dpemu
    datapack-emulator-cli world  samples/hat --ticks 5 --scores
    datapack-emulator-cli explain samples/hat --at hat:tick
    datapack-emulator-cli check  samples/hat --declared
    datapack-emulator-cli versions
    datapack-emulator-cli vanilla --download 1.21.4

See docs/cli.md. No Qt is imported here.
"""

from __future__ import annotations

import argparse
import logging
import traceback

from datapack_emulator import __version__
from datapack_emulator.cli import check, inspect, jars, projects, runs, world
from datapack_emulator.cli.common import CRASHED, USAGE, CliError, err

PROG = "datapack-emulator-cli"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Run, test and profile Minecraft datapacks without the game.",
        allow_abbrev=False,
    )
    parser.add_argument("--quiet", action="store_true", help="silence the Python logger")
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="print the version of this copy and stop",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True, metavar="COMMAND")
    runs.register_run(subparsers)
    runs.register_matrix(subparsers)
    runs.register_test(subparsers)
    world.register(subparsers)
    inspect.register(subparsers)
    check.register(subparsers)
    projects.register(subparsers)
    jars.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if arguments.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return arguments.handler(arguments)
    except CliError as exc:
        err(f"{PROG}: {exc}")
        return USAGE
    except OSError as exc:  # a report or file that cannot be written or read
        err(f"{PROG}: {exc}")
        return USAGE
    except KeyboardInterrupt:
        err("interrupted")
        return 130
    except Exception:  # a bug: never mistaken for a failed test
        traceback.print_exc()
        err(f"{PROG}: internal error, please report it")
        return CRASHED


__all__ = ["build_parser", "main"]
