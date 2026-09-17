"""Living entities: health, attributes, status effects, riding, and what
entities do on their own each tick.

A model, not the game. Base attribute values and maximum health come from
the tables below (the game keeps them in code, not in data files); effects
only count down (``instant_health``/``instant_damage`` change health, the
others change nothing else); items age, despawn after 6000 ticks, merge with
items at the same spot and are picked up by players standing on them. No
movement, gravity, AI or regeneration.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from datapack_emulator.emulator import versions
from datapack_emulator.emulator.common import normalise_id
from datapack_emulator.emulator.runtime.inventory import ItemStack, has_equipment
from datapack_emulator.emulator.versions import Version

if TYPE_CHECKING:  # pragma: no cover
    from datapack_emulator.emulator.runtime.world import Entity, World

#: attributes lost their ``generic.``/``player.`` prefix in 1.21.2
ATTRIBUTE_PREFIX_REMOVED = "1.21.2"
#: modifier operations got their new names in 1.20.5 …
MODIFIER_OPERATIONS_SINCE = "1.20.5"
#: … and resource location ids (instead of a UUID and a name) in 1.21
MODIFIER_IDS_SINCE = "1.21"
#: ``attribute … base reset``
BASE_RESET_SINCE = "1.21.4"
#: effects are stored as ``active_effects`` with string ids from 1.20.2
EFFECT_IDS_SINCE = "1.20.2"
#: ``effect give … infinite``
INFINITE_EFFECTS_SINCE = "1.19.4"

#: attribute (unprefixed) -> (old prefix, default base value)
ATTRIBUTES: dict[str, tuple[str, float]] = {
    "max_health": ("generic", 20.0),
    "follow_range": ("generic", 32.0),
    "knockback_resistance": ("generic", 0.0),
    "movement_speed": ("generic", 0.7),
    "flying_speed": ("generic", 0.4),
    "attack_damage": ("generic", 2.0),
    "attack_knockback": ("generic", 0.0),
    "attack_speed": ("generic", 4.0),
    "armor": ("generic", 0.0),
    "armor_toughness": ("generic", 0.0),
    "luck": ("generic", 0.0),
    "max_absorption": ("generic", 0.0),
    "scale": ("generic", 1.0),
    "step_height": ("generic", 0.6),
    "gravity": ("generic", 0.08),
    "safe_fall_distance": ("generic", 3.0),
    "fall_damage_multiplier": ("generic", 1.0),
    "jump_strength": ("generic", 0.42),
    "burning_time": ("generic", 1.0),
    "explosion_knockback_resistance": ("generic", 0.0),
    "movement_efficiency": ("generic", 0.0),
    "oxygen_bonus": ("generic", 0.0),
    "water_movement_efficiency": ("generic", 0.0),
    "block_interaction_range": ("player", 4.5),
    "entity_interaction_range": ("player", 3.0),
    "block_break_speed": ("player", 1.0),
    "mining_efficiency": ("player", 0.0),
    "sneaking_speed": ("player", 0.3),
    "submerged_mining_speed": ("player", 0.2),
    "sweeping_damage_ratio": ("player", 0.0),
    "spawn_reinforcements": ("zombie", 0.0),
}
#: maximum health by entity type (the rest: 20)
MAX_HEALTH: dict[str, float] = {
    "allay": 20, "armadillo": 12, "axolotl": 14, "bat": 6, "bee": 10, "blaze": 20,
    "bogged": 16, "breeze": 30, "camel": 32, "cat": 10, "cave_spider": 12, "chicken": 4,
    "cod": 3, "cow": 10, "creaking": 1, "creeper": 20, "dolphin": 10, "donkey": 15,
    "drowned": 20, "elder_guardian": 80, "ender_dragon": 200, "enderman": 40,
    "endermite": 8, "evoker": 24, "fox": 10, "frog": 10, "ghast": 10, "glow_squid": 10,
    "goat": 10, "guardian": 30, "hoglin": 40, "horse": 15, "iron_golem": 100, "llama": 15,
    "mooshroom": 10, "mule": 15, "ocelot": 10, "panda": 20, "parrot": 6, "phantom": 20,
    "pig": 10, "piglin": 16, "piglin_brute": 50, "pillager": 24, "polar_bear": 30,
    "pufferfish": 3, "rabbit": 3, "ravager": 100, "salmon": 3, "sheep": 8, "shulker": 30,
    "silverfish": 8, "skeleton_horse": 15, "slime": 1, "magma_cube": 1, "sniffer": 14,
    "snow_golem": 4, "spider": 16, "squid": 10, "strider": 20, "tadpole": 6,
    "trader_llama": 15, "tropical_fish": 3, "turtle": 30, "vex": 14, "vindicator": 24,
    "warden": 500, "witch": 26, "wither": 300, "wolf": 8, "zoglin": 40,
    "zombie_horse": 15, "happy_ghast": 20,
}  # fmt: skip
#: other base values that differ by type
BASE_OVERRIDES: dict[str, dict[str, float]] = {
    "player": {"movement_speed": 0.1, "attack_damage": 1.0},
    "zombie": {"movement_speed": 0.23, "attack_damage": 3.0, "follow_range": 35.0, "armor": 2.0},
    "husk": {"movement_speed": 0.23, "attack_damage": 3.0, "follow_range": 35.0, "armor": 2.0},
    "drowned": {"movement_speed": 0.23, "attack_damage": 3.0, "follow_range": 35.0, "armor": 2.0},
    "skeleton": {"movement_speed": 0.25},
    "stray": {"movement_speed": 0.25},
    "creeper": {"movement_speed": 0.25},
    "pig": {"movement_speed": 0.25},
    "cow": {"movement_speed": 0.2},
    "sheep": {"movement_speed": 0.23},
    "chicken": {"movement_speed": 0.25},
    "villager": {"movement_speed": 0.5, "follow_range": 48.0},
    "iron_golem": {"movement_speed": 0.25, "attack_damage": 15.0, "knockback_resistance": 1.0},
    "spider": {"movement_speed": 0.3},
    "enderman": {"movement_speed": 0.3, "attack_damage": 7.0, "follow_range": 64.0},
    "wolf": {"movement_speed": 0.3, "attack_damage": 4.0},
}

#: numeric effect ids of versions before 1.20.2
LEGACY_EFFECT_IDS = (
    "speed", "slowness", "haste", "mining_fatigue", "strength", "instant_health",
    "instant_damage", "jump_boost", "nausea", "regeneration", "resistance", "fire_resistance",
    "water_breathing", "invisibility", "blindness", "night_vision", "hunger", "weakness",
    "poison", "wither", "health_boost", "absorption", "saturation", "glowing", "levitation",
    "luck", "unluck", "slow_falling", "conduit_power", "dolphins_grace", "bad_omen",
    "hero_of_the_village", "darkness",
)  # fmt: skip
INSTANT_EFFECTS = frozenset(
    {"minecraft:instant_health", "minecraft:instant_damage", "minecraft:saturation"}
)
#: the undead when no client jar gives #minecraft:undead
UNDEAD = frozenset(
    f"minecraft:{name}"
    for name in (
        "zombie", "husk", "drowned", "zombie_villager", "zombified_piglin", "skeleton", "stray",
        "wither_skeleton", "bogged", "wither", "phantom", "zoglin", "skeleton_horse",
        "zombie_horse", "parched", "camel_husk", "zombie_nautilus", "giant",
    )
)  # fmt: skip
#: effects an entity type shrugs off (besides the undead and poison/regeneration)
IMMUNITIES: dict[str, frozenset[str]] = {
    "minecraft:spider": frozenset({"minecraft:poison"}),
    "minecraft:cave_spider": frozenset({"minecraft:poison"}),
    "minecraft:wither": frozenset({"minecraft:wither"}),
    "minecraft:wither_skeleton": frozenset({"minecraft:wither"}),
    "minecraft:silverfish": frozenset({"minecraft:infested"}),
    "minecraft:slime": frozenset({"minecraft:oozing"}),
}
#: tamable entities: what ``execute on owner`` works on
OWNABLE = frozenset(
    f"minecraft:{name}"
    for name in (
        "wolf", "cat", "parrot", "horse", "donkey", "mule", "llama", "trader_llama",
        "skeleton_horse", "zombie_horse", "camel", "camel_husk", "nautilus", "zombie_nautilus",
        "happy_ghast",
    )
)  # fmt: skip
#: vanilla's value ranges (RangedAttribute): min, max
RANGES: dict[str, tuple[float, float]] = {
    "max_health": (1.0, 1024.0),
    "follow_range": (0.0, 2048.0),
    "knockback_resistance": (0.0, 1.0),
    "movement_speed": (0.0, 1024.0),
    "flying_speed": (0.0, 1024.0),
    "attack_damage": (0.0, 2048.0),
    "attack_knockback": (0.0, 5.0),
    "attack_speed": (0.0, 1024.0),
    "armor": (0.0, 30.0),
    "armor_toughness": (0.0, 20.0),
    "luck": (-1024.0, 1024.0),
    "max_absorption": (0.0, 2048.0),
    "scale": (0.0625, 16.0),
    "step_height": (0.0, 10.0),
    "gravity": (-1.0, 1.0),
    "safe_fall_distance": (-1024.0, 1024.0),
    "fall_damage_multiplier": (0.0, 100.0),
    "jump_strength": (0.0, 32.0),
    "block_interaction_range": (0.0, 64.0),
    "entity_interaction_range": (0.0, 64.0),
    "block_break_speed": (0.0, 1024.0),
    "burning_time": (0.0, 1024.0),
    "explosion_knockback_resistance": (0.0, 1.0),
    "mining_efficiency": (0.0, 1024.0),
    "movement_efficiency": (0.0, 1.0),
    "oxygen_bonus": (0.0, 1024.0),
    "sneaking_speed": (0.0, 1.0),
    "submerged_mining_speed": (0.0, 20.0),
    "sweeping_damage_ratio": (0.0, 1.0),
    "water_movement_efficiency": (0.0, 1.0),
    "spawn_reinforcements": (0.0, 1.0),
}
#: animals and other mobs without attack damage
PASSIVE = frozenset(
    f"minecraft:{name}"
    for name in (
        "pig", "cow", "mooshroom", "sheep", "chicken", "rabbit", "horse", "donkey", "mule",
        "skeleton_horse", "zombie_horse", "llama", "trader_llama", "camel", "cat", "ocelot",
        "parrot", "fox", "frog", "tadpole", "turtle", "villager", "wandering_trader", "bat",
        "squid", "glow_squid", "cod", "salmon", "pufferfish", "tropical_fish", "axolotl",
        "strider", "sniffer", "armadillo", "allay", "snow_golem", "armor_stand", "mule",
        "happy_ghast",
    )
)  # fmt: skip

#: entity NBT keys the Living model keeps
LIVING_KEYS = ("attributes", "Attributes", "active_effects", "ActiveEffects")

OPERATIONS_NEW = ("add_value", "add_multiplied_base", "add_multiplied_total")
OPERATIONS_OLD = ("add", "multiply_base", "multiply")


def is_living(entity_type: str) -> bool:
    return has_equipment(entity_type)


def attribute_key(text: str) -> str | None:
    """``minecraft:generic.max_health`` / ``max_health`` -> ``max_health``."""
    path = normalise_id(text).split(":", 1)[1]
    name = path.split(".", 1)[1] if "." in path else path
    return name if name in ATTRIBUTES else None


def attribute_id(name: str, version: Version | None) -> str:
    """The id a version writes an attribute with."""
    if version is not None and version < versions.parse(ATTRIBUTE_PREFIX_REMOVED):
        return f"minecraft:{ATTRIBUTES[name][0]}.{name}"
    return f"minecraft:{name}"


FLYING = frozenset(f"minecraft:{name}" for name in ("parrot", "bee", "allay", "happy_ghast"))
ZOMBIES = frozenset(
    f"minecraft:{name}"
    for name in ("zombie", "husk", "drowned", "zombie_villager", "zombified_piglin")
)


def has_attribute(entity_type: str, name: str, is_player: bool) -> bool:
    """Whether a kind of entity has an attribute at all (a model of the
    attribute suppliers)."""
    prefix = ATTRIBUTES.get(name, ("generic", 0.0))[0]
    if prefix == "player" or name in ("attack_speed", "luck"):
        return is_player
    if prefix == "zombie":
        return entity_type in ZOMBIES
    if name in ("attack_damage", "attack_knockback"):
        return is_player or entity_type not in PASSIVE
    if name == "follow_range":
        return not is_player and entity_type != "minecraft:armor_stand"
    if name == "flying_speed":
        return entity_type in FLYING
    return True


def default_base(entity_type: str, name: str) -> float:
    path = entity_type.split(":", 1)[-1]
    if name == "max_health":
        return float(MAX_HEALTH.get(path, 20.0))
    fallback = ATTRIBUTES.get(name, ("generic", 0.0))[1]
    return BASE_OVERRIDES.get(path, {}).get(name, fallback)


@dataclass
class Modifier:
    id: str
    amount: float
    operation: int  # 0 add, 1 multiply base, 2 multiply total
    name: str = ""


@dataclass
class Attribute:
    base: float
    modifiers: dict[str, Modifier] = field(default_factory=dict)

    @property
    def value(self) -> float:
        total = self.base + sum(m.amount for m in self.modifiers.values() if m.operation == 0)
        result = total
        result += total * sum(m.amount for m in self.modifiers.values() if m.operation == 1)
        for modifier in self.modifiers.values():
            if modifier.operation == 2:
                result *= 1.0 + modifier.amount
        return result

    def sanitized(self, name: str) -> float:
        """The value vanilla reports: clamped to the attribute's range."""
        low, high = RANGES.get(name, (-1e300, 1e300))
        value = self.value
        return low if value != value else max(low, min(high, value))


