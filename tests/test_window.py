"""Offscreen checks of the main window. Skipped when PySide6 is not installed."""

import json
import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    from datapack_emulator.window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
    # a test that edits settings would otherwise wait on the "save changes?" box
    window.projects.modified = False
    window.close()


def test_panels_open_and_survive_hiding(app, window):
    assert all(dock.isVisible() for dock in window.docks)
    window.hide()
    app.processEvents()
    window.show()
    app.processEvents()
    assert all(dock.isVisible() for dock in window.docks)


def test_opening_a_project_keeps_its_version(window, make_pack):
    from datapack_emulator.project import Project

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    project = Project(name="p", datapacks=[pack], version="1.20.4")
    window.projects.apply(project)
    assert window.version.id == "1.20.4"


def test_source_view_keeps_a_single_highlighter(window, make_pack):
    from PySide6.QtGui import QSyntaxHighlighter

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    for relative in (
        "data/test/function/tick.mcfunction",
        "pack.mcmeta",
        "data/test/function/tick.mcfunction",
    ):
        window.navigation.show_source(pack / relative)
    document = window.source_edit.document()
    attached = [child for child in document.children() if isinstance(child, QSyntaxHighlighter)]
    assert len(attached) == 1


def test_reload_keeps_the_selected_version(window, make_pack):
    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    window.datapacks.load(pack)
    index = window.combo_version.findData("1.20.4")
    window.combo_version.setCurrentIndex(index)
    window.datapacks.reload()
    assert window.version.id == "1.20.4"
    assert window.emulator is not None and window.emulator.version.id == "1.20.4"


def test_imported_pack_defaults_to_its_newest_declared_version(window, make_pack):
    pack = make_pack(
        {"data/test/function/tick.mcfunction": "say hi\n"},
        mcmeta={"pack": {"pack_format": 5, "supported_formats": [5, 61]}},
    )
    window.datapacks.load(pack)
    assert window.version.id == "1.21.4"


def _hat_like_pack(make_pack):
    return make_pack(
        {
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\nsay loaded\n",
            "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
        }
    )


def test_finite_run_ticks_to_the_end(window, make_pack):
    window.datapacks.load(_hat_like_pack(make_pack))
    window.spin_ticks.setValue(7)
    window.runs.run_all()
    assert window.runs.running and window.stop_button.isEnabled()
    window.runs.wait()
    assert not window.runs.running and not window.stop_button.isEnabled()
    assert window.emulator.world.tick == 7
    assert window.emulator.world.scoreboard.get("#ticks", "t") == 7
    assert window.tick_label.text() == "tick 7"
    assert window.call_graph is not None


def test_endless_run_stops_on_request_and_step_continues(window, make_pack):
    window.datapacks.load(_hat_like_pack(make_pack))
    window.spin_ticks.setValue(-1)
    assert window.spin_ticks.text() == "∞ until stopped"
    window.runs.run_emulator()
    for _ in range(3):
        window.runs._on_timer()
    ticked = window.emulator.world.tick
    assert ticked > 3 and window.runs.running
    window.stop_button.click()
    assert not window.runs.running
    assert window.statusBar().currentMessage().startswith(f"stopped on {window.version.id}:")

    window.runs.step()
    assert window.emulator.world.tick == ticked + 1


def test_tests_table_runs_commands_and_saves_with_the_project(window, make_pack, tmp_path):
    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.project import Project

    window.datapacks.load(_hat_like_pack(make_pack))
    window.environment.set_tests(
        [
            CommandTest("scoreboard players get #ticks t", at_tick=4),
            CommandTest("say hi", expect="bye"),
        ]
    )
    results = window.environment.run()
    assert [result.passed for result in results] == [True, False]
    assert window.table_tests.item(0, 5).text().startswith("✔")
    assert window.table_tests.item(1, 5).text().startswith("✘")
    assert window.test_summary.text().startswith("1/2 passed")

    window.spin_seed.setValue(42)
    window.runs.speed = "realtime"
    saved = window.projects.capture().save(tmp_path / "p.json")
    loaded = Project.load(saved)
    assert loaded.seed == 42 and loaded.speed == "realtime"
    assert [test["command"] for test in loaded.tests] == [
        "scoreboard players get #ticks t",
        "say hi",
    ]


def test_log_records_can_be_copied_and_opened_in_the_source_view(app, window, make_pack):
    from PySide6.QtWidgets import QApplication

    from datapack_emulator.emulator.runtime.output import LogLevel

    window.datapacks.load(
        make_pack(
            {
                "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
                "data/test/function/tick.mcfunction": "say hi\nscoreboard players set @a missing 1\n",
            }
        )
    )
    window.combo_level.setCurrentText("debug")
    window.spin_ticks.setValue(1)
    window.runs.run_emulator()
    window.runs.wait()
    window.log_view.flush()

    model = window.log_view.model
    rows = [model.record_at(row) for row in range(model.rowCount())]
    failure = next(r for r in rows if r.failure)
    assert failure.function == "test:tick" and failure.line == 2

    menu = window.log_view.menu_for(failure)
    texts = [action.text() for action in menu.actions() if action.text()]
    assert texts[0] == "copy error message"
    assert "open file in source view (test:tick:2)" in texts

    window.log_view.copy_message(failure)
    assert QApplication.clipboard().text() == "Unknown scoreboard objective 'missing'"

    window.log_view.open_source(failure)
    assert window.source_label.text().endswith("tick.mcfunction:2")
    assert window.source_edit.textCursor().blockNumber() == 1

    typed = next(r for r in rows if r.source.value == "app")
    assert window.log_view.source_of(typed) is None
    assert not [
        a for a in window.log_view.menu_for(typed).actions() if a.text().startswith("open")
    ][0].isEnabled()
    assert failure.level == LogLevel.DEBUG  # silent: it failed inside a function


def test_default_version_is_a_release_and_the_label_explains_incompatibility(window, make_pack):
    from datapack_emulator.emulator import versions

    newest = versions.NEWEST.format
    readable = make_pack(
        {"data/test/function/a.mcfunction": "say a\n"},
        mcmeta={
            "pack": {
                "pack_format": 15,
                "supported_formats": [15, newest],
                "min_format": 15,
                "max_format": newest,
            }
        },
    )
    window.datapacks.load(readable)
    assert window.version.stable and window.version.format <= newest
    assert "(" not in window.pack_label.text().split("emulating")[-1]

    # the range excludes pack_format, so 1.20.2+ collapse to 10: too old everywhere
    stale = make_pack(
        {"data/test/function/a.mcfunction": "say a\n"},
        mcmeta={"pack": {"pack_format": 10, "supported_formats": [15, 48]}},
    )
    window.datapacks.load(stale)
    assert window.version.id == "1.21.1"
    assert "marked incompatible: made for an older version" in window.pack_label.text()


def test_speed_change_applies_to_a_running_timer(window, make_pack):
    window.datapacks.load(_hat_like_pack(make_pack))
    window.spin_ticks.setValue(-1)
    window.runs.speed = "fast"
    window.runs.run_emulator()
    assert window.runs.timer.interval() == 0
    window.runs.speed = "realtime"
    assert window.runs.timer.interval() == window.runs.REALTIME_INTERVAL_MS
    window.runs._on_timer()
    window.runs.stop(refresh=False)
    assert window.tick_label.text() == f"tick {window.emulator.world.tick}"
    assert window.statusBar().currentMessage().startswith("stopped at tick")


