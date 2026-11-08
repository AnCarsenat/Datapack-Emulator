"""NBT paths (vanilla NbtPathArgument) and partial NBT matching (NbtUtils.compareNbt)."""

from __future__ import annotations

import pytest

from datapack_emulator.emulator import Datapack, Emulator
from datapack_emulator.emulator.common import (
    nbt_get,
    nbt_get_all,
    nbt_matches,
    nbt_remove,
    nbt_set,
    parse_path,
    parse_snbt,
    to_snbt,
)

PACK = {"data/test/function/noop.mcfunction": "say noop\n"}


@pytest.fixture
def emulator(make_pack):
    emulator = Emulator(Datapack.load(make_pack(PACK)), version="1.21.4")
    emulator.start()
    emulator.run_tick()
    return emulator


def passes(emulator, line: str) -> bool:
    result, _ = emulator.run_typed(line)
    return result is not None and result.success


def storage(emulator, name: str = "test:s"):
    return emulator.world.storage.get(name, {})


# --- the repro from the notes -------------------------------------------------


def test_filter_on_a_nested_node(emulator):
    emulator.run_typed("data modify storage test:s x set value {a:{b:1b}}")
    assert passes(emulator, "execute if data storage test:s x{a:{b:1b}}")
    assert passes(emulator, "execute if data storage test:s x.a{b:1b}")
    assert not passes(emulator, "execute if data storage test:s x.a{b:0b}")
    assert passes(emulator, "execute unless data storage test:s x.a{b:0b}")
    assert passes(emulator, "execute if data storage test:s {x:{a:{b:1b}}}")
    assert passes(emulator, "execute if data storage test:s x.a{}")
    # a filter selects only compounds
    emulator.run_typed("data modify storage test:s n set value 3")
    assert not passes(emulator, "execute if data storage test:s n{}")


def test_filter_after_a_quoted_key(emulator):
    emulator.run_typed('data modify storage test:s x set value {"k k":{b:1b},"a.b":{c:2}}')
    assert passes(emulator, 'execute if data storage test:s x."k k"{b:1b}')
    assert not passes(emulator, 'execute if data storage test:s x."k k"{b:2b}')
    assert passes(emulator, "execute if data storage test:s x.'a.b'{c:2}")
    # the shapes Redstone Additions uses
    emulator.run_typed(
        'data modify storage ra:dh types set value {"minecraft:hopper":{delay:{i:5}}}'
    )
    assert passes(emulator, 'execute if data storage ra:dh types."minecraft:hopper".delay{i:5}')
    assert not passes(emulator, 'execute if data storage ra:dh types."minecraft:hopper".delay{i:6}')


def test_numbers_match_type_and_value(emulator):
    emulator.run_typed("data modify storage test:s x set value {n:5,b:5b,d:5.0d,f:5.0f,t:true}")
    assert passes(emulator, "execute if data storage test:s x{n:5}")
    assert not passes(emulator, "execute if data storage test:s x{n:5b}")
    assert passes(emulator, "execute if data storage test:s x{b:5b}")
    assert not passes(emulator, "execute if data storage test:s x{b:5}")
    assert passes(emulator, "execute if data storage test:s x{d:5.0}")
    assert not passes(emulator, "execute if data storage test:s x{d:5.0f}")
    assert passes(emulator, "execute if data storage test:s x{f:5.0f}")
    assert passes(emulator, "execute if data storage test:s {x:{t:1b}}")


def test_chained_filters(emulator):
    emulator.run_typed("data modify storage test:s a set value {b:1b,c:{d:2,e:[1,2]}}")
    assert passes(emulator, "execute if data storage test:s a{b:1b}.c")
    assert passes(emulator, "execute if data storage test:s a{b:1b}.c{d:2}")
    assert not passes(emulator, "execute if data storage test:s a{b:0b}.c")
    assert not passes(emulator, "execute if data storage test:s a{b:1b}.c{d:3}")
    assert passes(emulator, "execute if data storage test:s {a:{b:1b}}.a.c{e:[2]}")


def test_element_filters(emulator):
    emulator.run_typed(
        'data modify storage test:s l set value [{id:"a",n:1},{id:"b",n:2},{id:"b",n:3}]'
    )
    assert passes(emulator, 'execute if data storage test:s l[{id:"b"}]')
    assert not passes(emulator, 'execute if data storage test:s l[{id:"c"}]')
    assert passes(emulator, 'execute if data storage test:s l[{id:"b",n:3}]')
    assert not passes(emulator, 'execute if data storage test:s l[{id:"a",n:3}]')
    assert passes(emulator, "execute if data storage test:s l[{}]")
    # a compound filter cannot follow an element node: invalid in game
    assert not passes(emulator, "execute if data storage test:s l[-1]{n:3}")
    data = storage(emulator)
    assert nbt_get_all(data, 'l[{id:"b"}].n') == [2, 3]
    assert nbt_get_all(data, "l[].id") == ["a", "b", "b"]