@dataclass
class Effect:
    id: str
    amplifier: int = 0
    duration: int = 600  # ticks; -1 is infinite
    ambient: bool = False
    show_particles: bool = True
    show_icon: bool = True
    #: a weaker but longer effect that comes back when this one ends
    hidden: Effect | None = None

    def to_nbt(self, version: Version | None) -> dict[str, Any]:
        if version is not None and version < versions.parse(EFFECT_IDS_SINCE):
            path = self.id.split(":", 1)[1]
            number = LEGACY_EFFECT_IDS.index(path) + 1 if path in LEGACY_EFFECT_IDS else 0
            data: dict[str, Any] = {
                "Id": number,
                "Amplifier": self.amplifier,
                "Duration": self.duration,
                "Ambient": int(self.ambient),
                "ShowParticles": int(self.show_particles),
                "ShowIcon": int(self.show_icon),
            }
            if self.hidden is not None:
                data["HiddenEffect"] = self.hidden.to_nbt(version)
            return data
        data = {
            "id": self.id,
            "amplifier": self.amplifier,
            "duration": self.duration,
            "ambient": int(self.ambient),
            "show_particles": int(self.show_particles),
            "show_icon": int(self.show_icon),
        }
        if self.hidden is not None:
            data["hidden_effect"] = self.hidden.to_nbt(version)
        return data

    @classmethod
    def from_nbt(cls, data: Any) -> Effect | None:
        if not isinstance(data, dict):
            return None
        raw = data.get("id", data.get("Id"))
        if isinstance(raw, int):
            if not 1 <= raw <= len(LEGACY_EFFECT_IDS):
                return None
            effect_id = f"minecraft:{LEGACY_EFFECT_IDS[raw - 1]}"
        elif isinstance(raw, str):
            effect_id = normalise_id(raw)
        else:
            return None

        def number(*keys: str, default: int) -> int:
            for key in keys:
                if isinstance(data.get(key), (int, float)):
                    return int(data[key])
            return default

        return cls(
            effect_id,
            number("amplifier", "Amplifier", default=0),
            number("duration", "Duration", default=0),
            bool(number("ambient", "Ambient", default=0)),
            bool(number("show_particles", "ShowParticles", default=1)),
            bool(number("show_icon", "ShowIcon", default=1)),
            cls.from_nbt(data.get("hidden_effect", data.get("HiddenEffect"))),
        )

    def _longer_than(self, other: Effect) -> bool:
        return other.duration != -1 and (self.duration == -1 or self.duration > other.duration)

    def update(self, new: Effect) -> bool:
        """MobEffectInstance.update: take ``new`` over this effect; whether
        anything changed. A weaker, longer effect is kept hidden underneath."""
        changed = False
        if new.amplifier > self.amplifier:
            if self._longer_than(new):
                hidden = self.hidden
                self.hidden = Effect(
                    self.id, self.amplifier, self.duration, self.ambient,
                    self.show_particles, self.show_icon, hidden,
                )  # fmt: skip
            self.amplifier = new.amplifier
            self.duration = new.duration
            changed = True
        elif new._longer_than(self):
            if new.amplifier == self.amplifier:
                self.duration = new.duration
                changed = True
            elif self.hidden is None:
                self.hidden = Effect(
                    new.id, new.amplifier, new.duration, new.ambient,
                    new.show_particles, new.show_icon,
                )  # fmt: skip
            else:
                self.hidden.update(new)
        if (not new.ambient and self.ambient) or new.ambient != self.ambient:
            self.ambient = new.ambient
            changed = True
        if new.show_particles != self.show_particles:
            self.show_particles = new.show_particles
            changed = True
        if new.show_icon != self.show_icon:
            self.show_icon = new.show_icon
            changed = True
        return changed

    def _tick_down(self) -> None:
        if self.duration > 0:
            self.duration -= 1
        if self.hidden is not None:  # hidden effects run down too
            self.hidden._tick_down()

    def tick(self) -> bool:
        """One tick down; whether the effect (with what it hides) is still there."""
        self._tick_down()
        if self.duration == 0 and self.hidden is not None:
            hidden = self.hidden
            self.amplifier, self.duration = hidden.amplifier, hidden.duration
            self.ambient, self.show_particles = hidden.ambient, hidden.show_particles
            self.show_icon, self.hidden = hidden.show_icon, hidden.hidden
        return self.duration != 0


