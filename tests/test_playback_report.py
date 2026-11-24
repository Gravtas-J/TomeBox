import pytest

from core.controllers.stats_manager import StatsManager
from core.events import EventBus
from core.playback_report import MAX_LISTENED_SECONDS_PER_UPDATE, apply_playback_report
from tests.fakes import FakeDBManager


@pytest.fixture
def stats():
    return StatsManager(FakeDBManager(), event_bus=EventBus())


def test_listening_time_is_added(stats):
    newly_finished, unlocked = apply_playback_report(stats, {"title": "A"}, listened_seconds=12.5)
    assert (newly_finished, unlocked) == (False, [])
    assert stats.get_snapshot()["stats"]["seconds_listened"] == 12.5


def test_listening_time_counts_without_a_library_entry(stats):
    apply_playback_report(stats, None, listened_seconds=5)
    assert stats.get_snapshot()["stats"]["seconds_listened"] == 5.0


def test_zero_listening_time_records_nothing(stats):
    apply_playback_report(stats, {"title": "A"})
    assert stats.get_snapshot()["stats"]["seconds_listened"] == 0


def test_finished_marks_entry_and_counts_once(stats):
    entry = {"title": "A"}
    first = apply_playback_report(stats, entry, finished=True)
    second = apply_playback_report(stats, entry, finished=True)

    assert entry["read_status"] == "Finished"
    assert first[0] is True and [a["id"] for a in first[1]] == ["first_finish"]
    assert second == (False, [])
    assert stats.get_snapshot()["stats"]["books_finished"] == 1


def test_already_finished_entry_is_not_recounted(stats):
    apply_playback_report(stats, {"title": "A", "read_status": "Finished"}, finished=True)
    assert stats.get_snapshot()["stats"]["books_finished"] == 0


@pytest.mark.parametrize(
    "seconds", [-1, MAX_LISTENED_SECONDS_PER_UPDATE + 1, float("nan"), float("inf")]
)
def test_out_of_range_listening_time_is_rejected(stats, seconds):
    with pytest.raises(ValueError):
        apply_playback_report(stats, {"title": "A"}, listened_seconds=seconds)
    assert stats.get_snapshot()["stats"]["seconds_listened"] == 0


def test_finished_without_entry_is_rejected(stats):
    with pytest.raises(ValueError):
        apply_playback_report(stats, None, finished=True)