def test_partial_list_matching():
    assert nbt_matches({"t": ["a", "b"]}, parse_snbt('{t:["b"]}'))
    assert nbt_matches({"t": ["a", "b"]}, parse_snbt('{t:["b","a"]}'))
    assert not nbt_matches({"t": ["a", "b"]}, parse_snbt('{t:["c"]}'))
    # an empty list matches only an empty list; an empty compound matches any compound
    assert not nbt_matches({"t": ["a"]}, parse_snbt("{t:[]}"))
    assert nbt_matches({"t": []}, parse_snbt("{t:[]}"))
    assert nbt_matches({"t": {"a": 1}}, parse_snbt("{t:{}}"))
    # arrays compare whole: order and length count
    assert nbt_matches({"u": [1, 2]}, parse_snbt("{u:[I;1,2]}"))
    assert not nbt_matches({"u": [1, 2]}, parse_snbt("{u:[I;2,1]}"))
    assert not nbt_matches({"u": [1, 2, 3]}, parse_snbt("{u:[I;1,2]}"))
    # nested compounds in lists match partially too
    assert nbt_matches({"l": [{"a": 1, "b": 2}]}, parse_snbt("{l:[{a:1}]}"))


def test_data_get_through_filters(emulator):
    emulator.run_typed("data modify storage test:s x set value {a:{b:1b,v:7}}")
    result, _ = emulator.run_typed("data get storage test:s x.a{b:1b}.v")
    assert result is not None and result.success and result.value == 7
    result, _ = emulator.run_typed("data get storage test:s x.a{b:0b}.v")
    assert result is not None and not result.success


def test_data_modify_set_reaches_the_filtered_node(emulator):
    emulator.run_typed("data modify storage test:s x set value {a:{b:1b,v:1},c:{b:0b,v:1}}")
    assert passes(emulator, "data modify storage test:s x.a{b:1b}.v set value 2")
    assert storage(emulator)["x"] == {"a": {"b": 1, "v": 2}, "c": {"b": 0, "v": 1}}
    assert not passes(emulator, "data modify storage test:s x.c{b:1b}.v set value 2")
    assert storage(emulator)["x"]["c"] == {"b": 0, "v": 1}
    # quoted keys and chained filters
    emulator.run_typed('data modify storage test:s y set value {"k k":{b:1b,c:{d:1}}}')
    assert passes(emulator, 'data modify storage test:s y."k k"{b:1b}.c{d:1}.e set value 3')
    assert storage(emulator)["y"]["k k"]["c"] == {"d": 1, "e": 3}
    # list element filters set every match
    emulator.run_typed('data modify storage test:s l set value [{id:"a"},{id:"b"},{id:"b"}]')
    assert passes(emulator, 'data modify storage test:s l[{id:"b"}].n set value 1')
    assert storage(emulator)["l"] == [{"id": "a"}, {"id": "b", "n": 1}, {"id": "b", "n": 1}]
    # a filter as the last node replaces a matching compound
    assert passes(emulator, "data modify storage test:s x.a{b:1b} set value {z:1}")
    assert storage(emulator)["x"]["a"] == {"z": 1}
    # a type change is a change
    emulator.run_typed("data modify storage test:s t set value 5")
    assert passes(emulator, "data modify storage test:s t set value 5b")


def test_data_remove_through_filters(emulator):
    emulator.run_typed("data modify storage test:s x set value {a:{b:1b,v:1},c:{b:0b}}")
    assert not passes(emulator, "data remove storage test:s x.a{b:0b}")
    assert "a" in storage(emulator)["x"]
    assert passes(emulator, "data remove storage test:s x.a{b:1b}.v")
    assert storage(emulator)["x"]["a"] == {"b": 1}
    assert passes(emulator, "data remove storage test:s x.a{b:1b}")
    assert storage(emulator)["x"] == {"c": {"b": 0}}
    emulator.run_typed('data modify storage test:s l set value [{id:"a"},{id:"b"},{id:"b"}]')
    assert passes(emulator, 'data remove storage test:s l[{id:"b"}]')
    assert storage(emulator)["l"] == [{"id": "a"}]
    assert passes(emulator, "data remove storage test:s l[]")
    assert storage(emulator)["l"] == []
    # the root compound node removes nothing
    assert not passes(emulator, "data remove storage test:s {}")


def test_path_parsing():
    kinds = [node.kind for node in parse_path('{a:1}.b."c d"{e:1}[0][][{f:1}].g')]
    assert kinds == ["root", "key", "key", "index", "all", "match", "key"]
    assert parse_path("a.[0]")[1].kind == "index"
    for bad in ("", "a..b", "a{b:1}{c:1}", "a.{b:1}", "a[x]", "a[0"):
        assert parse_path(bad) is None, bad


def test_set_creates_missing_nodes():
    data: dict = {}
    assert nbt_set(data, "a.b{c:1}.d", 2)
    assert data == {"a": {"b": {"c": 1, "d": 2}}}
    assert nbt_set(data, "l[{id:1}].v", 3) and data["l"] == [{"id": 1, "v": 3}]
    assert nbt_remove(data, "a.b.d") and nbt_get(data, "a.b") == {"c": 1}


def test_typed_numbers_print_with_their_suffix():
    assert to_snbt(parse_snbt("{a:1b,b:2s,c:3,d:4L,e:1.5f,f:2.5}")) == (
        "{a: 1b, b: 2s, c: 3, d: 4L, e: 1.5f, f: 2.5d}"
    )


def test_execute_store_keeps_the_type(emulator):
    emulator.run_typed("scoreboard objectives add n dummy")
    emulator.run_typed("scoreboard players set #a n 3")
    emulator.run_typed(
        "execute store result storage test:s v byte 1 run scoreboard players get #a n"
    )
    assert passes(emulator, "execute if data storage test:s {v:3b}")
    assert not passes(emulator, "execute if data storage test:s {v:3}")
    emulator.run_typed(
        "execute store result storage test:s w int 1 run scoreboard players get #a n"
    )
    assert passes(emulator, "execute if data storage test:s {w:3}")
