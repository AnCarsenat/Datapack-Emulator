"""check --server: a real server's log turned into problems.

tests/data/server-log-26.2-*.log are real 26.2 dedicated server logs (stack
frames removed): one of a pack with a broken file of every reloadable kind,
one of a pack whose worldgen stops the world from loading.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from datapack_emulator.cli import main
from datapack_emulator.emulator import server as dedicated
from datapack_emulator.emulator import versions
from datapack_emulator.emulator.server_check import check_on_server, parse_log

DATA = Path(__file__).parent / "data"
RELOADABLE = (DATA / "server-log-26.2-reloadable.log").read_text()
WORLD = (DATA / "server-log-26.2-world.log").read_text()


def by_resource(problems):
    return {(p.resource, p.code): p for p in problems}


def test_reloadable_failures_are_errors():
    found = by_resource(parse_log(RELOADABLE, ["zoo"], ["zoo"]))
    assert {severity for severity in (p.severity for p in found.values())} == {"error"}
    function = found[("zoo:bad_cmd", "function-not-loaded")]
    assert function.line == 2 and "Unknown or incomplete command" in function.message
    assert found[("zoo:bad_nbt", "function-not-loaded")].line == 1
    assert ("zoo:bad_json", "json") in found
    assert ("zoo:bad_shape", "loot-table") in found
    assert ("zoo:old_result", "recipe") in found
    assert "No key criteria" in found[("zoo:no_criteria", "advancement")].message
    assert ("zoo:bad", "condition") in found  # predicate
    assert ("zoo:bad", "item-function") in found  # item modifier
    # a tag of the pack's own namespace, and one of minecraft's it wrote into
    assert ("#zoo:bad", "tag-not-loaded") in found
    assert ("#minecraft:tick", "tag-not-loaded") in found
    # a good function is not mentioned, and neither is the world's own noise
    assert not any("good" in resource for resource, _ in found)
    assert not any("layers" in p.message for p in found.values())


def test_world_failures_are_fatal_and_name_the_cause():
    problems = parse_log(WORLD, ["crystal"], ["crystal"])
    fatal = [p for p in problems if p.severity == "fatal"]
    messages = " | ".join(p.message for p in fatal)
    assert "has_ender_dragon_fight" in messages
    assert "preliminary_surface_level" in messages
    assert "minecraft:random_patch" in messages
    assert "refused to load the world" in messages
    # the "unbound" consequence of the feature that failed is not a second problem
    assert sum("amethyst_clusters" in p.message for p in fatal) == 1
    assert problems[0].severity == "fatal"


def test_other_packs_are_left_out():
    assert parse_log(RELOADABLE, ["someone_else"], ["someone_else"]) == []


def test_running_needs_the_eula(tmp_path):
    with pytest.raises(PermissionError, match="EULA"):
        check_on_server(
            [tmp_path], ["x"], versions.parse("26.2"), tmp_path / "s", accept_eula=False
        )
    assert not (tmp_path / "s").exists()  # nothing written before the check


def test_cli_server_needs_accept_eula(make_pack, capsys):
    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    assert main(["check", str(pack), "--version", "26.2", "--no-vanilla", "--server"]) == 2
    assert "EULA" in capsys.readouterr().err


def test_agent_server_needs_accept_eula(make_pack):
    from datapack_emulator import agent

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    with pytest.raises(agent.AgentError, match="EULA"):
        agent.check(pack, "26.2", vanilla=None, server=True)


def _fake_java(tmp_path: Path, log: str, starts: bool) -> Path:
    """A stand-in for `java -jar server.jar`: writes the log a real server
    wrote, then says Done and waits for `stop` -- or exits, as a server that
    gave up on the world does."""
    script = tmp_path / "fake-java"
    (tmp_path / "server.log").write_text(log)
    script.write_text(
        f"""#!{sys.executable}
import os, pathlib, sys
log = pathlib.Path({str(tmp_path / "server.log")!r}).read_text()
pathlib.Path("logs").mkdir(exist_ok=True)
pathlib.Path("logs/latest.log").write_text(log)
assert pathlib.Path("eula.txt").read_text().strip() == "eula=true"
assert any(pathlib.Path("world/datapacks").iterdir())
if {starts!r}:
    print('[00:00:00] [Server thread/INFO]: Done (1.0s)! For help, type "help"', flush=True)
    for line in sys.stdin:
        if line.strip() == "stop":
            break
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.skipif(os.name == "nt", reason="the fake server is a POSIX script")
def test_check_on_server_runs_reads_and_stops(make_pack, tmp_path):
    pack = make_pack({"data/zoo/function/bad_cmd.mcfunction": "frobnicate\n"}, name="zoo")
    jar = tmp_path / "server.jar"
    jar.write_text("not a jar")
    java = _fake_java(tmp_path, RELOADABLE, starts=True)
    result = check_on_server(
        [pack],
        ["zoo"],
        versions.parse("26.2"),
        tmp_path / "srv",
        accept_eula=True,
        jar=jar,
        java=str(java),
        timeout=30,
    )
    assert result.started
    assert any(p.code == "function-not-loaded" for p in result.problems)
    assert result.to_dict()["counts"]["error"] >= 5


@pytest.mark.skipif(os.name == "nt", reason="the fake server is a POSIX script")
def test_a_server_that_gives_up_is_fatal(make_pack, tmp_path):
    pack = make_pack({"data/crystal/function/x.mcfunction": "say x\n"}, name="crystal")
    jar = tmp_path / "server.jar"
    jar.write_text("not a jar")
    java = _fake_java(tmp_path, WORLD, starts=False)
    result = check_on_server(
        [pack],
        ["crystal"],
        versions.parse("26.2"),
        tmp_path / "srv",
        accept_eula=True,
        jar=jar,
        java=str(java),
        timeout=30,
    )
    assert not result.started
    assert any(p.severity == "fatal" and p.code == "pack" for p in result.problems)


def test_prepare_writes_eula_and_properties(tmp_path):
    jar = tmp_path / "server.jar"
    jar.write_text("x")
    out = dedicated.prepare(
        versions.parse("26.2"), tmp_path / "srv", accept_eula=True, jar=jar, java=sys.executable
    )
    assert out == jar
    properties = (tmp_path / "srv" / "server.properties").read_text()
    assert "online-mode=false" in properties and "pause-when-empty-seconds=-1" in properties
    assert (tmp_path / "srv" / "eula.txt").read_text() == "eula=true\n"


def test_a_folder_not_made_by_a_check_is_refused(tmp_path):
    """Pointed at someone's real server folder, a run would delete its world."""
    real = tmp_path / "my-server"
    (real / "world").mkdir(parents=True)
    (real / "world" / "level.dat").write_text("precious")
    jar = tmp_path / "server.jar"
    jar.write_text("x")
    with pytest.raises(FileExistsError, match="not empty"):
        check_on_server(
            [tmp_path],
            ["x"],
            versions.parse("26.2"),
            real,
            accept_eula=True,
            jar=jar,
            java=sys.executable,
        )
    assert (real / "world" / "level.dat").read_text() == "precious"
    assert not (real / "server.properties").exists()


def test_the_eula_is_checked_before_the_folder(tmp_path):
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "keep").write_text("x")
    with pytest.raises(PermissionError, match="EULA"):
        check_on_server(
            [tmp_path], ["x"], versions.parse("26.2"), tmp_path / "srv", accept_eula=False
        )
    assert sorted(p.name for p in (tmp_path / "srv").iterdir()) == ["keep"]
