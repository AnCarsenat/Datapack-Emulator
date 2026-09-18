"""The sample packs must behave the way they do in game."""

from pathlib import Path

import pytest

from datapack_emulator.emulator import Datapack, TestEngine, versions
from datapack_emulator.emulator.runtime.output import LogLevel, LogSource

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.mark.parametrize(
    "version", ["1.16.1", "1.17", "1.20.2", "1.20.5", "1.21", "1.21.9", "26.2", "26.3"]
)
def test_hat_v2_runs_cleanly_from_1_16_1_to_26_3(version):
    pack = Datapack.load(SAMPLES / "hat_v2")
    run = TestEngine(pack, ticks=3).run_version(versions.parse(version))

    assert run.errors == 0, [r.format() for r in run.records if r.level >= LogLevel.ERROR]
    assert any("Datapack Loaded use /trigger hat" in message for message in run.chat)

    expected = ("Failed to load function hat:", "Couldn't load hat_v2 pack metadata:")
    unexpected = [
        r.format()
        for r in run.records
        if r.level == LogLevel.WARNING
        and not (r.source is LogSource.GAME and r.message.startswith(expected))
    ]
    assert unexpected == []

    # tick_legacy uses replaceitem (gone in 1.17), tick_modern uses item (added in
    # 1.17); from 1.21 the plural folders holding both are not read at all
    failed = set(run.failed_functions)
    if run.version < versions.parse("1.17"):
        assert "hat:tick_modern" in failed and "hat:tick_legacy" not in failed
    elif run.version < versions.parse("1.21"):
        assert "hat:tick_legacy" in failed and "hat:tick_modern" not in failed
    else:
        assert not failed


@pytest.mark.parametrize("version", ["1.21", "1.21.4", "1.21.9", "26.2", "26.3"])
def test_the_packaged_starter_runs_on_every_version_it_declares(version):
    """The pack an installed copy opens with: it must load and tick on every
    version its pack.mcmeta claims (1.21 upwards; it uses the singular
    ``function/`` folders only)."""
    from datapack_emulator.settings import PATHS

    pack = Datapack.load(PATHS.PACKAGED_SAMPLES / "starter")
    assert pack.errors == []
    run = TestEngine(pack, ticks=101).run_version(versions.parse(version))

    assert run.errors == 0, [r.format() for r in run.records if r.level >= LogLevel.ERROR]
    assert not run.failed_functions
    unexpected = [r.format() for r in run.records if r.level == LogLevel.WARNING]
    assert unexpected == []
    # #minecraft:load ran, #minecraft:tick counted, and the 100th tick fired
    assert any("[starter] " in message for message in run.chat)
    assert any("five seconds of game time have passed" in message for message in run.chat)


def test_the_packaged_starter_is_refused_where_it_does_not_apply():
    """Below what it declares, the version reads none of its folders: the pack
    says so rather than pretending to run."""
    from datapack_emulator.settings import PATHS

    pack = Datapack.load(PATHS.PACKAGED_SAMPLES / "starter")
    run = TestEngine(pack, ticks=5).run_version(versions.parse("1.20.4"))
    assert run.commands == 0
