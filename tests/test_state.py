"""Server state: time, weather, difficulty, world border, random, teams,
game modes, experience, forceload and spawn points."""

from __future__ import annotations

import pytest
from conftest import chat, visible_errors

from datapack_emulator.emulator import Datapack, Emulator


@pytest.fixture
def world(make_pack):
    pack = Datapack.load(make_pack({"data/test/function/tick.mcfunction": "\n"}))

    def make(version: str = "1.21.4", players: int = 2):
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


def test_time_advances_and_answers(world):
    emulator = world()
    assert emulator.world.state.day_time == 1
    result, records = emulator.typed("time query daytime")
    assert result.value == 1 and chat(records) == ["The time is 1"]
    assert emulator.typed("time set noon")[0].value == 6000
    assert emulator.typed("time add 1d")[0].value == 6000
    assert emulator.typed("time query day")[0].value == 1
    assert emulator.typed("time query gametime")[0].value == 1
    emulator.typed("gamerule doDaylightCycle false")
    emulator.run_tick()
    assert emulator.world.state.day_time == 30000
    result, records = emulator.typed("time set -5")
    assert visible_errors(records) == ["The tick count must be non-negative"] or not result.success


def test_weather_and_difficulty(world):
    emulator = world()
    result, records = emulator.typed("weather thunder 10s")
    assert chat(records) == ["Set the weather to rain & thunder"]
    assert emulator.world.state.weather == "thunder" and result.value == 200
    assert world("1.18.2").typed("weather rain 10")[0].value == 200  # seconds back then
    result, records = emulator.typed("difficulty")
    assert result.value == 1 and chat(records) == ["The difficulty is Easy"]
    assert emulator.typed("difficulty hard")[0].value == 0
    result, records = emulator.typed("difficulty hard")
    assert visible_errors(records) == ["The difficulty did not change; it is already set to Hard"]
    assert emulator.typed("weather clear")[0].value == -1
    _, records = emulator.typed("weather clear 0")
    assert visible_errors(records) == ["The tick count must not be less than 1: found 0"]


def test_world_border(world):
    emulator = world()
    result, records = emulator.typed("worldborder get")
    assert result.value == 59999968
    assert chat(records) == ["The world border is currently 59999968 block(s) wide"]
    result, records = emulator.typed("worldborder set 100")
    assert chat(records) == ["Set the world border to 100.0 block(s) wide"]
    assert result.value == 100 - 59999968
    assert emulator.typed("worldborder add 20 5")[0].value == 20
    assert emulator.typed("worldborder get")[0].value == 120
    _, records = emulator.typed("worldborder set 0.5")
    assert visible_errors(records) == ["World border cannot be smaller than 1 block wide"]
    _, records = emulator.typed("worldborder center 10 20")
    assert chat(records) == ["Set the center of the world border to 10.50, 20.50"]
    assert emulator.typed("worldborder damage amount 1")[0].success
    assert not emulator.typed("worldborder damage amount 1")[0].success
    assert emulator.typed("worldborder warning distance 9")[0].value == 9


def test_random_values_are_seeded_and_in_range(world):
    first, second = world(), world()
    values = [first.typed("random value 1..6")[0].value for _ in range(20)]
    assert values == [second.typed("random value 1..6")[0].value for _ in range(20)]
    assert all(1 <= value <= 6 for value in values)
    _, records = first.typed("random value 5..5")
    assert visible_errors(records) == ["The range of the random value must be at least 2"]
    result, records = first.typed("random roll 1..2 test:seq")
    assert chat(records)[0].startswith("Server rolled ") and chat(records)[0].endswith(
        "(from 1 to 2)"
    )
    sequence = [first.typed("random value 1..1000 test:seq")[0].value for _ in range(3)]
    first.typed("random reset test:seq")
    again = [first.typed("random value 1..1000 test:seq")[0].value for _ in range(4)]
    assert sequence != again[1:] or sequence == again[:3]
    result, records = first.typed("random reset *")
    assert chat(records) == ["Reset 1 random sequence(s)"]


