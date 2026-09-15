# What is emulated

One `Emulator` is one version and one world, running one datapack or a
`DatapackSet` — several packs enabled together, like a world's datapack list:
in load order, a resource with the same id comes from the last pack that has
it, and tags add their values across packs unless a later tag has
`"replace": true` (overlays still apply inside each pack). `run(ticks)` runs
`#minecraft:load` once, then each tick `#minecraft:tick` followed by the
schedules that became due (see *Tick order* below). On 1.16.1–1.19.2 the
first tick runs `#minecraft:tick` before `#minecraft:load`, as those servers
did; from 1.19.3 load comes first.

## The world

Deliberately small: no blocks, chunks or physics.

* **entities** — type, UUID, name, position, rotation, dimension, tags and
  the rest of their NBT. `Entity.data()` is what `data get entity` shows:
  `Pos`, `Motion`, `Rotation`, `UUID` (int array), the common defaults
  (`Air`, `Fire`, `OnGround`, …), `id`, `Tags`, player fields for players
  (`Health`, `foodLevel`, …), the inventory (below) and everything that
  was summoned, merged, modified or stored onto the entity. Writing `Pos`,
  `Rotation` or `Tags` moves, turns or retags the entity; the UUID never
  changes. `players` fake players (`Player1`, …) exist from the start;
  killed players stay (they respawn), other killed entities are removed.
