"""Real timings: /perf reports (server_profile) and the cost calibration.

tests/data/profiling-26.2.txt is a real report: a 26.2 dedicated server
running a pack with ``probe:sb`` (2000 x scoreboard), ``probe:data``
(500 x data modify storage) and ``probe:empty`` in #minecraft:tick.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from datapack_emulator.cli import main
from datapack_emulator.emulator import calibration, costs, versions
from datapack_emulator.emulator.server_profile import ServerProfile, compare

DATA = Path(__file__).parent / "data"
REPORT = DATA / "profiling-26.2.txt"


@pytest.fixture
def fresh_costs():
    """Each test leaves the cost model as it found it."""
    saved = calibration.snapshot()
    yield
    calibration.restore(saved)


# -- server_profile -----------------------------------------------------------


def test_parses_a_real_report():
    profile = ServerProfile.read(REPORT)
    assert profile.version == "26.2"
    assert profile.ticks == 201
    assert profile.time_span_ms == 10011
    rows = {row["function"]: row for row in profile.functions()}
    assert set(rows) == {"probe:sb", "probe:data", "probe:empty"}
    sb = rows["probe:sb"]
    assert sb["calls_per_tick"] == pytest.approx(1.0)
    # 1.53% of 10 s over 201 ticks, from the parents' shares
    assert sb["us_per_tick"] == pytest.approx(765, rel=0.01)
    (line,) = sb["lines"]
    assert line["command"] == "scoreboard players add #x p 1"
    assert line["runs_per_tick"] == pytest.approx(2000)
    assert 0.1 < line["us_each"] < 0.3


def test_reads_a_perf_zip(tmp_path):
    archive = tmp_path / "2026-11-06_21_34_37-world-26_2.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("system.txt", "x")
        zf.writestr("server/profiling.txt", REPORT.read_text())
    assert ServerProfile.read(archive).ticks == 201


def test_rejects_what_is_not_a_report(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        ServerProfile.read(bad)
    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "x")
    with pytest.raises(ValueError, match="profiling.txt"):
        ServerProfile.read(archive)


def test_prepare_sections_count_as_lines():
    text = REPORT.read_text().replace(
        "execute data modify storage probe:s v set value 1",
        "prepare execute if score #c p matches 0.. run say x",
    )
    rows = {row["function"]: row for row in ServerProfile.parse(text).functions()}
    assert rows["probe:data"]["lines"][0]["command"].startswith("execute if score")


def test_prepare_and_execute_of_a_line_are_one_run():
    lines = REPORT.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if "execute scoreboard players add" in line)
    prepare = lines[index].replace("execute scoreboard", "prepare scoreboard")
    text = "\n".join(lines[: index + 1] + [prepare] + lines[index + 1 :])
    rows = {row["function"]: row for row in ServerProfile.parse(text).functions()}
    (line,) = rows["probe:sb"]["lines"]
    assert line["runs_per_tick"] == pytest.approx(2000)


def test_compare_with_the_emulator(make_pack):
    from datapack_emulator.emulator.datapack import DatapackSet
    from datapack_emulator.emulator.runtime.emulator import Emulator

    pack = make_pack(
        {
            "data/probe/function/sb.mcfunction": "scoreboard players add #x p 1\n" * 2000,
            "data/probe/function/data.mcfunction": "data modify storage probe:s v set value 1\n"
            * 500,
            "data/minecraft/tags/function/tick.json": {"values": ["probe:sb", "probe:data"]},
        }
    )
    emulator = Emulator(DatapackSet.load([pack]), version="26.2")
    emulator.run(ticks=10)
    rows = {row["function"]: row for row in compare(ServerProfile.read(REPORT), emulator.profiler)}
    assert rows["probe:sb"]["estimated_ms_per_tick"] > 0
    assert rows["probe:empty"]["estimated_ms_per_tick"] is None
    # the built-in constants are measured: within a factor of 3 of this report
    assert 1 / 3 < rows["probe:sb"]["ratio"] < 3
    assert 1 / 3 < rows["probe:data"]["ratio"] < 3


def test_server_profile_cli_json(capsys):
    assert main(["server-profile", str(REPORT), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["measured"] and data["functions"][0]["function"] == "probe:sb"


# -- the benchmark pack -------------------------------------------------------


@pytest.mark.parametrize("version", ["1.20.4", "1.21.4", "26.2"])
def test_benchmark_pack_loads_in_every_version(tmp_path, version):
    from datapack_emulator import agent

    target = versions.parse(version)
    pack = calibration.write_benchmark_pack(tmp_path / "calib", target)
    folder = "function" if versions.uses_singular_registries(target) else "functions"
    assert (pack / "data" / "calib" / folder / "bench" / "score_add.mcfunction").is_file()
    result = agent.run(pack, version, ticks=1, vanilla=None, world=False)
    assert result["failed_functions"] == [], result["failed_functions"]
    assert result["ignored_folders"] == []
    meta = json.loads((pack / "pack.mcmeta").read_text())["pack"]
    if versions.supports_min_max_format(target):
        assert "pack_format" not in meta and meta["min_format"] == meta["max_format"]
    else:
        assert meta["pack_format"] == target.pack_format


def test_running_a_server_needs_the_eula(tmp_path):
    with pytest.raises(PermissionError, match="EULA"):
        calibration.run_server(versions.parse("26.2"), tmp_path, accept_eula=False)


def test_calibrate_cli_needs_accept_eula(capsys):
    assert main(["calibrate", "--version", "26.2"]) == 2
    assert "EULA" in capsys.readouterr().err


# -- fitting ------------------------------------------------------------------


def _synthetic(truth: dict) -> list[calibration.Measurement]:
    """What a server would measure if ``truth`` were the real constants."""
    saved = calibration.snapshot()
    try:
        calibration.apply(truth)
        out = []
        for phase, entities in (("base", 1), ("markers", 1 + calibration.MARKERS)):
            for bench in calibration.BENCHMARKS:
                if phase not in bench.phases:
                    continue
                us = calibration.model_cost(bench.executed_lines(), entities)
                out.append(calibration.Measurement(bench, phase, entities, us))
        return out
    finally:
        calibration.restore(saved)


def test_fit_recovers_known_constants(fresh_costs):
    truth = {
        "COMMAND_COST_US": {"scoreboard": 0.4, "data": 0.5, "tp": 2.5, "fill": 7.0},
        "CONDITION_COST_US": {"score": 0.15, "predicate": 0.2},
        "SELECTOR_PER_ENTITY_COST_US": 0.01,
        "NBT_FILTER_COST_US": 1.0,
        "VOLUME_COST_US_PER_BLOCK": 2.0,
        "DATA_ENTITY_EXTRA_US": 2.0,
    }
    result = calibration.fit(_synthetic(truth), ridge=1e-9)
    values = result.values
    for name, expected in truth.items():
        if isinstance(expected, dict):
            for key, value in expected.items():
                assert values[name][key] == pytest.approx(value, rel=0.05), (name, key)
        else:
            assert values[name] == pytest.approx(expected, rel=0.05), name
    assert values["LIMIT_DISCOUNT"] == calibration.LIMIT_DISCOUNT
    # fitting does not change the live model; apply() does
    assert costs.COMMAND_COST_US["tp"] != pytest.approx(2.5)
    calibration.apply(values)
    assert costs.COMMAND_COST_US["tp"] == pytest.approx(2.5, rel=0.05)


def test_fit_keeps_unmeasured_constants_at_the_scaled_prior(fresh_costs):
    before = costs.COMMAND_COST_US["reload"]
    measurements = _synthetic({})  # the current model, measured exactly
    result = calibration.fit(measurements)
    assert result.scale == pytest.approx(1.0, rel=0.01)
    assert result.values["COMMAND_COST_US"]["reload"] == pytest.approx(before, rel=0.05)
    assert "COMMAND_COST_US[scoreboard]" in result.measured
    assert "COMMAND_COST_US[reload]" not in result.measured


def test_measure_reads_reports(tmp_path):
    text = REPORT.read_text().replace("probe:sb", "calib:bench/score_add")
    report = tmp_path / "base.txt"
    report.write_text(text)
    (m,) = calibration.measure({"base": [report]})
    assert m.benchmark.name == "score_add" and m.phase == "base"
    assert m.us == pytest.approx(765, rel=0.01)


# -- applying -----------------------------------------------------------------


def test_apply_calibration_tool_rewrites_numbers_only():
    import runpy

    module = runpy.run_path(str(Path(__file__).parents[1] / "tools" / "apply_calibration.py"))
    text = (Path(costs.__file__)).read_text()
    document = {
        "minecraft": "26.2",
        "measured_at": "2026-01-01T00:00:00",
        "machine": {"cpu": "test cpu", "cores": "4", "system": "test", "java": "java 25"},
        "measured": ["COMMAND_COST_US[scoreboard]"],
        "benchmarks": [],
        "scale_of_old_model": 1.0,
        "values": {"COMMAND_COST_US": {"scoreboard": 9.25}, "NBT_FILTER_COST_US": 3.5},
    }
    out = module["apply"](document, text)
    assert '"scoreboard": 9.25,' in out
    assert "NBT_FILTER_COST_US: float = 3.5" in out
    assert "test cpu" in out and 'CALIBRATED_ON: str = "26.2"' in out
    # only the numbers and the provenance block change
    changed = [a for a, b in zip(text.splitlines(), out.splitlines(), strict=False) if a != b]
    assert all(
        "#:" in line or "CALIBRATED_ON" in line or "scoreboard" in line or "NBT_FILTER" in line
        for line in changed
    ), changed


def test_user_calibration_file(tmp_path, fresh_costs, monkeypatch):
    path = tmp_path / "costs.json"
    path.write_text(
        json.dumps({"values": {"COMMAND_COST_US": {"tp": 42.0}, "LIMIT_DISCOUNT": 0.3}})
    )
    assert costs.load_user_calibration(path) == str(path)
    assert costs.COMMAND_COST_US["tp"] == 42.0 and costs.LIMIT_DISCOUNT == 0.3
    monkeypatch.setenv("DATAPACK_EMULATOR_COSTS", str(tmp_path / "missing.json"))
    assert costs.load_user_calibration() == ""


def test_builtin_constants_are_measured():
    assert costs.CALIBRATED_ON
    # what the measurements overturned: storage is cheap, NBT on entities is not
    assert costs.COMMAND_COST_US["data"] < 2
    assert costs.COMMAND_COST_US["data"] < costs.DATA_ENTITY_EXTRA_US
    assert costs.NBT_FILTER_COST_US > 10 * costs.SELECTOR_PER_ENTITY_COST_US


def test_agent_server_profile(make_pack):
    from datapack_emulator import agent

    result = agent.server_profile(REPORT)
    assert result["ok"] and result["measured"] and "probe:sb" in result["summary"]
    pack = make_pack(
        {
            "data/probe/function/sb.mcfunction": "scoreboard players add #x p 1\n" * 10,
            "data/minecraft/tags/function/tick.json": {"values": ["probe:sb"]},
        }
    )
    compared = agent.server_profile(REPORT, pack, ticks=5)["compared"]
    assert compared["version"] == "26.2"
    assert {row["function"] for row in compared["functions"]} >= {"probe:sb"}
    json.dumps(compared)
    with pytest.raises(agent.AgentError):
        agent.server_profile(REPORT.parent / "nope.zip")


def test_execute_selectors_are_charged_once():
    from datapack_emulator.emulator.commands import Command

    scan = (
        Command.parse("tag @e[tag=x] add y").estimate_cost(1000, player_count=0)
        - costs.COMMAND_COST_US["tag"]
    )
    line = Command.parse("execute if entity @e[tag=x]").estimate_cost(1000, player_count=0)
    expected = (
        costs.COMMAND_COST_US["execute"]
        + costs.SUBCOMMAND_COST_US["if"]
        + costs.CONDITION_COST_US["entity"]
        + scan
    )
    assert line == pytest.approx(expected)
    wrapped = Command.parse("execute as @s run kill @e[tag=x]").estimate_cost(1000, player_count=0)
    alone = Command.parse("kill @e[tag=x]").estimate_cost(1000, player_count=0)
    assert wrapped - alone < scan  # the child's @e is not charged a second time
