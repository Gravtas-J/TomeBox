import json
import threading
from unittest.mock import MagicMock

import pytest

from core.controllers.stats_manager import ACHIEVEMENTS, StatsManager
from core.database import DatabaseManager
from core.events import EventBus


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path))


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def manager(db, bus):
    return StatsManager(db_manager=db, event_bus=bus)


def _unlock_events(bus_publish_mock):
    return [
        c
        for c in bus_publish_mock.call_args_list
        if c[0][0] == "stats.achievement_unlocked"
    ]


# --- Concurrency ---


def test_concurrent_increments_are_atomic(manager, db):
    """10 threads incrementing at once must total exactly 10."""
    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def worker():
        barrier.wait()
        manager.add_stat("books_finished", 1)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert manager.get_snapshot()["stats"]["books_finished"] == 10


def test_increments_are_atomic_even_without_the_python_lock(manager, monkeypatch):
    """The increment is a single SQL upsert, so correctness no longer depends on
    stats_lock. (The old read-modify-write of the settings blob lost updates.)"""

    class DummyLock:
        def __enter__(self):
            pass

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(manager, "stats_lock", DummyLock())

    thread_count = 10
    barrier = threading.Barrier(thread_count)

    def worker():
        barrier.wait()
        manager.add_stat("books_finished", 1)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert manager.get_snapshot()["stats"]["books_finished"] == 10


# --- Validation ---


def test_unknown_stat_is_rejected(manager):
    with pytest.raises(ValueError):
        manager.add_stat("books_finsihed", 1)


def test_negative_increment_is_rejected(manager):
    with pytest.raises(ValueError):
        manager.add_stat("seconds_listened", -5)


def test_snapshot_reports_every_known_stat_typed(manager):
    manager.add_stat("seconds_listened", 12.5)
    snap = manager.get_snapshot()
    assert snap["stats"] == {
        "seconds_listened": 12.5,
        "books_finished": 0,
        "books_downloaded": 0,
    }
    assert isinstance(snap["stats"]["books_finished"], int)
    assert snap["unlocked_achievements"] == []


# --- Achievements ---


def test_achievement_triggers_on_threshold(manager, bus):
    bus.publish = MagicMock()

    unlocked = manager.add_stat("books_finished", 1)

    assert [a["id"] for a in unlocked] == ["first_finish"]
    assert unlocked[0]["title"] == "Core Consumed"
    events = _unlock_events(bus.publish)
    assert len(events) == 1
    assert events[0].kwargs["achievement"]["title"] == "Core Consumed"


def test_multiple_achievements_can_trigger(manager, bus):
    bus.publish = MagicMock()

    unlocked = manager.add_stat("books_finished", 5)

    assert {a["id"] for a in unlocked} == {"first_finish", "finish_5"}
    assert len(_unlock_events(bus.publish)) == 2


def test_achievement_is_announced_only_once(manager, bus):
    bus.publish = MagicMock()

    manager.add_stat("books_finished", 1)
    second = manager.add_stat("books_finished", 1)

    assert second == []
    assert len(_unlock_events(bus.publish)) == 1
    assert manager.get_snapshot()["unlocked_achievements"] == ["first_finish"]


def test_listening_achievement(manager):
    threshold = ACHIEVEMENTS["listen_10h"]["threshold"]
    assert manager.add_stat("seconds_listened", threshold - 1) == []
    unlocked = manager.add_stat("seconds_listened", 1)
    assert [a["id"] for a in unlocked] == ["listen_10h"]


def test_legacy_on_achievement_callback_still_fires(db, bus):
    on_achievement = MagicMock()
    manager = StatsManager(db, callbacks={"on_achievement": on_achievement}, event_bus=bus)

    manager.add_stat("books_downloaded", 1)

    on_achievement.assert_called_once_with(
        "System Integration Complete", "Download your first audiobook."
    )


# --- Isolation from the settings blob (the regression this design fixes) ---


def test_stale_settings_save_cannot_roll_back_stats(manager, db):
    stale_settings = db.load_settings()  # long-lived copy, like app.settings

    manager.add_stat("books_finished", 3)
    db.save_settings(stale_settings)  # what every save_settings(app.settings) does

    assert manager.get_snapshot()["stats"]["books_finished"] == 3
    assert "stats" not in db.load_settings()


# --- Migration from settings['stats'] ---


def _write_legacy_stats(base_dir, legacy):
    db = DatabaseManager(str(base_dir))
    db.conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('stats', ?)",
        (json.dumps(legacy),),
    )
    db.conn.commit()
    db.conn.close()


def test_migration_copies_legacy_stats_once(tmp_path):
    _write_legacy_stats(
        tmp_path,
        {
            "seconds_listened": 4000.5,
            "books_finished": 2,
            "books_downloaded": 7,
            "unlocked_achievements": ["first_dl", "first_finish"],
        },
    )

    db = DatabaseManager(str(tmp_path))
    snap = StatsManager(db, event_bus=EventBus()).get_snapshot()

    assert snap["stats"] == {
        "seconds_listened": 4000.5,
        "books_finished": 2,
        "books_downloaded": 7,
    }
    assert set(snap["unlocked_achievements"]) == {"first_dl", "first_finish"}
    assert "stats" not in db.load_settings()


def test_migration_discards_legacy_blob_when_tables_have_data(tmp_path):
    db = DatabaseManager(str(tmp_path))
    db.increment_stat("books_finished", 9)
    db.conn.close()

    _write_legacy_stats(tmp_path, {"books_finished": 1})

    db = DatabaseManager(str(tmp_path))
    assert db.get_stats() == {"books_finished": 9.0}
    assert "stats" not in db.load_settings()


def test_migration_ignores_malformed_legacy_values(tmp_path):
    _write_legacy_stats(
        tmp_path,
        {"books_finished": "lots", "seconds_listened": True, "books_downloaded": 3},
    )

    db = DatabaseManager(str(tmp_path))
    assert db.get_stats() == {"books_downloaded": 3.0}