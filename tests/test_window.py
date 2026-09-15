"""Offscreen checks of the main window. Skipped when PySide6 is not installed."""

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from datapack_emulator.settings import PATHS

    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "GENERATED", tmp_path / "generated")
    from datapack_emulator.window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    yield window
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
