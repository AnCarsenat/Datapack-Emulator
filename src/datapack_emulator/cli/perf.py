"""``server-profile`` and ``calibrate``: real timings from a Minecraft server.

``server-profile`` reads a ``/perf`` report (the zip a server writes to
``debug/profiling/``) and prints what each function and line really cost;
given the pack, it also runs the emulator and puts its estimate beside each.

``calibrate`` measures the emulator's cost model on a real server: it runs a
benchmark pack on a vanilla dedicated server, reads ``/perf`` reports, and
fits the constants. Running a server means accepting the Minecraft EULA, so
it needs ``--accept-eula``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from datapack_emulator.cli.common import OK, CliError, err, load_inputs, parse_version
from datapack_emulator.emulator import costs
from datapack_emulator.emulator.server_profile import ServerProfile, compare


def command_server_profile(arguments: argparse.Namespace) -> int:
    try:
        profile = ServerProfile.read(arguments.report)
    except (OSError, ValueError) as exc:
        raise CliError(f"cannot read {arguments.report}: {exc}") from exc
    data = profile.to_dict(top_lines=arguments.lines)
    rows = None
    if arguments.pack:
        from datapack_emulator.emulator.runtime.emulator import Emulator
        from datapack_emulator.emulator.runtime.output import OutputBus

        inputs = load_inputs(arguments.pack)
        version = parse_version(arguments.version or profile.version)
        emulator = Emulator(
            inputs.datapack, version=version, players=arguments.players, output=OutputBus()
        )
        emulator.run(ticks=arguments.ticks)
        rows = compare(profile, emulator.profiler)
        data["compared_with"] = {
            "version": version.id,
            "ticks": arguments.ticks,
            "players": arguments.players,
            "cost_model": costs.USER_CALIBRATION or f"built in (measured on {costs.CALIBRATED_ON})",
            "functions": rows,
        }
    if arguments.json:
        print(json.dumps(data, indent=2))
        return OK
    print(
        f"{profile.version}: {profile.ticks} ticks in {profile.time_span_ms / 1000:.1f} s, "
        f"{data['mspt']:.3f} ms per tick, datapacks {data['datapack_ms_per_tick']:.3f} ms"
    )
    print(f"\n{'function':44} {'calls/tick':>10} {'ms/tick':>10}")
    for row in data["functions"][: arguments.top]:
        print(f"{row['function']:44} {row['calls_per_tick']:10.2f} {row['ms_per_tick']:10.4f}")
        for line in row["lines"]:
            print(
                f"    {line['command'][:60]:60} "
                f"{line['runs_per_tick']:8.1f}x {line['us_each']:8.3f} us"
            )
    if rows is not None:
        print(f"\n{'function':44} {'measured':>10} {'estimated':>10} {'ratio':>7}")
        for row in rows[: arguments.top]:
            estimate = row["estimated_ms_per_tick"]
            ratio = row["ratio"]
            print(
                f"{row['function']:44} {row['measured_ms_per_tick']:10.4f} "
                f"{estimate if estimate is not None else '-':>10} "
                f"{ratio if ratio is not None else '-':>7}"
            )
        print("(ms per tick; ratio > 1: the emulator's estimate is too low)")
    return OK


def command_calibrate(arguments: argparse.Namespace) -> int:
    from datapack_emulator.emulator import calibration
    from datapack_emulator.settings.main import PATHS

    if not arguments.accept_eula:
        raise CliError(
            "calibrate runs a Minecraft server, which means accepting the Minecraft EULA "
            "(https://aka.ms/MinecraftEULA): pass --accept-eula if you agree to it"
        )
    version = parse_version(arguments.version)
    workdir = arguments.workdir or PATHS.CACHE / "calibration" / version.id
    try:
        document = calibration.calibrate(
            version,
            workdir,
            accept_eula=True,
            jar=arguments.server_jar,
            java=arguments.java,
            runs=arguments.runs,
            progress=err,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        raise CliError(f"calibration failed: {exc}") from exc
    out = arguments.out or Path("generated") / f"calibration-{version.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    if arguments.install:
        target = PATHS.DATA / "costs.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
        err(f"installed: {target} (used by every run from now on)")
    if arguments.json:
        print(json.dumps(document, indent=2))
        return OK
    print(f"{'benchmark':18} {'phase':8} {'measured':>10} {'old model':>10} {'fitted':>10}  us/rep")
    for row in document["benchmarks"]:
        print(
            f"{row['benchmark']:18} {row['phase']:8} {row['measured_us_per_rep']:10.4f} "
            f"{row['old_model_us_per_rep']:10.3f} {row['fitted_us_per_rep']:10.4f}"
        )
    print(f"\ncalibration: {out}")
    return OK


def register(subparsers) -> None:
    report = subparsers.add_parser(
        "server-profile",
        help="real timings: read a server's /perf report",
        description="Read the zip a Minecraft server writes after /perf start (debug/profiling/) "
        "and print the measured time of every function and line. With the pack, also run it "
        "in the emulator and compare.",
    )
    report.add_argument("report", type=Path, help="the /perf zip, or its server/profiling.txt")
    report.add_argument("--pack", type=Path, nargs="+", default=None, help="compare with this pack")
    report.add_argument("--version", default=None, help="default: the report's version")
    report.add_argument("--ticks", type=int, default=200, help="emulated ticks (default 200)")
    report.add_argument("--players", type=int, default=1, help="emulated players (default 1)")
    report.add_argument("--top", type=int, default=30, help="functions printed (default 30)")
    report.add_argument("--lines", type=int, default=5, help="lines per function (default 5)")
    report.add_argument("--json", action="store_true", help="print JSON")
    report.set_defaults(handler=command_server_profile)

    calibrate = subparsers.add_parser(
        "calibrate",
        help="measure the cost model on a real Minecraft server",
        description="Run a benchmark pack on a vanilla dedicated server (offline, flat world, no "
        "players; Java required), read its /perf reports and fit the cost model's constants. "
        "Takes about two minutes. Running a server means accepting the Minecraft EULA.",
    )
    calibrate.add_argument("--version", required=True, help="Minecraft version (1.20.3+)")
    calibrate.add_argument(
        "--accept-eula",
        action="store_true",
        help="you accept the Minecraft EULA (https://aka.ms/MinecraftEULA)",
    )
    calibrate.add_argument("--server-jar", type=Path, default=None, help="default: download it")
    calibrate.add_argument("--java", default="java", help="the Java to run it with")
    calibrate.add_argument("--workdir", type=Path, default=None, help="the server's folder")
    calibrate.add_argument("--runs", type=int, default=3, help="/perf reports per phase")
    calibrate.add_argument(
        "--out", type=Path, default=None, help="default: generated/calibration-<version>.json"
    )
    calibrate.add_argument(
        "--install", action="store_true", help="also use it for every run (data folder costs.json)"
    )
    calibrate.add_argument("--json", action="store_true", help="print the calibration as JSON")
    calibrate.set_defaults(handler=command_calibrate)
