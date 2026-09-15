# Execution-time estimates

**These are a model, not measurements.** Mojang publishes no per-command
timings, and community benchmark packs only give relative numbers valid for
one machine and one world. The profiler's milliseconds are useful to compare
functions, versions and edits of the same pack — not as wall-clock truth.

## The model

All values live in `src/datapack_emulator/emulator/costs.py`, in microseconds, and are read at
call time, so patching them at runtime works.

A command's cost is the sum of:

| part | table | e.g. |
| --- | --- | --- |
| base cost of the command | `COMMAND_COST_US` | `scoreboard` 1.5, `summon` 120, `data` 180 |
| fallback for unlisted commands | `DEFAULT_COMMAND_COST_US` | 5 |
| each `execute` subcommand | `SUBCOMMAND_COST_US` | `as` 1, `on` 8 |
| each `if`/`unless` condition | `CONDITION_COST_US` | `score` 2, `data` 180 |
| each selector | see below | |
| macro line | `MACRO_CALL_OVERHEAD_US` | 30 |
| `fill`/`clone`/`fillbiome` volume | `VOLUME_COST_US_PER_BLOCK` | 0.8 per block, capped at 32 768 blocks |
| the `run` child | recursive | |

A selector costs `SELECTOR_BASE_COST_US[kind]` plus a scan: `@e`/`@n` scan
every entity, player selectors about a tenth; the scan costs
`SELECTOR_PER_ENTITY_COST_US` per entity, times `TYPE_FILTER_DISCOUNT` with
`type=`, times `LIMIT_DISCOUNT` with `limit`; `nbt=` adds
`NBT_FILTER_COST_US` per quarter of the scanned entities.

A function's *self* time is its own lines; *total* includes everything it
called. A tick over `TICK_BUDGET_US` (50 000 µs) is reported by the emulator.

## Where the magnitudes come from

Chosen to match the qualitative guidance in:

* [Tutorial: Optimizing a data pack](https://minecraft.wiki/w/Tutorial:Optimizing_a_data_pack)
  — NBT access is "really expensive", `@e` scans are expensive, `type=` is a
  cheap filter, macro calls have an overhead
* [datapack.wiki: How to measure performance](https://datapack.wiki/guide/performance/how-to-measure)
* [Function-Benchmark-Pack](https://github.com/Silicon42/Function-Benchmark-Pack),
  [mch](https://github.com/mcenv/mch)

## Calibrating

Benchmark two commands on your reference machine with one of the packs above,
then scale the corresponding entries so their ratio matches. Keep the change
in `costs.py` with a comment saying what it was measured against.
