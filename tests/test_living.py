"""Living entities: effects, attributes, health and damage, riding, boss
bars, per-type defaults and item entities over time."""

from __future__ import annotations

import pytest
from conftest import chat, visible_errors

from datapack_emulator.emulator import Datapack, Emulator


@pytest.fixture
def world(make_pack):
    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "\n"}))

    def make(version: str = "1.21.4", players: int = 1):
        emulator = Emulator(pack, version=version, players=players)
        emulator.start()
        emulator.run_tick()

        def typed(line: str):
            before = len(emulator.output.records)
            result, _ = emulator.run_typed(line)
            return result, emulator.output.records[before:]

        emulator.typed = typed
        return emulator

    return make


def test_per_type_defaults(world):
    emulator = world()
    emulator.typed("summon pig ~ ~ ~ {Tags:[p]}")
    pig = emulator.world.entities[-1]
    data = pig.data(emulator.version)
    assert data["Health"] == 10.0 and data["CanPickUpLoot"] == 0
    assert {"id": "minecraft:max_health", "base": 10.0} in data["attributes"]
    player = emulator.world.players[0].data(emulator.version)
    assert player["Health"] == 20.0 and "CanPickUpLoot" not in player
    old = world("1.20.4").world.players[0].data(world("1.20.4").version)
    assert {"Name": "minecraft:generic.max_health", "Base": 20.0} in old["Attributes"]
    emulator.typed("summon marker")
    assert "Health" not in emulator.world.entities[-1].data()
    assert emulator.typed("execute if entity @e[nbt={Health:10f}]")[0].value == 1


def test_effects_are_given_counted_down_and_cleared(world):
    emulator = world()
    result, records = emulator.typed("effect give Player1 speed 2 1")
    assert result.value == 1 and chat(records) == ["Applied effect Speed to Player1"]
    data = emulator.world.players[0].data(emulator.version)
    assert data["active_effects"][0] == {
        "id": "minecraft:speed", "amplifier": 1, "duration": 40, "ambient": 0,
        "show_particles": 1, "show_icon": 1,
    }  # fmt: skip
    _, records = emulator.typed("effect give Player1 speed 1 0")
    assert visible_errors(records) == [
        "Unable to apply this effect (target is either immune to effects, or has something stronger)"
    ]
    assert emulator.typed('execute if entity @a[nbt={active_effects:[{id:"minecraft:speed"}]}]')[
        0
    ].success
    for _ in range(40):
        emulator.run_tick()
    assert not emulator.world.players[0].living.effects
    emulator.typed("effect give @a glowing infinite")
    emulator.run_tick()
    assert emulator.world.players[0].living.effects["minecraft:glowing"].duration == -1
    _, records = emulator.typed("effect clear Player1 glowing")
    assert chat(records) == ["Removed effect Glowing from Player1"]
    _, records = emulator.typed("effect clear Player1")
    assert visible_errors(records) == ["Target has no effects to remove"]
    old = world("1.20.1")
    old.typed("effect give Player1 speed 5")
    assert old.world.players[0].data(old.version)["ActiveEffects"][0]["Id"] == 1
    assert not world("1.19.2").typed("effect give Player1 speed infinite")[0].success


def test_damage_health_and_death(world):
    emulator = world()
    emulator.typed("summon pig ~ ~ ~ {Tags:[p]}")
    result, records = emulator.typed("damage @e[tag=p,limit=1] 4")
    assert chat(records) == ["Applied 4.0 damage to Pig"]
    pig = emulator.world.entities[-1]
    assert pig.data()["Health"] == 6.0
    emulator.typed("damage @e[tag=p,limit=1] 20")
    assert pig not in emulator.world.entities
    emulator.typed("scoreboard objectives add hp health")
    emulator.typed("give Player1 diamond")
    emulator.typed("damage Player1 5")
    assert emulator.world.scoreboard.get("Player1", "hp") == 15
    emulator.typed("damage Player1 50")
    player = emulator.world.players[0]
    assert player.data()["Health"] == 20.0 and not list(player.inventory.items())
    emulator.typed("gamemode creative Player1")
    _, records = emulator.typed("damage Player1 1")
    assert visible_errors(records) == ["Target is invulnerable to the given damage type"]
    emulator.typed("summon zombie ~ ~ ~ {Tags:[z],Health:5f}")
    emulator.typed("effect give @e[tag=z] instant_health")  # hurts the undead
    emulator.run_tick()  # instant effects take hold on the entity's tick
    assert all(entity.type != "minecraft:zombie" for entity in emulator.world.entities)


