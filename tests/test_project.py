import json

import pytest

from src import project as project_module
from src.project import Project, default_sample, list_projects
from src.settings import PATHS


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Point PATHS at a throwaway repository layout."""
    monkeypatch.setattr(PATHS, "ROOT", tmp_path)
    monkeypatch.setattr(PATHS, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(PATHS, "SAMPLES", tmp_path / "samples")
    return tmp_path


def test_round_trip_keeps_repo_paths_relative(repo):
    pack = repo / "samples" / "hat"
    pack.mkdir(parents=True)
    saved = Project(
        name="hat", datapack=pack, version="1.21.4", ticks=7, engine_versions=["1.21"]
    ).save()

    data = json.loads(saved.read_text())
    assert saved == repo / "projects" / "hat.json"
    assert data["datapack"] == "samples/hat"

    loaded = Project.load(saved)
    assert loaded.datapack == pack and loaded.ticks == 7 and loaded.engine_versions == ["1.21"]
    assert list_projects() == [saved]


def test_paths_outside_the_repo_stay_absolute(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere")
    saved = Project(name="x", datapack=outside).save()
    assert json.loads(saved.read_text())["datapack"] == str(outside)


def test_unsafe_names_are_cleaned(repo):
    assert Project(name="../../evil").default_path().parent == repo / "projects"
    assert project_module._safe_name("a/b:c") == "a-b-c"


def test_default_sample_is_first_pack(repo):
    (repo / "samples" / "b").mkdir(parents=True)
    (repo / "samples" / "b" / "pack.mcmeta").write_text("{}")
    (repo / "samples" / "a_not_a_pack").mkdir()
    assert default_sample() == repo / "samples" / "b"
