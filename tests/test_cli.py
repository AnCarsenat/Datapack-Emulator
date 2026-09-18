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


# -- parity with the window: inspector, analyze line, search, graph, world, shell, projects


def test_info_describes_the_pack_and_resources(make_pack, tmp_path, capsys):
    path = _project(tmp_path, make_pack, [], version="1.21.4")
    project = Project.load(path)
    project.function_notes["test:tick"] = "counts ticks"
    project.save(path)
    assert main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "1.21.4: lists the pack as compatible" in out and "namespaces" in out
    assert main(["info", str(path), "-r", "test:tick", "-r", "#minecraft:tick"]) == 0
    out = capsys.readouterr().out
    assert "estimated cost" in out and "called by" in out and "counts ticks" in out
    assert main(["info", str(path), "-r", "test:nope"]) == 2
    assert main(["info", str(path), "--json"]) == 0
    import json

    rows = json.loads(capsys.readouterr().out)
    assert any(row["label"] == "functions" for row in rows)


def test_explain_lines_and_functions(make_pack, capsys):
    pack = str(_pack(make_pack))
    assert main(["explain", pack, "--version", "1.21.4", "-l", "execute as @a run say hi"]) == 0
    out = capsys.readouterr().out
    assert "step 1: as @a" in out and "loads in 1.21.4" in out
    assert main(["explain", pack, "--at", "test:tick"]) == 0
    assert "from" in capsys.readouterr().out
    assert main(["explain", pack, "--at", "test:tick:9"]) == 2
    assert main(["explain", pack]) == 2


def test_search_text_and_ids(make_pack, capsys):
    pack = str(_pack(make_pack))
    assert main(["search", pack, "PLAYERS add"]) == 0
    assert "test:tick:1" in capsys.readouterr().out
    assert main(["search", pack, "tick", "--ids"]) == 0
    out = capsys.readouterr().out
    assert "test:tick  (function)" in out and "#minecraft:tick  (function tag)" in out
    assert main(["search", pack, "nothing like this"]) == 1


def test_graph_lists_calls_and_writes_dot(make_pack, tmp_path, capsys):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": "function test:a\nfunction test:gone\n",
            "data/test/function/a.mcfunction": "schedule function test:a 1t\n",
            "data/test/function/unused.mcfunction": "say never\n",
        }
    )
    dot = tmp_path / "g.dot"
    assert main(["graph", str(pack), "--dot", str(dot)]) == 0
    out = capsys.readouterr().out
    assert "test:a (call)" in out and "recursion: test:a -> test:a" in out
    assert "missing function: test:gone" in out and "never called: test:unused" in out
    assert dot.is_file()
    assert main(["graph", str(pack), "-f", "test:a"]) == 0
    assert "called by" in capsys.readouterr().out
    assert main(["graph", str(pack), "-f", "test:none"]) == 2


