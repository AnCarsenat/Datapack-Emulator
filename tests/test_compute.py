"""``/compute`` and ``data modify … compute`` (26.3), and the context int and
float number providers they evaluate."""

from __future__ import annotations

import math
import random

import pytest
from conftest import chat, game_errors

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.runtime import numbers
from datapack_emulator.emulator.runtime.numbers import (
    NumberContext,
    ProviderError,
    ProviderFormatError,
    float_unsafe,
    get_float,
    get_int,
    int_unsafe,
    java_float_text,
)
from datapack_emulator.emulator.runtime.predicates import Context

PACK_26_3 = {"pack": {"description": "test", "pack_format": 121}}


@pytest.fixture
def world(make_pack):
    files = {
        "data/test/function/tick.mcfunction": "\n",
        "data/test/context_int_provider/seven.json": 7,
        "data/test/context_int_provider/double_a.json": {
            "type": "minecraft:mul",
            "inputs": [
                2,
                {
                    "type": "minecraft:score",
                    "target": {"type": "fixed", "name": "#a"},
                    "score": "t",
                },
            ],
        },
        "data/test/context_float_provider/half.json": {"type": "minecraft:constant", "value": 0.5},
        "data/test/predicate/always.json": {"type": "minecraft:random_chance", "chance": 1.0},
    }
    pack = Datapack.load(make_pack(files, mcmeta=PACK_26_3))

    def make(version: str = "26.3"):
        emulator = Emulator(pack, version=version, players=1)
        emulator.start()
        emulator.run_tick()

        def typed(line: str):
            before = len(emulator.output.records)
            result, _ = emulator.run_typed(line)
            return result, emulator.output.records[before:]

        def score(holder: str, objective: str = "t"):
            return emulator.world.scoreboard.get(holder, objective)

        emulator.typed = typed
        emulator.score = score
        emulator.typed("scoreboard objectives add t dummy")
        return emulator

    return make


def value(emulator, line: str) -> int | None:
    result, _ = emulator.typed(line)
    return result.value if result.success else None


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def test_repro_from_the_notes_gives_ten(world):
    emulator = world()
    emulator.typed("scoreboard players set #a t 7")
    emulator.typed(
        "execute store result score #r t run compute default integer "
        '{type:"minecraft:add",inputs:[{type:"minecraft:score",'
        'target:{type:"minecraft:fixed",name:"#a"},score:"t"},3]}'
    )
    result, records = emulator.typed("scoreboard players get #r t")
    assert result.value == 10
    assert emulator.score("#r") == 10


def test_feedback_and_named_providers(world):
    emulator = world()
    result, records = emulator.typed("compute default integer {type:constant,value:4}")
    assert result.success and result.value == 4
    assert chat(records) == ["Number provider returned value 4"]
    result, records = emulator.typed("compute default integer test:seven")
    assert result.value == 7 and chat(records) == ["test:seven returned value 7"]
    emulator.typed("scoreboard players set #a t 21")
    assert value(emulator, "compute default integer test:double_a") == 42
    # ids inside a provider, and a bare number file
    assert value(emulator, 'compute default integer {type:add,inputs:["test:seven",1]}') == 8
    # an id comes first in the argument grammar: a bare number is an id
    result, records = emulator.typed("compute default integer 5")
    assert not result.success
    assert game_errors(records) == [
        "Can't find element 'minecraft:5' in registry 'minecraft:context_int_provider'"
    ]


def test_float_results_are_scaled_and_floored(world):
    emulator = world()
    result, records = emulator.typed("compute default float {type:constant,value:2.5} 10")
    assert result.value == 25
    assert chat(records) == ["Number provider returned value 2.5 (rounded to 25)"]
    assert value(emulator, "compute default float {type:constant,value:-2.5}") == -3
    assert value(emulator, "compute default float {type:constant,value:3.0}") == 3
    result, records = emulator.typed("compute default float {type:div,left:1,right:3} 10")
    assert result.value == 3
    assert chat(records) == ["Number provider returned value 0.33333334 (rounded to 3)"]
    assert value(emulator, "compute default float test:half 4") == 2
    # the integer form takes no scale
    assert value(emulator, "compute default integer {type:constant,value:1} 2") is None


def test_store_result_of_compute(world):
    emulator = world()
    emulator.typed(
        "execute store result storage s:x v double 0.5 run compute default float "
        "{type:constant,value:7.0}"
    )
    assert emulator.world.storage["s:x"]["v"] == 3.5


