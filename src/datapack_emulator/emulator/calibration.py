"""Measuring the cost model on a real Minecraft server.

The emulator charges every command a cost from :mod:`costs`. Those numbers
used to be guesses. This module replaces them with measurements:

1. :func:`write_benchmark_pack` writes a datapack in which every
   ``#minecraft:tick`` function repeats one kind of command many times
   (``scoreboard players add`` x 2000, ``data get entity @s Pos`` x 500, an
   ``@e`` scan x 200, ...).
2. :func:`run_server` starts a vanilla dedicated server on it (Java, offline,
   flat world, no players) and takes ``/perf`` reports -- once with no extra
   entities, once with ``MARKERS`` markers, so selector scans can be told
   apart from the rest.
3. :func:`fit` reads each benchmark function's real time per tick from the
   reports (:mod:`server_profile`) and solves for the cost-model constants.
   The model is linear in its constants, so the emulator's own
   ``Command.estimate_cost`` gives the equations: for each constant, how much
   of it each benchmark line pays. A ridge term keeps constants no benchmark
   exercises at the old guess times one global scale, so they move with the
   machine instead of staying at an unrelated number.

Running a server means accepting the Minecraft EULA
(https://aka.ms/MinecraftEULA); :func:`run_server` refuses unless told the
user did (``accept_eula=True``, ``calibrate --accept-eula``).
"""

from __future__ import annotations

import collections
import contextlib
import copy
import functools
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from datapack_emulator.emulator import costs, versions
from datapack_emulator.emulator.commands import Command
from datapack_emulator.emulator.server_profile import ServerProfile
from datapack_emulator.emulator.versions import Version

log = logging.getLogger(__name__)

NS = "calib"
#: entities added for the second report, to measure selector scans
MARKERS = 1000
#: the first version whose profiler times each line (and the one this was built on)
PER_LINE_SINCE = "1.20.3"

Progress = Callable[[str], None]