def test_attributes_and_modifiers(world):
    emulator = world()
    result, records = emulator.typed("attribute Player1 minecraft:max_health get")
    assert result.value == 20 and chat(records) == [
        "The value of attribute Max Health for entity Player1 is 20.0"
    ]
    emulator.typed("attribute Player1 max_health modifier add test:boost 10 add_value")
    assert emulator.typed("attribute Player1 max_health get 2")[0].value == 60
    _, records = emulator.typed("attribute Player1 max_health modifier add test:boost 1 add_value")
    assert visible_errors(records)[0].startswith("Modifier test:boost is already present")
    emulator.typed("attribute Player1 max_health modifier add test:double 1 add_multiplied_total")
    assert emulator.typed("attribute Player1 max_health get")[0].value == 60
    assert (
        emulator.typed("attribute Player1 max_health modifier value get test:boost")[0].value == 10
    )
    emulator.typed("attribute Player1 max_health modifier remove test:boost")
    emulator.typed("attribute Player1 max_health modifier remove test:double")
    emulator.typed("attribute Player1 max_health base set 4")
    assert (
        emulator.world.players[0].data()["Health"] == 4.0
        or emulator.typed("attribute Player1 max_health base get")[0].value == 4
    )
    assert emulator.typed("attribute Player1 max_health base reset")[0].success
    _, records = emulator.typed("attribute Player1 generic.max_health get")
    assert visible_errors(records)  # renamed in 1.21.2
    old = world("1.20.4")
    assert old.typed("attribute Player1 minecraft:generic.max_health get")[0].value == 20
    assert old.typed(
        "attribute Player1 generic.movement_speed modifier add "
        "01020304-0506-0708-090a-0b0c0d0e0f10 fast 0.5 multiply_base"
    )[0].success
    assert old.typed("attribute Player1 generic.movement_speed get 100")[0].value == 15
    emulator.typed("summon marker ~ ~ ~ {Tags:[m]}")
    _, records = emulator.typed("attribute @e[tag=m,limit=1] max_health get")
    assert visible_errors(records) == ["Marker is not a valid entity for this command"]


def test_riding_and_execute_on(world):
    emulator = world()
    emulator.typed('summon pig ~ ~ ~ {Tags:[v],Passengers:[{id:"minecraft:zombie",Tags:[r]}]}')
    assert emulator.typed("execute as @e[tag=v] on passengers if entity @s[tag=r]")[0].value == 1
    assert emulator.typed("execute as @e[tag=r] on vehicle if entity @s[tag=v]")[0].value == 1
    data = emulator.world.entities[-2].data()
    assert data["Passengers"][0]["id"] == "minecraft:zombie"
    result, records = emulator.typed("ride @e[tag=r,limit=1] dismount")
    assert chat(records) == ["Zombie stopped riding Pig"]
    _, records = emulator.typed("ride @e[tag=r,limit=1] dismount")
    assert visible_errors(records) == ["Zombie is not riding any vehicle"]
    assert emulator.typed("ride Player1 mount @e[tag=v,limit=1]")[0].success
    _, records = emulator.typed("ride @e[tag=v,limit=1] mount Player1")
    assert visible_errors(records) == ["Players can't be ridden"]
    emulator.typed("tp @e[tag=v] 10 0 0")
    assert emulator.world.players[0].position == [10.0, 0.0, 0.0]
    emulator.typed("tp Player1 0 0 0")
    assert emulator.world.players[0].vehicle is None
    emulator.typed("summon wolf ~ ~ ~ {Tags:[w]}")
    wolf = emulator.world.entities[-1]
    from datapack_emulator.emulator.runtime.world import uuid_to_ints

    wolf.nbt["Owner"] = uuid_to_ints(emulator.world.players[0].uuid)
    assert emulator.typed("execute as @e[tag=w] on owner if entity @s[type=player]")[0].value == 1
    assert not emulator.typed("execute as @e[tag=w] on attacker run say x")[0].success


