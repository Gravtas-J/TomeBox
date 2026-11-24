import pytest
from fastapi.testclient import TestClient

from core.controllers.stats_manager import ACHIEVEMENTS
from server.web_app import MAX_LISTENED_SECONDS_PER_UPDATE, create_server_app
from tests.fakes import FakeTomebox



@pytest.fixture
def box(tmp_path):
    box = FakeTomebox()
    box.settings["auth_token"] = "test_master_token"

    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"x" * 100)
    aax = tmp_path / "book.aax"
    aax.write_bytes(b"x" * 100)

    box.library_manager.local_library = {
        str(m4b): {"title": "Plain Book", "format": "M4B", "asin": "B_M4B"},
        str(aax): {"title": "Locked Book", "format": "AAX", "asin": "B_AAX"},
        "/fake/legacy.mp3": {"title": "No Format Recorded", "asin": "B_OLD"},
    }
    box.library_manager.cloud_items = [
        {"title": "Cloud Only", "asin": "B_CLOUD", "authors": []}
    ]
    box.m4b_path, box.aax_path = str(m4b), str(aax)
    return box


@pytest.fixture
def client(box):
    c = TestClient(create_server_app(box))
    c.cookies.set("tomebox_token", "test_master_token")
    return c


# --- playable ---


def test_library_reports_playable_per_entry(client, box):
    lib = client.get("/api/library").json()

    assert (lib[box.m4b_path]["playable"], lib[box.m4b_path]["playable_reason"]) == (True, None)
    assert (lib[box.aax_path]["playable"], lib[box.aax_path]["playable_reason"]) == (False, "drm")
    assert lib["/fake/legacy.mp3"]["playable"] is True
    assert lib["cloud:B_CLOUD"]["playable"] is False
    assert lib["cloud:B_CLOUD"]["playable_reason"] == "not_downloaded"


def test_stream_refuses_drm_files(client, box):
    res = client.get("/api/stream", params={"path": box.aax_path})
    assert res.status_code == 409
    assert "Convert" in res.json()["detail"]


def test_stream_serves_playable_files(client, box):
    res = client.get("/api/stream", params={"path": box.m4b_path})
    assert res.status_code in (200, 206)

# --- /api/progress ---


def _post(client, path, **body):
    return client.post("/api/progress", json={"path": path, "profile": "Main", **body})


def test_progress_without_listened_seconds_records_no_stats(client, box):
    res = _post(client, box.m4b_path, position=10)
    assert res.status_code == 200
    assert res.json() == {"status": "success", "achievements_unlocked": []}
    assert box.stats_manager.get_snapshot()["stats"]["seconds_listened"] == 0


def test_progress_adds_listened_seconds(client, box):
    _post(client, box.m4b_path, position=10, listened_seconds=7.5)
    _post(client, box.m4b_path, position=20, listened_seconds=2.5)
    assert box.stats_manager.get_snapshot()["stats"]["seconds_listened"] == 10.0


def test_progress_marks_dirty_and_writes_last_played_keys(client, box):
    box.library_manager._playback_dirty = False
    _post(client, box.m4b_path, position=42)

    assert box.library_manager._playback_dirty is True
    assert box.db.settings["last_played_Main"] == box.m4b_path
    assert box.db.settings["last_played_at_Main"] > 0
    assert box.library_manager.local_library[box.m4b_path]["progress"]["Main"] == 42


def test_progress_returns_unlocked_listening_achievement(client, box):
    box.db.increment_stat("seconds_listened", ACHIEVEMENTS["listen_10h"]["threshold"] - 1)
    res = _post(client, box.m4b_path, position=5, listened_seconds=1)
    assert [a["id"] for a in res.json()["achievements_unlocked"]] == ["listen_10h"]


def test_finished_counts_once(client, box):
    first = _post(client, box.m4b_path, position=99, finished=True).json()
    second = _post(client, box.m4b_path, position=99, finished=True).json()

    entry = box.library_manager.local_library[box.m4b_path]
    assert entry["read_status"] == "Finished"
    assert box.stats_manager.get_snapshot()["stats"]["books_finished"] == 1
    assert [a["id"] for a in first["achievements_unlocked"]] == ["first_finish"]
    assert second["achievements_unlocked"] == []


def test_finished_bumps_library_version(client, box):
    before = box.settings.get("library_version", 0)
    _post(client, box.m4b_path, position=99, finished=True)
    assert box.settings["library_version"] == before + 1


@pytest.mark.parametrize(
    "body",
    [
        {"position": -1},
        {"position": "nan"},
        {"position": "abc"},
        {"position": 1, "listened_seconds": -1},
        {"position": 1, "listened_seconds": MAX_LISTENED_SECONDS_PER_UPDATE + 1},
        {"position": 1, "listened_seconds": "inf"},
    ],
)
def test_progress_rejects_invalid_bodies(client, box, body):
    res = _post(client, box.m4b_path, **body)
    assert res.status_code == 422
    assert box.stats_manager.get_snapshot()["stats"]["seconds_listened"] == 0


def test_progress_unknown_path_is_404(client):
    assert _post(client, "/not/in/library.m4b", position=1).status_code == 404


# --- /api/stats ---


def test_stats_endpoint_lists_all_achievements(client, box):
    _post(client, box.m4b_path, position=99, finished=True, listened_seconds=30)

    body = client.get("/api/stats").json()
    assert body["stats"]["books_finished"] == 1
    assert body["stats"]["seconds_listened"] == 30.0

    by_id = {a["id"]: a for a in body["achievements"]}
    assert set(by_id) == set(ACHIEVEMENTS)
    assert by_id["first_finish"]["unlocked"] is True
    assert by_id["listen_50h"]["unlocked"] is False