def test_world_prints_scores_entities_storage_and_history(make_pack, capsys):
    pack = str(_pack(make_pack))
    code = main(
        [
            "world",
            pack,
            "--ticks",
            "3",
            "-c",
            "summon minecraft:pig ~ ~ ~ {Tags:[a]}",
            "-c",
            "data modify storage test:mem x set value 5",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "#ticks" in out and "Pig" in out and "test:mem  {x: 5}" in out
    assert main(["world", pack, "--ticks", "3", "--history", "#ticks", "t"]) == 0
    out = capsys.readouterr().out
    assert "tick 2: 3" in out and "values taken: 1, 2, 3" in out
    assert main(["world", pack, "--ticks", "2", "--json"]) == 0
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["scores"]["#ticks"] == {"t": 2} and data["tick"] == 2


def test_shell_runs_a_script_like_the_command_line(make_pack, tmp_path, capsys):
    pack = str(_pack(make_pack))
    script = tmp_path / "session.txt"
    script.write_text(
        "\n".join(
            [
                "scoreboard players set #x t 7",
                ".step 2",
                ".scores",
                ".test 1:scoreboard players get #ticks t",
                ".expect-value 1 2",
                ".runtests",
                ".save " + str(tmp_path / "saved.dpemu"),
                ".quit",
                "say never reached",
            ]
        ),
        encoding="utf-8",
    )
    assert main(["shell", pack, "--version", "1.21.4", "--script", str(script), "--strict"]) == 0
    out = capsys.readouterr().out
    assert "(started the world: ran the first tick)" in out
    assert "game time 3" in out and "#x" in out and "1/1 passed" in out
    assert "never reached" not in out
    saved = Project.load(tmp_path / "saved.dpemu")
    assert saved.version == "1.21.4" and saved.tests[0]["expect_value"] == "2"


def test_shell_reports_mistakes_and_fails_when_strict(make_pack, tmp_path, capsys, monkeypatch):
    import io

    pack = str(_pack(make_pack))
    monkeypatch.setattr("sys.stdin", io.StringIO(".nope\n.step x\n.remove 3\n"))
    assert main(["shell", pack]) == 0
    out = capsys.readouterr().out
    assert "unknown dot-command .nope" in out and "not a whole number" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(".nope\n"))
    assert main(["shell", pack, "--strict"]) == 1


def test_shell_step_on_command_and_tests_during_runs(make_pack, tmp_path, capsys, monkeypatch):
    import io

    tests = [{"command": "scoreboard players get #ticks t", "at_tick": 2, "expect_value": "3"}]
    path = _project(tmp_path, make_pack, tests, tests_during_runs=True, step_on_command=True)
    monkeypatch.setattr("sys.stdin", io.StringIO("say hi\n.step 2\n"))
    assert main(["shell", str(path)]) == 0
    out = capsys.readouterr().out
    assert "stepped to 2" in out
    assert "PASS" in out and "tick 2" in out


def test_project_new_show_set_packs_tests_and_notes(make_pack, tmp_path, capsys):
    first, second = _pack(make_pack), make_pack({}, name="extra")
    path = tmp_path / "made"
    assert main(["project", "new", str(path), str(first), "--test", "3:say hi"]) == 0
    path = tmp_path / "made.dpemu"
    assert main(["project", "new", str(path), str(first)]) == 2  # exists
    # numbers refer to the list before the edits: pack 1 moves below the new one
    assert main(["project", "packs", str(path), "--add", str(second), "--move", "1", "down"]) == 0
    assert main(["project", "set", str(path), "--version", "1.20.4", "--ticks", "-1"]) == 0
    assert main(["project", "set", str(path), "--tests-during-runs", "on", "--notes", "n"]) == 0
    assert (
        main(
            [
                "project",
                "tests",
                str(path),
                "--add",
                "say b",
                "--expect",
                "1",
                "hi",
                "--duplicate",
                "1",
                "--disable",
                "1",
            ]
        )
        == 0
    )
    assert main(["project", "tests", str(path), "--expect-value", "1", "bad"]) == 2
    assert main(["project", "tests", str(path), "--remove", "9"]) == 2
    assert main(["project", "note", str(path), "test:tick", "a note"]) == 0
    capsys.readouterr()
    assert main(["project", "note", str(path), "test:tick"]) == 0
    assert capsys.readouterr().out.strip() == "a note"

    project = Project.load(path)
    assert [pack.name for pack in project.datapacks] == ["extra", "pack1"]
    assert project.version == "1.20.4" and project.ticks == -1 and project.tests_during_runs
    assert project.notes == "n" and project.function_notes == {"test:tick": "a note"}
    # the duplicate is made before --disable 1, so only the original is disabled
    assert [(t["command"], t["expect"], t["enabled"]) for t in project.tests] == [
        ("say hi", "hi", False),
        ("say hi", "hi", True),
        ("say b", "", True),
    ]
    assert main(["project", "show", str(path)]) == 0
    out = capsys.readouterr().out
    assert "∞ until stopped" in out and "note on test:tick" in out
    assert main(["project", "show", str(tmp_path / "missing.dpemu")]) == 2
    assert main(["project", "note", str(path), "test:tick", ""]) == 0
    assert Project.load(path).function_notes == {}


def test_run_can_tick_in_real_time(make_pack, monkeypatch, capsys):
    from datapack_emulator.cli import runs

    slept = []
    monkeypatch.setattr(runs.time, "sleep", slept.append)
    assert main(["run", str(_pack(make_pack)), "--ticks", "3", "--realtime"]) == 0
    assert len(slept) == 3 and all(0 <= value <= runs.TICK_SECONDS for value in slept)


# -- review fixes and the remaining window features


def test_project_edits_number_the_list_as_it_was(make_pack, tmp_path, capsys):
    path = _project(tmp_path, make_pack, [{"command": "say a"}, {"command": "say b"}])
    args = ["project", "tests", str(path)]
    assert main([*args, "--duplicate", "1", "--remove", "2"]) == 0
    assert [t["command"] for t in Project.load(path).tests] == ["say a", "say a"]
    assert main([*args, "--move", "1", "down", "--expect", "1", "first", "--remove", "2"]) == 0
    assert [(t["command"], t["expect"]) for t in Project.load(path).tests] == [("say a", "first")]
    assert main([*args, "--disable", "1", "--enable", "1"]) == 0
    assert Project.load(path).tests[0]["enabled"] is True
    assert main([*args, "--remove", "1", "--expect", "1", "x"]) == 2
    assert main([*args, "--tick", "1", "x"]) == 2
    assert main(["project", "packs", str(path), "--remove", "1"]) == 2  # the last one
    capsys.readouterr()


def test_project_notes_use_normalised_ids(make_pack, tmp_path, capsys):
    path = _project(tmp_path, make_pack, [])
    assert main(["project", "note", str(path), "test:tick ", "counts"]) == 0
    assert main(["project", "note", str(path), "TEST:TICK", "invalid"]) == 2
    assert main(["project", "note", str(path), "tick", "vanilla"]) == 0
    notes = Project.load(path).function_notes
    assert notes == {"test:tick": "counts", "minecraft:tick": "vanilla"}
    assert "not in the project's datapacks" in capsys.readouterr().err
    assert main(["project", "note", str(path), "test:other", ""]) == 0
    assert "no note on test:other" in capsys.readouterr().out


def test_notes_and_notes_file_are_exclusive(make_pack, tmp_path):
    path = _project(tmp_path, make_pack, [])
    with pytest.raises(SystemExit):
        main(["project", "set", str(path), "--notes", "a", "--notes-file", "b"])


def test_shell_mistakes_never_end_the_session(make_pack, tmp_path, capsys, monkeypatch):
    import io

    pack = str(_pack(make_pack))
    lines = [
        ".seed abc",
        '.explain say "hi',
        ".scores it's",
        '.history "a',
        ".save " + str(tmp_path / "missing" / "dir" / "x.dpemu"),
        ".save",
        ".test say hi",
        ".expect-value 1 abc",
        ".expect-value 1 5..1",
        ".at 1 x",
        ".records 1",
        "say still here",
    ]
    (tmp_path / "missing").write_text("a file, not a folder")
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    assert main(["shell", pack, "--strict"]) == 1
    out = capsys.readouterr().out
    assert "not a whole number: 'abc'" in out
    assert out.count("cannot read the arguments") == 1
    assert "cannot save" in out and "no project open" in out
    assert "invalid expected value 'abc'" in out and "invalid expected value '5..1'" in out
    assert "has no result yet" in out
    assert "[Server] still here" in out


def test_shell_save_keeps_settings_it_did_not_change(make_pack, tmp_path, capsys, monkeypatch):
    import io

    path = _project(tmp_path, make_pack, [], ticks=-1, speed="fast")
    monkeypatch.setattr("sys.stdin", io.StringIO(".test say x\n.save\n"))
    assert main(["shell", str(path)]) == 0
    saved = Project.load(path)
    assert saved.version == "" and saved.ticks == -1 and len(saved.tests) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO(".realtime on\n.seed -4\n.save\n"))
    assert main(["shell", str(path)]) == 0
    saved = Project.load(path)
    assert saved.speed == "realtime" and saved.seed == -4 and saved.version == ""


