# Execution costs: measured, and how to measure your own

The profiler's milliseconds come from a cost model: every command the
emulator runs is charged a cost, and the costs add up per line, per function
and per tick. The model's constants are **measured on a real Minecraft
server**, not guessed. This page says how, how far to trust them, and how to
get real timings for your own pack instead.

## Where the numbers come from

A vanilla server profiles itself. `/perf start` records ten seconds and
writes `debug/profiling/<date>-<world>-<version>.zip`; its
`server/profiling.txt` has a section for every function the server ran
(`function ns:tick`) and, from 1.20.3, for every command line inside it, with
how often it ran. No mod is needed (spark profiles Java methods, which do not
map back to datapack functions; the vanilla profiler does).

`datapack-emulator-cli calibrate` uses that:

1. it writes a benchmark datapack in which every `#minecraft:tick` function
   repeats one kind of command (2000 × `scoreboard players add`, 500 ×
   `data get entity @s Pos`, 200 × an `@e` scan, 50 × a 64-block `fill`, …;
   the list is `BENCHMARKS` in `src/datapack_emulator/emulator/calibration.py`);
2. it starts a vanilla dedicated server on it (offline, flat world, no
   players), warms it up, and takes three `/perf` reports with one armor stand
   in the world and three more with 1000 markers added, so the cost of
   scanning entities can be separated from everything else;
3. it fits the constants. The model is linear in them, so the emulator's own
   `Command.estimate_cost` gives the equations — for each constant, how much
   of it each benchmark line pays — and a weighted least-squares solve
   (relative error, a light ridge term) finds the values. Constants no
   benchmark exercises (commands that need a real player, admin commands)
   stay at the earlier guesses times the median measured/guessed ratio.

`tools/apply_calibration.py` writes a result into `costs.py` as the new
defaults; the block between the `calibration:` markers there says where and
when they were measured. The run behind the built-in numbers is
[`calibration-26.2.json`](calibration-26.2.json): Minecraft 26.2, an Intel
Core 5 120U laptop, OpenJDK 27, every benchmark with its measured, old-model
and fitted cost.

What the measurements overturned:

| | old guess | measured (26.2) |
| --- | --- | --- |
| `scoreboard players add` | 1.5 µs | ~0.4 µs |
| `data` on command storage | 180 µs | ~0.4–0.8 µs |
| `data` on an entity (`data get entity @s Pos`) | 180 µs | ~3.4 µs |
| `@e` scan, per entity | 0.35 µs | ~0.01 µs |
| `nbt=` in a selector, per entity checked | 37 µs | ~1.4 µs |
| `type=` in a selector | keeps 25 % of the scan | keeps all of it (every entity is still visited) |
| `fill`, per block | 0.8 µs | ~2.3 µs |
| `summon` | 120 µs | ~3 µs |

## The model

A line's cost is the sum of (calibrating found and fixed one model bug on
the way: an `execute`'s selectors used to be charged twice):

| part | constant(s) |
| --- | --- |
| the command | `COMMAND_COST_US[name]` (`DEFAULT_COMMAND_COST_US` if unlisted) |
| `data` on an `entity` or `block` (also `execute if data`) | `+ DATA_ENTITY_EXTRA_US` |
| each `execute` subcommand | `SUBCOMMAND_COST_US[name]` |
| each `if`/`unless` condition | `CONDITION_COST_US[name]` |
| each selector | `SELECTOR_BASE_COST_US[kind]` + entities scanned × `SELECTOR_PER_ENTITY_COST_US` (× `TYPE_FILTER_DISCOUNT` with `type=`, × `LIMIT_DISCOUNT` with `limit=`) + entities scanned × `NBT_FILTER_COST_US` with `nbt=` |
| a macro (`$`) line | `+ MACRO_CALL_OVERHEAD_US` |
| `fill` / `clone` / `fillbiome` | `+ volume × VOLUME_COST_US_PER_BLOCK` |
| the `run` child | its own cost |

`@e`/`@n` scan every entity in the world, `@a`/`@p`/`@r` the players, `@s`
nothing. A function's *self* time is its own lines; *total* includes what it
called. A tick over 50 ms (`TICK_BUDGET_US`) is reported as lag.

## How far to trust it

* **Against the benchmarks it was fitted on, the median error is about 9 %**
  (90 % of them within ~40 %). Misses that remain are structural: two `data`
  commands on storage share one constant (`get` is cheaper than `merge`),
  `kill` costs the same however many it kills, and `limit=` is the average of
  its best and worst case (below).
* **The benchmarks measure warm costs.** They repeat one line hundreds of
  times, so the server's caches already know it. A line run once per tick
  among many different lines is colder and can cost 10–50× more: a one-line
  tick function measured 27 µs on the same server, estimated 0.7 µs. So read
  the numbers as a **lower bound and a ranking**. They are right about what
  is dear (entity NBT, `nbt=` scans, `@e` with many entities, `fill` volume)
  and what is cheap (scoreboards, storage), and about how a change moves the
  cost. They are not a prediction of a real server's tick time.
* `limit=` stops at the first match. Measured from ×0.01 (the match comes
  first) to ×1.0 (it comes last); the model takes ×0.5.
* The numbers are one machine and one version. Another CPU or JVM scales
  them; another version may change some.

## Real timings for your pack

Take a report on a server running it — singleplayer works too (open to LAN
is not needed): `/perf start`, wait ten seconds, find the zip in
`debug/profiling/` (in the world's folder for singleplayer, the server's
folder otherwise). Then:

```sh
datapack-emulator-cli server-profile debug/profiling/2026-11-06_22_02_13-world-26_2.zip
datapack-emulator-cli server-profile report.zip --pack ./my_pack --ticks 200 --players 1
```

The first prints the measured ms per tick of every function and the cost of
each of its lines; the second also emulates the pack and puts the estimate
and the ratio (measured / estimated) beside each function. `--json` for
scripts; `agent.server_profile(report, packs)` from Python.

## Calibrating on your machine

```sh
datapack-emulator-cli calibrate --version 26.2 --accept-eula            # ~2 minutes
datapack-emulator-cli calibrate --version 26.2 --accept-eula --install  # and use it from now on
```

It downloads that version's server jar from Mojang (or takes
`--server-jar`), needs Java (`--java`), runs in the cache folder
(`--workdir`), and writes `generated/calibration-<version>.json` (`--out`).
Running a Minecraft server means accepting the
[Minecraft EULA](https://aka.ms/MinecraftEULA); `--accept-eula` says you do.
Versions from 1.20.3 (the first whose profiler times each line).

`--install` copies the result to `costs.json` in the per-user data folder
(`~/.local/share/datapack-emulator`, `~/Library/Application Support/…`,
`%APPDATA%\datapack-emulator`); every run applies it on top of the built-in
numbers. `DATAPACK_EMULATOR_COSTS=<file>` names another one. To make a run
the new defaults for everyone, `python tools/apply_calibration.py <file>`
and commit `costs.py` with the JSON next to this page.
