import time
import threading
import pytest
from unittest.mock import MagicMock
from core.controllers.stats_manager import StatsManager
import copy

class DictDB:
    """A thread-unsafe dict database to accurately reflect SQLite read/write gaps."""
    def __init__(self):
        self.settings = {"stats": {}}

    def load_settings(self):
        # Deep copy so concurrent loaders get isolated snapshots,
        # the same way json.loads(value) does in the real DatabaseManager.
        data = copy.deepcopy(self.settings)
        time.sleep(0.05)
        return data

    def save_settings(self, settings_dict):
        self.settings = copy.deepcopy(settings_dict)

@pytest.fixture
def db():
    return DictDB()

@pytest.fixture
def callbacks():
    return {"on_achievement": MagicMock()}

@pytest.fixture
def manager(db, callbacks):
    return StatsManager(db_manager=db, callbacks=callbacks)


# --- Concurrency Tests ---

def test_race_condition_without_lock(manager, db, monkeypatch):
    """Proves that without your lock, concurrent writes overwrite each other."""
    
    # 1. Temporarily disable the lock you built by making it a dummy context manager
    class DummyLock:
        def __enter__(self): pass
        def __exit__(self, exc_type, exc_val, exc_tb): pass
    monkeypatch.setattr(manager, "stats_lock", DummyLock())

    # 2. Use a Barrier to force exactly 2 threads to start their read/write cycle at the exact same millisecond
    barrier = threading.Barrier(2)
    
    def worker():
        barrier.wait()
        manager.add_stat("books_finished", 1)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 3. Because the lock was disabled, both threads read 0, added 1, and wrote 1.
    # One increment was entirely lost to the void.
    assert db.settings["stats"]["books_finished"] == 1


def test_lock_resolves_race_condition(manager, db):
    """Proves your implemented threading.Lock safely queues concurrent access."""
    
    # Hammer the manager with 10 threads hitting the barrier simultaneously
    thread_count = 10
    barrier = threading.Barrier(thread_count)
    
    def worker():
        barrier.wait()
        manager.add_stat("books_finished", 1)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    
    for t in threads: t.start()
    for t in threads: t.join()

    # 10 threads * 1 increment = 10. The lock successfully protected the DB.
    assert db.settings["stats"]["books_finished"] == 10


# --- Achievement Logic Tests ---

def test_achievement_triggers_on_threshold(manager, db, callbacks):
    # 'first_finish' requires 1 book.
    manager.add_stat("books_finished", 1)
    
    callbacks["on_achievement"].assert_called_once_with(
        "Core Consumed", "Finish an audiobook."
    )
    
    # Verify the unlocked list prevents duplicate firings
    assert "first_finish" in db.settings["stats"]["unlocked_achievements"]
    
    # Add another book (should not trigger the achievement again)
    manager.add_stat("books_finished", 1)
    assert callbacks["on_achievement"].call_count == 1 # Still 1

def test_multiple_achievements_can_trigger(manager, db, callbacks):
    # Jump straight to 5 books to unlock both 'first_finish' and 'finish_5'
    manager.add_stat("books_finished", 5)
    
    assert callbacks["on_achievement"].call_count == 2
    
    unlocked = db.settings["stats"]["unlocked_achievements"]
    assert "first_finish" in unlocked
    assert "finish_5" in unlocked