def test_shell_tests_packs_jars_and_reports(make_pack, tmp_path, fake_jar, capsys, monkeypatch):
    import io

    pack = _pack(make_pack)
    extra = make_pack(
        {"data/test/function/tick.mcfunction": "scoreboard players add #ticks t 10\n"},
        name="extra",
    )
    lines = [
        ".test 1:scoreboard players get #ticks t",
        ".expect-value 1 2",
        ".duplicate 1",
        ".at 2 3",
        ".expect-value 2 4",
        ".disable 1 2",
        ".enable 1 2",
        ".runtests",
        ".records 2",
        ".tests",
        ".add-pack " + str(extra),
        ".packs",
        ".runtests 1",
        ".move-pack 2 up",
        ".remove-pack 1",
        ".reload",
        ".jar " + str(fake_jar),
        ".jar none",
        ".jar 0.0.1",
        ".report " + str(tmp_path / "r.html"),
        ".dot " + str(tmp_path / "g.dot"),
        ".run 2",
        ".profile",
        ".quit",
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    assert main(["shell", str(pack), "--version", "1.21.4"]) == 0
    out = capsys.readouterr().out
    assert "2/2 passed" in out and "→ PASS" in out
    assert "test: scoreboard players get #ticks t (tick 3)" in out
    assert "2. extra" in out and "FAIL" in out  # the extra pack's tick adds 10
    assert "client jar: 9.9" in out and "client jar: none" in out
    assert "error: unknown Minecraft version" in out
    assert (tmp_path / "r.html").is_file() and (tmp_path / "g.dot").is_file()
    assert "#minecraft:tick" in out  # the profile tree


def test_shell_reports_tests_a_run_does_not_reach(make_pack, tmp_path, capsys, monkeypatch):
    import io

    tests = [{"command": "say late", "at_tick": 9}, {"command": "say early", "at_tick": 0}]
    path = _project(tmp_path, make_pack, tests, tests_during_runs=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(".run 3\n.quit\n"))
    assert main(["shell", str(path)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "not reached: the run ended before tick 9" in out


def test_shell_quit_stops_every_script(make_pack, tmp_path, capsys):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text(".quit\n")
    second.write_text("say never\n")
    pack = str(_pack(make_pack))
    assert main(["shell", pack, "--script", str(first), "--script", str(second)]) == 0
    assert "never" not in capsys.readouterr().out
    assert main(["shell", pack, "--script", str(tmp_path / "none.txt")]) == 2


def test_world_json_is_clean_and_filtered(make_pack, capsys):
    import json

    pack = str(_pack(make_pack))
    args = ["world", pack, "--json", "-c", "say hi", "-c", "data merge storage a:b {x:1}"]
    assert main([*args, "--scores", "--holder", "#ti", "--level", "debug"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert set(data) == {"tick", "version", "objectives", "scores", "enabled_triggers", "gamerules"}
    assert list(data["scores"]) == ["#ticks"]
    assert "> say hi" in captured.err and "[Server] hi" in captured.err
    assert main(["world", pack, "--json", "--history", "#ticks", "t", "-c", "say x"]) == 0
    assert json.loads(capsys.readouterr().out)[0] == {"tick": 0, "value": 1}


def test_record_filters_seen_by_grep_and_details(make_pack, capsys):
    pack = make_pack({"data/test/function/tick.mcfunction": 'tellraw Player2 "psst"\nsay loud\n'})
    args = ["run", str(pack), "--ticks", "1", "--players", "2", "--version", "1.21.4"]
    assert main([*args, "--seen-by", "Player1", "--sources", "game"]) == 0
    out = capsys.readouterr().out
    assert "[Server] loud" in out and "psst" not in out
    assert main([*args, "--grep", "PSST", "--details"]) == 0
    out = capsys.readouterr().out
    assert "to Player2: psst" in out and "loud" not in out
    assert "seen by: Player2" in out
    assert "version: 1.21.4" in out


def test_matrix_prints_records_when_verbose(make_pack, capsys):
    pack = str(_pack(make_pack))
    args = ["matrix", pack, "--versions", "1.21.4", "--html", "m.html", "--verbose"]
    assert main([*args, "--level", "debug", "--sources", "app"]) == 0
    assert "running pack1 against 1.21.4" in capsys.readouterr().out


def test_run_until_interrupted_keeps_the_report(make_pack, monkeypatch, capsys):
    from datapack_emulator.emulator.runtime.emulator import Emulator

    calls = {"n": 0}
    original = Emulator.run_tick

    def ticking(self):
        calls["n"] += 1
        if calls["n"] > 4:
            raise KeyboardInterrupt
        return original(self)

    monkeypatch.setattr(Emulator, "run_tick", ticking)
    assert main(["run", str(_pack(make_pack)), "--ticks", "-1"]) == 0
    captured = capsys.readouterr()
    assert "stopped after 4 tick(s)" in captured.err and "4 tick(s)" in captured.out
    with pytest.raises(SystemExit):
        main(["world", str(_pack(make_pack)), "--ticks", "-1"])


def test_show_records_of_every_test(make_pack, capsys):
    pack = str(_pack(make_pack))
    assert main(["test", pack, "--test", "say hi", "--show-records", "all"]) == 0
    assert "[Server] hi" in capsys.readouterr().out
    assert main(["test", pack, "--test", "say hi", "--show-records"]) == 0
    assert "[Server] hi" not in capsys.readouterr().out


def test_last_project_and_recent_lists(make_pack, tmp_path, capsys):
    import json

    path = _project(tmp_path, make_pack, [], version="1.20.4")
    assert main(["run", "@last"]) == 2
    state = PATHS.CACHE / "window-state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"recent_projects": [str(tmp_path / "gone.dpemu"), str(path)]}),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["run", "@last", "--ticks", "1"]) == 0
    assert "1.20.4: 1 tick(s)" in capsys.readouterr().out
    assert main(["project", "recent"]) == 0
    out = capsys.readouterr().out
    assert "gone.dpemu  (missing)" in out and "recent datapacks:\n  none" in out


def test_info_graph_and_search_details(make_pack, capsys):
    pack = make_pack(
        {
            "data/test/tags/block/stones.json": {"values": ["minecraft:stone"]},
            "data/test/function/tick.mcfunction": "say x\n",
        }
    )
    assert main(["info", str(pack), "-r", "#test:stones"]) == 0
    assert "tags/block" in capsys.readouterr().out
    assert main(["info", str(pack), "-r", "minecraft:tick"]) == 2  # a tag needs its #
    assert main(["graph", str(pack), "-f", "#minecraft:tick", "--dot"]) == 0
    out = capsys.readouterr().out
    assert "\ntag " in out and "\nfunction " not in out
    assert list(Path("generated").glob("pack1-*.dot"))
    assert main(["search", str(pack), " "]) == 2