def test_bossbars(world):
    emulator = world()
    result, records = emulator.typed('bossbar add test:bar {"text":"Boss"}')
    assert chat(records) == ["Created custom bossbar [Boss]"]
    assert emulator.typed("bossbar set test:bar max 50")[0].value == 50
    assert emulator.typed("bossbar set test:bar value 20")[0].value == 20
    _, records = emulator.typed("bossbar set test:bar value 20")
    assert visible_errors(records) == ["Nothing changed. That's already the value of this bossbar"]
    emulator.typed("execute store result bossbar test:bar value run scoreboard objectives list")
    assert emulator.typed("bossbar get test:bar value")[0].value == 0
    assert emulator.typed("bossbar set test:bar players @a")[0].value == 1
    assert emulator.typed("bossbar get test:bar players")[0].value == 1
    assert emulator.typed("bossbar get test:bar visible")[0].value == 1
    assert emulator.typed("bossbar list")[0].value == 1
    _, records = emulator.typed("bossbar get test:none max")
    assert visible_errors(records) == ["No bossbar exists with the ID 'test:none'"]
    assert emulator.typed("bossbar remove test:bar")[0].success


def test_items_age_merge_despawn_and_get_picked_up(world):
    emulator = world()
    emulator.typed(
        'summon item 5 0 5 {Item:{id:"minecraft:stone",count:2},PickupDelay:100,Tags:[small]}'
    )
    emulator.typed('summon item 5.6 0.1 5 {Item:{id:"minecraft:stone",count:3},PickupDelay:10}')
    for _ in range(39):
        emulator.run_tick()
    assert len([e for e in emulator.world.entities if e.type == "minecraft:item"]) == 2
    emulator.run_tick()  # a still item looks for neighbours every 40 ticks
    items = [e for e in emulator.world.entities if e.type == "minecraft:item"]
    # the bigger stack takes the smaller one
    assert len(items) == 1 and items[0].nbt["Item"]["count"] == 5 and not items[0].tags
    assert items[0].nbt["PickupDelay"] == 59  # the larger delay, then its own tick
    items[0].nbt["Age"] = 5999
    emulator.run_tick()
    assert not any(e.type == "minecraft:item" for e in emulator.world.entities)
    emulator.typed('summon item 0 0 0 {Item:{id:"minecraft:apple",count:1},PickupDelay:1}')
    emulator.run_tick()
    emulator.run_tick()
    assert emulator.world.players[0].inventory.get("container.0").id == "minecraft:apple"
    emulator.typed('summon item 0 0 0 {Item:{id:"minecraft:apple",count:1},Age:-32768}')
    item = emulator.world.entities[-1]
    item.nbt["PickupDelay"] = 32767
    for _ in range(3):
        emulator.run_tick()
    assert item in emulator.world.entities and item.nbt["Age"] == -32768


def test_effect_updates_immunities_and_hidden_effects(world):
    emulator = world()
    emulator.typed("effect give Player1 speed infinite")
    assert emulator.typed("effect give Player1 speed infinite 0 true")[0].success
    _, records = emulator.typed("effect give Player1 speed 10 0 maybe")
    assert visible_errors(records)
    emulator.typed("effect clear Player1")
    emulator.typed("effect give Player1 speed 10 0")
    assert emulator.typed("effect give Player1 speed 2 1")[0].success
    effect = emulator.world.players[0].living.effects["minecraft:speed"]
    assert effect.amplifier == 1 and effect.hidden is not None
    for _ in range(40):
        emulator.run_tick()
    effect = emulator.world.players[0].living.effects["minecraft:speed"]
    assert effect.amplifier == 0 and 0 < effect.duration < 200
    emulator.typed("summon zombie ~ ~ ~ {Tags:[z]}")
    emulator.typed("summon spider ~ ~ ~ {Tags:[s]}")
    assert not emulator.typed("effect give @e[tag=z] regeneration")[0].success
    assert emulator.typed("effect give @e[type=!item] poison 10 1")[0].value == 1
    assert emulator.typed("effect give Player1 instant_health")[0].success
    assert not emulator.typed("effect give Player1 instant_health")[0].success


def test_instant_damage_follows_java_arithmetic(world):
    emulator = world()
    emulator.typed("effect give Player1 instant_damage 1 255")
    emulator.run_tick()
    assert emulator.world.players[0].data()["Health"] == 20.0
    emulator.typed("gamemode creative Player1")
    emulator.typed("effect give Player1 instant_damage")
    emulator.run_tick()
    assert emulator.world.players[0].data()["Health"] == 20.0


