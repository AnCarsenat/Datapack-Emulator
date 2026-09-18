import json
import zipfile

import pytest

from datapack_emulator import project as project_module
from datapack_emulator.project import Project, default_sample, list_projects, unpacked_dir
from datapack_emulator.settings import PATHS


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Point PATHS at a throwaway repository layout."""
    monkeypatch.setattr(PATHS, "ROOT", tmp_path)
    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "SAMPLES", tmp_path / "samples")
    monkeypatch.setattr(PATHS, "CACHE", tmp_path / ".cache")
    return tmp_path


def _pack(folder):
    (folder / "data" / "hat" / "function").mkdir(parents=True)
    (folder / "pack.mcmeta").write_text('{"pack": {"pack_format": 48, "description": ""}}')
    (folder / "data" / "hat" / "function" / "tick.mcfunction").write_text("say hi\n")
    (folder / ".git").mkdir()
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return folder


def test_archive_carries_settings_tests_and_the_datapack(repo):
    pack = _pack(repo / "samples" / "hat")
    tests = [{"command": "function hat:tick", "at_tick": 3, "expect": "hi", "enabled": True}]
    saved = Project(
        name="hat",
        datapacks=[pack],
        version="1.21.4",
        ticks=7,
        engine_versions=["1.21"],
        tests=tests,
    ).save()

    assert saved == repo / "projects" / "hat.dpemu"
    with zipfile.ZipFile(saved) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("project.json"))
    assert {
        "project.json",
        "datapacks/0/hat/pack.mcmeta",
        "datapacks/0/hat/data/hat/function/tick.mcfunction",
    } <= names
    assert not any(".git" in name for name in names)
    assert manifest["datapacks"] == ["datapacks/0/hat"] and manifest["archive_format"] == 2

    loaded = Project.load(saved)
    assert loaded.path == saved
    assert loaded.datapacks == [unpacked_dir(saved) / "datapacks" / "0" / "hat"]
    assert (
        loaded.datapack / "data" / "hat" / "function" / "tick.mcfunction"
    ).read_text() == "say hi\n"
    assert loaded.ticks == 7 and loaded.engine_versions == ["1.21"] and loaded.tests == tests
    assert list_projects() == [saved]


def test_saving_again_writes_edits_made_to_the_unpacked_pack(repo):
    saved = Project(name="hat", datapacks=[_pack(repo / "samples" / "hat")]).save()
    loaded = Project.load(saved)
    (loaded.datapack / "data" / "hat" / "function" / "tick.mcfunction").write_text("say edited\n")
    loaded.save()

    with zipfile.ZipFile(saved) as archive:
        assert archive.read("datapacks/0/hat/data/hat/function/tick.mcfunction") == b"say edited\n"
    assert not saved.with_name(saved.name + ".part").exists()


def test_only_dpemu_is_written(repo, tmp_path):
    project = Project(name="x")
    assert project.save(tmp_path / "legacy.json") == tmp_path / "legacy.dpemu"
    assert project.save(tmp_path / "no-suffix") == tmp_path / "no-suffix.dpemu"
    assert project.default_path().suffix == ".dpemu"


def test_legacy_json_projects_still_open(repo):
    pack = _pack(repo / "samples" / "hat")
    legacy = repo / "projects" / "old.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"name": "old", "datapack": "samples/hat", "ticks": 4}))
    loaded = Project.load(legacy)
    assert loaded.datapack == pack and loaded.ticks == 4
    assert loaded.save() == repo / "projects" / "old.dpemu"


def test_archives_cannot_write_outside_their_folder(repo, tmp_path):
    evil = tmp_path / "evil.dpemu"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr("../../escaped.txt", "nope")
    with pytest.raises(ValueError, match="unsafe path"):
        Project.load(evil)
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_broken_or_newer_archives_are_refused(repo, tmp_path):
    not_zip = tmp_path / "broken.dpemu"
    not_zip.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        Project.load(not_zip)
    newer = tmp_path / "newer.dpemu"
    with zipfile.ZipFile(newer, "w") as archive:
        archive.writestr("project.json", json.dumps({"archive_format": 99}))
    with pytest.raises(ValueError, match="newer"):
        Project.load(newer)


def test_unsafe_names_are_cleaned(repo):
    assert Project(name="../../evil").default_path().parent == repo / "projects"
    assert project_module._safe_name("a/b:c") == "a-b-c"


def test_default_sample_is_first_pack(repo):
    (repo / "samples" / "b").mkdir(parents=True)
    (repo / "samples" / "b" / "pack.mcmeta").write_text("{}")
    (repo / "samples" / "a_not_a_pack").mkdir()
    assert default_sample() == repo / "samples" / "b"


def test_an_archive_saved_inside_its_own_pack_does_not_contain_itself(repo):
    pack = _pack(repo / "samples" / "hat")
    project = Project(name="hat", datapacks=[pack])
    first = project.save(pack / "hat.dpemu")
    size = first.stat().st_size
    project.save()
    with zipfile.ZipFile(first) as archive:
        assert not any(name.endswith((".dpemu", ".dpemu.part")) for name in archive.namelist())
    assert first.stat().st_size == size


def test_symlinks_are_left_out_and_a_failed_save_leaves_no_part_file(repo, tmp_path):
    pack = _pack(repo / "samples" / "hat")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside the pack")
    (pack / "data" / "hat" / "link.mcfunction").symlink_to(outside)
    (pack / "data" / "linked_folder").symlink_to(pack / "data" / "hat", target_is_directory=True)
    saved = Project(name="hat", datapacks=[pack]).save()
    with zipfile.ZipFile(saved) as archive:
        names = archive.namelist()
    assert "datapacks/0/hat/data/hat/function/tick.mcfunction" in names
    assert not any("link" in name for name in names)

    (pack / "data" / "hat" / "function" / "tick.mcfunction").chmod(0)
    try:
        with pytest.raises(OSError):
            Project(name="broken", datapacks=[pack]).save()
    finally:
        (pack / "data" / "hat" / "function" / "tick.mcfunction").chmod(0o644)
    assert not list((repo / "projects").glob("*.part"))


@pytest.mark.parametrize("manifest", ["[1]", '{"ticks": {"a": 1}}', '"text"'])
def test_malformed_manifests_are_value_errors(repo, tmp_path, manifest):
    archive_path = tmp_path / "odd.dpemu"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project.json", manifest)
    with pytest.raises(ValueError):
        Project.load(archive_path)
    legacy = tmp_path / "odd.json"
    legacy.write_text(manifest)
    with pytest.raises(ValueError):
        Project.load(legacy)


def test_archives_hold_several_datapacks_in_order_and_old_ones_still_open(repo, tmp_path):
    first = _pack(repo / "samples" / "hat")
    second = repo / "samples" / "extra"
    (second / "data" / "extra" / "function").mkdir(parents=True)
    (second / "pack.mcmeta").write_text('{"pack": {"pack_format": 61, "description": ""}}')
    (second / "data" / "extra" / "function" / "go.mcfunction").write_text("say go\n")
    saved = Project(name="both", datapacks=[first, second]).save()
    loaded = Project.load(saved)
    assert [path.name for path in loaded.datapacks] == ["hat", "extra"]
    assert (loaded.datapacks[1] / "data" / "extra" / "function" / "go.mcfunction").is_file()

    old = tmp_path / "old.dpemu"
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr(
            "project.json", json.dumps({"name": "old", "datapack": "datapack", "archive_format": 1})
        )
        archive.writestr("datapack/pack.mcmeta", "{}")
    assert Project.load(old).datapacks == [unpacked_dir(old) / "datapack"]


def test_a_project_archive_that_unpacks_to_far_more_is_refused(tmp_path):
    import zipfile

    import pytest

    from datapack_emulator.project import Project

    path = tmp_path / "bomb.dpemu"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", '{"name": "bomb", "archive_format": 2}')
        archive.writestr("big.txt", "0" * (40 * 1024 * 1024))  # compresses to nothing
    with pytest.raises(ValueError) as error:
        Project.load(path, unpack_to=tmp_path / "unpacked")
    assert "refusing to unpack it" in str(error.value)
    assert not (tmp_path / "unpacked").exists()