def test_all_tests_disabled_says_so(window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    window.datapacks.load(_hat_like_pack(make_pack))
    window.environment.set_tests([CommandTest("say hi", enabled=False)])
    assert window.environment.run() == []
    assert window.statusBar().currentMessage().startswith("enable at least one test")


def test_hat_v2_defaults_to_a_version_that_reads_its_metadata(window):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat_v2")
    assert window.version.stable
    assert window.datapack.compatibility(window.version).status == "compatible"
    assert "(" not in window.pack_label.text().split("emulating")[-1]


def test_environment_tab_scrolls(window):
    from PySide6.QtWidgets import QScrollArea

    scroll = window.findChild(QScrollArea, "scrollEnvironment")
    assert scroll is not None and scroll.widgetResizable()
    assert window.table_tests.minimumHeight() >= 200
    assert scroll.widget().isAncestorOf(window.table_tests)


def test_ctrl_s_saves_tests_into_a_dpemu_and_open_restores_them(app, window, make_pack):
    import zipfile

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.settings import PATHS

    window.datapacks.load(_hat_like_pack(make_pack))
    window.projects.mark_saved()
    window.environment.set_tests([CommandTest("say hi", at_tick=2)])
    assert window.projects.modified and window.windowTitle().startswith("Datapack Emulator — *")

    # a cell still being edited when Ctrl+S is pressed is kept
    table = window.table_tests
    table.editItem(table.item(0, 1))
    app.processEvents()
    editor = next(w for w in table.viewport().findChildren(QLineEdit) if w.isVisible())
    editor.setText("function test:tick")
    window.activateWindow()
    QTest.keyClick(window, Qt.Key_S, Qt.ControlModifier)
    app.processEvents()

    saved = window.project.path
    assert saved is not None and saved.suffix == ".dpemu" and saved.parent == PATHS.PROJECTS
    assert not window.projects.modified and "*" not in window.windowTitle()
    with zipfile.ZipFile(saved) as archive:
        assert any(
            name.endswith("/pack.mcmeta") and name.startswith("datapacks/0/")
            for name in archive.namelist()
        )

    window.environment.set_tests([])
    window.projects.mark_saved()
    assert window.projects.open_path(saved)
    assert [(t.command, t.at_tick) for t in window.environment.tests()] == [
        ("function test:tick", 2)
    ]
    assert window.datapack.path == window.project.datapack
    assert not window.projects.modified


def test_closing_with_unsaved_changes_asks_first(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window.spin_seed.setValue(window.spin_seed.value() + 1)
    assert window.projects.modified
    monkeypatch.setattr(window.projects, "ask_save_changes", lambda: QMessageBox.Cancel)
    assert not window.close() and window.isVisible()
    monkeypatch.setattr(window.projects, "ask_save_changes", lambda: QMessageBox.Discard)
    assert window.close()


def test_saving_never_silently_replaces_a_project_with_the_same_name(window, monkeypatch):
    from datapack_emulator.project import Project

    other = Project(name=window.project.name, tests=[{"command": "say keep me"}]).save()
    window.projects.mark_modified()
    asked = []
    monkeypatch.setattr(window.projects, "save_as", lambda: asked.append(True) or False)
    assert window.projects.save() is False and asked
    assert Project.load(other).tests == [{"command": "say keep me"}]

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a: (other.stem, True))
    monkeypatch.setattr(window.projects, "confirm_overwrite", lambda path: False)
    window.projects.new()
    assert Project.load(other).tests == [{"command": "say keep me"}]


def test_a_failed_save_as_keeps_the_current_project(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    assert window.projects.save_to(tmp_path / "first.dpemu")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert not window.projects.save_to(locked / "other.dpemu")
    finally:
        locked.chmod(0o700)
    assert window.project.path == tmp_path / "first.dpemu" and window.project.name == "first"


def test_loading_another_client_jar_is_an_unsaved_change(window, fake_jar):
    window.projects.mark_saved()
    window.jars.use(window.library.load_jar(fake_jar))
    assert window.projects.modified


def test_a_project_restores_the_engine_selection(window, make_pack):
    from datapack_emulator.project import Project

    pack = _hat_like_pack(make_pack)
    window.projects.apply(Project(name="p", datapacks=[pack], engine_versions=["1.20.4", "1.21.4"]))
    window.runs.open_engine()
    engine = window.engine_window
    assert [v.id for v in engine.selected_versions()] == ["1.20.4", "1.21.4"]

    engine.select_ids(["1.21"])
    window.runs.open_engine()  # reopening the same pack keeps the ticks
    assert [v.id for v in engine.selected_versions()] == ["1.21"]
    assert window.projects.capture().engine_versions == ["1.21"]

    window.projects.apply(Project(name="q", datapacks=[pack], engine_versions=["26.2", "nope"]))
    assert [v.id for v in engine.selected_versions()] == ["26.2"]
    engine.close()


def test_tests_table_triggers_as_a_player(window):
    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.environment.set_tests([CommandTest("execute as Player1 run trigger hat")])
    assert [result.passed for result in window.environment.run()] == [True]


def test_console_runs_commands_in_the_current_world(app, window):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.edit_console.setText("/execute as Player1 run trigger hat")
    window.edit_console.returnPressed.emit()
    app.processEvents()
    assert window.emulator.world.tick == 1  # the first tick ran, enabling the trigger
    assert window.emulator.world.scoreboard.get("Player1", "hat") == 1
    assert window.edit_console.text() == ""
    assert (
        window.statusBar()
        .currentMessage()
        .startswith("execute as Player1 run trigger hat: succeeded")
    )

    result = window.console.run("trigger hat")
    assert not result.success
    window.log_view.flush()
    messages = [
        window.log_view.model.record_at(row).message
        for row in range(window.log_view.model.rowCount())
    ]
    assert "A player is required to run this command here" in messages

    window.edit_console.setText("")
    window.console.browse(-1)
    assert window.edit_console.text() == "trigger hat"


def test_world_dock_shows_scores_entities_and_storage(app, window):
    from PySide6.QtCore import Qt

    from datapack_emulator.settings import PATHS

    assert window.dock_world.isVisible()
    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.spin_ticks.setValue(3)
    window.runs.run_all()
    window.runs.wait()
    window.console.run("data modify storage test:mem list append value {a:1}")
    window.console.run("execute as Player1 run trigger hat")

    window.tabs_world.setCurrentIndex(0)
    table = window.table_scores
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    rows = [table.verticalHeaderItem(r).text() for r in range(table.rowCount())]
    assert headers == ["hat\ntrigger"] and rows[0] == "Player1"
    cell = table.item(0, 0)
    assert cell.text() == "1" and "values taken: 0, 1" in cell.toolTip()

    window.tabs_world.setCurrentIndex(1)
    tree = window.tree_entities
    tops = {
        tree.topLevelItem(i).text(0): tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
    }
    stand = tops["Armor Stand"]
    assert "tags hat_saver" in stand.text(1)
    stand.setExpanded(True)  # the NBT is built on expansion
    keys = {stand.child(i).text(0): stand.child(i).text(1) for i in range(stand.childCount())}
    assert keys["Pos"] == "[0.0d, 300.0d, 0.0d]" and keys["id"] == '"minecraft:armor_stand"'
    assert stand.data(0, Qt.UserRole + 1)

    window.tabs_world.setCurrentIndex(2)
    storage = window.tree_storage
    assert storage.topLevelItem(0).text(0) == "test:mem"
    assert "entities" in window.world_label.text()

    window.console.run('setblock 1 2 3 chest{Items:[{Slot:0b,id:"stone",count:2}]}')
    window.console.run("execute in minecraft:the_nether run setblock 0 0 0 dirt")
    window.tabs_world.setCurrentIndex(3)
    blocks = window.tree_blocks
    tops = {
        blocks.topLevelItem(i).text(0): blocks.topLevelItem(i)
        for i in range(blocks.topLevelItemCount())
    }
    chest = tops["1 2 3"]
    assert chest.text(1) == "minecraft:chest · 2 item(s)"
    assert "0 0 0 (minecraft:the_nether)" in tops
    keys = {chest.child(i).text(0): chest.child(i) for i in range(chest.childCount())}
    assert set(keys) == {"id", "Items"}
    assert "2 blocks" in window.world_label.text()
    # editing a value runs data modify block
    from unittest import mock

    count = keys["Items"].child(0).child(2)
    assert count.text(0) == "count"
    with mock.patch(
        "datapack_emulator.window.controllers.world.QInputDialog.getText", return_value=("5", True)
    ):
        window.world_view.edit_value(count)
    block = window.emulator.world.blocks.get("minecraft:overworld", (1, 2, 3))
    assert block.items[0].count == 5


def test_tests_run_during_runs_and_steps_when_ticked(window, make_pack, tmp_path):
    from datapack_emulator.emulator.testing import CommandTest
    from datapack_emulator.project import Project

    window.datapacks.load(_hat_like_pack(make_pack))
    window.environment.set_tests(
        [
            CommandTest("scoreboard players get #ticks t", at_tick=2),
            CommandTest("say never", at_tick=50),
        ]
    )
    window.check_tests_during_runs.setChecked(True)
    window.spin_ticks.setValue(5)
    window.runs.run_emulator()
    window.runs.wait()
    assert window.table_tests.item(0, 5).text() == "✔ passed (value 3)"
    assert window.table_tests.item(1, 5).text().startswith("✘ not reached")
    assert window.emulator.world.tick == 5  # the tests ran inside the run's world

    window.runs.step()  # tick 5: no test is due, the results stay
    assert window.table_tests.item(0, 5).text() == "✔ passed (value 3)"
    window.environment.set_tests([CommandTest("scoreboard players get #ticks t", at_tick=6)])
    window.runs.step()  # tick 6
    assert window.table_tests.item(0, 5).text() == "✔ passed (value 7)"

    saved = window.projects.capture().save(tmp_path / "p.dpemu")
    assert Project.load(saved).tests_during_runs is True


def test_engine_window_runs_the_environment_tests(window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    window.datapacks.load(_hat_like_pack(make_pack))
    window.environment.set_tests([CommandTest("scoreboard players get #ticks t", at_tick=1)])
    window.runs.open_engine()
    engine = window.engine_window
    # the pack uses 1.21's function/ folders: 1.20.4 loads none of it
    engine.select_ids(["1.20.4", "1.21.4"])
    engine.check_run_tests.setChecked(True)
    engine.run_matrix()
    assert engine.running and not engine.button_run.isEnabled()
    engine.wait()
    assert not engine.running and engine.button_run.isEnabled()
    assert [run.tests_summary for run in engine._runs] == ["0/1", "1/1"]
    assert engine._runs[0].status in ("errors", "tests failed")
    headers = [
        engine.results.headerData(c, Qt.Horizontal) for c in range(engine.results.columnCount())
    ]
    assert "tests" in headers
    engine.close()


def test_entity_selector_matches_exactly_that_entity(window):
    from datapack_emulator.settings import PATHS
    from datapack_emulator.window.controllers.world import entity_selector

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.console.run("summon minecraft:pig 1 2 3")
    window.console.run("summon minecraft:pig 4 5 6")
    world = window.emulator.world
    first = next(e for e in world.entities if e.type == "minecraft:pig")
    selector = entity_selector(first)
    assert selector.startswith("@e[nbt={UUID:[I;") and selector.endswith("]},limit=1]")
    window.console.run(f"execute as {selector} at @s run tag @s add picked")
    assert [e.tags for e in world.entities if e.type == "minecraft:pig"] == [{"picked"}, set()]
    assert entity_selector(world.players[0]) == "Player1"


def test_analyze_line_from_the_source_view_and_a_log_record(window):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.navigation.open_function("hat:tick", line=2)
    window.navigation.analyze_cursor_line()
    rows = {
        window.inspector.topLevelItem(i).text(0): window.inspector.topLevelItem(i).text(1)
        for i in range(window.inspector.topLevelItemCount())
    }
    assert rows["from"] == "hat:tick:2" and rows["command"].startswith("execute")
    assert "step 1: as @a" in rows

    window.console.run("scoreboard players get Nobody hat")
    window.log_view.flush()
    model = window.log_view.model
    record = next(
        model.record_at(r)
        for r in range(model.rowCount())
        if model.record_at(r).message.startswith("> ")
    )
    assert window.log_view.command_of(record) == "scoreboard players get Nobody hat"
    window.log_view.analyze(record)
    assert window.inspector.topLevelItem(1).text(1).startswith("scoreboard")


def test_project_and_function_notes_are_saved(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from datapack_emulator.project import Project
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.projects.mark_saved()
    window.edit_project_notes.setPlainText("check the armor stand")
    assert window.projects.modified

    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a: ("swaps the hat", True))
    window.notes.edit_function_note("hat:tick")
    rows = {
        window.inspector.topLevelItem(i).text(0): window.inspector.topLevelItem(i).text(1)
        for i in range(window.inspector.topLevelItemCount())
    }
    assert rows["your note"] == "swaps the hat"
    path = window.navigation.function_path("hat:tick")
    labels = [a.text() for a in window.navigation.path_menu(path).actions()]
    assert "edit note…" in labels

    saved = window.projects.capture().save(tmp_path / "notes.dpemu")
    loaded = Project.load(saved)
    assert loaded.notes == "check the armor stand"
    assert loaded.function_notes == {"hat:tick": "swaps the hat"}

    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a: ("", True))
    window.notes.edit_function_note("hat:tick")
    assert window.project.function_notes == {}


def test_tests_workflow_buttons_and_records(app, window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    window.datapacks.load(_hat_like_pack(make_pack))
    env = window.environment
    env.set_tests([CommandTest("say one"), CommandTest("say two", expect_value="2")])
    window.table_tests.selectRow(0)
    env.duplicate_selected()
    assert [t.command for t in env.tests()] == ["say one", "say one", "say two"]
    window.table_tests.clearSelection()
    window.table_tests.selectRow(2)
    assert env.selected_rows() == [2], env.selected_rows()
    env.move_selected(-1)
    assert [t.command for t in env.tests()] == ["say one", "say two", "say one"]

    results = env.run([1])  # only "say two"
    assert len(results) == 1 and not results[0].passed  # say returns 1, not 2
    assert window.table_tests.item(0, 5).text() == "not run"
    env.reveal_records(1)
    selected = window.log_table.selectionModel().selectedRows()
    assert selected and window.log_view.model.record_at(selected[0].row()).message.startswith(
        "test: say two"
    )

    window.console.run("scoreboard players add #x t 1")
    window.edit_console.clear()
    window.console.add_as_test()
    last = env.tests()[-1]
    assert last.command == "scoreboard players add #x t 1" and last.at_tick == 0


def test_search_dialog_quick_open_and_text(app, window):
    from datapack_emulator.settings import PATHS
    from datapack_emulator.window.search_dialog import MODE_TEXT, SearchDialog

    window.datapacks.load(PATHS.SAMPLES / "hat")
    view = window.datapack.view_for(window.version)
    opened = []
    dialog = SearchDialog(view, lambda path, line: opened.append((path.name, line)), parent=window)
    dialog.edit.setText("tick")
    labels = [dialog.results.item(i).text() for i in range(dialog.results.count())]
    assert "hat:tick  (function)" in labels and "#minecraft:tick  (function tag)" in labels
    dialog.combo_mode.setCurrentIndex(MODE_TEXT)
    dialog.edit.setText("hat_saver")
    assert dialog.results.count() >= 2 and dialog.results.item(0).text().startswith("hat:tick:")
    dialog.activate_current()
    assert opened and opened[0][0] == "tick.mcfunction" and opened[0][1] > 0


def test_source_view_runs_functions_and_shows_callers(window):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.navigation.open_function("hat:tick")
    window.console.run("function hat:tick")
    assert any(e.type == "minecraft:armor_stand" for e in window.emulator.world.entities)
    window.navigation.calls_and_callers("hat:tick")
    rows = {
        window.inspector.topLevelItem(i).text(0): window.inspector.topLevelItem(i).text(1)
        for i in range(window.inspector.topLevelItemCount())
    }
    assert "#minecraft:tick" in rows["called by"]


def test_world_dock_edits_scores_and_nbt_through_commands(app, window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from datapack_emulator.settings import PATHS
    from datapack_emulator.window.score_graph import ScoreGraphDialog, step_segments

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.console.run("scoreboard objectives add kills dummy")
    window.console.run("scoreboard players set Player1 kills 2")
    window.console.run("summon minecraft:pig 1 2 3")
    window.console.run("data modify storage demo:mem n set value 4")
    view = window.world_view

    window.tabs_world.setCurrentIndex(0)
    view.refresh()
    row = view._holders.index("Player1")
    column = view._objectives.index("kills")
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (9, True))
    window.table_scores.cellDoubleClicked.emit(row, column)
    board = window.emulator.world.scoreboard
    assert board.get("Player1", "kills") == 9

    window.edit_objective_filter.setText("kil")
    assert view._objectives == ["kills"]
    window.edit_objective_filter.clear()

    ((edges, values),) = step_segments(board, "Player1", "kills", window.emulator.world.tick)
    assert values == [2, 9] and len(edges) == len(values) + 1
    ScoreGraphDialog(board, "Player1", "kills", window.emulator.world.tick, window).close()

    window.tabs_world.setCurrentIndex(1)
    tree = window.tree_entities
    pig = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == "Pig"
    )
    pig.setExpanded(True)
    pos = next(pig.child(i) for i in range(pig.childCount()) if pig.child(i).text(0) == "Pos")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("[7.0d, 8.0d, 9.0d]", True))
    view.edit_value(pos)
    assert next(
        e for e in window.emulator.world.entities if e.type == "minecraft:pig"
    ).position == [7.0, 8.0, 9.0]

    window.tabs_world.setCurrentIndex(2)
    storage = window.tree_storage.topLevelItem(0)
    storage.setExpanded(True)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("12", True))
    view.edit_value(storage.child(0))
    assert window.emulator.world.storage["demo:mem"]["n"] == 12


