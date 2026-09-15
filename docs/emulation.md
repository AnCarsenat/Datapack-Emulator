# What is emulated

One `Emulator` is one pack, one version and one world. `run(ticks)` runs
`#minecraft:load` once, then each tick: schedules that are due, then
`#minecraft:tick`.

## The world

Deliberately small: no blocks, chunks, inventories or physics.

* **entities** — type, UUID, name, position, rotation, dimension, tags, NBT.
  `players` fake players (`Player1`, …) exist from the start.
* **scoreboard** — objectives with criteria, scores per holder (entities by
  name/UUID, fake players by name), enabled triggers.
* **storage** — `data … storage` compounds.
* **gamerules** — stored; `maxCommandChainLength` is enforced.

Selectors: `@s @p @a @r @e @n` with `type` (including `!` and, with a jar,
`#tags`), `tag` (including `tag=` and `!`), `name`, `scores`, `distance`,
`limit`/`c`, `sort`.

## Commands

**Emulated with state:**

| command | notes |
| --- | --- |
| `execute` | `as at positioned rotated in if unless store run summon`; `anchored align facing on` pass through |
| `if` / `unless` conditions | `score` (matches and comparisons), `entity`, `data` (entity, storage), `dimension`, `loaded`, `function`; others warn and fail |
| `function` | tags, `$` macros with inline SNBT or `with storage|entity [path]` |
| `schedule` | `function <id> <time> [append|replace]`, `clear`; `t`/`s`/`d` units |
| `return` | value, `run`, `fail` |
| `scoreboard` | objectives add/remove; players set/add/remove/reset/get/enable/operation |
| `trigger` | `set`/`add`, enablement and objective type checked |
| `tag` | add/remove |
| `summon`, `kill`, `tp`/`teleport` | |
| `data` | get/merge/remove/modify on entities and storage (set, merge, append, prepend) |
| `say me msg tell w tellraw title teammsg` | logged as `game` output |
| `gamerule` | |

**Checked, no state change:** `setblock`, `give`, `clear`, `effect`,
`particle` validate their id when a client jar is loaded
([vanilla-assets.md](vanilla-assets.md)).

**Dispatched and costed only:** every other command vanilla has in that
version (`fill`, `item`, `loot`, `playsound`, `bossbar`, …).

**Not in that version:** answered like the game, with the translated
*Unknown or incomplete command* and a `<--[HERE]` marker, plus an emulator note
saying which release added it.

Limits: `maxCommandChainLength` 65 536 commands per tick, as in vanilla. The
emulator also stops a recursion 1024 function calls deep so it cannot exhaust
the Python stack; vanilla has no such limit, and it is reported as an
emulator note, not a game error.

Tick order follows vanilla: `#minecraft:tick` runs, then the game time
advances and due schedules run, so `schedule … 1t` from a tick function runs
later in the same server tick.

## Output

Every message is a `LogRecord` on the `OutputBus` with a source, a level, the
tick, the function and line, the command, the version and — when it mirrors a
vanilla string — the translation key.

| source | who is talking | examples |
| --- | --- | --- |
| `app` | this program | loaded a pack, wrote a report, which overlays are active |
| `emulator` | the emulation engine | command not in this version, unemulated condition, tick over budget, folders this version ignores |
| `game` | Minecraft | chat (`[Player1] hello`), command feedback (debug level), red errors |

Game strings are vanilla's own (`runtime/messages.py`, or the loaded jar's
`en_us.json`): `No entity was found`, `Unknown scoreboard objective 'x'`,
`Target does not have this tag`, `Can't get value of hat for Player1; none is
set`, `Missing argument name to function test:helper`, …

Identical messages are capped at five per tick; the sixth says further copies
were suppressed.

## Call graph

`CallGraph.from_pack(view)` records edges for `function` (`call`, or `macro`
when `with`/`$` is involved), `schedule function` (`schedule`),
`execute if|unless function` (`condition`) and function tags (`tag`).

| method | returns |
| --- | --- |
| `cycles()` | every recursion; non-empty means not a DAG |
| `topological_order()` | Kahn order, cyclic nodes last |
| `depths()` | longest path from a root |
| `unreachable()` | functions nothing reachable from load/tick calls |
| `missing()` | called but not defined |
| `layout()` | layered positions with barycentre ordering |
| `to_dot()` / `write_dot()` | Graphviz |
