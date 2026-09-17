"""The command line: datapack-emulator-cli."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest

from datapack_emulator.cli import main
from datapack_emulator.project import Project
from datapack_emulator.settings import PATHS

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """Unpacked projects, reports and client jars stay in a throwaway folder."""
    from datapack_emulator.cli import common
    from datapack_emulator.emulator.vanilla import VanillaLibrary

    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    monkeypatch.chdir(tmp_path)
    jars = tmp_path / "jars"
    monkeypatch.setattr(common, "default_library", lambda: VanillaLibrary(jars, search_dirs=[]))
    return jars


def _install(jars: Path, fake_jar: Path, version: str) -> Path:
    target = jars / version / f"minecraft-{version}-client.jar"
    target.parent.mkdir(parents=True)
    target.write_bytes(fake_jar.read_bytes())
    return target


def _pack(make_pack):
    return make_pack(
        {
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
            "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
        }
    )


def _project(tmp_path, make_pack, tests, **settings) -> Path:
    project = Project(name="demo", datapacks=[_pack(make_pack)], tests=tests, **settings)
    return project.save(tmp_path / "demo.dpemu")


def test_run_prints_the_profiler_and_writes_the_report(make_pack, tmp_path, capsys):
    report = tmp_path / "out" / "index.html"
    code = main(["run", str(_pack(make_pack)), "--ticks", "3", "--html", str(report), "--dot"])
    out = capsys.readouterr().out
    assert code == 0
    assert "test:tick" in out and "3 tick(s)" in out
    assert report.is_file() and (report.parent / "pack1.dot").is_file()


def test_run_uses_a_projects_settings(make_pack, tmp_path, capsys):
    path = _project(tmp_path, make_pack, [], version="1.20.4", ticks=4)
    assert main(["run", str(path), "--html", str(tmp_path / "r.html")]) == 0
    assert "1.20.4: 4 tick(s)" in capsys.readouterr().out


def test_test_runs_the_projects_tests_and_writes_junit(make_pack, tmp_path, capsys):
    path = _project(
        tmp_path,
        make_pack,
        [
            {"command": "scoreboard players get #ticks t", "at_tick": 2, "expect_value": "3"},
            {"command": "scoreboard players get #ticks t", "at_tick": 0, "expect_value": "5"},
            {"command": "say skipped", "enabled": False},
        ],
        version="1.21.4",
    )
    junit = tmp_path / "junit.xml"
    code = main(["test", str(path), "--junit", str(junit)])
    out = capsys.readouterr().out
    assert code == 1
    assert "PASS 1.21.4" in out and "FAIL 1.21.4" in out and "1/2 passed in 1.21.4" in out
    assert "say skipped" not in out

    root = ElementTree.parse(junit).getroot()
    assert root.get("tests") == "2" and root.get("failures") == "1"
    failures = root.findall(".//failure")
    assert [failure.get("message") for failure in failures] == ["value 1 is not exactly 5"]


def test_test_passes_across_versions_and_counts_disabled_tests_on_request(
    make_pack, tmp_path, capsys
):
    path = _project(tmp_path, make_pack, [{"command": "say hi", "expect": "hi", "enabled": False}])
    assert main(["test", str(path)]) == 2  # nothing enabled
    capsys.readouterr()
    code = main(["test", str(path), "--include-disabled", "--versions", "1.20.4", "1.21.4"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2/2 passed across 2 versions" in out


def test_test_takes_tests_from_the_command_line(make_pack, capsys):
    pack = str(_pack(make_pack))
    code = main(
        ["test", pack, "--version", "1.21.4", "--test", "4:scoreboard players get #ticks t"]
    )
    assert code == 0
    assert "tick 4" in capsys.readouterr().out
    code = main(["test", pack, "--test", "say hello", "--expect", "bye", "--show-records"])
    out = capsys.readouterr().out
    assert code == 1
    assert "expected output not found" in out and "[Server] hello" in out


def test_test_only_ignores_the_projects_tests(make_pack, tmp_path, capsys):
    path = _project(tmp_path, make_pack, [{"command": "function test:missing"}])
    assert main(["test", str(path)]) == 1
    capsys.readouterr()
    assert main(["test", str(path), "--only", "--test", "say ok"]) == 0


def test_usage_errors_exit_with_two(make_pack, tmp_path, capsys):
    pack = str(_pack(make_pack))
    assert main(["run", str(tmp_path / "missing")]) == 2
    assert main(["run", pack, "--version", "0.0.1"]) == 2
    assert main(["test", pack, "--test", "a", "--test", "b", "--expect", "x"]) == 2
    path = _project(tmp_path, make_pack, [])
    assert main(["run", str(path), pack]) == 2
    assert "not both" in capsys.readouterr().err


def test_matrix_runs_tests_and_fails_when_strict(make_pack, tmp_path, capsys):
    path = _project(
        tmp_path,
        make_pack,
        [{"command": "function test:missing"}],
        engine_versions=["1.20.4", "1.21.4"],
    )
    html = tmp_path / "matrix.html"
    junit = tmp_path / "matrix.xml"
    code = main(
        ["matrix", str(path), "--tests", "--strict", "--html", str(html), "--junit", str(junit)]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "1.20.4" in out and "1.21.4" in out and "0/1" in out
    assert html.is_file() and junit.is_file()
    assert main(["matrix", str(path), "--html", str(html)]) == 0


@pytest.mark.parametrize("sample", ["hat", "hat_v2"])
def test_samples_run_from_the_command_line(sample, tmp_path, capsys):
    code = main(["run", str(SAMPLES / sample), "--ticks", "2", "--html", str(tmp_path / "r.html")])
    assert code == 0


def test_versions_lists_releases(capsys):
    assert main(["versions"]) == 0
    assert "1.21.4" in capsys.readouterr().out


def test_the_module_entry_points_share_the_parser():
    from datapack_emulator.emulator import __main__ as legacy

    assert legacy.main is main


def test_reports_default_to_generated_in_the_working_directory(make_pack, tmp_path):
    assert main(["run", str(_pack(make_pack)), "--ticks", "1"]) == 0
    assert (tmp_path / "generated" / "index.html").is_file()


def test_run_runs_the_projects_tests_when_the_project_says_so(make_pack, tmp_path, capsys):
    tests = [{"command": "scoreboard players get #ticks t", "at_tick": 1, "expect_value": "9"}]
    path = _project(tmp_path, make_pack, tests, tests_during_runs=True, ticks=3)
    assert main(["run", str(path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "tests: 0/1 passed" in out
    assert main(["run", str(path), "--no-tests"]) == 0
    assert "tests:" not in capsys.readouterr().out
    path = _project(tmp_path, make_pack, tests, ticks=3)
    assert main(["run", str(path)]) == 0
    assert main(["run", str(path), "--tests"]) == 1


def test_test_reads_the_projects_ticks_only_when_asked(make_pack, tmp_path, capsys):
    path = _project(
        tmp_path, make_pack, [{"command": "say late", "at_tick": 30}], ticks=5, version="1.21.4"
    )
    # like the window's run tests, the world ticks until the last test ran
    assert main(["test", str(path)]) == 0
    assert main(["test", str(path), "--ticks", "5"]) == 1
    captured = capsys.readouterr()
    assert "not reached" in captured.out and "will fail as not reached" in captured.err
    assert main(["matrix", str(path), "--versions", "1.21.4", "--junit", "m.xml"]) == 0
    captured = capsys.readouterr()
    assert "will fail as not reached" in captured.err and (tmp_path / "m.xml").is_file()


def test_test_usage_mistakes(make_pack, tmp_path, capsys):
    pack = str(_pack(make_pack))
    path = _project(tmp_path, make_pack, [{"command": "say hi"}])
    assert main(["test", str(path), "--only"]) == 2
    assert main(["test", pack, "--test", "say hi", "--engine"]) == 2
    assert main(["test", pack, "--test", "say hi", "--expect-value", "abc"]) == 2
    assert main(["test", pack, "--test", "say hi", "--version", "1.21.4", "--all"]) == 2
    assert main(["test", pack]) == 2
    assert "no project was given" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["run", pack, "--players", "-2"])


def test_non_ascii_digits_are_part_of_the_command(make_pack, capsys):
    assert main(["test", str(_pack(make_pack)), "--test", "\u00b2:say hi"]) == 1
    assert "tick 0" in capsys.readouterr().out


def test_unwritable_reports_are_usage_errors_not_failures(make_pack, tmp_path, capsys):
    pack = str(_pack(make_pack))
    (tmp_path / "dir").mkdir()
    assert main(["test", pack, "--test", "say hi", "--junit", str(tmp_path / "dir")]) == 2
    assert main(["run", pack, "--vanilla", str(_pack(make_pack) / "pack.mcmeta")]) == 2


def test_internal_errors_exit_with_three(make_pack, monkeypatch, capsys):
    from datapack_emulator.cli import runs

    def broken(arguments):
        raise RuntimeError("boom")

    monkeypatch.setattr(runs, "load_inputs", broken)
    assert main(["run", "anything"]) == 3
    assert "internal error" in capsys.readouterr().err


def test_junit_strips_characters_xml_cannot_hold(make_pack, tmp_path):
    from xml.dom import minidom

    junit = tmp_path / "c.xml"
    command = "say a\u0001b"
    assert main(["test", str(_pack(make_pack)), "--test", command, "--junit", str(junit)]) == 0
    minidom.parse(str(junit))  # well-formed


def test_the_installed_jar_of_the_version_is_used_by_default(
    make_pack, tmp_path, scratch, fake_jar, capsys
):
    pack = str(_pack(make_pack))
    _install(scratch, fake_jar, "1.21.4")
    unknown = "5:give Player1 minecraft:not_an_item"
    assert main(["test", pack, "--version", "1.21.4", "--test", unknown]) == 1
    assert main(["test", pack, "--version", "1.21.4", "--test", unknown, "--no-vanilla"]) == 0
    assert main(["test", pack, "--versions", "1.21.4", "1.20.4", "--test", unknown]) == 1
    out = capsys.readouterr().out
    assert "FAIL 1.21.4" in out and "PASS 1.20.4" in out


def test_a_projects_jar_is_used_when_none_is_installed(make_pack, tmp_path, fake_jar, capsys):
    tests = [{"command": "give Player1 minecraft:not_an_item"}]
    path = _project(tmp_path, make_pack, tests, version="1.21.4", vanilla_jar=str(fake_jar))
    assert main(["test", str(path)]) == 1
    assert main(["run", str(path), "--ticks", "1"]) == 0
    assert "vanilla assets: 9.9" in capsys.readouterr().err