class Living:
    """What a living entity has besides NBT: attributes, effects, health, rider links."""

    def __init__(self, entity_type: str):
        self.type = entity_type
        self.attributes: dict[str, Attribute] = {}
        self.effects: dict[str, Effect] = {}
        #: vanilla's invulnerableTime and lastHurt (not saved)
        self.invulnerable_time = 0
        self.last_hurt = 0.0

    def attribute(self, name: str) -> Attribute:
        if name not in self.attributes:
            self.attributes[name] = Attribute(default_base(self.type, name))
        return self.attributes[name]

    def max_health(self) -> float:
        return self.attribute("max_health").sanitized("max_health")

    # -- NBT ---------------------------------------------------------------

    def to_nbt(self, version: Version | None) -> dict[str, Any]:
        data: dict[str, Any] = {}
        names = ["max_health", "movement_speed", "armor", *self.attributes]
        seen = []
        for name in names:
            if name in seen:
                continue
            seen.append(name)
        modern = version is None or version >= versions.parse(MODIFIER_OPERATIONS_SINCE)
        with_ids = version is None or version >= versions.parse(MODIFIER_IDS_SINCE)
        entries = []
        for name in seen:
            attribute = self.attribute(name)
            modifiers = [_modifier_nbt(m, modern, with_ids) for m in attribute.modifiers.values()]
            if modern:
                entry: dict[str, Any] = {"id": attribute_id(name, version), "base": attribute.base}
                if modifiers:
                    entry["modifiers"] = modifiers
            else:
                entry = {"Name": attribute_id(name, version), "Base": attribute.base}
                if modifiers:
                    entry["Modifiers"] = modifiers
            entries.append(entry)
        data["attributes" if modern else "Attributes"] = entries
        if self.effects:
            key = (
                "active_effects"
                if version is None or version >= versions.parse(EFFECT_IDS_SINCE)
                else "ActiveEffects"
            )
            data[key] = [effect.to_nbt(version) for effect in self.effects.values()]
        return data

    def load_nbt(self, data: dict[str, Any]) -> None:
        for key in ("attributes", "Attributes"):
            entries = data.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = attribute_key(str(entry.get("id", entry.get("Name", ""))))
                if name is None:
                    continue
                attribute = self.attribute(name)
                base = entry.get("base", entry.get("Base"))
                if isinstance(base, (int, float)):
                    attribute.base = float(base)
                attribute.modifiers = {}
                for raw in entry.get("modifiers", entry.get("Modifiers")) or []:
                    modifier = _modifier_from_nbt(raw)
                    if modifier is not None:
                        attribute.modifiers[modifier.id] = modifier
        for key in ("active_effects", "ActiveEffects"):
            if key in data:
                effects = [Effect.from_nbt(raw) for raw in data.get(key) or []]
                self.effects = {effect.id: effect for effect in effects if effect is not None}


