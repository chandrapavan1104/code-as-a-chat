import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import config, main, project_diff


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    parent = tmp_path / "Projects"
    r = parent / "demo"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "app.py").write_text("one\ntwo\nthree\n")
    (r / "gone.txt").write_text("bye\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(parent))
    monkeypatch.setattr(config, "WORKSPACE_DIR", r)
    return r


def test_modified_deleted_untracked_and_binary(repo):
    (repo / "app.py").write_text("one\nTWO\nthree\nfour\n")
    (repo / "gone.txt").unlink()
    (repo / "new file.md").write_text("hello\nworld\n")
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02")
    out = project_diff.collect(repo)
    by = {f["path"]: f for f in out["files"]}
    assert out["branch"] == "main" and out["head"]
    assert set(by) == {"app.py", "gone.txt", "new file.md", "logo.png"}

    app = by["app.py"]
    assert (app["status"], app["additions"], app["deletions"]) == ("M", 2, 1)
    lines = app["hunks"][0]["lines"]
    assert {"t": "-", "old": 2, "new": None, "text": "two"} in lines
    assert {"t": "+", "old": None, "new": 2, "text": "TWO"} in lines
    assert {"t": "+", "old": None, "new": 4, "text": "four"} in lines

    assert by["gone.txt"]["status"] == "D"
    assert (by["new file.md"]["status"], by["new file.md"]["additions"]) == ("A", 2)
    assert by["logo.png"]["binary"] and not by["logo.png"]["hunks"]
    assert (out["additions"], out["deletions"]) == (4, 2)


def test_rename_is_reported_as_rename(repo):
    _git(repo, "mv", "app.py", "main.py")
    f = project_diff.collect(repo)["files"][0]
    assert (f["status"], f["path"], f["old_path"]) == ("R", "main.py", "app.py")


def test_large_changes_are_capped_not_dropped_silently(repo):
    (repo / "app.py").write_text("x\n" * 5000)
    (repo / "b.txt").write_text("y\n" * 5000)
    out = project_diff.collect(repo, max_file_bytes=2000, max_total_bytes=6000)
    first = out["files"][0]
    assert first["truncated"] and len(first["patch"]) <= 2000
    assert len(json.dumps(first)) < 6000      # the cap bounds what is really sent
    assert out["omitted_files"] == 1


def test_clean_repo_and_non_repo(repo, tmp_path):
    assert project_diff.collect(repo)["files"] == []
    with pytest.raises(project_diff.NotARepo):
        project_diff.collect(tmp_path)


def test_endpoint_resolves_project_and_requires_token(repo):
    (repo / "app.py").write_text("changed\n")
    client = TestClient(main.app)
    headers = {"X-API-Token": config.API_TOKEN}
    r = client.get("/api/projects/diff", params={"project": "demo"}, headers=headers)
    assert r.status_code == 200 and r.json()["files"][0]["path"] == "app.py"
    assert client.get("/api/projects/diff", params={"project": "nope"},
                      headers=headers).status_code == 400
    assert client.get("/api/projects/diff").status_code == 401
