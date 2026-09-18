"""Offscreen checks of the main window. Skipped when PySide6 is not installed."""

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
    assert window.table_tests.item(0, 4).text().startswith("✔")
    assert window.table_tests.item(1, 4).text().startswith("✘")
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
    assert window.table_tests.item(0, 4).text() == "✔ passed (value 3)"
    assert window.table_tests.item(1, 4).text().startswith("✘ not reached")
    assert window.emulator.world.tick == 5  # the tests ran inside the run's world

    window.runs.step()  # tick 5: no test is due, the results stay
    assert window.table_tests.item(0, 4).text() == "✔ passed (value 3)"
    window.environment.set_tests([CommandTest("scoreboard players get #ticks t", at_tick=6)])
    window.runs.step()  # tick 6
    assert window.table_tests.item(0, 4).text() == "✔ passed (value 7)"

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
    assert window.table_tests.item(0, 4).text() == "not run"
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
