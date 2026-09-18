from datapack_emulator.emulator import versions


def test_versions_sort_numerically():
    assert versions.parse("1.21.10") > versions.parse("1.21.9")
    assert versions.parse("26.1") > versions.parse("1.21.11")
    assert tuple(sorted(versions.VERSIONS)) == versions.VERSIONS


def test_parse_latest_oldest_and_partial():
    assert versions.parse("latest") is versions.LATEST
    assert versions.parse(None) is versions.LATEST
    assert versions.parse("oldest") is versions.OLDEST
    assert versions.parse("1.20").id == "1.20"  # an exact release id wins
    newest_26 = [v for v in versions.VERSIONS if v.stable and v.id.startswith("26.")][-1]
    assert versions.parse("26").id == newest_26.id  # a line with no exact release: newest stable


def test_parse_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        versions.parse("0.0.1-nope")


def test_version_range_is_inclusive_and_order_insensitive():
    forward = versions.version_range("1.20.4", "1.21")
    backward = versions.version_range("1.21", "1.20.4")
    assert forward == backward
    assert forward[0].id == "1.20.4" and forward[-1].id == "1.21"


def test_pack_format_lookup_handles_minor_formats():
    assert [v.id for v in versions.for_pack_format(48)] == ["1.21", "1.21.1"]
    assert versions.for_pack_format(107.1)[0].id == "26.2"
    assert versions.closest_to_pack_format(50).id == "1.21.1"


def test_feature_since_and_until():
    assert not versions.supports(versions.parse("1.19.4"), "command:return")
    assert versions.supports(versions.parse("1.20"), "command:return")
    assert not versions.supports(versions.parse("1.20"), "return:run")
    assert versions.supports(versions.parse("1.20.3"), "return:run")
    assert versions.supports(versions.parse("1.16.5"), "command:replaceitem")
    assert not versions.supports(versions.parse("1.17"), "command:replaceitem")


def test_structural_rules():
    assert not versions.uses_singular_registries(versions.parse("1.20.6"))
    assert versions.uses_singular_registries(versions.parse("1.21"))
    assert not versions.supports_overlays(versions.parse("1.20.1"))
    assert versions.supports_overlays(versions.parse("1.20.2"))


def test_vanilla_commands_contains_known_commands():
    commands = versions.vanilla_commands(versions.parse("1.21.4"))
    assert {"execute", "scoreboard", "return", "item"} <= commands
    assert "replaceitem" not in commands


def test_upcoming_prerelease_sorts_between_releases():
    prereleases = [v for v in versions.VERSIONS if not v.stable]
    if not prereleases:  # the table was regenerated after the release shipped
        return
    upcoming = prereleases[-1]
    base = upcoming.id.split("-")[0]
    assert upcoming > versions.LATEST
    assert versions.parse(base) is upcoming  # "26.3" means the rc until 26.3 ships
    assert versions._key(base) > upcoming.sort_key  # the release will sort after it
    assert versions.NEWEST is upcoming and versions.LATEST.stable