def test_damage_cooldown_armor_stands_and_numbers(world):
    emulator = world()
    assert emulator.typed("damage Player1 1")[0].success
    _, records = emulator.typed("damage Player1 1")
    assert visible_errors(records) == ["Target is invulnerable to the given damage type"]
    assert emulator.typed("damage Player1 3")[0].success  # only the difference hurts
    assert emulator.world.players[0].data()["Health"] == 17.0
    emulator.typed("summon armor_stand ~ ~ ~ {Tags:[a]}")
    assert not emulator.typed("damage @e[tag=a,limit=1] 1000")[0].success
    assert emulator.typed("damage @e[tag=a,limit=1] 1000 minecraft:player_attack")[0].success
    for _ in range(20):
        emulator.run_tick()
    _, records = emulator.typed("damage Player1 0.00001")
    assert chat(records) == ["Applied 1.0E-5 damage to Player1"]
    emulator.typed("scoreboard objectives add hp health")
    emulator.typed("damage Player1 1000")
    assert emulator.world.scoreboard.get("Player1", "hp") == 20


def test_attributes_per_kind_ranges_and_versions(world):
    emulator = world()
    emulator.typed("summon pig ~ ~ ~ {Tags:[p]}")
    _, records = emulator.typed("attribute @e[tag=p,limit=1] attack_damage get")
    assert visible_errors(records) == ["Entity Pig has no attribute Attack Damage"]
    emulator.typed("attribute Player1 max_health base set 5000")
    assert emulator.typed("attribute Player1 max_health get")[0].value == 1024
    assert emulator.typed("attribute Player1 max_health get 1e30")[0].value == 2147483647
    emulator.typed("summon zombie ~ ~ ~ {Tags:[z]}")
    emulator.typed("attribute @e[tag=z,limit=1] max_health base set 40")
    zombie = [e for e in emulator.world.entities if "z" in e.tags][0]
    assert zombie.data()["Health"] == 20.0  # raising the maximum does not heal
    assert "NoAI" not in zombie.data()
    old = world("1.16.5")
    assert not old.typed("attribute Player1 generic.max_health base reset")[0].success
    mid = world("1.20.5")
    assert mid.typed(
        'attribute Player1 generic.movement_speed modifier add 1-2-3-4-5 "my mod" 2 add_value'
    )[0].success
    modifiers = mid.world.players[0].data(mid.version)["attributes"]
    speed = [a for a in modifiers if a["id"].endswith("movement_speed")][0]
    assert speed["modifiers"][0] == {
        "uuid": [1, 131075, 262144, 5],
        "name": "my mod",
        "amount": 2.0,
        "operation": "add_value",
    }
    assert not mid.typed(
        "attribute Player1 generic.movement_speed modifier add foo:bar 2 add_value"
    )[0].success


def test_relations_and_store_bossbar_follow_vanilla(world):
    emulator = world()
    emulator.typed('summon armor_stand ~ ~ ~ {Tags:[s],Passengers:[{id:"minecraft:zombie"}]}')
    assert emulator.typed("execute as @e[tag=s] on controller run say x")[0].value == 0
    emulator.typed('summon pig ~ ~ ~ {Tags:[p],Passengers:[{id:"minecraft:zombie"}]}')
    assert emulator.typed("execute as @e[tag=p] on controller run say x")[0].success
    from datapack_emulator.emulator.runtime.world import uuid_to_ints

    owner = uuid_to_ints(emulator.world.players[0].uuid)
    emulator.typed("summon arrow ~ ~ ~ {Tags:[a]}")
    arrow = emulator.world.entities[-1]
    arrow.nbt["Owner"] = owner
    assert not emulator.typed("execute as @e[tag=a] on owner run say x")[0].success
    assert emulator.typed("execute as @e[tag=a] on origin run say x")[0].success
    emulator.typed("bossbar add t:b x")
    emulator.typed("execute store result bossbar t:b value run scoreboard players set #x o -5")
    _, records = emulator.typed("execute store result bossbar t:nope value run say hi")
    assert visible_errors(records) == ["No bossbar exists with the ID 't:nope'"]
    assert "[Server] hi" not in chat(records)
    _, records = emulator.typed("bossbar add t:c")
    assert visible_errors(records)
    emulator.typed('summon pig ~ ~ ~ {Tags:[q],Passengers:[{id:"minecraft:player"},{id:"nope:x"}]}')
    pig = [e for e in emulator.world.entities if "q" in e.tags][0]
    assert pig.passengers == []


def test_items_respect_their_owner_and_boats_are_not_living(world):
    emulator = world()
    emulator.typed(
        'summon item 0 0 0 {Item:{id:"minecraft:dirt",count:1},Owner:[I;1,2,3,4],PickupDelay:0}'
    )
    emulator.run_tick()
    assert emulator.world.players[0].inventory.get("container.0") is None
    old = world("1.20.4")
    old.typed("summon boat")
    assert old.world.entities[-1].living is None