def test_entity_and_block_sources(world):
    emulator = world()
    emulator.typed('summon minecraft:pig 0 64 0 {Tags:["p"]}')
    emulator.typed("scoreboard players set @e[tag=p] t 4")
    emulator.typed("scoreboard players set Player1 t 9")
    target = '{type:score,target:"target_entity",score:"t"}'
    this = '{type:score,target:{type:"context",target:"this"},score:"t",fallback:-1}'
    assert (
        value(emulator, f"execute as Player1 run compute entity @e[tag=p,limit=1] integer {target}")
        == 4
    )
    assert (
        value(emulator, f"execute as Player1 run compute entity @e[tag=p,limit=1] integer {this}")
        == 9
    )
    # without an executor `this` has no score: the fallback
    assert value(emulator, f"compute default integer {this}") == -1
    result, records = emulator.typed(f"compute entity @e[tag=nothing] integer {target}")
    assert not result.success and game_errors(records) == ["No entity was found"]
    assert value(emulator, "compute block 0 64 0 integer {type:constant,value:3}") == 3


def test_version_gate(world):
    result, records = world("26.2").typed("compute default integer {type:constant,value:1}")
    assert not result.success
    assert any("Unknown or incomplete command" in text for text in game_errors(records))
    assert value(world(), "compute default integer {type:constant,value:1}") == 1


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("integer {type:div,left:1,right:0}", "/ by zero"),
        ("integer {type:mod,left:1,right:0}", "/ by zero"),
        ("integer {type:floor_div,left:1,right:0}", "/ by zero"),
        ("integer {type:floor_mod,left:1,right:0}", "/ by zero"),
        (
            "integer {type:add,inputs:[2147483647,1]}",
            "Value 2147483648 can't be safely converted to int",
        ),
        (
            "integer {type:mul,inputs:[65536,65536]}",
            "Value 4294967296 can't be safely converted to int",
        ),
        ("integer {type:sub,left:-2147483648,right:1}", "integer overflow"),
        ("integer {type:negate,input:-2147483648}", "integer overflow"),
        (
            "integer {type:abs,input:-2147483648}",
            "Overflow to represent absolute value of Integer.MIN_VALUE",
        ),
        ("integer {type:pow,base:2,exponent:-1}", "negative exponent"),
        ("integer {type:floor_div,left:-2147483648,right:-1}", "integer overflow"),
        ("integer {type:pow,base:2,exponent:31}", "integer overflow"),
        ("integer {type:pow,base:0,exponent:0}", "Result of 0 to the power of 0 is undefined"),
        ("integer {type:avg,inputs:[]}", "/ by zero"),
        (
            "integer {type:from_float,input:{type:div,left:1,right:0}}",
            "Value Infinity can't be safely converted to int",
        ),
        (
            "integer {type:from_float,input:3e9}",
            "Value 3000000000 can't be safely converted to int",
        ),
        ("float {type:div,left:1,right:0}", "Infinity"),
        ("float {type:div,left:-1,right:0}", "-Infinity"),
        ("float {type:div,left:0,right:0}", "NaN"),
        ("float {type:mod,left:1,right:0}", "NaN"),
        ("float {type:pow,base:0,exponent:0}", "NaN"),
        ("float {type:sqrt,input:-1}", "NaN"),
        ("float {type:mul,inputs:[3e38,10]}", "Infinity"),
    ],
)
def test_compute_fails_on_errors(world, line, reason):
    result, records = world().typed(f"compute default {line}")
    assert not result.success and result.value == 0
    assert game_errors(records) == [f"Number provider returned invalid value ({reason})"]


def test_named_failure_and_bad_providers(world):
    emulator = world()
    result, records = emulator.typed("compute default integer {type:nope}")
    assert not result.success
    assert game_errors(records) == [
        "Failed to parse structure: Unknown int provider type: minecraft:nope"
    ]
    _, records = emulator.typed("compute default integer {inputs:[1]}")
    assert game_errors(records) == ["Failed to parse structure: a number provider needs a type"]
    _, records = emulator.typed("compute default integer {type:add}")
    assert game_errors(records)[0].startswith("Failed to parse structure: No key inputs")
    # sqrt is a float provider only
    assert not emulator.typed("compute default integer {type:sqrt,input:4}")[0].success


def test_execute_store_after_a_failure_stores_zero(world):
    emulator = world()
    emulator.typed("scoreboard players set #r t 5")
    emulator.typed(
        "execute store result score #r t run compute default integer {type:div,left:1,right:0}"
    )
    assert emulator.score("#r") == 0


# ---------------------------------------------------------------------------
# data modify … compute
# ---------------------------------------------------------------------------