def test_session_remembers_recent_items_and_layout(app, window, make_pack, tmp_path):
    from datapack_emulator.window.controllers.session import state_file

    pack = _hat_like_pack(make_pack)
    window.datapacks.load(pack)
    saved = window.projects.capture().save(tmp_path / "recent.dpemu")
    window.projects._write(window.project, saved)
    assert window.session.recent_datapacks[0] == str(pack.resolve())
    assert window.session.recent_projects[0] == str(saved.resolve())

    window.recent_datapacks_menu.aboutToShow.emit()
    labels = [action.text() for action in window.recent_datapacks_menu.actions()]
    assert any(f"  {pack.name}  —" in label for label in labels)

    window.dock_world.hide()
    window.session.save()
    assert state_file().is_file()
    window.session.reset_layout()
    assert window.dock_world.isVisible()

    window.session.focus_console()
    assert window.dock_logs.isVisible()


def test_help_notes_explain_rows_versions_and_columns(window):
    from PySide6.QtCore import Qt as QtCore_Qt

    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat_v2")
    assert window.version_note.text().startswith("1.21.8: lists the pack as compatible")
    index = window.combo_version.findData("26.2")
    window.combo_version.setCurrentIndex(index)
    assert "cannot read pack.mcmeta" in window.version_note.text()
    first = window.inspector.topLevelItem(0)
    assert first.text(0) == "path" and first.toolTip(0) == "path\nthe datapack folder"
    model = window.log_view.model
    assert "silent in game" in model.headerData(2, QtCore_Qt.Horizontal, QtCore_Qt.ToolTipRole)
    from PySide6.QtGui import QAction

    missing = [
        action.objectName()
        for action in window.findChildren(QAction)
        if action.objectName().startswith("action") and not action.statusTip()
    ]
    assert missing == [], "every menu action explains itself in the status bar"


