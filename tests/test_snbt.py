import pytest

from datapack_emulator.emulator import Datapack
from datapack_emulator.emulator.analysis.problems import find_problems
from datapack_emulator.emulator.common import NbtArray, nbt_type, parse_snbt, parse_value
from datapack_emulator.emulator.snbt import SnbtError, is_valid_snbt, read_snbt

MODERN_ONLY = [
    '[1,"a",{c:1b}]',  # a list of mixed types
    "0x1F",
    "0b101",
    "1_000",
    "5ub",
    "-1sb",
    ".5",
    "+3",
    "bool(1)",
    'uuid("f81d4fae-7dec-11d0-a765-00a0c91e6bf6")',
    '"a\\nb\\x41\\u0041\\N{LATIN SMALL LETTER A}"',
]
BOTH = [
    "{}",
    "{a:1b,b:2s,c:3L,d:4f,e:5.0d,f:1.5,g:-2}",
    "{text:\"x\",color:gray,italic:false,'k':'it\\'s'}",
    '[{text:"a",color:gray,italic:0b},{text:"b"}]',
    "{a:[1,2,],b:{c:1},}",  # trailing commas
    "[B;1b,2b]",
    "[I;1,2,3]",
    "[]",
    "rare",
    "1.e3f",
]
NEITHER = [
    "{a:[1,}",
    "{a:",
    "{a:}",
    "{a:,b:1}",
    "[1,,2]",
    "{:1}",
    "{a:1 b:2}",
    '{a:"open}',
    "[I;1,a]",
]
MODERN_REFUSES = ["{a:01}", "{a:-a}", '{"":1}', "{a:1abc}", "{a:foo(1)}", "256ub", "-1ub", "-0x10L"]


@pytest.mark.parametrize("text", BOTH + MODERN_ONLY)
def test_modern_grammar_accepts(text):
    assert is_valid_snbt(text, modern=True)


@pytest.mark.parametrize("text", BOTH)
def test_legacy_grammar_accepts(text):
    assert is_valid_snbt(text, modern=False)


@pytest.mark.parametrize("text", ['[1,"a"]', '"a\\nb"', "bool(1)"])
def test_legacy_grammar_refuses_the_modern_forms(text):
    assert not is_valid_snbt(text, modern=False)


@pytest.mark.parametrize("text", NEITHER + MODERN_REFUSES)
def test_modern_grammar_refuses(text):
    with pytest.raises(SnbtError):
        read_snbt(text, modern=True)


def test_values_are_typed():
    value = read_snbt("{a:1b,b:0xFFub,c:1_000L,d:.5f,e:true,f:[L;1L],g:bool(0)}")
    assert value == {"a": 1, "b": -1, "c": 1000, "d": 0.5, "e": True, "f": [1], "g": False}
    assert [nbt_type(value[key]) for key in "abcd"] == ["b", "b", "l", "f"]
    assert isinstance(value["f"], NbtArray) and value["f"].kind == "L"
    assert read_snbt('uuid("00000000-0000-0001-0000-000000000002")') == [0, 1, 0, 2]
    # the legacy parser reads what does not fit a number as a string
    assert read_snbt("{a:01,b:1abc}", modern=False) == {"a": "01", "b": "1abc"}


def test_common_parsers_fall_back_to_the_full_grammar():
    assert parse_snbt("{a:0x10,b:bool(1)}") == {"a": 16, "b": True}
    assert parse_value("5ub") == 5 and nbt_type(parse_value("5ub")) == "b"


GIVE = (
    'give @s minecraft:bundle[item_name="Bundle",lore=[{text:"Remote",color:"gray",'
    'italic:false}],custom_data={ra:{n:"x"}},bundle_contents=[{id:"minecraft:stone",count:1,'
    'components:{"minecraft:rarity":rare,"minecraft:enchantment_glint_override":1b,'
    '"minecraft:lore":[{text:"a \\"b\\"",color:gray,italic:0b}]}}]]\n'
)


def _snbt_problems(make_pack, body, version="26.3", pack_format=121):
    pack = make_pack(
        {"data/test/function/tick.mcfunction": body},
        mcmeta={"pack": {"description": "t", "min_format": pack_format, "max_format": 121}},
    )
    return [p for p in find_problems(Datapack.load(pack), version) if p.code == "snbt"]


def test_item_components_are_checked_one_by_one(make_pack):
    assert _snbt_problems(make_pack, GIVE) == []
    clear = "clear @s *[custom_data~{ra:{wrench:1b}}|minecraft:rarity=rare,count~{min:1}]\n"
    assert _snbt_problems(make_pack, clear) == []
    broken = 'give @s minecraft:stone[lore=[{text:"a"]]\n'
    (problem,) = _snbt_problems(make_pack, broken)
    assert problem.line == 1 and "give" in problem.message


def test_snbt_checks_follow_the_version_grammar(make_pack):
    mixed = 'give @s minecraft:stone[custom_data={list:[1,"a"]}]\n'
    assert _snbt_problems(make_pack, mixed) == []
    assert len(_snbt_problems(make_pack, mixed, "1.21.4", 61)) == 1
