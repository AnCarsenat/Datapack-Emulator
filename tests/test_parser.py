from src.emulator.commands.parser import Command, Selector, parse_duration
from src.emulator.common import in_range, parse_snbt, tokenize


def test_tokenize_keeps_brackets_and_quotes_together():
    assert tokenize('say "a b" @e[tag=x, limit=1] {a:"b c"}') == [
        "say",
        '"a b"',
        "@e[tag=x, limit=1]",
        '{a:"b c"}',
    ]


def test_selector_arguments():
    selector = Selector.parse("@e[type=!minecraft:pig,tag=a,tag=!b,limit=2,scores={x=1..}]")
    assert selector.kind == "@e"
    assert selector.arguments["tag"] == ["a", "!b"]
    assert selector.limit == 2
    assert Selector.parse("Steve").kind == "literal"


def test_comments_and_blank_lines_parse_to_none():
    assert Command.parse("# comment") is None
    assert Command.parse("   ") is None


def test_execute_chain_and_run_child():
    command = Command.parse(
        "execute as @a at @s positioned as @e[limit=1] if score @s x matches 1.. "
        "store result score @s y run say hi"
    )
    assert [sub.name for sub in command.subcommands] == ["as", "at", "positioned", "if", "store"]
    assert command.subcommands[2].arguments == ["as", "@e[limit=1]"]
    assert command.subcommands[3].arguments == ["score", "@s", "x", "matches", "1.."]
    assert command.child is not None and command.child.name == "say"


def test_features_cover_subcommands_conditions_and_macros():
    command = Command.parse("$execute on vehicle if items entity @s weapon * run return run say $(x)")
    features = command.features()
    assert {"command:execute", "execute:on", "condition:items", "return:run", "function:with"} <= features


def test_calls_report_function_schedule_and_condition_edges():
    assert Command.parse("function test:a").calls() == [("test:a", "call")]
    assert Command.parse("schedule function test:b 5t").calls() == [("test:b", "schedule")]
    assert Command.parse("execute if function #test:c run say x").calls() == [("#test:c", "condition")]
    assert Command.parse("function test:d with storage test:s").calls() == [("test:d", "macro")]


def test_macro_expansion_reports_missing_keys():
    command = Command.parse("$say $(greeting) $(name)")
    expanded, missing = command.expand_macro({"greeting": "hello"})
    assert expanded is None and missing == ["name"]
    expanded, missing = command.expand_macro({"greeting": "hello", "name": "Alex"})
    assert missing == [] and expanded.arguments == ["hello", "Alex"]


def test_cost_model_orders_cheap_and_expensive_commands():
    cheap = Command.parse("scoreboard players add #g x 1").estimate_cost()
    scan = Command.parse("execute as @e run say hi").estimate_cost()
    nbt = Command.parse("data get entity @s Inventory").estimate_cost()
    assert cheap < scan < nbt


def test_helpers():
    assert parse_duration("5s") == 100
    assert parse_duration("2t") == 2
    assert parse_duration("1d") == 24000
    assert in_range(5, "1..5") and not in_range(6, "..5") and in_range(3, "3")
    assert parse_snbt('{Tags:["a"],NoGravity:1b,x:1.5d}') == {"Tags": ["a"], "NoGravity": 1, "x": 1.5}