def test_teams_and_team_selectors(world):
    emulator = world()
    result, records = emulator.typed('team add red {"text":"Red Team"}')
    assert chat(records) == ["Created team [Red Team]"]
    assert emulator.typed("team join red Player1")[0].value == 1
    emulator.typed("summon pig")
    assert emulator.typed("team join red @e[type=pig]")[0].success
    assert emulator.typed("execute if entity @e[team=red]")[0].value == 2
    assert emulator.typed("execute if entity @e[team=!red]")[0].value == 1
    assert emulator.typed("execute if entity @e[team=]")[0].value == 1
    assert emulator.typed("execute if entity @e[team=!]")[0].value == 2
    _, records = emulator.typed("team list red")
    assert chat(records)[0].startswith("Team [Red Team] has 2 member(s): Player1, ")  # then a UUID
    assert emulator.typed("team modify red color red")[0].success
    _, records = emulator.typed("team modify red color red")
    assert visible_errors(records) == ["Nothing changed. That team already has that color"]
    _, records = emulator.typed("team modify red friendlyFire false")
    assert chat(records) == ["Disabled friendly fire for team [Red Team]"]
    _, records = emulator.typed("team modify blue color red")
    assert visible_errors(records) == ["Unknown team 'blue'"]
    result, records = emulator.typed("execute as Player1 run teammsg hello")
    assert chat(records) == ["-> [Red Team] <Player1> hello"] and result.value == 1
    _, records = emulator.typed("execute as Player2 run tm hi")
    assert visible_errors(records) == ["You must be on a team to message your team"]
    assert emulator.typed("team leave Player1")[0].success
    assert emulator.typed("team empty red")[0].value == 1
    assert emulator.typed("team remove red")[0].success
    assert emulator.typed("team list")[0].value == 0


def test_game_modes_and_selector(world):
    emulator = world()
    result, records = emulator.typed("gamemode creative Player1")
    assert result.value == 1 and chat(records) == ["Set Player1's game mode to Creative Mode"]
    assert emulator.typed("gamemode creative Player1")[0].value == 0
    _, records = emulator.typed("execute as Player2 run gamemode spectator")
    assert chat(records) == ["Set own game mode to Spectator Mode"]
    assert emulator.typed("execute if entity @a[gamemode=creative]")[0].value == 1
    assert emulator.typed("execute if entity @a[gamemode=!survival]")[0].value == 2
    player = emulator.world.players[0]
    assert player.data()["playerGameType"] == 1
    _, records = emulator.typed("gamemode creative")
    assert visible_errors(records) == ["A player is required to run this command here"]


def test_experience_levels_points_and_criteria(world):
    emulator = world()
    emulator.typed("scoreboard objectives add lvl level")
    emulator.typed("scoreboard objectives add pts xp")
    result, records = emulator.typed("xp add Player1 10")
    assert chat(records) == ["Gave 10 experience points to Player1"]
    # 7 points make level 1, the other 3 count towards level 2
    assert emulator.typed("experience query Player1 levels")[0].value == 1
    assert emulator.typed("experience query Player1 points")[0].value == 3
    assert emulator.world.scoreboard.get("Player1", "lvl") == 1
    assert emulator.world.scoreboard.get("Player1", "pts") == 10
    assert emulator.typed("xp add @a 5 levels")[0].value == 2
    assert emulator.typed("execute if entity @a[level=6]")[0].value == 1
    assert emulator.typed("execute if entity @a[level=5..]")[0].value == 2
    _, records = emulator.typed("xp set Player1 1000 points")
    assert visible_errors(records) == [
        "Cannot set experience points above the maximum points for the player's current level"
    ]
    assert emulator.typed("xp set Player1 30 levels")[0].success
    assert emulator.world.players[0].data()["XpLevel"] == 30


def test_rotation_selectors(world):
    emulator = world()
    emulator.typed("summon pig ~ ~ ~ {Rotation:[175f,-40f]}")
    assert emulator.typed("execute if entity @e[y_rotation=170..-170]")[0].value == 1
    assert emulator.typed("execute if entity @e[y_rotation=-10..10]")[0].value == 2
    # an open end is 0 (or 359): ..-30 wraps around through the players' 0
    assert emulator.typed("execute if entity @e[x_rotation=..-30]")[0].value == 3
    assert emulator.typed("execute if entity @e[x_rotation=-50..-30]")[0].value == 1