def test_damaged_window_state_and_tag_notes(app, tmp_path, monkeypatch):
    import json

    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "window-state.json").write_text(
        json.dumps({"recent_projects": None, "recent_datapacks": [1, "x"], "docks": 5})
    )
    from datapack_emulator.window import MainWindow

    window = MainWindow()
    try:
        assert window.session.recent_projects == []
        window.notes.set_function_note("#minecraft:tick", "the tag")
        assert window.notes.function_note("minecraft:tick") == ""
        assert window.notes.function_note("#minecraft:tick") == "the tag"
    finally:
        window.projects.modified = False
        window.close()


def test_score_graph_segments_leave_gaps_for_resets():
    from collections import deque

    from datapack_emulator.emulator.runtime.world import Scoreboard
    from datapack_emulator.window.score_graph import step_segments

    board = Scoreboard()
    board.history[("p", "o")] = deque([(1, 5), (3, None), (5, 7), (8, None)])
    assert step_segments(board, "p", "o", 10) == [([1, 3], [5]), ([5, 8], [7])]
    board.history[("p", "o")] = deque([(1, 5), (4, 6)])
    assert step_segments(board, "p", "o", 10) == [([1, 4, 10], [5, 6])]


def test_log_table_keeps_visible_records_on_long_runs(app, monkeypatch):
    from datapack_emulator.emulator.runtime.output import LogLevel, LogRecord, LogSource
    from datapack_emulator.window.panels import LogTableModel

    monkeypatch.setattr(LogTableModel, "MAX_RECORDS", 50)
    model = LogTableModel()
    model.extend([LogRecord(LogSource.GAME, LogLevel.INFO, "[Player1] hello", tick=0)])
    for tick in range(20):
        model.extend(
            [
                LogRecord(LogSource.GAME, LogLevel.DEBUG, f"feedback {i}", tick=tick)
                for i in range(10)
            ]
        )
    assert model.record_at(0).message == "[Player1] hello"  # debug records went first


def test_profiler_tab_shows_a_per_tick_call_tree(window, make_pack):
    window.datapacks.load(_hat_like_pack(make_pack))
    window.spin_ticks.setValue(4)
    window.runs.run_all()
    window.runs.wait()
    tree = window.tree_profile
    tops = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert "#minecraft:tick" in tops and "#minecraft:load" in tops
    tick = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == "#minecraft:tick"
    )
    child = tick.child(0)
    assert child.text(0) == "test:tick" and child.text(4) == "1.00"  # one call per tick
    assert window.profile_summary.text().startswith("average of 4 tick(s)")


def test_world_dock_shows_inventories(window):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(PATHS.SAMPLES / "hat")
    window.console.run("give Player1 minecraft:stone 5")
    window.tabs_world.setCurrentIndex(1)
    window.world_view.refresh()
    tree = window.tree_entities
    player = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == "Player1"
    )
    assert "5 item(s)" in player.text(1)
    player.setExpanded(True)
    keys = [player.child(i).text(0) for i in range(player.childCount())]
    assert "Inventory" in keys and "SelectedItem" in keys


def test_several_datapacks_are_analyzed_and_removed_one_by_one(app, window, make_pack, tmp_path):
    from datapack_emulator.project import Project
    from datapack_emulator.settings import PATHS
    from datapack_emulator.window.panels import PACK_INDEX_ROLE

    window.datapacks.load(PATHS.SAMPLES / "hat")
    extra = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["extra:tick"]},
            "data/extra/function/tick.mcfunction": "say extra\n",
        },
        name="extra",
    )
    window.datapacks.add(extra)
    packs = window.datapack
    assert [pack.name for pack in packs] == ["hat", "extra"]
    model = window.tree.model()
    assert model.rowCount() == 2 and model.item(1).data(PACK_INDEX_ROLE) == 1
    assert window.project.datapacks == packs.paths and window.projects.modified

    window.spin_ticks.setValue(1)
    window.runs.run_emulator()
    window.runs.wait()
    view = window.datapack.view_for(window.version)
    assert view.resolve_function_tag("#minecraft:tick") == ["hat:tick", "extra:tick"]

    saved = window.projects.capture().save(tmp_path / "two.dpemu")
    window.datapacks.remove(0)
    assert [pack.name for pack in window.datapack] == ["extra"]
    window.datapacks.remove(0)
    assert window.datapack is None and window.emulator is None
    assert window.tree.model().rowCount() == 1  # the "no datapack" row

    window.datapacks.load(PATHS.SAMPLES / "hat_v2")  # like the sample opened at start
    window.projects.mark_saved()
    assert window.projects.open_path(saved)
    assert [pack.name for pack in window.datapack] == ["hat", "extra"]  # the project's own packs
    assert all(".cache" in str(path) for path in window.datapack.paths)  # unpacked from the archive

    window.remove_datapack_menu.aboutToShow.emit()
    labels = [action.text() for action in window.remove_datapack_menu.actions()]
    assert labels == ["1. hat", "2. extra"]
    window.datapacks.move(1, -1)
    assert [pack.name for pack in window.datapack] == ["extra", "hat"]
    assert (
        Project.load(window.projects.capture().save(tmp_path / "moved.dpemu")).datapacks[0].name
        == "extra"
    )


def test_shared_ids_inspect_the_clicked_file_and_the_engine_follows(app, window, make_pack):
    first = make_pack({"data/test/function/tick.mcfunction": "say one\n"}, name="p1")
    second = make_pack({"data/test/function/tick.mcfunction": "say two\nsay again\n"}, name="p2")
    window.datapacks.load_many([first, second])
    later = second / "data" / "test" / "function" / "tick.mcfunction"
    resource = window.datapacks.find_resource("test:tick", str(later))
    assert resource.path == later and len(resource.content) == 2

    window.runs.open_engine()
    window.datapacks.remove(1)
    assert window.engine_window.datapack.name == "p1"
    window.engine_window.close()


def test_logs_show_what_each_player_reads(app, window, make_pack):
    pack = make_pack(
        {
            "data/test/function/tick.mcfunction": (
                'tellraw @a {"text":"to all"}\n'
                'tellraw Player2 {"text":"only two"}\n'
                'title Player1 actionbar {"text":"bar"}\n'
                "say everyone hears this\n"
                'tellraw @a {"translate":"missing.key","fallback":"fallback text"}\n'
                'data modify storage t:m note set value "stored"\n'
                'tellraw Player1 {"nbt":"note","storage":"t:m"}\n'
            )
        }
    )
    window.spin_players.setValue(2)
    window.datapacks.load(pack)
    window.spin_ticks.setValue(1)
    window.runs.run_emulator()
    window.runs.wait()
    model = window.log_view.model

    def shown():
        return [model.record_at(row).message for row in range(model.rowCount())]

    assert {"to Player1: to all", "to Player2: to all", "to Player2: only two"} <= set(shown())
    assert "to Player1: fallback text" in shown() and "to Player1: stored" in shown()
    assert [window.combo_seen_by.itemText(i) for i in range(window.combo_seen_by.count())] == [
        "everyone",
        "Player1",
        "Player2",
    ]
    window.combo_seen_by.setCurrentText("Player2")
    assert shown() == [
        "to Player2: to all",
        "to Player2: only two",
        "[Server] everyone hears this",
        "to Player2: fallback text",
    ]


def test_removing_every_pack_without_a_project_opens_the_default_pack(window, make_pack):
    from datapack_emulator.project import default_sample

    window.datapacks.load(make_pack({"data/test/function/tick.mcfunction": "say x\n"}))
    assert window.project.path is None
    window.datapacks.remove(0)
    assert window.datapack is not None and window.datapack.paths == [default_sample()]


def test_open_recent_and_last_project(app, window, make_pack, tmp_path):
    window.datapacks.load(_hat_like_pack(make_pack))
    first = window.projects.capture().save(tmp_path / "first.dpemu")
    window.projects._write(window.project, first)
    second = tmp_path / "second.dpemu"
    window.projects.save_to(second)
    assert window.session.recent_projects[:2] == [str(second.resolve()), str(first.resolve())]

    window.recent_projects_menu.aboutToShow.emit()
    texts = [action.text() for action in window.recent_projects_menu.actions() if action.text()]
    assert texts[0].startswith("&1  second") and texts[-1] == "clear the list"

    window.projects.mark_saved()
    assert window.session.open_project(first)
    assert window.project.path == first
    assert (
        window.session.open_last_project() and window.project.path == first
    )  # first is newest now

    first.unlink()
    assert not window.session.open_project(first)
    assert str(first.resolve()) not in window.session.recent_projects
    window.session.clear_recent_projects()
    assert not window.session.open_last_project()


def test_step_on_sent_command(window, make_pack, tmp_path):
    from datapack_emulator.project import Project

    window.datapacks.load(_hat_like_pack(make_pack))
    window.console.run("say first")  # starts the world: the first tick runs
    assert window.emulator.world.tick == 1
    window.check_step_on_command.setChecked(True)
    window.console.run("scoreboard players get #ticks t")
    assert window.emulator.world.tick == 2  # the command, then one more tick
    assert window.emulator.world.scoreboard.get("#ticks", "t") == 2
    assert window.statusBar().currentMessage().endswith("then stepped to game time 2")
    saved = window.projects.capture().save(tmp_path / "step.dpemu")
    assert Project.load(saved).step_on_command is True


