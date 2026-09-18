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

Deliberately small: no chunks, physics or block updates.

* **entities** — type, UUID, name, position, rotation, dimension, tags and
  the rest of their NBT. `Entity.data()` is what `data get entity` shows:
  `Pos`, `Motion`, `Rotation`, `UUID` (int array), the common defaults
  (`Air`, `Fire`, `OnGround`, …), `id`, `Tags`, player fields for players
  (`Health`, `foodLevel`, …), the inventory (below) and everything that
  was summoned, merged, modified or stored onto the entity. Writing `Pos`,
  `Rotation` or `Tags` moves, turns or retags the entity; the UUID never
  changes. `players` fake players (`Player1`, …) exist from the start;
  killed players stay (they respawn), other killed entities are removed.
* **living entities** (`runtime/living.py`) — players, mobs and armor stands
  have health (their maximum health when they appear), attributes with base
  values, modifiers and vanilla's value ranges (only the attributes their
  kind has: pigs have no attack damage), and status effects (a weaker, longer
  effect waits hidden under a stronger one), all in `data get entity` in the
  version's format (`Attributes`/`attributes`, `ActiveEffects` with numeric ids
  before 1.20.2, `active_effects` after, `generic.` attribute ids before
  1.21.2, modifier UUIDs before 1.21). Mobs also report `CanPickUpLoot`,
  `PersistenceRequired` and `LeftHanded`; armor stands their flags and pose. Base values and maximum health by type are a table
  in the code, not read from the game. Effects count down each tick;
  `instant_health`/`instant_damage` heal or hurt on the entity's next tick
  (reversed for `#inverted_healing_and_harm`, with Java's int arithmetic);
  the others have no other effect. The undead ignore poison and
  regeneration, spiders poison, withers wither. A hit makes an entity
  invulnerable for 10 ticks except to harder hits (which only deal the
  difference); armor stands only break from player attacks. Health reaching
  0 kills the entity (players drop their items and respawn with full
  health); `health` criteria follow. No regeneration, hunger, AI or
  movement.
* **riding** — vehicles and passengers (`Passengers` when summoning, and in
  `data get entity`); killing or teleporting an entity gets it off its
  vehicle, teleporting a vehicle moves its riders.
* **item entities** — age each tick and despawn at 6000 (`Age: -32768`
  never does), count their `PickupDelay` down (32767 never), every 40 ticks
  merge with items of the same kind and owner next to them (the bigger stack
  takes from the smaller), and are picked up by players standing within
  reach (not in spectator mode, and only their `Owner` when they have one).
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
* **blocks** (`runtime/blocks.py`) — only the positions a command set are
  stored, per dimension; everything else is air. A block keeps its id, the
  block-state properties it was given and, for blocks with a block entity,
  its NBT, under its block entity type (`oak_sign` → `minecraft:sign`).
  Containers (chests, barrels, shulker boxes, hoppers, dispensers,
  furnaces, brewing stands, crafters, chiseled bookshelves, jukeboxes,
  decorated pots, shelves) keep their items as slots `container.0…`, shown in
  the version's item format — `Items` (left out for an empty shulker box),
  `RecordItem` for jukeboxes, `item` for decorated pots. Campfires and
  lecterns have block entity data but are not containers. This is a model:
  * default block states are not known — a block has only the properties
    it was placed with, so `if block … stairs[facing=north]` does not match
    `stairs` placed without `facing` (an emulator note says so);
  * which blocks have a block entity and how many slots a container has
    come from lists in the code, not from the game;
  * nothing ticks: no gravity, fluids, redstone, block updates or drops
    (`setblock … destroy` drops nothing).

  With a client jar, block ids are checked; a property or value its
  `assets/minecraft/blockstates` file does not list is only noted, since those
  files leave out properties that do not change the model (`waterlogged`,
  `power`, …). Coordinates are whole numbers or `~`/`^` offsets (`^` cannot be
  mixed with the others) and must be inside the world: ±30 000 000
  horizontally, -64..319 from 1.18 (0..255 before, and in the Nether and the
  End; a pack's own dimensions count as overworld-like).
* **storage** — `data … storage` compounds.
* **server state** (`runtime/state.py`) — the time of day (advancing one tick
  per tick while `doDaylightCycle` is true), weather, difficulty (easy at
  start), the world border (center, size, damage, warning — a timed change
  is applied at once), the world spawn, force-loaded chunks, random
  sequences (seeded from the world seed and their id), the tick rate and
  frozen flag (kept, but the emulator keeps ticking), and teams with their
  options and members. Players keep their game mode (`playerGameType`),
  experience (`XpLevel`, `XpP`, `XpTotal` — the `level` and `xp` criteria
  follow) and spawn point in their NBT.
* **gamerules** — stored; `maxCommandChainLength` is enforced.

Selectors: `@s @p @a @r @e @n` with `type` (including `!` and, with a jar,
`#tags`), `tag` (including `tag=` and `!`), `name`, `scores`, `team`
(`team=` no team, `team=!` any team), `gamemode`, `level`, `x_rotation` /
`y_rotation` (ranges wrap around), `distance`,
`x`/`y`/`z` (move the origin), `dx`/`dy`/`dz` (box from the origin),
`nbt` (vanilla subset matching against `Entity.data()`), `limit`/`c`, `sort`.
`@s[...]` applies its arguments to the executor.

Not evaluated: `advancements` and `predicate` match every entity, and `nbt`
keys the emulator does
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
| `execute` | `as at positioned rotated in align if unless store run summon on`; `anchored facing` pass through. `on vehicle\|passengers\|controller\|owner\|origin\|leasher` follow the entity's links (`controller`: a mob with AI ridden by a mob; `owner`: tamed animals' `Owner`; `origin`: a projectile's `Owner` or an item's `Thrower`; `leash`); `on attacker\|target` end the branch (no combat or AI). `store` (score, storage, entity, block, bossbar) binds its target where it appears in the chain; `run return` leaves the function on the first branch that reaches it |
| `if` / `unless` conditions | `score` (matches and comparisons), `entity`, `block <pos> <predicate>` (id or `#tag` from the jar or the pack, properties, NBT subset), `blocks <start> <end> <destination> all\|masked` (the count is the number of blocks compared), `data` (entity, block, storage), `items entity\|block <…> <slots> <predicate>` (slot wildcards like `container.*`; the count is the number of matching items), `dimension`, `loaded`, `function` (passes only when a function returns a non-zero value); others are noted and fail. With no `run`, a trailing `if entity` reports how many entities matched |
| `setblock` | `replace`, `keep`, `destroy`, `strict` (1.21.5+). As in vanilla a container there is emptied first, and "Could not set the block" when the block state is already the same (the NBT is then not applied) |
| `fill` | `replace [filter]`, `keep`, `hollow`, `outline`, `destroy`, `strict`, and from 1.21.5 `replace <filter> destroy\|hollow\|outline\|strict`; counts the blocks that changed; limited to 32 768 blocks, or `commandModificationBlockLimit` from 1.19.4 ("Too many blocks in the specified area") |
| `clone` | `[strict]` (1.21.5+) then `replace`, `masked`, `filtered <predicate>` with `normal`, `force` and `move`; `from`/`to` dimensions (1.20.2+); overlapping regions need `force`; counts every block placed |
| `function` | tags, `$` macros with inline SNBT or `with storage\|entity\|block [path]` |
| `schedule` | `function <id> <time> [append|replace]`, `clear`; `t`/`s`/`d` units |
| `return` | value, `run`, `fail` |
| `scoreboard` | every subcommand: objectives `list`, `add` (criteria checked, display name), `remove`, `setdisplay`, `modify`; players `list`, `get`, `set`, `add`, `remove`, `reset`, `enable` (trigger objectives only), `operation`, `display`. Vanilla feedback and errors: read-only criteria (`health`, `food`, …), invalid integers, negative `add` amounts, one holder for `get`. `operation` combines every target with every source, creates missing scores as 0, and divides with `floorDiv` / `floorMod` (`/=` and `%=` by zero fail) |
| `trigger` | only a player can trigger ("A player is required to run this command here" otherwise); the objective must be a trigger and enabled for that player, and using it locks it again until the next `scoreboard players enable`; `set`/`add` with vanilla feedback |
| `tag` | add/remove |
| `summon` | keeps the NBT (a `UUID` is used, duplicates refused); the position argument wins over `Pos` |
| `kill`, `tp`/`teleport` | `tp <entity>`, `tp <x y z>`, `tp <targets> <entity|x y z> [<yaw> <pitch>]` |
| `give` | players only; stacks onto matching stacks (selected slot, offhand, container order), then empty slots; what does not fit drops as item entities; at most 100 stacks |
| `clear` | `[targets] [item predicate] [maxCount]`: removes matching items (not the ender chest); `maxCount 0` only counts. Predicates: an id, `*`, `#tag` (from the client jar or the pack), `[component=value]`, `[component]`, `!` negation, `|` alternatives, and the old `{nbt}` subset; `~` sub-predicates and `count` are noted and not checked. Items are taken in slot order; a negative count is rejected |
| `item` | counts must be 1–99 (1–64 before 1.20.5) and fit the item's stack; `replace entity\|block <…> <slot> with <item> [count]` (`air` empties the slot), `replace … from entity\|block <source> <slot> [modifier]`, `modify entity\|block <…> <slot> <modifier>` (pack item modifiers: `set_count`, `set_components`, `set_nbt`); a block must be a container with that slot ("Target position 1, 2, 3 is not a container", "The target does not have slot container.30") |
| `replaceitem` | `entity\|block <…> <slot> <item> [count]` (before 1.17) |
| `enchant` | adds the enchantment to the held item in the version's format (`Enchantments`, `enchantments.levels`, `enchantments`); which items accept it is not checked |
| `loot` | targets `give` (what does not fit is lost), `spawn`, `insert <pos>` (into a container, like a hopper) and `replace entity\|block <…> <slot> [count]` (numbered slots from that one); sources `loot <table>`, `fish <table> <pos> [tool]`, `kill <entity>` (its `entities/<type>` table) and `mine <pos> [tool]` (the block's `blocks/<id>` table; a shulker box's `minecraft:contents` dynamic entry gives its items); `insert` goes through the slots once like vanilla (merging, stopping at the first empty slot) and counts the stacks that went in, from the pack or the client jar: rolls (uniform ranges inclusive), weights, nested tables, `alternatives`/`group`/`sequence`, `set_count`, `set_components`, `set_nbt`, stacks split to their limit; returns the number of stacks. Conditions count as passing and other functions are skipped (noted); the tool and the fishing context are not modelled |
| `data` | on one entity, block entity or storage: `get [path] [scale]` (prints the SNBT like vanilla; floored, saturated to int), deep `merge`, `remove`, `modify` with set / merge / append / prepend / insert from `value`, `from` or `string` (sliced). Player data can be read but not modified ("Unable to modify player data"); a block without a block entity cannot be read ("The target block is not a block entity") |
| `say me msg tell w tellraw title teammsg` | logged as `game` output, one record per player who reads it: `[Player1] hi` / `* Player1 waves` for everyone (say, me), `to Player2: text` (tellraw), `to Player1 (actionbar): text` (title), `Server whispers to Player2: text` (msg). Each record names its reader (`recipient`). Text components in JSON or (1.21.5+) SNBT, with `score`, `selector`, `nbt` (storage, entity and block) and `translate` (with its `with` arguments, from the client jar's language file, else the fallback) resolved per reader |
| `gamerule` | |
| `time` | `set <time>\|day\|noon\|night\|midnight`, `add`, `query daytime\|gametime\|day`, with vanilla's return values (the 26.x clock subcommands are not modelled) |
| `weather` | `clear\|rain\|thunder [duration]` (whole seconds before 1.19.3, a time argument of at least 1 tick after); returns the duration, -1 without one |
| `difficulty` | query (returns 0–3) and set (returns 0; "did not change" when it is the same) |
| `worldborder` | `get` (rounded size), `set`/`add` (return the change), `center` (whole numbers are centered on the block), `damage amount\|buffer`, `warning distance\|time`, with vanilla's limits and messages; times are whole seconds, time arguments from 26.1 |
| `random` | `value`/`roll <min..max> [sequence]` (roll is announced to everyone; open ends are the int limits), `reset <sequence> [seed] [includeWorldSeed] [includeSequenceId]`, `reset * …` (forgets every sequence and sets the defaults of new ones); ranges of 2 to 2 147 483 646 values |
| `team` | `add [display name]`, `remove`, `empty`, `join`, `leave`, `list [team]`, `modify` (`displayName`, `color`, `friendlyFire`, `seeFriendlyInvisibles`, `nametagVisibility`, `deathMessageVisibility`, `collisionRule`, `prefix`, `suffix`) with the "Nothing changed" errors |
| `teammsg`, `tm` | to every player on the sender's team: `-> [Team] <Player1> text` for the sender, `[Team] <Player1> text` for the others; "You must be on a team" otherwise |
| `gamemode`, `defaultgamemode` | players only; returns how many changed |
| `experience`, `xp` | `add\|set <targets> <amount> [levels\|points]`, `query <player> levels\|points`, following vanilla's arithmetic: points roll levels over both ways and change `XpTotal`; levels keep the progress and reset everything below 0; `set … points` above the level's maximum fails |
| `seed`, `list [uuids]` | the world seed; the players online |
| `forceload` | `add`/`remove <from> [to]` (at most 256 chunks), `remove all`, `query [pos]` |
| `effect` | `give <targets> <effect> [seconds\|infinite] [amplifier] [hideParticles]` (30 s by default; `infinite` from 1.19.4; fails when nothing changes, as vanilla's update decides), `clear [targets] [effect]` |
| `attribute` | `get [scale]`, `base get [scale]\|set`, `base reset` (1.21.4+), `modifier add\|remove\|value get`: resource location ids from 1.21 (a UUID and a name before), operations `add_value`/`add_multiplied_base`/`add_multiplied_total` from 1.20.5 (`add`/`multiply_base`/`multiply` before); ids checked against the jar's registry, or the table with `generic.` before 1.21.2; values clamped to vanilla's ranges and scaled results saturated; "has no attribute" for kinds without it |
| `damage` | `<target> <amount> [type] […]`: absorption, then health; invulnerable entities, creative/spectator players, armor stands (except player attacks) and entities still recovering from a harder hit refuse ("Target is invulnerable to the given damage type"); the damage type id is checked with a jar |
| `ride` | `mount` (no loops, not onto players, same dimension) and `dismount`, with vanilla's errors |
| `bossbar` | `add`, `remove`, `list`, `get value\|max\|visible\|players`, `set name\|color\|style\|value\|max\|visible\|players`, with the "Nothing changed" errors; `execute store … bossbar` stores the raw value and fails before running on an unknown bar |
| `setworldspawn`, `spawnpoint` | stored (`SpawnX`… on players; the angle wraps); `spawnpoint` takes players only |
| `tick` | `rate` (1–10 000), `freeze`/`unfreeze`, `query` are kept; `step` needs a frozen game; stepping and sprinting are noted |

**Checked, no state change:** `effect`, `particle` validate their id when a
client jar is loaded ([vanilla-assets.md](vanilla-assets.md)); `give`,
`clear`, `item`, `summon`, `enchant`, `setblock`, `fill` check theirs too.

**Dispatched and costed only:** every other command vanilla has in that
version (`place`, `playsound`, `bossbar`, …).

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

Limitations of the emulator itself (a condition it cannot evaluate, `execute
on`, selector arguments it cannot check, default block states) are noted once per
run at `info` level, so they do not mark a working pack as having warnings.
So is every command that runs without changing the emulated world, with the
reason (`'weather' runs, but the weather is not modelled`, `'effect' runs, but status
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