def uuid_ints(text: str) -> list[int] | None:
    """A UUID (any hex group widths) as NBT's four ints; None when not one."""
    import uuid as _uuid

    parts = text.split("-")
    try:
        if len(parts) != 5:
            return None
        widths = (8, 4, 4, 4, 12)
        value = _uuid.UUID("-".join(p.zfill(w) for p, w in zip(parts, widths, strict=True)))
    except ValueError:
        return None
    number = value.int
    ints = [(number >> shift) & 0xFFFFFFFF for shift in (96, 64, 32, 0)]
    return [part - 2**32 if part >= 2**31 else part for part in ints]


def _modifier_nbt(m: Modifier, modern: bool, with_ids: bool) -> dict[str, Any]:
    if with_ids:
        return {"id": m.id, "amount": m.amount, "operation": OPERATIONS_NEW[m.operation]}
    uuid = uuid_ints(m.id) or m.id
    if modern:  # 1.20.5–1.20.6
        return {
            "uuid": uuid,
            "name": m.name,
            "amount": m.amount,
            "operation": OPERATIONS_NEW[m.operation],
        }
    return {"UUID": uuid, "Amount": m.amount, "Operation": m.operation, "Name": m.name}


def _modifier_from_nbt(raw: Any) -> Modifier | None:
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("id", raw.get("uuid", raw.get("UUID")))
    if isinstance(identifier, list) and len(identifier) == 4:
        number = 0
        for part in identifier:
            number = (number << 32) | (int(part) & 0xFFFFFFFF)
        import uuid as _uuid

        identifier = str(_uuid.UUID(int=number))
    amount = raw.get("amount", raw.get("Amount"))
    operation = raw.get("operation", raw.get("Operation", 0))
    if isinstance(operation, str):
        names = OPERATIONS_NEW if operation in OPERATIONS_NEW else OPERATIONS_OLD
        operation = names.index(operation) if operation in names else 0
    if identifier is None or not isinstance(amount, (int, float)):
        return None
    name = raw.get("name", raw.get("Name", ""))
    if not isinstance(operation, (int, float, str)):
        return None
    return Modifier(str(identifier), float(amount), int(operation), str(name))


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def health(entity: Entity) -> float:
    value = entity.nbt.get("Health")
    if isinstance(value, (int, float)):
        return float(value)
    return entity.living.max_health() if entity.living else 0.0