DEBUG_PACK = {
    "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
    "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
    "data/test/function/load.mcfunction": "scoreboard objectives add n dummy\n",
    "data/test/function/tick.mcfunction": (
        "scoreboard players add #t n 1\nfunction test:inner\nscoreboard players add #t n 10\n"
    ),
    "data/test/function/inner.mcfunction": "# comment\nscoreboard players add #i n 1\n",
}


def _answers(window, answers):
    """Answer each stop in turn from inside the nested loop: a button name, or
    (console command, button name). What each stop showed is recorded; a stop
    with no answer left is abandoned so a failing test cannot hang."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton

    seen = []
    answered = []
    debug = window.debug

    def poll():
        pause = debug.current()
        if pause is None or (answered and answered[-1] is pause):
            if answers or not seen:
                QTimer.singleShot(2, poll)
            return
        answered.append(pause)
        watches = debug.tree_watches
        seen.append(
            (
                pause.function_id,
                pause.line,
                window.source_edit.stopped_line,
                debug.tree_stack.topLevelItemCount(),
                [watches.topLevelItem(i).text(1) for i in range(watches.topLevelItemCount())],
            )
        )
        answer = answers.pop(0) if answers else "buttonDebugStop"
        if isinstance(answer, tuple):
            window.console.run(answer[0])
            answer = answer[1]
        QTimer.singleShot(2, poll)
        window.findChild(QPushButton, answer).click()

    QTimer.singleShot(0, poll)
    return seen


def test_debugger_stops_steps_and_keeps_breakpoints(app, window, make_pack, tmp_path):
    pack = make_pack(DEBUG_PACK)
    window.datapacks.load(pack)
    debug = window.debug
    window.navigation.open_function("test:tick")
    # F9 on a comment moves to the next command; the gutter shows the dot
    window.navigation.open_function("test:inner", line=1)
    debug.toggle_at_cursor()
    assert debug.debugger.lines_of("test:inner") == {2}
    assert window.source_edit.breakpoints == {2: True}
    window.navigation.open_function("test:tick")
    debug.toggle_source_line(1)
    assert window.source_edit.breakpoints == {1: True}
    debug.edit_watch.setText("score #t n")
    debug.add_watch()
    assert debug.tree_breakpoints.topLevelItemCount() == 2

    seen = _answers(
        window,
        [
            "buttonDebugStepOver",
            "buttonDebugContinue",
            ("scoreboard players set #t n 100", "buttonDebugStepOut"),
            "buttonDebugContinue",
        ],
    )
    window.runs.step()
    assert seen[0][:4] == ("test:tick", 1, 1, 1)
    assert seen[1][:4] == ("test:tick", 2, 2, 1)
    assert seen[2][:4] == ("test:inner", 2, 2, 2)
    assert seen[2][4] == ["1"]  # the first line ran, the watch shows it
    assert seen[3][:2] == ("test:tick", 3)  # stepped out to the caller
    assert seen[3][4] == ["100"]
    assert not debug.paused and window.source_edit.stopped_line == 0
    # the console command ran while stopped, then the tick finished
    assert window.emulator.world.scoreboard.get("#t", "n") == 110
    assert window.run_button.isEnabled() and window.step_button.isEnabled()

    # disabled breakpoints do not stop; the stop button abandons the tick
    item = debug.tree_breakpoints.topLevelItem(1)
    item.setCheckState(0, Qt.Unchecked)
    assert not debug.debugger.breakpoints[("test:tick", 1)].enabled
    seen = _answers(window, ["buttonDebugStop"])
    window.runs.step()
    assert [entry[:2] for entry in seen] == [("test:inner", 2)]
    assert window.emulator.world.scoreboard.get("#t", "n") == 111
    assert window.emulator.world.scoreboard.get("#i", "n") == 1

    # a stop during a run: stop from the run controls abandons it
    debug.debugger.breakpoints[("test:tick", 1)].enabled = True
    window.spin_ticks.setValue(5)
    from PySide6.QtCore import QTimer

    stops = []

    def stop_run():
        if debug.paused:
            stops.append(debug.current().line)
            window.runs.stop()
        else:
            QTimer.singleShot(2, stop_run)

    QTimer.singleShot(0, stop_run)
    window.runs.start(graph=False)
    window.runs.wait()
    assert stops == [1]
    assert not window.runs.running and not debug.paused
    assert window.emulator.world.scoreboard.get("#t", "n") is None  # load ran, the tick did not

    # breakpoints and watches are saved with the project
    project = window.projects.capture()
    assert project.breakpoints == ["test:inner:2", "test:tick:1"]
    assert project.watches == ["score #t n"]
    debug.clear_breakpoints()
    assert window.source_edit.breakpoints == {}
    debug.load(["test:tick:3 if score #t n matches 5", "!test:inner:2"], [])
    assert debug.debugger.breakpoints[("test:tick", 3)].condition == "if score #t n matches 5"
    assert not debug.debugger.breakpoints[("test:inner", 2)].enabled
    assert debug.tree_watches.topLevelItemCount() == 0


def test_engine_window_runs_in_the_background_and_cancels(window, make_pack, monkeypatch):
    import threading

    from datapack_emulator.emulator.runtime.emulator import Emulator

    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "say hi\n",
        }
    )
    window.datapacks.load(pack)
    window.runs.open_engine()
    engine = window.engine_window
    engine.select_ids(["1.20.4", "1.21.4", "1.21.5"])
    engine.spin_ticks.setValue(50)

    # the worker blocks in its third tick until the test lets it go on
    ticking = threading.Event()
    go_on = threading.Event()
    original = Emulator.run_tick
    main_thread = threading.current_thread()

    def run_tick(self):
        if threading.current_thread() is not main_thread and self.world.tick == 2:
            ticking.set()
            go_on.wait(10)
        return original(self)

    monkeypatch.setattr(Emulator, "run_tick", run_tick)
    engine.label_detail.setText("stale")
    engine.run_matrix()
    assert engine.running and engine.label_detail.text() == ""
    assert not engine.combo_from.isEnabled()
    engine.run_matrix()  # a second run waits for the first
    assert "cancel it first" in engine.statusBar().currentMessage()
    assert ticking.wait(10)
    engine.cancel()
    go_on.set()
    engine.wait()
    assert not engine.running and engine.combo_from.isEnabled()
    assert [run.status for run in engine._runs] == ["cancelled"]
    assert engine._runs[0].ticks_summary == "3/50"
    message = engine.statusBar().currentMessage()
    assert message.startswith("cancelled: 1 version(s)") and "2 version(s) not run" in message
    assert "3/50 tick(s)" in engine.label_detail.text()

    # a finished run reports every version
    monkeypatch.setattr(Emulator, "run_tick", original)
    engine.spin_ticks.setValue(2)
    engine.run_matrix()
    engine.wait()
    assert len(engine._runs) == 3 and all(run.ticks == 2 for run in engine._runs)
    assert engine.statusBar().currentMessage().startswith("done: 3 version(s)")

    # switching packs forgets a run without waiting for it
    ticking.clear()
    go_on.clear()
    monkeypatch.setattr(Emulator, "run_tick", run_tick)
    engine.spin_ticks.setValue(50)
    engine.run_matrix()
    assert ticking.wait(10)
    engine.set_datapack(window.datapack)
    assert not engine.running and engine._runs == []
    go_on.set()
    old = list(engine._jobs.values())
    for thread, _worker in old:
        thread.wait(10000)
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    assert engine._runs == [] and engine._jobs == {}

    # a failing version says which one
    def broken(self, version, output=None, cancelled=None):
        raise RuntimeError("kaput")

    from datapack_emulator.emulator.engine import TestEngine

    monkeypatch.setattr(TestEngine, "run_version", broken)
    engine.select_ids(["1.20.4", "1.21.4"])
    engine.run_matrix()
    engine.wait()
    assert (
        engine.statusBar()
        .currentMessage()
        .startswith("the run failed on 1.20.4: RuntimeError: kaput")
    )
    engine.close()


def test_debugger_guards_while_stopped(app, window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    pack = make_pack(
        {
            **DEBUG_PACK,
            "data/test/function/probe.mcfunction": "say probe\n",
        }
    )
    window.datapacks.load(pack)
    debug = window.debug
    debug.debugger.add("test:tick", 1)
    checks = {}

    def while_stopped():
        checks["stop enabled"] = window.stop_button.isEnabled()
        checks["run refused"] = window.environment.run() == []
        checks["message"] = window.statusBar().currentMessage()
        window.runs.step()  # refused too: no second stop
        checks["still one stop"] = debug.paused

    from PySide6.QtCore import QTimer

    def poll():
        if debug.paused:
            while_stopped()
            window.findChild(QPushButton_type(), "buttonDebugStop").click()
        else:
            QTimer.singleShot(2, poll)

    QTimer.singleShot(0, poll)
    window.runs.step()
    assert checks == {
        "stop enabled": True,
        "run refused": True,
        "message": "stopped in the debugger: continue or stop first",
        "still one stop": True,
    }
    assert window.emulator.world.tick == 1  # the abandoned tick still counted
    assert not window.stop_button.isEnabled()

    # a stop inside a test run during a step is abandoned cleanly
    debug.debugger.clear()
    debug.debugger.add("test:probe", 1)
    window.check_tests_during_runs.setChecked(True)
    window.environment.set_tests([CommandTest("function test:probe", at_tick=1)])
    QTimer.singleShot(0, poll)
    window.runs.step()
    assert not debug.paused and window.step_button.isEnabled()

    # a pause nothing reached is forgotten when the run ends
    window.check_tests_during_runs.setChecked(False)
    debug.debugger.clear()
    window.spin_ticks.setValue(-1)
    window.runs.start(graph=False)
    debug.pause()
    window.runs.stop()
    assert not debug.debugger._pause_requested.is_set()

    # unticking a breakpoint rebuilds the list later, not inside the signal
    debug.debugger.add("test:tick", 3)
    debug.fill_breakpoints()
    item = debug.tree_breakpoints.topLevelItem(0)
    item.setCheckState(0, Qt.Unchecked)
    assert debug.tree_breakpoints.topLevelItem(0) is item
    app.processEvents()
    assert not debug.debugger.breakpoints[("test:tick", 3)].enabled
    debug.edit_watch.setText("if function test:probe")
    debug.add_watch()
    assert debug.debugger.watches == []
    assert "cannot be a condition" in window.statusBar().currentMessage()


def QPushButton_type():  # noqa: N802
    from PySide6.QtWidgets import QPushButton

    return QPushButton


def test_tests_have_checks(window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    pack = make_pack(
        {
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
            "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
        }
    )
    window.datapacks.load(pack)
    env = window.environment
    env.set_tests([CommandTest("", at_tick=1, checks=["score #ticks t = 2"])])
    assert window.table_tests.item(0, 4).text() == "score #ticks t = 2"
    assert not env.edit_checks(0, "score #ticks t = 2\nnonsense")
    assert "not saved" in window.statusBar().currentMessage()
    assert env.edit_checks(0, "score #ticks t = 2\n\n@e[type=pig] = 0\n")
    assert env.tests()[0].checks == ["score #ticks t = 2", "@e[type=pig] = 0"]
    assert window.projects.modified
    results = env.run()
    assert results[0].passed, results[0].reason
    assert window.table_tests.item(0, 5).text() == "✔ passed (checks only), 2 check(s) held"
    env.edit_checks(0, "score #ticks t = 7")
    env.run()
    assert window.table_tests.item(0, 5).text() == "✘ score #ticks t: expected exactly 7, got 2"
    assert window.projects.capture().tests[0]["checks"] == ["score #ticks t = 7"]


def test_checks_edits_clear_results_and_keep_records(window, make_pack):
    from datapack_emulator.emulator.testing import CommandTest

    pack = make_pack(
        {
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
            "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
        }
    )
    window.datapacks.load(pack)
    env = window.environment
    env.set_tests(
        [
            CommandTest("", at_tick=1, checks=["score #ticks t = 2"]),
            CommandTest("say hi", at_tick=1),
        ]
    )
    env.run()
    assert env.results[0].records  # a checks-only test has records too
    env.reveal_records(0)
    assert "run this test first" not in window.statusBar().currentMessage()
    window.projects.mark_saved()
    assert env.edit_checks(0, "score #ticks t = 2")  # unchanged: nothing to do
    assert not window.projects.modified and 0 in env.results
    assert env.edit_checks(0, "score #ticks t = 99")
    assert window.table_tests.item(0, 5).text() == "edited: run it again"
    assert 0 not in env.results and 1 in env.results
    window.table_tests.item(1, 1).setText("say changed")
    assert window.table_tests.item(1, 5).text() == "edited: run it again"
    assert window.test_summary.text() == "tests edited since the last run"
    env.duplicate_selected()  # no selection: nothing
    window.table_tests.selectRow(0)
    env.duplicate_selected()
    assert env.tests()[1].checks == ["score #ticks t = 99"]


def test_problems_dock(app, window, make_pack):
    from test_problems import BAD_PACK

    window.datapacks.load(make_pack(BAD_PACK))
    app.processEvents()  # the check runs once the load is over
    problems = window.problems
    assert problems.problems and "error(s)" in problems.label.text()
    count_all = problems.tree.topLevelItemCount()
    problems.combo_severity.setCurrentText("error")
    errors = problems.tree.topLevelItemCount()
    assert 0 < errors < count_all
    problems.edit_filter.setText("tpye")
    problems.fill()  # the filter waits for typing to pause
    assert problems.tree.topLevelItemCount() == 1
    item = problems.tree.topLevelItem(0)
    assert item.text(1) == "test:tick:1" and item.text(3) == "function-not-loaded"
    problems.open(item)
    assert window.navigation.source_path.name == "tick.mcfunction"
    assert window.source_edit.textCursor().blockNumber() == 0
    # a fixed pack checks clean after a reload
    window.datapacks.load(make_pack({"data/test/function/tick.mcfunction": "say hi\n"}))
    app.processEvents()
    problems.edit_filter.clear()
    problems.combo_severity.setCurrentText("info")
    assert problems.problems == [] and problems.tree.topLevelItemCount() == 0


def test_inspector_and_source_reveal_the_explorer_row_and_the_graph(app, window, make_pack):
    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "function test:deep/inner\n",
            "data/test/function/deep/inner.mcfunction": "say hi\n",
            "data/test/function/other.mcfunction": "say other\n",
        }
    )
    window.datapacks.load(pack)
    tree = window.tree
    tree.collapseAll()
    tree.clearSelection()
    # show in inspector (graph, profiler, notes menus) selects and reveals the file
    window.navigation.inspect_function("test:deep/inner")
    index = tree.currentIndex()
    assert index.data() == "inner.mcfunction"
    assert tree.isExpanded(index.parent()) and tree.isExpanded(index.parent().parent())
    # opening a file in the source view follows in the explorer too
    window.navigation.open_function("test:other")
    assert tree.currentIndex().data() == "other.mcfunction"
    assert window.navigation.reveal_in_explorer(pack / "nope.mcfunction") is False

    # from the source view, the call graph opens centred on the function
    window.navigation.open_function("test:deep/inner")
    assert window.call_graph is None
    window._action("actionshow_in_graph").trigger()
    assert window.call_graph is not None
    assert window.tabs.currentWidget() is window.tab_page("graph_view")
    assert window.graph_widget.focused == "test:deep/inner"
    assert "called by 1, calls 0" in window.statusBar().currentMessage()
    window.navigation.show_in_graph("test:missing")
    assert window.graph_widget.focused is None
    assert "not in the call graph" in window.statusBar().currentMessage()
    window.navigation.source_path = None
    window.navigation.show_in_graph()
    assert "open a function" in window.statusBar().currentMessage()


def test_problems_check_once_and_only_when_shown(app, window, make_pack):
    from unittest import mock

    from test_problems import BAD_PACK

    problems = window.problems
    with mock.patch(
        "datapack_emulator.window.controllers.problems.find_problems",
        wraps=__import__(
            "datapack_emulator.emulator.analysis.problems", fromlist=["find_problems"]
        ).find_problems,
    ) as checked:
        window.datapacks.load(make_pack(BAD_PACK))
        app.processEvents()
        assert checked.call_count == 1
        window.dock_problems.hide()
        window.datapacks.reload()
        app.processEvents()
        assert checked.call_count == 1 and problems.stale
        window.dock_problems.show()
        app.processEvents()
        assert checked.call_count == 2 and not problems.stale
    with mock.patch(
        "datapack_emulator.window.controllers.problems.find_problems",
        side_effect=RecursionError("deep"),
    ):
        problems.refresh()
    assert problems.label.text() == "the check failed: RecursionError: deep"
    assert window.datapack is not None


def test_navigation_follows_tags_overlays_and_hubs(app, window, make_pack):
    files = {
        "data/minecraft/tags/function/tick.json": {"values": ["#test:tick"]},
        "data/test/tags/function/tick.json": {"values": ["test:tick"]},
        "data/test/function/tick.mcfunction": "".join(
            f"function test:f{index}\n" for index in range(40)
        ),
        "overlay_old/data/test/function/tick.mcfunction": "say old\n",
    }
    for index in range(40):
        files[f"data/test/function/f{index}.mcfunction"] = "say hi\n"
    pack = make_pack(
        files,
        mcmeta={
            "pack": {"pack_format": 61, "description": "t"},
            "overlays": {"entries": [{"formats": [1, 10], "directory": "overlay_old"}]},
        },
    )
    window.datapacks.load(pack)
    navigation = window.navigation
    tree = window.tree

    # a tag named like a function is the tag, in the inspector and the explorer
    navigation.inspect_function("#test:tick")
    assert tree.currentIndex().data() == "tick.json"
    navigation.calls_and_callers("test:tick")
    assert tree.currentIndex().data() == "tick.mcfunction"

    # a tag file in the source view opens its node in the call graph
    navigation.show_source(pack / "data/test/tags/function/tick.json")
    navigation.show_in_graph()
    assert window.graph_widget.focused == "#test:tick"
    # a file the version does not use still names its function
    assert (
        navigation.resource_id_at(pack / "overlay_old/data/test/function/tick.mcfunction")
        == "test:tick"
    )

    # a hub stays centred and readable
    navigation.show_in_graph("test:tick")
    view_range = window.graph_widget.plot.viewRange()
    x, y = window.graph_widget._positions["test:tick"]
    assert abs(sum(view_range[0]) / 2 - x) < 1e-6
    assert view_range[0][1] - view_range[0][0] <= 24.0 + 1e-6
    assert "calls 40" in window.statusBar().currentMessage()

    # a reload drops the old rings with the old graph
    window.datapacks.reload()
    assert window.graph_widget.focused is None and window.graph_widget.graph is None

    # opening from the explorer does not move its row; analyze shows the function
    navigation.reveal_in_explorer(pack / "data/test/function/f3.mcfunction")
    index = tree.currentIndex()
    tree.scrollTo(index)
    before = tree.visualRect(index)
    window.datapacks.on_tree_clicked(index)
    assert tree.visualRect(tree.currentIndex()) == before
    window.dock_explorer.hide()
    navigation.analyze("say hi", "test:f5:1", function_id="test:f5")
    assert window.dock_explorer.isVisible() or not window.isVisible()
    assert tree.currentIndex().data() == "f5.mcfunction"
    menu = navigation.path_menu(pack / "data/test/function/f5.mcfunction", in_explorer=True)
    labels = [action.text() for action in menu.actions()]
    assert "show in explorer" not in labels and "show in call graph" in labels


def test_source_view_edits_save_and_check_lines(app, window, make_pack, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "say one\n",
            "data/test/function/other.mcfunction": "say other\n",
        }
    )
    window.datapacks.load(pack)
    navigation, editor, edit = window.navigation, window.editor, window.source_edit
    navigation.open_function("test:tick")
    assert not edit.isReadOnly() and not editor.modified
    edit.setPlainText("say one\nfrobnicate\nkill @e[tpye=pig]")
    edit.document().setModified(True)
    assert editor.modified and window.source_label.text().startswith("● ")
    editor.check_lines()
    assert set(edit.line_marks) == {2, 3}
    assert "tpye" in edit.line_marks[3]

    # leaving the file asks first; cancel stays
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.Cancel)
    navigation.open_function("test:other")
    assert navigation.source_path.name == "tick.mcfunction"

    # Ctrl+S with the focus in the view saves the file and reloads the pack
    edit.setFocus()
    monkeypatch.setattr(edit, "hasFocus", lambda: True)
    edit.setPlainText("say two")
    edit.document().setModified(True)
    window._action("actionsave_project").trigger()
    path = pack / "data/test/function/tick.mcfunction"
    assert path.read_text() == "say two\n"
    assert not editor.modified
    assert "say two" in [
        c.raw for c in window.datapack.view_for(window.version).function("test:tick").content
    ]
    assert not window.source_label.text().startswith("● ")

    # revert throws edits away; discard lets another file open
    edit.setPlainText("say three")
    edit.document().setModified(True)
    window._action("actionrevert_file").trigger()
    assert edit.toPlainText() == "say two\n" and not editor.modified
    edit.setPlainText("say four")
    edit.document().setModified(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.Discard)
    navigation.open_function("test:other")
    assert navigation.source_path.name == "other.mcfunction"
    assert path.read_text() == "say two\n"

    # JSON errors are marked on their line; images stay read-only
    navigation.show_source(pack / "data/minecraft/tags/function/tick.json")
    edit.setPlainText('{\n  "values": [\n')
    editor.check_lines()
    assert list(edit.line_marks) == [2]  # the error is past the end: the last line
    edit.document().setModified(False)
    image = pack / "pack.png"
    image.write_bytes(b"\x89PNG")
    navigation.show_source(image)
    assert edit.isReadOnly()


def test_world_snapshots_rewind_and_compare(app, window, make_pack):
    pack = make_pack(
        {
            "data/minecraft/tags/function/load.json": {"values": ["test:load"]},
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/load.mcfunction": "scoreboard objectives add t dummy\n",
            "data/test/function/tick.mcfunction": "scoreboard players add #ticks t 1\n",
        }
    )
    window.datapacks.load(pack)
    snapshots = window.snapshots
    window.runs.step()
    first = snapshots.take("start")
    assert first is not None and snapshots.tree.topLevelItemCount() == 1
    window.console.run("summon pig 1 2 3")
    window.runs.step()
    snapshots.take()
    assert [s.name for s in snapshots.snapshots] == ["start", "tick 2"]

    # compare the two, then rewind to the first
    snapshots.tree.selectAll()
    snapshots.compare_selected()
    rows = [
        snapshots.changes.topLevelItem(row).text(0)
        for row in range(snapshots.changes.topLevelItemCount())
    ]
    assert "state game time" in rows and "score #ticks t" in rows
    assert any(row.startswith("entity ") for row in rows)
    assert "2 change(s)" not in snapshots.label.text()
    snapshots.tree.clearSelection()
    snapshots.tree.topLevelItem(0).setSelected(True)
    snapshots.rewind()
    world = window.emulator.world
    assert world.tick == 1 and world.scoreboard.get("#ticks", "t") == 1
    assert not [e for e in world.entities if not e.is_player]
    assert "rewound to 1. start" in window.statusBar().currentMessage()
    # the world goes on from there
    window.runs.step()
    assert window.emulator.world.scoreboard.get("#ticks", "t") == 2

    # comparing one snapshot with the world now
    snapshots.compare_selected()
    assert "1. start → the world now" in snapshots.label.text()

    # renaming, removing, and a new world forgetting them
    item = snapshots.tree.topLevelItem(0)
    item.setText(0, "checkpoint")
    assert snapshots.snapshots[0].label == "checkpoint"
    assert [item.text(0) for item in snapshots.tree.selectedItems()] == ["checkpoint"]  # kept
    snapshots.tree.clearSelection()
    snapshots.tree.topLevelItem(1).setSelected(True)
    snapshots.remove_selected()
    assert len(snapshots.snapshots) == 1
    window.datapacks.reload()
    assert snapshots.snapshots == [] and snapshots.tree.topLevelItemCount() == 0


def test_profiler_keeps_a_baseline_and_reports_what_changed(window, make_pack):
    from datapack_emulator.settings import PATHS

    window.datapacks.load(_hat_like_pack(make_pack))
    window.spin_ticks.setValue(2)
    window.runs.run_all()
    window.runs.wait()
    window.profile_baseline_button.click()  # the button, not only the method
    assert window.runs.baseline is not None
    assert "2 tick(s)" in window.profile_baseline_label.text()
    assert window.profile_clear_baseline_button.isEnabled()

    window.runs.run_all()
    window.runs.wait()
    html = (PATHS.GENERATED / "index.html").read_text(encoding="utf-8")
    assert "Compared with the kept run" in html
    assert "Flame graph, per tick" in html and "Dearest lines" in html

    window.profile_clear_baseline_button.click()
    assert window.runs.baseline is None
    assert window.profile_baseline_label.text() == "no baseline kept"
    assert not window.profile_clear_baseline_button.isEnabled()
    html = (PATHS.GENERATED / "index.html").read_text(encoding="utf-8")
    assert "Compared with the kept run" not in html


def test_snapshots_refuse_to_run_while_the_debugger_is_stopped(window, make_pack):
    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "scoreboard players add #t n 1\n",
        }
    )
    window.datapacks.load(pack)
    window.runs.step()
    snapshots = window.snapshots
    snapshots.take("start")
    window.runs.step()
    from PySide6.QtCore import QEventLoop

    window.debug._loop = QEventLoop()  # as if stopped at a breakpoint
    window.debug._set_paused(True)
    try:
        assert snapshots.take("while stopped") is None
        assert len(snapshots.snapshots) == 1
        snapshots.tree.topLevelItem(0).setSelected(True)
        tick = window.emulator.world.tick
        snapshots.rewind()
        assert window.emulator.world.tick == tick  # the world was not swapped
        assert "stopped in the debugger" in window.statusBar().currentMessage()
    finally:
        window.debug._loop = None
        window.debug._set_paused(False)


def test_the_world_dock_tabs_are_scores_entities_storage_blocks_snapshots(window):
    tabs = window.tabs_world
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "scoreboard",
        "entities",
        "storage",
        "blocks",
        "snapshots",
    ]


def test_source_view_save_guards_and_breakpoints_follow_edits(app, window, make_pack, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "say one\nsay two\nsay three\n",
        }
    )
    window.datapacks.load(pack)
    path = pack / "data/test/function/tick.mcfunction"
    navigation, editor, edit = window.navigation, window.editor, window.source_edit
    navigation.open_function("test:tick")

    # clicking the file already open keeps the edits and asks nothing
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kw: asked.append(args[2]) or QMessageBox.Discard
    )
    edit.setPlainText("say one\nsay two\nsay three\nsay four\n")
    edit.document().setModified(True)
    navigation.show_source(path, reveal=False)
    assert asked == [] and editor.modified

    # a breakpoint on the third line follows a line inserted above it
    window.debug.debugger.add("test:tick", 3)
    edit.setPlainText("say zero\nsay one\nsay two\nsay three\nsay four\n")
    edit.document().setModified(True)
    assert editor.save()
    assert window.debug.debugger.lines_of("test:tick") == {4}

    # the file changed on disk: saving asks before it overwrites
    path.write_text("say from another editor\n", encoding="utf-8")
    edit.setPlainText("say mine\n")
    edit.document().setModified(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.Cancel)
    assert not editor.save()
    assert path.read_text() == "say from another editor\n"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.Save)
    assert editor.save() and path.read_text() == "say mine\n"

    # the debugger stopping never asks, and saving is refused while it is stopped
    from PySide6.QtCore import QEventLoop

    edit.setPlainText("say mine\nsay more\n")
    edit.document().setModified(True)
    window.debug._loop = QEventLoop()
    try:
        asked.clear()
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kw: asked.append(args[2]) or QMessageBox.Save
        )
        navigation.open_function("test:tick", 1, ask=False)
        assert asked == [] and editor.modified
        assert not editor.save()
        assert "stopped in the debugger" in window.statusBar().currentMessage()
    finally:
        window.debug._loop = None
    assert editor.save()  # and once it goes on, the save works

    # windows line endings survive a save, and the tab says there are edits
    path.write_bytes(b"say a\r\nsay b\r\n")
    navigation.show_source(path, reveal=False, ask=False)
    edit.setPlainText("say a\nsay b\nsay c")
    edit.document().setModified(True)
    index = window.tabs.indexOf(window.tab_page("source_view"))
    assert window.tabs.tabText(index).startswith("●")
    assert editor.save()
    assert path.read_bytes() == b"say a\r\nsay b\r\nsay c\r\n"
    assert not window.tabs.tabText(index).startswith("●")


def test_the_window_opens_the_last_project_on_launch(app, tmp_path, monkeypatch, make_pack):
    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    from datapack_emulator.window import MainWindow

    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    first = MainWindow()
    try:
        first.datapacks.load(pack)
        first.project.name = "kept"
        assert first.projects.save()
        saved = first.project.path
        assert saved is not None and first.session.recent_projects
    finally:
        first.projects.modified = False
        first.close()

    # a fresh window starts on it, and says so
    again = MainWindow()
    try:
        assert again.project.name == "kept"
        assert again.datapack is not None and again.datapack.name == pack.name
        assert again.session.open_last_on_launch
    finally:
        again.projects.modified = False
        again.close()

    # the command line and the menu entry both say "the sample instead"
    plain = MainWindow(open_last=False)
    try:
        assert plain.datapack is not None and plain.datapack.name == "hat"  # the sample
        action = plain._action("actionopen_last_on_launch")
        assert action is not None and action.isChecked()
        action.setChecked(False)  # the menu entry, not the method behind it
        assert not plain.session.open_last_on_launch
    finally:
        plain.projects.modified = False
        plain.close()

    remembered = MainWindow()
    try:
        assert not remembered.session.open_last_on_launch
        assert remembered.project.name != "kept"  # the setting is kept
        # a path given on the command line wins over both
        chosen = MainWindow(start_path=saved)
        assert chosen.project.name == "kept"
        chosen.projects.modified = False
        chosen.close()
    finally:
        remembered.projects.modified = False
        remembered.close()


def test_the_window_survives_what_it_opens_on_launch(app, tmp_path, monkeypatch, make_pack):
    from PySide6.QtWidgets import QMessageBox

    from datapack_emulator.project import state_file
    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    from datapack_emulator.window import MainWindow

    boxes = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kw: boxes.append(args[2]) or QMessageBox.Ok
    )
    broken = tmp_path / "broken.dpemu"
    broken.write_bytes(b"not a zip at all")
    gone = tmp_path / "gone.dpemu"
    state_file().parent.mkdir(parents=True, exist_ok=True)
    state_file().write_text(
        json.dumps({"recent_projects": [str(gone), str(broken)]}), encoding="utf-8"
    )

    window = MainWindow()
    try:
        # no box in front of a window that is not there yet, and the sample opened
        assert boxes == []
        assert window.datapack is not None and window.datapack.name == "hat"
        # the file that cannot be read is off the list, so the next launch is quiet
        assert str(broken) not in window.session.recent_projects
    finally:
        window.projects.modified = False
        window.close()

    # a path on the command line that is not a pack says so instead of loading nothing
    loose = tmp_path / "notes.txt"
    loose.write_text("hello", encoding="utf-8")
    window = MainWindow(start_path=loose)
    try:
        assert "not a project or a datapack folder" in window.statusBar().currentMessage()
        assert window.datapack is not None and window.datapack.name == "hat"
    finally:
        window.projects.modified = False
        window.close()

    # a datapack folder given on the command line is opened as one
    pack = make_pack({"data/test/function/tick.mcfunction": "say hi\n"})
    window = MainWindow(start_path=pack)
    try:
        assert window.datapack is not None and window.datapack.name == pack.name
    finally:
        window.projects.modified = False
        window.close()


def test_the_window_entry_point_reads_its_arguments():
    from datapack_emulator.app import parse_arguments

    arguments, for_qt = parse_arguments([])
    assert arguments.path is None and arguments.open_last is None and for_qt == []
    assert parse_arguments(["--no-last-project"])[0].open_last is False
    assert parse_arguments(["--last-project"])[0].open_last is True
    assert parse_arguments(["some/project.dpemu"])[0].path.name == "project.dpemu"
    # Qt's own options are left for it, not refused
    arguments, for_qt = parse_arguments(["-platform", "offscreen", "--no-last-project"])
    assert for_qt == ["-platform", "offscreen"] and arguments.open_last is False


def _shown(completer) -> list[str]:
    """What the popup lists, in its order."""
    model = completer.completionModel()
    return [model.index(row, 0).data() for row in range(model.rowCount())]


def test_source_view_completes_and_renames(app, window, make_pack, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    pack = make_pack(
        {
            "data/minecraft/tags/function/tick.json": {"values": ["test:tick"]},
            "data/test/function/tick.mcfunction": "function test:helper\n",
            "data/test/function/helper.mcfunction": "say hi\n",
        }
    )
    window.datapacks.load(pack)
    edit = window.source_edit
    window.navigation.open_function("test:tick")

    # Ctrl+Space on a half-typed command offers what fits, and inserting it works
    edit.setPlainText("function test:hel")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    edit.ask_completions()
    assert edit.completer.popup().isVisible()
    assert _shown(edit.completer) == ["test:helper"]
    edit.insert_completion("test:helper")
    assert edit.toPlainText() == "function test:helper"
    edit.completer.popup().hide()

    # a word that only appears inside a candidate: the popup shows it too
    # (the module's own fallback; Qt must not filter the list a second time)
    edit.setPlainText("function hel")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    edit.ask_completions()
    assert edit.completer.popup().isVisible()
    assert _shown(edit.completer) == ["test:helper"]
    edit.completer.popup().hide()

    # a macro line is a command line: completion reads past the leading $
    edit.setPlainText("$function test:hel")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    edit.ask_completions()
    assert _shown(edit.completer) == ["test:helper"]
    edit.insert_completion("test:helper")
    assert edit.toPlainText() == "$function test:helper"
    edit.completer.popup().hide()

    # nothing to offer: no popup
    edit.setPlainText("say hello ")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    edit.ask_completions()
    app.processEvents()
    assert not edit.completer.popup().isVisible()

    # Ctrl+Space is the shortcut of the menu entry too
    assert window._action("actioncomplete").shortcut() == "Ctrl+Space"
    assert window._action("actionrename_function").shortcut() == "F2"

    # rename: the dialog asks, the file moves and the references follow
    edit.document().setModified(False)
    window.navigation.open_function("test:helper")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("test:deep/worker", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    assert window.editor.rename_function()
    assert (pack / "data/test/function/deep/worker.mcfunction").is_file()
    assert window.navigation.source_path.name == "worker.mcfunction"
    assert window.datapack.view_for(window.version).function("test:deep/worker") is not None
    assert not (pack / "data/test/function/helper.mcfunction").exists()
    assert (pack / "data/test/function/tick.mcfunction").read_text() == (
        "function test:deep/worker\n"
    )

    # a name that cannot be used says so and changes nothing
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("NOT AN ID", True))
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **kw: warned.append(a[2]) or QMessageBox.Ok
    )
    assert not window.editor.rename_function()
    assert warned and "resource location" in warned[0]

    # Enter, Tab and Escape belong to the popup while it is open
    edit.setPlainText("function wor")  # the function was renamed above
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    edit.ask_completions()
    assert edit.completer.popup().isVisible()
    assert _shown(edit.completer) == ["test:deep/worker"]
    for key in (Qt.Key_Return, Qt.Key_Tab, Qt.Key_Escape):
        event = QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier)
        edit.keyPressEvent(event)
        assert not event.isAccepted()  # the popup takes it, the text is unchanged
    assert edit.toPlainText() == "function wor"
    edit.completer.popup().hide()
    app.processEvents()

    # typing a letter goes to the text and asks again
    event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_K, Qt.NoModifier, "k")
    edit.keyPressEvent(event)
    assert edit.toPlainText() == "function work"