@dataclass
class Benchmark:
    """One ``#minecraft:tick`` function: ``line`` repeated ``reps`` times.

    ``inner`` runs the repeated lines in a function called once ``as`` the
    benchmark armor stand (for commands that need an entity as ``@s``).
    ``alternate`` alternates the line with a second one (``tag add`` /
    ``tag remove``), so each run does real work.
    """

    name: str
    line: str
    reps: int
    alternate: str = ""
    inner: bool = False
    #: the body of the function each line calls, if it calls one
    callee: tuple[str, ...] = ()
    #: the phases whose times say something about the model (``nbt=`` with
    #: no candidate entity measures nothing)
    phases: tuple[str, ...] = ("base", "markers")

    def body(self) -> list[str]:
        lines = []
        for index in range(self.reps):
            lines.append(self.alternate if self.alternate and index % 2 else self.line)
        return lines

    def outer_lines(self) -> list[str]:
        if not self.inner:
            return self.body()
        return [
            f"execute as @e[type=minecraft:armor_stand,tag={NS}.as,limit=1] at @s "
            f"run function {NS}:inner/{self.name}"
        ]

    def executed_lines(self) -> list[str]:
        """Every line one run of the benchmark executes (for the model)."""
        lines = self.outer_lines() + (self.body() if self.inner else [])
        return lines + list(self.callee) * self.reps


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark("score_add", f"scoreboard players add #c {NS} 1", 2000),
    Benchmark("score_operation", f"scoreboard players operation #a {NS} += #c {NS}", 2000),
    Benchmark("score_get", f"scoreboard players get #c {NS}", 1000),
    Benchmark("storage_set", f"data modify storage {NS}:s v set value 1", 1000),
    Benchmark("storage_get", f"data get storage {NS}:s v", 1000),
    Benchmark("storage_merge", f"data merge storage {NS}:s {{a:1,b:[1,2,3]}}", 500),
    Benchmark("execute_run", f"execute run scoreboard players add #c {NS} 1", 2000),
    Benchmark(
        "if_score",
        f"execute if score #c {NS} matches 0.. run scoreboard players add #d {NS} 1",
        2000,
    ),
    Benchmark("if_data", f"execute if data storage {NS}:s v", 1000),
    Benchmark("if_block", "execute if block ~ ~ ~ minecraft:air", 1000),
    Benchmark("if_predicate", f"execute if predicate {NS}:chance", 1000),
    Benchmark("if_entity_all", f"execute if entity @e[tag={NS}.none]", 200),
    Benchmark("if_entity_type", f"execute if entity @e[type=minecraft:marker,tag={NS}.none]", 200),
    Benchmark("if_entity_limit", f"execute if entity @e[tag={NS}.m,limit=1]", 200),
    Benchmark(
        "if_entity_nbt",
        "execute if entity @e[type=minecraft:marker,nbt={data:{z:1b}}]",
        20,
        phases=("markers",),
    ),
    Benchmark(
        "store_result",
        f"execute store result score #s {NS} run scoreboard players get #c {NS}",
        1000,
    ),
    Benchmark(
        "as_at",
        f"execute as @e[type=minecraft:armor_stand,tag={NS}.as,limit=1] at @s "
        f"run scoreboard players add #c {NS} 1",
        500,
    ),
    Benchmark("tag", f"tag @s add {NS}.t", 1000, alternate=f"tag @s remove {NS}.t", inner=True),
    Benchmark("entity_get", "data get entity @s Pos", 500, inner=True),
    Benchmark("if_data_entity", "execute if data entity @s Pos", 500, inner=True),
    Benchmark("entity_modify", "data modify entity @s Invisible set value 1b", 300, inner=True),
    Benchmark("tp", "tp @s ~ ~ ~", 1000, inner=True),
    Benchmark(
        "effect",
        "effect give @s minecraft:speed 1 0 true",
        500,
        alternate="effect clear @s minecraft:speed",
        inner=True,
    ),
    Benchmark("attribute", "attribute @s minecraft:max_health base get", 1000, inner=True),
    Benchmark("item", "item replace entity @s armor.head with minecraft:stone", 500, inner=True),
    Benchmark("function", f"function {NS}:noop", 2000),
    Benchmark(
        "macro",
        f"function {NS}:macro {{v:1}}",
        500,
        callee=(f"$scoreboard players set #m {NS} $(v)",),
    ),
    Benchmark("schedule", f"schedule function {NS}:noop 1t replace", 1000),
    Benchmark(
        "setblock",
        "setblock ~ ~5 ~ minecraft:stone",
        500,
        alternate="setblock ~ ~5 ~ minecraft:air",
    ),
    Benchmark(
        "fill",
        "fill ~ ~10 ~ ~3 ~13 ~3 minecraft:stone",
        50,
        alternate="fill ~ ~10 ~ ~3 ~13 ~3 minecraft:air",
    ),
    Benchmark(
        "fill_large",
        "fill ~ ~20 ~ ~7 ~27 ~7 minecraft:stone",
        10,
        alternate="fill ~ ~20 ~ ~7 ~27 ~7 minecraft:air",
    ),
    Benchmark("summon", f'summon minecraft:marker ~ ~ ~ {{Tags:["{NS}.tmp"]}}', 100),
    Benchmark("kill", f"kill @e[type=minecraft:marker,tag={NS}.tmp,limit=1]", 100),
    Benchmark("tellraw", 'tellraw @a {"text":"x"}', 1000),
    Benchmark("particle", "particle minecraft:flame ~ ~ ~ 0 0 0 0 1", 500),
)
"""What is measured. Order is the order of ``#minecraft:tick``: ``summon``
runs before ``kill``, so each tick removes the markers it made."""


# ---------------------------------------------------------------------------
# the benchmark pack
# ---------------------------------------------------------------------------


def _folder(version: Version, singular: str, plural: str) -> str:
    return singular if versions.uses_singular_registries(version) else plural