def set_health(world: World, entity: Entity, value: float) -> None:
    """Clamp to 0..max health, store it and update ``health`` criteria."""
    if entity.living is None:
        return
    value = max(0.0, min(entity.living.max_health(), value))
    entity.nbt["Health"] = value
    if entity.is_player:
        board = world.scoreboard
        for objective, criterion in board.objectives.items():
            if criterion == "health":
                board.set(entity.id, objective, math.ceil(value))


def hurt(world: World, entity: Entity, amount: float) -> bool | None:
    """Damage absorbed first, then health; whether the entity died, or None
    when the hit did nothing (still recovering from a harder hit)."""
    living = entity.living
    if living is not None:
        if living.invulnerable_time > 10:
            if amount <= living.last_hurt:
                return None
            living.last_hurt, amount = amount, amount - living.last_hurt
        else:
            living.last_hurt = amount
            living.invulnerable_time = 20
            entity.nbt["HurtTime"] = 10
    absorption = float(entity.nbt.get("AbsorptionAmount", 0.0) or 0.0)
    absorbed = min(absorption, amount)
    if absorbed:
        entity.nbt["AbsorptionAmount"] = absorption - absorbed
    set_health(world, entity, health(entity) - (amount - absorbed))
    return health(entity) <= 0


# ---------------------------------------------------------------------------
# each tick
# ---------------------------------------------------------------------------