def test_data_modify_compute(world):
    emulator = world()
    emulator.typed("scoreboard players set #a t 6")
    result, _ = emulator.typed(
        "data modify storage s:x v set compute default integer "
        '{type:mul,inputs:[{type:score,target:{type:fixed,name:"#a"},score:"t"},7]}'
    )
    assert result.success
    assert emulator.world.storage["s:x"]["v"] == 42
    emulator.typed("data modify storage s:x f set compute default float {type:div,left:1,right:4}")
    assert emulator.world.storage["s:x"]["f"] == 0.25
    emulator.typed("data modify storage s:x l set value []")
    emulator.typed("data modify storage s:x l append compute default integer test:seven")
    emulator.typed(
        "data modify storage s:x l insert 0 compute default integer {type:constant,value:1}"
    )
    assert emulator.world.storage["s:x"]["l"] == [1, 7]
    # outside /compute an error or a non-finite result is 0, and the command works
    result, _ = emulator.typed(
        "data modify storage s:x v set compute default integer {type:div,left:1,right:0}"
    )
    assert result.success and emulator.world.storage["s:x"]["v"] == 0
    emulator.typed("data modify storage s:x f set compute default float {type:div,left:1,right:0}")
    assert emulator.world.storage["s:x"]["f"] == 0.0
    # a provider that does not decode is an error, not 0
    assert not emulator.typed("data modify storage s:x v set compute default integer {type:nope}")[
        0
    ].success


def test_data_modify_compute_stores_typed_tags(world):
    """``FloatTag.valueOf`` / ``IntTag.valueOf``: the SNBT says ``f``, and the
    tag matches a float filter."""
    emulator = world()
    emulator.typed("data modify storage s:x v set compute default float {type:add,inputs:[1,0.5]}")
    emulator.typed("data modify storage s:x i set compute default integer 2147483647")
    emulator.typed("data modify storage s:x i set compute default integer {type:add,inputs:[3,4]}")
    _, records = emulator.typed("data get storage s:x")
    assert chat(records) == ["Storage s:x has the following contents: {v: 1.5f, i: 7}"]
    assert emulator.typed("execute if data storage s:x {v:1.5f}")[0].success
    assert not emulator.typed("execute if data storage s:x {v:1.5d}")[0].success
    assert emulator.typed("execute if data storage s:x {i:7}")[0].success
    # 32-bit rounding: 0.1 + 0.2 is the float 0.3, printed as such
    emulator.typed(
        "data modify storage s:x v set compute default float {type:add,inputs:[0.1,0.2]}"
    )
    _, records = emulator.typed("data get storage s:x v")
    assert chat(records) == ["Storage s:x has the following contents: 0.3f"]


# ---------------------------------------------------------------------------
# score and storage, with fallbacks
# ---------------------------------------------------------------------------


def test_is_this_storage_value_a_number(world):
    """Two storage providers with fallbacks 0 and 1 agree only on a number."""
    emulator = world()

    def is_number(snbt: str) -> bool:
        emulator.typed(f"data modify storage s:d v set value {snbt}")
        for holder, fallback in (("#n0", 0), ("#n1", 1)):
            emulator.typed(
                f"execute store result score {holder} t run compute default integer "
                f'{{type:storage,storage:"s:d",path:"v",fallback:{fallback}}}'
            )
        return emulator.typed("execute if score #n0 t = #n1 t")[0].success

    assert is_number("5")
    assert is_number("0")
    assert is_number("2.5d")
    assert not is_number('"5"')
    assert not is_number("[1, 2]")
    assert not is_number("{a: 1}")
    emulator.typed("data remove storage s:d v")
    for holder, fallback in (("#n0", 0), ("#n1", 1)):
        emulator.typed(
            f"execute store result score {holder} t run compute default integer "
            f'{{type:storage,storage:"s:d",path:"v",fallback:{fallback}}}'
        )
    assert not emulator.typed("execute if score #n0 t = #n1 t")[0].success


def test_score_fallback(world):
    emulator = world()
    unset = (
        '{type:score,target:{type:fixed,name:"#none"},score:"t",fallback:{type:constant,value:12}}'
    )
    assert value(emulator, f"compute default integer {unset}") == 12
    missing_objective = '{type:score,target:{type:fixed,name:"#a"},score:"nope"}'
    assert value(emulator, f"compute default integer {missing_objective}") == 0
    emulator.typed("scoreboard players set #a t -3")
    fixed = '{type:score,target:{type:fixed,name:"#a"},score:"t",fallback:12}'
    assert value(emulator, f"compute default integer {fixed}") == -3