def pack_mcmeta(version: Version) -> dict[str, Any]:
    pack: dict[str, Any] = {"description": "datapack-emulator cost calibration"}
    if versions.supports_min_max_format(version):
        # pack_format must be absent once min_format is above 81
        fmt = [version.pack_format, version.pack_format_minor]
        pack["min_format"] = fmt
        pack["max_format"] = fmt
    else:
        pack["pack_format"] = version.pack_format
    return {"pack": pack}


def write_benchmark_pack(root: Path, version: Version) -> Path:
    """Write the benchmark datapack folder ``root`` for ``version``."""
    if root.exists():
        shutil.rmtree(root)
    function = _folder(version, "function", "functions")
    tags = _folder(version, "tags/function", "tags/functions")
    predicate = _folder(version, "predicate", "predicates")
    data = root / "data"
    fn = data / NS / function

    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write(root / "pack.mcmeta", json.dumps(pack_mcmeta(version), indent=2))
    write(
        fn / "load.mcfunction",
        "\n".join(
            [
                "forceload add ~ ~",
                f"scoreboard objectives add {NS} dummy",
                f"scoreboard players set #c {NS} 0",
                f"data modify storage {NS}:s v set value 1",
                f"kill @e[tag={NS}.as]",
                f'summon minecraft:armor_stand ~ ~ ~ {{Tags:["{NS}.as"],NoGravity:1b,'
                "Invulnerable:1b}",
                "",
            ]
        ),
    )
    write(fn / "noop.mcfunction", "")
    macro = next(b for b in BENCHMARKS if b.name == "macro")
    write(fn / "macro.mcfunction", "\n".join(macro.callee) + "\n")
    write(
        fn / "spawn_markers.mcfunction",
        f'summon minecraft:marker ~ ~ ~ {{Tags:["{NS}.m"],data:{{z:0b}}}}\n' * MARKERS,
    )
    write(fn / "clear_markers.mcfunction", f"kill @e[type=minecraft:marker,tag={NS}.m]\n")
    for bench in BENCHMARKS:
        write(fn / "bench" / f"{bench.name}.mcfunction", "\n".join(bench.outer_lines()) + "\n")
        if bench.inner:
            write(fn / "inner" / f"{bench.name}.mcfunction", "\n".join(bench.body()) + "\n")
    write(
        data / NS / predicate / "chance.json",
        json.dumps({"condition": "minecraft:random_chance", "chance": 0.5}),
    )
    write(data / "minecraft" / tags / "load.json", json.dumps({"values": [f"{NS}:load"]}))
    write(
        data / "minecraft" / tags / "tick.json",
        json.dumps({"values": [f"{NS}:bench/{b.name}" for b in BENCHMARKS]}),
    )
    return root


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------

SERVER_PROPERTIES = {
    "online-mode": "false",
    "level-type": "minecraft\\:flat",
    "generate-structures": "false",
    "view-distance": "3",
    "simulation-distance": "3",
    "spawn-protection": "0",
    "max-tick-time": "-1",
    "spawn-monsters": "false",
    "spawn-animals": "false",
    "spawn-npcs": "false",
    "level-name": "world",
    "sync-chunk-writes": "false",
    # an empty server stops ticking after a minute (1.21.2+): never here
    "pause-when-empty-seconds": "-1",
    "enable-rcon": "false",
    "enable-query": "false",
    "log-ips": "false",
}


