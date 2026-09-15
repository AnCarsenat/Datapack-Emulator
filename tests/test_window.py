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
    project = Project(name="p", datapack=pack, version="1.20.4")
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
    assert window.table_tests.item(0, 3).text().startswith("✔")
    assert window.table_tests.item(1, 3).text().startswith("✘")
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
        assert "datapack/pack.mcmeta" in archive.namelist()

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
    window.projects.apply(Project(name="p", datapack=pack, engine_versions=["1.20.4", "1.21.4"]))
    window.runs.open_engine()
    engine = window.engine_window
    assert [v.id for v in engine.selected_versions()] == ["1.20.4", "1.21.4"]

    engine.select_ids(["1.21"])
    window.runs.open_engine()  # reopening the same pack keeps the ticks
    assert [v.id for v in engine.selected_versions()] == ["1.21"]
    assert window.projects.capture().engine_versions == ["1.21"]

    window.projects.apply(Project(name="q", datapack=pack, engine_versions=["26.2", "nope"]))
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
    assert window.table_tests.item(0, 3).text() == "✔ passed (value 3)"
    assert window.table_tests.item(1, 3).text().startswith("✘ not reached")
    assert window.emulator.world.tick == 5  # the tests ran inside the run's world

    window.runs.step()  # tick 5: no test is due, the results stay
    assert window.table_tests.item(0, 3).text() == "✔ passed (value 3)"
    window.environment.set_tests([CommandTest("scoreboard players get #ticks t", at_tick=6)])
    window.runs.step()  # tick 6
    assert window.table_tests.item(0, 3).text() == "✔ passed (value 7)"

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