#: an item this old disappears
ITEM_LIFETIME = 6000
#: Age and PickupDelay values that mean "never"
NEVER_DESPAWN = -32768
NEVER_PICKUP = 32767


def tick_entities(
    world: World,
    version: Version | None,
    on_instant: Callable[[Entity, Effect], None] | None = None,
) -> None:
    """Effects count down (instant ones take effect through ``on_instant``);
    hurt timers run down; items age, merge, despawn and get picked up."""
    for entity in list(world.entities):
        if entity not in world.entities:
            continue
        living = entity.living
        if living is not None:
            if living.invulnerable_time > 0:
                living.invulnerable_time -= 1
            hurt_time = entity.nbt.get("HurtTime")
            if isinstance(hurt_time, int) and hurt_time > 0:
                entity.nbt["HurtTime"] = hurt_time - 1
            for effect_id, effect in list(living.effects.items()):
                if effect_id in INSTANT_EFFECTS:
                    del living.effects[effect_id]
                    if on_instant is not None:
                        on_instant(entity, effect)
                elif not effect.tick():
                    del living.effects[effect_id]
        if entity.type == "minecraft:item":
            _tick_item(world, entity, version)


def _tick_item(world: World, item: Entity, version: Version | None) -> None:
    """ItemEntity.tick: pickup delay, merging (every 40 ticks), age, despawn."""
    delay = item.nbt.get("PickupDelay", 0)
    if isinstance(delay, int) and 0 < delay < NEVER_PICKUP:
        item.nbt["PickupDelay"] = delay - 1
    stack = ItemStack.from_nbt(item.nbt.get("Item"))
    if stack is None:
        world.kill(item)
        return
    if (world.tick - item.born) % MERGE_INTERVAL == 0 and _mergeable(item, stack):
        _merge_neighbours(world, item, stack, version)
        if item not in world.entities:
            return
        stack = ItemStack.from_nbt(item.nbt.get("Item")) or stack
    age = item.nbt.get("Age", 0)
    if isinstance(age, int) and age != NEVER_DESPAWN:
        age += 1
        item.nbt["Age"] = age
        if age >= ITEM_LIFETIME:
            world.kill(item)
            return
    if item.nbt.get("PickupDelay", 0) == 0:
        _pickup(world, item, stack, version)


