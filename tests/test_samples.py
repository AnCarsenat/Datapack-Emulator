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