def download_server_jar(
    version: Version, cache_dir: Path, progress: Progress | None = None
) -> Path:
    """Mojang's dedicated server jar for ``version``, cached and SHA-1 checked."""
    from datapack_emulator.emulator.vanilla import VanillaLibrary

    target = cache_dir / version.id / f"minecraft-{version.id}-server.jar"
    if target.is_file():
        return target
    entry = next(
        (v for v in VanillaLibrary.manifest().get("versions", []) if v["id"] == version.id), None
    )
    if entry is None:
        raise KeyError(f"Mojang's manifest has no version {version.id!r}")
    with urllib.request.urlopen(entry["url"], timeout=30) as response:
        meta = json.loads(response.read())
    server = meta.get("downloads", {}).get("server", {})
    if not server.get("url"):
        raise KeyError(f"{version.id} has no server download")
    if progress:
        size = int(server.get("size", 0)) // 1024
        progress(f"downloading the {version.id} server jar ({size} KiB)")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha1()
    try:
        with (
            urllib.request.urlopen(server["url"], timeout=120) as response,
            partial.open("wb") as out,
        ):
            while chunk := response.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
        expected = str(server.get("sha1", "")).lower()
        if expected and digest.hexdigest() != expected:
            raise OSError(f"server jar is corrupt: sha1 {digest.hexdigest()} != {expected}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


class _Server:
    """A dedicated server driven through its console."""

    def __init__(self, jar: Path, workdir: Path, java: str, memory: str, progress: Progress):
        self.workdir = workdir
        self.progress = progress
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.process = subprocess.Popen(
            [java, f"-Xmx{memory}", f"-Xms{memory}", "-jar", str(jar), "--nogui"],
            cwd=workdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            with self._lock:
                self.lines.append(line.rstrip("\n"))

    def wait_for(self, text: str, timeout: float, since: int = 0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for line in self.lines[since:]:
                    if text in line:
                        return line
            if self.process.poll() is not None:
                raise RuntimeError("the server exited:\n" + "\n".join(self.lines[-30:]))
            time.sleep(0.2)
        raise TimeoutError(f"waited {timeout:.0f}s for {text!r}:\n" + "\n".join(self.lines[-30:]))

    def command(self, line: str) -> int:
        assert self.process.stdin is not None
        with self._lock:
            mark = len(self.lines)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return mark

    def perf(self) -> Path:
        mark = self.command("perf start")
        done = self.wait_for("Created debug report in", 60, since=mark)
        name = done.rsplit(" ", 1)[-1].strip()
        report = self.workdir / "debug" / "profiling" / name
        if not report.is_file():
            raise FileNotFoundError(f"the server said it wrote {report}, which is not there")
        return report

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.command("stop")
                self.process.wait(60)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()


def run_server(
    version: Version,
    workdir: Path,
    *,
    accept_eula: bool,
    jar: Path | None = None,
    cache_dir: Path | None = None,
    java: str = "java",
    memory: str = "2G",
    runs: int = 2,
    progress: Progress | None = None,
) -> dict[str, list[Path]]:
    """Run the benchmark pack on a real server; ``/perf`` report paths by phase.

    Phase ``"base"`` has the armor stand and nothing else; phase ``"markers"``
    adds :data:`MARKERS` markers. Each phase takes ``runs`` ten-second reports.
    """
    if not accept_eula:
        raise PermissionError(
            "running a Minecraft server means accepting the Minecraft EULA "
            "(https://aka.ms/MinecraftEULA); pass accept_eula=True / --accept-eula "
            "only if the user agreed to it"
        )
    say = progress or (lambda text: log.info("%s", text))
    if shutil.which(java) is None and not Path(java).is_file():
        raise FileNotFoundError(
            f"no Java runtime {java!r}: install one (Minecraft {version.id} needs it)"
        )
    if jar is None:
        from datapack_emulator.settings import PATHS

        jar = download_server_jar(version, cache_dir or PATHS.CACHE / "servers", say)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    (workdir / "server.properties").write_text(
        "".join(f"{key}={value}\n" for key, value in SERVER_PROPERTIES.items())
        + f"server-port={_free_port()}\n",
        encoding="utf-8",
    )
    write_benchmark_pack(workdir / "world" / "datapacks" / NS, version)
    say(f"starting the {version.id} server in {workdir}")
    server = _Server(jar, workdir, java, memory, say)
    reports: dict[str, list[Path]] = {"base": [], "markers": []}
    try:
        server.wait_for("Done (", 600)
        server.command(f"function {NS}:clear_markers")
        say("warming up (JIT) for 20 s")
        time.sleep(20)
        for phase in ("base", "markers"):
            if phase == "markers":
                server.command(f"function {NS}:spawn_markers")
                time.sleep(5)
            for index in range(runs):
                say(f"/perf {phase} {index + 1}/{runs} (10 s)")
                reports[phase].append(server.perf())
    finally:
        server.stop()
    return reports


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------

#: the model's constants, as (attribute, key or None)
Param = tuple[str, str | None]
_DICTS = (
    "COMMAND_COST_US",
    "SUBCOMMAND_COST_US",
    "CONDITION_COST_US",
    "SELECTOR_BASE_COST_US",
)
_SCALARS = (
    "DEFAULT_COMMAND_COST_US",
    "SELECTOR_PER_ENTITY_COST_US",
    "NBT_FILTER_COST_US",
    "MACRO_CALL_OVERHEAD_US",
    "VOLUME_COST_US_PER_BLOCK",
    "DATA_ENTITY_EXTRA_US",
)
#: multipliers, measured as ratios of scan slopes rather than fitted
_RATIOS = ("TYPE_FILTER_DISCOUNT", "LIMIT_DISCOUNT")
#: ``limit=`` stops at the first match: measured from 0.01 (the match comes
#: first) to 1.0 (it comes last); the model takes the middle
LIMIT_DISCOUNT = 0.5


def parameters() -> list[Param]:
    params: list[Param] = []
    for name in _DICTS:
        params += [(name, key) for key in getattr(costs, name)]
    params += [(name, None) for name in _SCALARS]
    return params


def _get(param: Param) -> float:
    name, key = param
    value = getattr(costs, name)
    return float(value[key]) if key is not None else float(value)


def _set(param: Param, value: float) -> None:
    name, key = param
    if key is not None:
        getattr(costs, name)[key] = value
    else:
        setattr(costs, name, value)


def snapshot() -> dict[str, Any]:
    return {name: copy.deepcopy(getattr(costs, name)) for name in _DICTS + _SCALARS + _RATIOS}


def restore(saved: dict[str, Any]) -> None:
    for name, value in saved.items():
        setattr(costs, name, copy.deepcopy(value))


@functools.lru_cache(maxsize=4096)
def _parsed(line: str) -> Command | None:
    return Command.parse(line)


def model_cost(lines: list[str], entity_count: int) -> float:
    """The model's cost of ``lines`` in the benchmark world, which has no
    players. Identical lines are costed once and multiplied: a benchmark is
    the same line hundreds of times, and the fit costs each one per constant."""
    total = 0.0
    for line, times in collections.Counter(lines).items():
        command = _parsed(line)
        if command is not None:
            total += times * command.estimate_cost(entity_count, player_count=0)
    return total


def design_row(
    lines: list[str], entity_count: int, params: list[Param]
) -> tuple[float, list[float]]:
    """``(constant, coefficients)``: the model cost of ``lines`` is
    ``constant + sum(coefficient * parameter)``, the ratios held fixed."""
    saved = snapshot()
    try:
        for param in params:
            _set(param, 0.0)
        constant = model_cost(lines, entity_count)
        row = []
        for param in params:
            _set(param, 1.0)
            row.append(model_cost(lines, entity_count) - constant)
            _set(param, 0.0)
        return constant, row
    finally:
        restore(saved)


@dataclass
class Measurement:
    benchmark: Benchmark
    phase: str
    entity_count: int
    #: microseconds per run of the benchmark function, as the server measured it
    us: float


def measure(reports: dict[str, list[Path]]) -> list[Measurement]:
    """Benchmark times per phase, averaged over the phase's reports."""
    entity_counts = {"base": 1, "markers": 1 + MARKERS}
    out = []
    for phase, paths in reports.items():
        per_bench: dict[str, list[float]] = {}
        for path in paths:
            profile = ServerProfile.read(path)
            rows = {row["function"]: row for row in profile.functions()}
            for bench in BENCHMARKS:
                row = rows.get(f"{NS}:bench/{bench.name}")
                if row is None or bench.reps == 0:
                    continue
                per_run = row["us_per_tick"] / max(row["calls_per_tick"], 1e-9)
                per_bench.setdefault(bench.name, []).append(max(per_run, 0.0))
        for bench in BENCHMARKS:
            values = per_bench.get(bench.name)
            if values and phase in bench.phases:
                out.append(
                    Measurement(bench, phase, entity_counts[phase], sum(values) / len(values))
                )
    return out


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small, dense, symmetric)."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-15:
            continue
        for row in range(n):
            if row != col and a[row][col]:
                factor = a[row][col] / a[col][col]
                a[row] = [x - factor * y for x, y in zip(a[row], a[col], strict=True)]
    return [a[i][n] / a[i][i] if abs(a[i][i]) > 1e-15 else 0.0 for i in range(n)]


@dataclass
class Fit:
    values: dict[str, Any]
    scale: float
    measured: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


def _scan_slope(measurements: list[Measurement], name: str) -> float | None:
    by_phase = {m.phase: m for m in measurements if m.benchmark.name == name}
    if "base" not in by_phase or "markers" not in by_phase:
        return None
    reps = by_phase["base"].benchmark.reps
    return (by_phase["markers"].us - by_phase["base"].us) / reps / MARKERS


def fit(measurements: list[Measurement], ridge: float = 1e-5) -> Fit:
    """Solve for the constants; returns the new values (not applied)."""
    if not measurements:
        raise ValueError("nothing was measured")
    saved = snapshot()
    try:
        params = parameters()
        before = [model_cost(m.benchmark.executed_lines(), m.entity_count) for m in measurements]

        # 1. the scan constants, straight from how a scan's cost grows with
        #    the entities in the world (a fit would trade them against the
        #    cheap best-case ``limit=`` rows)
        plain = _scan_slope(measurements, "if_entity_all")
        typed = _scan_slope(measurements, "if_entity_type")
        direct: dict[str, float] = {}
        if plain and plain > 0:
            direct["SELECTOR_PER_ENTITY_COST_US"] = plain
            type_share = min(max(typed / plain, 0.01), 1.0) if typed is not None else 1.0
            direct["TYPE_FILTER_DISCOUNT"] = type_share
            # the best case (the match comes first) measures ~0.01, the worst
            # 1.0; the model keeps the middle
            direct["LIMIT_DISCOUNT"] = LIMIT_DISCOUNT
            nbt = next((m for m in measurements if m.benchmark.name == "if_entity_nbt"), None)
            if nbt is not None and nbt.entity_count > 1:
                per_entity = nbt.us / nbt.benchmark.reps / nbt.entity_count
                direct["NBT_FILTER_COST_US"] = max(per_entity - plain * type_share, 0.0)
        for name, value in direct.items():
            setattr(costs, name, value)
        params = [p for p in params if p[0] not in direct]

        # 2. one global scale, the median measured/old ratio (robust to the
        #    few benchmarks the old guesses got wildly wrong): the prior for
        #    constants nothing measures
        ratios_seen = sorted(
            m.us / e for m, e in zip(measurements, before, strict=True) if e > 0 and m.us > 0
        )
        scale = ratios_seen[len(ratios_seen) // 2] if ratios_seen else 1.0
        prior = [_get(p) * scale for p in params]

        # 3. ridge least squares, rows weighted by their measured size (relative error)
        n = len(params)
        normal = [[0.0] * n for _ in range(n)]
        rhs = [0.0] * n
        used = [False] * n
        for m in measurements:
            constant, row = design_row(m.benchmark.executed_lines(), m.entity_count, params)
            weight = 1.0 / max(m.us, 0.05) ** 2
            target = m.us - constant
            for i, ai in enumerate(row):
                if not ai:
                    continue
                used[i] = True
                rhs[i] += weight * ai * target
                for j, aj in enumerate(row):
                    if aj:
                        normal[i][j] += weight * ai * aj
        for i in range(n):
            strength = ridge / max(prior[i], 1e-3) ** 2
            normal[i][i] += strength
            rhs[i] += strength * prior[i]
        solution = _solve(normal, rhs)

        values: dict[str, Any] = {name: copy.deepcopy(getattr(costs, name)) for name in _DICTS}
        for (name, key), value in zip(params, solution, strict=True):
            value = round(max(value, 0.0), 4)
            if key is not None:
                values[name][key] = value
            else:
                values[name] = value
        values.update({k: round(v, 5) for k, v in direct.items()})
        measured = sorted(
            f"{name}[{key}]" if key else name
            for (name, key), was_used in zip(params, used, strict=True)
            if was_used
        ) + sorted(direct)

        # 4. the before/after table
        apply(values)
        rows = []
        for m, estimate in zip(measurements, before, strict=True):
            after = model_cost(m.benchmark.executed_lines(), m.entity_count)
            rows.append(
                {
                    "benchmark": m.benchmark.name,
                    "phase": m.phase,
                    "entities": m.entity_count,
                    "reps": m.benchmark.reps,
                    "measured_us_per_rep": round(m.us / max(m.benchmark.reps, 1), 4),
                    "old_model_us_per_rep": round(estimate / max(m.benchmark.reps, 1), 4),
                    "fitted_us_per_rep": round(after / max(m.benchmark.reps, 1), 4),
                }
            )
        return Fit(values=values, scale=round(scale, 5), measured=measured, rows=rows)
    finally:
        restore(saved)


def apply(values: dict[str, Any]) -> None:
    """Set the cost model's constants (dicts are merged key by key)."""
    for name, value in values.items():
        if name not in _DICTS + _SCALARS + _RATIOS:
            continue
        if isinstance(value, dict):
            getattr(costs, name).update({k: float(v) for k, v in value.items()})
        else:
            setattr(costs, name, float(value))


def machine() -> dict[str, str]:
    java = ""
    with contextlib.suppress(OSError, subprocess.SubprocessError, IndexError):
        java = subprocess.run(
            ["java", "-version"], capture_output=True, text=True, timeout=20
        ).stderr.splitlines()[0]
    cpu = platform.processor() or ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "cpu": cpu,
        "system": f"{platform.system()} {platform.release()}",
        "java": java,
        "cores": str(os.cpu_count() or ""),
    }


def calibration_document(version: Version, result: Fit) -> dict[str, Any]:
    return {
        "kind": "datapack-emulator cost calibration",
        "minecraft": version.id,
        "measured_at": datetime.now().replace(microsecond=0).isoformat(),
        "machine": machine(),
        "scale_of_old_model": result.scale,
        "measured": result.measured,
        "values": result.values,
        "benchmarks": result.rows,
    }


def calibrate(
    version: str | Version,
    workdir: Path,
    *,
    accept_eula: bool,
    jar: Path | None = None,
    java: str = "java",
    runs: int = 2,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Run the benchmarks on a real server and fit; returns the calibration document."""
    target = versions.parse(version)
    if target < versions.parse(PER_LINE_SINCE):
        raise ValueError(
            f"calibration needs {PER_LINE_SINCE} or newer (its profiler times each line)"
        )
    reports = run_server(
        target, workdir, accept_eula=accept_eula, jar=jar, java=java, runs=runs, progress=progress
    )
    return calibration_document(target, fit(measure(reports)))


__all__ = [
    "BENCHMARKS",
    "Benchmark",
    "MARKERS",
    "Measurement",
    "apply",
    "calibrate",
    "fit",
    "measure",
    "run_server",
    "write_benchmark_pack",
]