#: a still item looks for neighbours to merge with this often
MERGE_INTERVAL = 40


def _mergeable(item: Entity, stack: ItemStack) -> bool:
    age = item.nbt.get("Age", 0)
    return (
        item.nbt.get("PickupDelay") != NEVER_PICKUP
        and age != NEVER_DESPAWN
        and (not isinstance(age, int) or age < ITEM_LIFETIME)
        and stack.count < stack.max_count
    )


def _merge_neighbours(world: World, item: Entity, stack: ItemStack, version) -> None:
    for other in list(world.entities):
        if other is item or other.type != "minecraft:item" or other.dimension != item.dimension:
            continue
        if other not in world.entities or item not in world.entities:
            continue
        dx = abs(other.position[0] - item.position[0])
        dy = abs(other.position[1] - item.position[1])
        dz = abs(other.position[2] - item.position[2])
        if dx >= 0.75 or dz >= 0.75 or dy >= 0.25:
            continue
        other_stack = ItemStack.from_nbt(other.nbt.get("Item"))
        if other_stack is None or not _mergeable(other, other_stack):
            continue
        if not other_stack.same_kind(stack) or other.nbt.get("Owner") != item.nbt.get("Owner"):
            continue
        # the bigger stack takes from the smaller one
        if other_stack.count < stack.count:
            _move(world, item, stack, other, other_stack, version)
        else:
            _move(world, other, other_stack, item, stack, version)
            if item not in world.entities:
                return
        stack = ItemStack.from_nbt(item.nbt.get("Item")) or stack


def _move(world, into: Entity, into_stack, source: Entity, source_stack, version) -> None:
    moved = min(into_stack.max_count - into_stack.count, source_stack.count)
    into_stack.count += moved
    source_stack.count -= moved
    into.nbt["Item"] = into_stack.to_nbt(version)
    into.nbt["PickupDelay"] = max(into.nbt.get("PickupDelay", 0), source.nbt.get("PickupDelay", 0))
    into.nbt["Age"] = min(into.nbt.get("Age", 0), source.nbt.get("Age", 0))
    if source_stack.count <= 0:
        world.kill(source)
    else:
        source.nbt["Item"] = source_stack.to_nbt(version)


def _near(a: list[float], b: list[float], horizontal: float, below: float, above: float) -> bool:
    return (
        abs(a[0] - b[0]) <= horizontal
        and abs(a[2] - b[2]) <= horizontal
        and b[1] - below <= a[1] <= b[1] + above
    )


def _pickup(world: World, item: Entity, stack: ItemStack, version: Version | None) -> None:
    owner = item.nbt.get("Owner")
    for player in world.players:
        if player.dimension != item.dimension or player.nbt.get("playerGameType") == 3:
            continue
        if owner is not None:
            from datapack_emulator.emulator.runtime.world import uuid_to_ints

            if owner != uuid_to_ints(player.uuid):
                continue
        if not _near(item.position, player.position, 1.425, 0.75, 2.3):
            continue
        leftover = player.inventory.add(stack.copy())
        if leftover == stack.count:
            continue
        if leftover:
            stack.count = leftover
            item.nbt["Item"] = stack.to_nbt(version)
        else:
            world.kill(item)
            return