* **scoreboard** — objectives with criteria, display names and display
  slots, scores per holder (players by name, other entities by UUID, fake
  players by name), enabled triggers, and the last 64 changes of every score
  with their game time (the world dock's grid shows them). Scores are 32-bit
  and wrap like Java ints; `*` means every holder with any score.
* **inventories** (`runtime/inventory.py`) — item stacks with their id,
  count, components (1.20.5+) or `tag` (before) and stack size (64, 16 for
  pearls, snowballs, signs…, 1 for tools, armour, potions…, or the
  `max_stack_size` component). Players have 36 container slots (hotbar 0–8,
  inventory 9–35; the selected slot is 0), armour, offhand and an ender
  chest; other living entities (armour stands included) have hands, armour,
  body armour and a saddle; markers, items, projectiles, displays, boats,
  minecarts and the like have no equipment slots.
  `data get entity` shows them in the version's own format:

  | version | items | where |
  | --- | --- | --- |
  | before 1.20.5 | `{id, Count, tag}` | players: `Inventory` (armour in slots 100–103, offhand -106), `EnderItems`, `SelectedItem`; mobs: `HandItems`, `ArmorItems` |
  | 1.20.5 | `{id, count, components}` | same places |
  | 1.21.5 | same | armour, hands, body and saddle in `equipment` |

  `data merge`/`modify` on a mob loads its items back; killed players drop
  their items as `minecraft:item` entities unless `keepInventory`
  (`keep_inventory` from 1.21.11) is true.
* **storage** — `data … storage` compounds.
* **gamerules** — stored; `maxCommandChainLength` is enforced.

Selectors: `@s @p @a @r @e @n` with `type` (including `!` and, with a jar,
`#tags`), `tag` (including `tag=` and `!`), `name`, `scores`, `distance`,
`x`/`y`/`z` (move the origin), `dx`/`dy`/`dz` (box from the origin),
`nbt` (vanilla subset matching against `Entity.data()`), `limit`/`c`, `sort`.
`@s[...]` applies its arguments to the executor.

Not evaluated: `team`, `gamemode`, `level`, `advancements`, `predicate`,
`x_rotation`, `y_rotation` match every entity, and `nbt` keys the emulator does
not store (effects, attributes, …) fail the check. Both produce one emulator
note per run.

NBT paths support compound keys, quoted keys (`"minecraft:custom_data"`),
list indexes (`Pos[0]`, `list[-1]`) and list filters (`Inventory[{Slot:103b}]`:
reading takes the first match, setting and `data remove` reach every match, and
setting through a filter with no match adds the element).

## Commands

**Emulated with state:**

| command | notes |
| --- | --- |
| `execute` | `as at positioned rotated in if unless store run summon`; `anchored align facing` pass through; `on` ends the branch (relations are not modelled). `store` binds its target where it appears in the chain; `run return` leaves the function on the first branch that reaches it |
| `if` / `unless` conditions | `score` (matches and comparisons), `entity`, `data` (entity, storage), `items entity <targets> <slots> <predicate>` (slot wildcards like `container.*`; the count is the number of matching items), `dimension`, `loaded`, `function` (passes only when a function returns a non-zero value); others are noted and fail. With no `run`, a trailing `if entity` reports how many entities matched |
| `function` | tags, `$` macros with inline SNBT or `with storage|entity [path]` |
| `schedule` | `function <id> <time> [append|replace]`, `clear`; `t`/`s`/`d` units |
| `return` | value, `run`, `fail` |
| `scoreboard` | every subcommand: objectives `list`, `add` (criteria checked, display name), `remove`, `setdisplay`, `modify`; players `list`, `get`, `set`, `add`, `remove`, `reset`, `enable` (trigger objectives only), `operation`, `display`. Vanilla feedback and errors: read-only criteria (`health`, `food`, …), invalid integers, negative `add` amounts, one holder for `get`. `operation` combines every target with every source, creates missing scores as 0, and divides with `floorDiv` / `floorMod` (`/=` and `%=` by zero fail) |
| `trigger` | only a player can trigger ("A player is required to run this command here" otherwise); the objective must be a trigger and enabled for that player, and using it locks it again until the next `scoreboard players enable`; `set`/`add` with vanilla feedback |
| `tag` | add/remove |
| `summon` | keeps the NBT (a `UUID` is used, duplicates refused); the position argument wins over `Pos` |
| `kill`, `tp`/`teleport` | `tp <entity>`, `tp <x y z>`, `tp <targets> <entity|x y z> [<yaw> <pitch>]` |
| `give` | players only; stacks onto matching stacks (selected slot, offhand, container order), then empty slots; what does not fit drops as item entities; at most 100 stacks |
| `clear` | `[targets] [item predicate] [maxCount]`: removes matching items (not the ender chest); `maxCount 0` only counts. Predicates: an id, `*`, `#tag` (from the client jar or the pack), `[component=value]`, `[component]`, `!` negation, `|` alternatives, and the old `{nbt}` subset; `~` sub-predicates and `count` are noted and not checked. Items are taken in slot order; a negative count is rejected |
| `item` | counts must be 1–99 (1–64 before 1.20.5) and fit the item's stack; `replace entity <targets> <slot> with <item> [count]`, `replace entity … from entity <source> <slot> [modifier]`, `modify entity <targets> <slot> <modifier>` (pack item modifiers: `set_count`, `set_components`, `set_nbt`); `block` forms are noted (no blocks) |
| `replaceitem` | `entity <targets> <slot> <item> [count]` (before 1.17) |
| `enchant` | adds the enchantment to the held item in the version's format (`Enchantments`, `enchantments.levels`, `enchantments`); which items accept it is not checked |
| `loot` | `give` (what does not fit is lost), `spawn` and `replace entity <targets> <slot> [count]` (numbered slots from that one) with a `loot <table>` source, from the pack or the client jar: rolls (uniform ranges inclusive), weights, nested tables, `alternatives`/`group`/`sequence`, `set_count`, `set_components`, `set_nbt`, stacks split to their limit; returns the number of stacks. Conditions count as passing and other functions are skipped (noted). `fish`, `kill`, `mine` and `insert` are noted |
| `data` | on one entity or a storage: `get [path] [scale]` (prints the SNBT like vanilla; floored, saturated to int), deep `merge`, `remove`, `modify` with set / merge / append / prepend / insert from `value`, `from` or `string` (sliced). Player data can be read but not modified ("Unable to modify player data") |
| `say me msg tell w tellraw title teammsg` | logged as `game` output, one record per player who reads it: `[Player1] hi` / `* Player1 waves` for everyone (say, me), `to Player2: text` (tellraw), `to Player1 (actionbar): text` (title), `Server whispers to Player2: text` (msg). Each record names its reader (`recipient`). Text components in JSON or (1.21.5+) SNBT, with `score`, `selector`, `nbt` (storage and entity) and `translate` (with its `with` arguments, from the client jar's language file, else the fallback) resolved per reader |
| `gamerule` | |

**Checked, no state change:** `setblock`, `effect`, `particle` validate their
id when a client jar is loaded ([vanilla-assets.md](vanilla-assets.md)); `give`,
`clear`, `item`, `summon`, `enchant` check theirs too.

**Dispatched and costed only:** every other command vanilla has in that
version (`fill`, `playsound`, `bossbar`, …).

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
| `game` | Minecraft | chat (`[Player1] hello`, `to Player2: …`), command feedback, red errors |

Game strings are vanilla's own (`runtime/messages.py`, or the loaded jar's
`en_us.json`): `No entity was found`, `Unknown scoreboard objective 'x'`,
`Target does not have this tag`, `Can't get value of hat for Player1; none is
set`, `Missing argument name to function test:helper`, …

Command feedback (`Set [kills] for Player1 to 3`) is shown to whoever typed
the command, so a typed or test command logs it at `info`; functions send
their feedback nowhere, so there it is `debug`.

A command that fails **inside a function** fails silently in vanilla: nobody
sees an error and the function carries on with its next line. Those failures
are recorded at `debug` level (set the logs dock to *debug* to see them, shown
in a muted red); only commands run directly — typed in the logs dock's
command line, or run by a test — produce
`error`-level game records. Every failure record has `failure=True` either way.

Limitations of the emulator itself (a condition or `data … block` it cannot
evaluate, `execute on`, selector arguments it cannot check) are noted once per
run at `info` level, so they do not mark a working pack as having warnings.
So is every command that runs without changing the emulated world, with the
reason (`'fill' runs, but blocks are not modelled`, `'effect' runs, but status
effects are not modelled (the effect id is still checked)`, …); purely
cosmetic ones (`particle`, `playsound`, `stopsound`) are not noted. The
*analyze this line* action shows the same information for any line.

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