def test_storage_numbers_convert_like_java(world):
    emulator = world()
    emulator.typed("data modify storage s:d v set value 2.9d")
    storage = '{type:storage,storage:"s:d",path:"v"}'
    assert value(emulator, f"compute default integer {storage}") == 2
    assert value(emulator, f"compute default float {storage} 10") == 29
    emulator.typed("data modify storage s:d v set value -1e20d")
    assert value(emulator, f"compute default integer {storage}") == -(2**31)


# ---------------------------------------------------------------------------
# each provider family, evaluated directly
# ---------------------------------------------------------------------------


def _context(**fields) -> NumberContext:
    return NumberContext(loot=Context(rng=random.Random(1)), **fields)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (5, 5),
        (2.9, 2),  # Codec.INT reads a decimal with intValue()
        ({"type": "constant", "value": -4}, -4),
        ({"type": "add", "inputs": [1, 2, 3]}, 6),
        ({"type": "add", "inputs": []}, 0),
        ({"type": "mul", "inputs": [2, -3, 4]}, -24),
        ({"type": "min", "inputs": [5, -2, 3]}, -2),
        ({"type": "max", "inputs": [5, -2, 3]}, 5),
        ({"type": "min", "inputs": []}, 2**31 - 1),
        ({"type": "max", "inputs": []}, -(2**31)),
        ({"type": "avg", "inputs": [1, 2, 4]}, 2),
        ({"type": "avg", "inputs": [-1, -2, -4]}, -2),
        ({"type": "sub", "left": 3, "right": 10}, -7),
        ({"type": "div", "left": -7, "right": 2}, -3),
        ({"type": "mod", "left": -7, "right": 2}, -1),
        ({"type": "floor_div", "left": -7, "right": 2}, -4),
        ({"type": "floor_mod", "left": -7, "right": 2}, 1),
        ({"type": "div", "left": -(2**31), "right": -1}, -(2**31)),  # Java's / wraps
        ({"type": "abs", "input": -9}, 9),
        ({"type": "negate", "input": 9}, -9),
        ({"type": "pow", "base": -2, "exponent": 3}, -8),
        ({"type": "pow", "base": 0, "exponent": 5}, 0),
        ({"type": "pow", "base": 7, "exponent": 0}, 1),
        ({"type": "from_float", "input": -2.7}, -2),
        ({"type": "from_float", "input": {"type": "sqrt", "input": 16}}, 4),
        ({"type": "uniform", "min": 4, "max": 4}, 4),
        ({"type": "uniform", "min": 9, "max": 2}, 9),
        ({"type": "binomial", "n": 5, "p": 1.0}, 5),
        ({"type": "binomial", "n": 5, "p": 0.0}, 0),
        ({"type": "weighted_list", "distribution": [{"data": 3, "weight": 1}]}, 3),
        (
            {
                "type": "weighted_list",
                "distribution": [{"data": 3, "weight": 0}, {"data": 8, "weight": 2}],
            },
            8,
        ),
    ],
)
def test_int_providers(provider, expected):
    assert int_unsafe(provider, _context()) == expected


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (2.5, 2.5),
        (0.1, numbers.f32(0.1)),
        ({"type": "constant", "value": 1.5}, 1.5),
        ({"type": "add", "inputs": [0.5, 0.25]}, 0.75),
        ({"type": "mul", "inputs": [1.5, 4]}, 6.0),
        ({"type": "min", "inputs": [1.5, -4]}, -4.0),
        ({"type": "max", "inputs": [1.5, -4]}, 1.5),
        ({"type": "avg", "inputs": [1, 2]}, 1.5),
        ({"type": "length", "inputs": [3, 4]}, 5.0),
        ({"type": "sub", "left": 1, "right": 0.25}, 0.75),
        ({"type": "div", "left": 1, "right": 4}, 0.25),
        # 26.3-pre-3: Java's remainder (sign of the left side), not floor modulus
        ({"type": "mod", "left": -7, "right": 3}, -1.0),
        ({"type": "mod", "left": 7.5, "right": -2}, 1.5),
        ({"type": "pow", "base": 2, "exponent": 0.5}, numbers.f32(math.sqrt(2))),
        ({"type": "pow", "base": 0, "exponent": 2}, 0.0),
        ({"type": "sqrt", "input": 2.25}, 1.5),
        ({"type": "abs", "input": -1.5}, 1.5),
        ({"type": "negate", "input": 1.5}, -1.5),
        ({"type": "floor", "input": -1.5}, -2.0),
        ({"type": "ceil", "input": -1.5}, -1.0),
        ({"type": "round", "input": -1.5}, -1.0),
        ({"type": "round", "input": 2.5}, 3.0),
        ({"type": "truncate", "input": -1.7}, -1.0),
        ({"type": "truncate", "input": 1.7}, 1.0),
        ({"type": "sin", "input": 0}, 0.0),
        ({"type": "cos", "input": 0}, 1.0),
        ({"type": "from_int", "input": {"type": "div", "left": 7, "right": 2}}, 3.0),
        ({"type": "uniform", "min": 2, "max": 2}, 2.0),
        ({"type": "weighted_list", "distribution": [{"data": 0.5, "weight": 3}]}, 0.5),
    ],
)
def test_float_providers(provider, expected):
    assert float_unsafe(provider, _context()) == expected


