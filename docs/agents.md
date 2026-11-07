# For AI agents and scripts

The emulator runs a Minecraft datapack without the game: it loads the pack the
way a server of a chosen version would, runs `#minecraft:load` and
`#minecraft:tick`, and reports what happened. For an agent writing or fixing a
datapack, that closes the loop: **write → run → read the result → fix**, in
seconds, with no Minecraft install.

There are three ways in, returning the same JSON (one exception: `check --json`, below):

| way in | use it when |
| --- | --- |
| the Python API, `datapack_emulator.agent` | you run Python in the same process (an MCP server, a notebook, a script) |
| the CLI with `--json` | you run shell commands (`run`, `matrix`, `test`, `check` take `--json`) |
| an MCP server that wraps it ([minecode-mcp](https://github.com/AnCarsenat/minecode-mcp)) | you are a model talking to tools |

```sh
pip install datapack-emulator          # the core: pure standard library, no Qt
```

## The loop

```text
1. check   — static: what a version refuses to load (cheap, run it first)
2. run     — dynamic: tick the pack, read errors, chat, the world, the call graph
3. test    — assert: "after tick 5, score #game timer = 5"
4. matrix  — breadth: the same pack in every version it claims to support
```

Always pass the **version** the pack targets. `pack.mcmeta` is read when you
do not, but saying it removes a guess. Versions are ids (`"1.21.4"`,
`"26.2"`), `"latest"`, or a line (`"1.21"` is exactly 1.21; `"26"` is the
newest 26.x).

## Python API

```python
from datapack_emulator import agent

agent.check("my_pack", version="1.21.4")
agent.run("my_pack", version="1.21.4", ticks=40,
          commands=["scoreboard players get #game timer", "data get storage my:state"])
agent.test("my_pack", ["5:"], checks=["score #game timer = 5"], version="1.21.4")
agent.matrix("my_pack", declared=True, boundaries=True)
agent.explain(None, "execute if function my:ok run say yes", version="1.20.2")
agent.versions()
```

`packs` is one folder (the one holding `pack.mcmeta`), a zip, or a list of
them in load order. Every function returns a `dict` that `json.dumps` accepts
unchanged. Bad input — a missing folder, an unknown version, a malformed
check — raises `agent.AgentError` with a message to show to whoever asked.

### What every result has

| key | meaning |
| --- | --- |
| `ok` | `true` when nothing is wrong (exact meaning per function below) |
| `summary` | one line: read this first, and often it is enough |
| `version` / `versions` | the version ids actually emulated, after resolving |
| `truncated`, `*_total` | a list was capped; the full count is beside it |

### `check(packs, version=None, versions=None, *, declared=False, boundaries=False, severity="info", vanilla="auto", download=False)`

Static problems, nothing runs. `ok` is false when any version has a fatal
or error-level problem or a pack failed to load.

**`load_blockers` comes first**: the `fatal` problems, which stop the world
or the pack from loading at all — a world-registry file (worldgen,
dimension, dimension type, enchantment, …) that does not parse, lacks a field
the version requires, or names a kind the version does not have; or an
unreadable `pack.mcmeta`. A server with one of those refuses to start the
world for *every* pack ("Failed to load datapacks, can't proceed with server
load"), so fix them before anything else. Without a client jar only the
parse failures are found; with one (`vanilla="auto"` finds an installed
jar) the missing fields and removed kinds are too.

```json
{
  "ok": false,
  "summary": "2 error(s) — 1.21.4: 2E/0W",
  "load_blockers": [],
  "versions": ["1.21.4"],
  "load_errors": [],
  "results": [{
    "version": "1.21.4", "client_jar": null,
    "counts": {"error": 2, "warning": 0, "info": 1},
    "problems": [{"severity": "error", "code": "function-not-loaded",
                  "resource": "my:tick", "line": 3,
                  "message": "Unknown or incomplete command ...", "path": "..."}],
    "truncated": false
  }]
}
```

Severities, dearest first: `fatal`, `error`, `warning`, `info`. Problem codes
(filter on `code`): `world-registry`, `pack`, `ignored-folder`,
`function-not-loaded`, `tag-not-loaded`, `unknown-id`, `text-component`,
`json`, `missing-function`, … — the full list is
`datapack_emulator.emulator.analysis.problems.CODES`.

`ignored-folder` is the 1.21 folder rename: from 1.21 a server reads
`function/`, `tags/function/`, `loot_table/` … and ignores `functions/`,
`tags/functions/`, `loot_tables/` …; before 1.21 the other way round. It is
an error when the version reads *no* resource of that kind (the mistake), a
note when the pack also ships the other spelling (deliberate dual-version
packs), a warning outside the versions `pack.mcmeta` declares.

### `run(packs, version=None, *, ticks=20, players=1, seed=0, commands=(), level="info", max_records=200, world=True, entities=False, vanilla="auto", download=False)`

Ticks the pack, then runs each of `commands` as if typed in the server
console — that is how you **ask the world a question** afterwards.

`ok` is false when the world would not load at all (`load_blockers`, as for
`check`; the summary then starts with `WILL NOT LOAD`), when a tick logged an
error, **a function or tag failed to load**, a called function does not exist, or **a whole kind of resource sits
in folders the version does not read** (`ignored_folders`). The real game only
*warns* when a function fails to parse and says nothing at all about folders
it does not read; those are the two usual causes of "my pack does nothing",
and `run` treats both as failures.

```json
{
  "ok": true,
  "summary": "1.21.4: 5 tick(s), 0 error(s), 0 function(s) not loaded, 0 warning(s), 1 chat line(s)",
  "version": "1.21.4", "pack_format": "61", "client_jar": null, "ticks": 5,
  "load_errors": [], "failed_functions": [], "failed_tags": [], "ignored_folders": [],
  "chat": ["to Player1: loaded"],
  "records": [{"tick": 0, "source": "game", "level": "info",
               "message": "to Player1: loaded", "at": "my:load:3"}],
  "records_total": 1, "truncated": false,
  "commands": [{"command": "scoreboard players get #g t", "success": true, "value": 5,
                "output": [{"message": "#g has 5 [t]", "...": "..."}]}],
  "call_graph": {"functions": 3, "missing_functions": [], "never_called": [], "recursion": []},
  "profile": {"estimated": true, "ms_per_tick": 0.01, "worst_tick_ms": 0.02,
              "functions": [{"function": "my:tick", "calls": 5, "...": "..."}],
              "dearest_lines": [{"at": "my:tick:1", "self_ms": 0.01, "runs": 5}]},
  "world": {"tick": 5, "objectives": {"t": "dummy"}, "scores": {"#g": {"t": 5}},
            "storage": {}, "entity_counts": {"minecraft:player": 1},
            "blocks_placed": 0, "tags": {}}
}
```

Records have a `source`, and the difference matters:

| source | who says it | trust it as |
| --- | --- | --- |
| `game` | what Minecraft itself would print: chat, feedback, red error text | what the player would see |
| `emulator` | the emulator's own notes: a command it does not model, an empty tag | a limit of the emulation, not a bug in the pack |
| `app` | loading, reports | context |

Pass `entities=True` for every entity's full NBT and the placed blocks (large).
`profile` numbers come from a cost model **measured on a real 26.2 server**
(see [costs.md](costs.md)), but measured in warm loops: read them as a
lower bound and a ranking — right about which function is dearest and how a
change moves it — not as a real tick time. For real numbers, use
`server_profile` below.

### `test(packs, tests, version=None, versions=None, *, expect="", expect_value="", checks=(), ticks=None, ...)`

Runs tests in one fresh world per version; the world ticks until the last
test ran. `ok` is true when every test passed (and there was at least one).

A test is a string `"[TICK:]COMMAND"` — run after that tick's functions,
tick 0 is the first — or a dict:

```python
{"command": "function my:reset", "at_tick": 3,
 "expect": "text the output must contain",
 "expect_value": "1..",            # the command's result: 5, 1.., ..3, 1..4
 "checks": ["score #g t = 0"]}     # what must hold afterwards
```

The command may be empty (`"5:"`) when only the checks matter. Check lines
(spaces around `=`/`!=` are required; checks run as the server at 0 0 0):

```text
score HOLDER OBJECTIVE = RANGE    score #global counter = 3     score @p points != 0
storage ID [PATH] = SNBT          storage my:mem phase = 2b
entity SELECTOR [PATH] = SNBT     entity @e[type=pig,limit=1] Tags = ["a"]
SELECTOR = RANGE                  @e[type=zombie,tag=boss] = 1   (how many match)
block X Y Z = BLOCK               block 0 64 0 = minecraft:chest[facing=north]
if CONDITION / unless CONDITION   if entity @a[tag=winner]
```

Each result row: `version`, `status` (`pass`/`fail`/`skip`), `at_tick`,
`command`, `reason`, `value`, and on failure the first `output` records.

### `matrix(packs, versions=None, *, start=None, end=None, declared=False, boundaries=False, ticks=20, tests=(), ...)`

One row per version: `status` (`ok`, `warnings`, `errors`, `tests failed`,
`unsupported` = pack.mcmeta does not claim it), `unknown_commands`,
`missing_features`, `failed_functions`, `overlays` (active ones), errors and
warnings with their records. Without a selection: the range `pack.mcmeta`
declares. `boundaries=True` keeps the first release of each pack format —
usually all a compatibility check needs.

### `server_profile(report, packs=None, version=None, *, ticks=200, players=1)`

Real timings from a server's `/perf` report (the zip a server writes to
`debug/profiling/` after `/perf start`). `functions[]`: each function's
measured `ms_per_tick`, `calls_per_tick` and its dearest `lines[]`
(`command`, `runs_per_tick`, `us_each`). `mspt` is the server's whole tick;
`ok` is false when it was over 50 ms. With `packs`, `compared.functions[]`
puts the emulator's estimate and the `ratio` beside each one.

### `explain(packs, command, version=None)`

Parses one line without running it: does the command exist in this version,
which features does it use (`execute:if`, `condition:function`, …) and in
which version did the missing ones arrive.

## CLI with `--json`

`run`, `matrix`, `test` and `check` take `--json`. Standard output then holds
**one JSON document and nothing else**; progress, tables and notes go to
standard error. For `run`, `matrix` and `test` the exit code is in the
document too (`exit_code`).

```sh
datapack-emulator-cli --quiet check  my_pack --version 1.21.4 --json
datapack-emulator-cli --quiet run    my_pack --version 1.21.4 --ticks 40 --json --max-records 50
datapack-emulator-cli --quiet test   my_pack --version 1.21.4 --test "5:" --check "score #g t = 5" --json
datapack-emulator-cli --quiet matrix my_pack --declared --boundaries --json
```

Exit codes: `0` ok; `1` a test failed, `check` found an error (or a warning,
with `--strict`), or `matrix --strict` saw errors or failed tests; `2` usage
error (bad path, version or option, or `test` with nothing to run); `3`
internal error (a bug — report it); `130` interrupted.

`run --json` reports the same shape as `agent.run` minus `commands` (use the
API, or `shell`, to type commands into the world); its `summary` is shorter.
Its `ok` can be false while `exit_code` is `0`: the exit code keeps its old
meaning (tests and usage), `ok` adds the silent failures described above.

`check --json` predates the agent API and keeps its own shape, so existing
scripts do not break: one object `{version, client_jar, schema, counts,
problems}` for one version, a **list** of them when versions were given as a
list (`--versions`, `--declared`, …). There is no `ok`/`summary`: `ok` is
`counts.error == 0` (or the exit code). `agent.check` wraps the same data as
`{ok, summary, pack, versions, load_errors, results: [...]}`. `run` still writes its HTML
report (`--html`, default `generated/index.html`).

## Recipes

**"My pack does nothing."** Run, then read three fields:

```python
r = agent.run(pack, version)
r["ignored_folders"]                        # functions/ on 1.21+, function/ before: never read
r["failed_functions"], r["failed_tags"]     # parse failures: the game skips them silently
r["call_graph"]["never_called"]             # not in #minecraft:tick/load, nobody calls them
```

then `check` for the reason on each line.

**"Does my counter / state machine work?"**

```python
agent.test(pack, [
    "0:",                                   # after load + first tick
    {"command": "", "at_tick": 19, "checks": ["score #timer t = 20"]},
    {"command": "function my:reset", "at_tick": 20, "checks": ["score #timer t = 0"]},
], version="1.21.4")
```

**"Does it still work on 1.20.4 … 1.21.4?"**

```python
agent.matrix(pack, start="1.20.4", end="1.21.4", boundaries=True, tests=["5:"])
```

**"Is this line valid on the server's version?"** `explain` says which
version added what; `check` on the whole pack says what fails to parse.

## What is not emulated

The world is deliberately small: no chunks, physics, block updates, mob AI,
movement, redstone, or gameplay-triggered advancements. Commands that need
those either act on the emulator's model (scores, storage, NBT, inventories,
placed blocks, entities by tag and type) or are reported once as
`emulator`-source notes. A pack that passes here can still fail on a real
server for those reasons — and one that fails here with a `game` error fails
there too. See [emulation.md](emulation.md) for the full list.

## Client jars

With a version's `client.jar` (Mojang's, read in place, never redistributed),
ids, tags, loot tables and JSON field names are checked against the real base
game and messages use the real wording. `vanilla="auto"` (the default) uses
an installed jar when there is one; `download=True` fetches it from Mojang
(one-off, ~30 MB, cached); `vanilla=None` uses none — fastest, and enough for
logic. See [vanilla-assets.md](vanilla-assets.md).