def test_forceload_seed_list_and_spawn(world):
    emulator = world()
    result, records = emulator.typed("forceload add 0 0 20 20")
    assert result.value == 4
    assert chat(records) == [
        "Marked 4 chunks in minecraft:overworld from [0, 0] to [1, 1] to be force loaded"
    ]
    assert not emulator.typed("forceload add 0 0")[0].success
    assert emulator.typed("forceload query 5 5")[0].success
    assert emulator.typed("forceload query")[0].value == 4
    assert emulator.typed("forceload remove all")[0].success
    assert emulator.typed("seed")[0].value == 0
    result, records = emulator.typed("list")
    assert chat(records) == ["There are 2 of a max of 20 players online: Player1, Player2"]
    assert emulator.typed("setworldspawn 1 70 2")[0].success
    assert emulator.world.state.spawn == (1, 70, 2)
    assert emulator.typed("spawnpoint Player1 5 64 5")[0].success
    assert emulator.world.players[0].data()["SpawnX"] == 5


def test_state_shows_in_the_world_dump(world):
    from datapack_emulator.emulator.analysis.world_view import state_text, world_to_dict

    emulator = world()
    emulator.typed("team add blue")
    emulator.typed("weather rain")
    data = world_to_dict(emulator.world)
    assert data["state"]["weather"] == "rain" and "blue" in data["state"]["teams"]
    assert "weather     rain" in state_text(emulator.world).replace("  ", " ") or "rain" in (
        state_text(emulator.world)
    )


def test_numbers_are_parsed_like_brigadier(world):
    emulator = world()
    for line in (
        "time set inf",
        "worldborder damage amount nan",
        "worldborder set nan",
        "worldborder set 1_000",
        "worldborder damage amount -5",
        "worldborder warning distance -5",
        "tick rate 20000",
        "random value 5..1",
        "random reset foo 1 maybe",
        "tick",
        "tick step",
        'team add t {"text":',
        "team add bad/name",
    ):
        result, records = emulator.typed(line)
        assert not result.success and visible_errors(records), line
    assert emulator.typed("time add 1.5")[0].value == 3  # rounded, not truncated
    _, records = emulator.typed("random value 5..1")
    assert visible_errors(records) == ["Min cannot be bigger than max"]
    _, records = emulator.typed("random value ..5")
    assert visible_errors(records) == ["The range of the random value must be at most 2147483646"]


def test_experience_follows_vanilla_totals(world):
    emulator = world()
    emulator.typed("xp set Player1 20 levels")
    emulator.typed("xp add Player1 31 points")
    player = emulator.world.players[0]
    assert player.nbt["XpTotal"] == 31 and player.nbt["XpLevel"] == 20
    emulator.typed("xp add Player1 -10 points")
    assert player.nbt["XpTotal"] == 21
    emulator.typed("xp add Player1 -15 levels")
    assert player.nbt["XpLevel"] == 5
    emulator.typed("xp add Player1 -100 levels")
    assert player.nbt["XpLevel"] == 0 and player.nbt["XpTotal"] == 0


def test_feedback_and_results_match_vanilla(world):
    emulator = world()
    assert emulator.typed("execute store success score #g x run gamemode survival Player1")[
        0
    ].success
    emulator.typed("team add t")
    _, records = emulator.typed('team modify t displayName "Blue"')
    assert chat(records) == ["Updated the name of team [Blue]"]
    _, records = emulator.typed("team modify t nametagVisibility never")
    assert chat(records) == ['Nametag visibility for team [Blue] is now "Never"']
    assert emulator.typed("team modify t prefix x")[0].value == 1
    assert emulator.typed("team modify t color DARK_RED")[0].success
    emulator.typed("team join t @a")
    _, records = emulator.typed("execute as Player1 run teammsg hi")
    assert chat(records) == ["-> [Blue] <Player1> hi", "[Blue] <Player1> hi"]
    emulator.typed("forceload add -20 0 20 0")
    _, records = emulator.typed("forceload query")
    assert chat(records)[0].endswith("[0, 0], [1, 0], [-2, 0], [-1, 0]")
    assert not emulator.typed("forceload add 30000000 0")[0].success
    _, records = emulator.typed("worldborder center 10.25 0")
    assert chat(records) == ["Set the center of the world border to 10.25, 0.50"]
    assert emulator.typed("setworldspawn 0 0 0 270")[0].success
    assert emulator.world.state.spawn_angle == -90.0
    emulator.typed("summon pig")
    _, records = emulator.typed("spawnpoint @e[type=pig]")
    assert visible_errors(records)
    emulator.typed("random value 1..6 a:b")
    emulator.typed("random reset * 42")
    assert emulator.world.state.sequence_defaults == (42, True, True)
    assert emulator.typed("execute if entity @a[gamemode=foo]")[0].value == 0