def test_conditional_and_number_dispatcher():
    yes = {"type": "minecraft:random_chance", "chance": 1.0}
    no = {"type": "minecraft:random_chance", "chance": 0.0}
    conditional = {"type": "conditional", "condition": yes, "on_true": 1, "on_false": 2}
    assert int_unsafe(conditional, _context()) == 1
    assert int_unsafe({**conditional, "condition": no}, _context()) == 2
    assert int_unsafe({"type": "conditional", "condition": no, "on_true": 1}, _context()) == 0
    dispatcher = {
        "type": "number_dispatcher",
        "cases": [{"condition": no, "value": 1}, {"condition": yes, "value": 2.5}],
        "default": 9,
    }
    assert float_unsafe(dispatcher, _context()) == 2.5
    assert int_unsafe({**dispatcher, "cases": [{"condition": no, "value": 1}]}, _context()) == 9
    assert int_unsafe({"type": "number_dispatcher", "cases": []}, _context()) == 0


def test_conditional_with_a_pack_predicate(world):
    emulator = world()
    provider = '{type:conditional,condition:"test:always",on_true:5,on_false:6}'
    assert value(emulator, f"compute default integer {provider}") == 5


def test_safe_getters_return_zero():
    context = _context()
    assert get_int({"type": "div", "left": 1, "right": 0}, context) == 0
    assert get_float({"type": "div", "left": 1, "right": 0}, context) == 0.0
    assert get_float({"type": "sqrt", "input": -1}, context) == 0.0
    with pytest.raises(ProviderError):
        int_unsafe({"type": "div", "left": 1, "right": 0}, context)
    with pytest.raises(ProviderFormatError):
        get_int({"type": "nope"}, context)
    with pytest.raises(ProviderFormatError):
        get_int("test:missing", context)


def test_unmodelled_providers_are_listed():
    context = _context()
    assert float_unsafe({"type": "enchantment_level", "amount": 1}, context) == 0.0
    assert int_unsafe({"type": "environment_attribute", "attribute": "x"}, context) == 0
    assert context.unchecked == {"minecraft:enchantment_level", "minecraft:environment_attribute"}
    with pytest.raises(ProviderFormatError):  # moved to float providers in 26.3-pre-1
        int_unsafe({"type": "enchantment_level", "amount": 1}, _context())


def test_float_text_matches_java():
    assert java_float_text(numbers.f32(1 / 3)) == "0.33333334"
    assert java_float_text(1e10) == "1.0E10"
    assert java_float_text(numbers.f32(1.5e-5)) == "1.5E-5"
    assert java_float_text(25.0) == "25.0"
    assert java_float_text(math.nan) == "NaN"
    assert java_float_text(-math.inf) == "-Infinity"


def test_jar_providers_are_read_once(tmp_path):
    import zipfile

    jar = tmp_path / "client.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("data/minecraft/context_float_provider/a/b.json", "0.5")
        archive.writestr(
            "data/minecraft/context_int_provider/c.json", '{"type":"constant","value":3}'
        )
        archive.writestr("data/minecraft/loot_table/d.json", "{}")
    assert numbers.jar_provider(jar, numbers.FLOAT_REGISTRY, "a/b") == 0.5
    found = numbers.jar_provider(jar, numbers.INT_REGISTRY, "minecraft:c")
    assert found == {"type": "constant", "value": 3}
    found["value"] = 9  # callers get their own copy
    assert numbers.jar_provider(jar, numbers.INT_REGISTRY, "c")["value"] == 3
    assert numbers.jar_provider(jar, numbers.INT_REGISTRY, "a/b") is None
    assert numbers._jar_providers.cache_info().currsize >= 1


def test_float_result_compared_as_a_float(world):
    """``(float) result == value``: 2^31 saturates to 2^31 - 1, whose float
    is 2^31 again, so vanilla calls it exact."""
    emulator = world()
    result, records = emulator.typed("compute default float {type:constant,value:2147483648}")
    assert result.value == 2147483647
    assert chat(records) == ["Number provider returned value 2147483647"]
