from unittest.mock import MagicMock

import pytest

from core.events import EventBus


@pytest.fixture
def bus():
    """Provides a fresh, isolated EventBus instance for each test."""
    return EventBus()


def test_publish_to_empty_topic_does_not_crash(bus):
    """Verifies that firing an event with no listeners fails silently and safely."""
    try:
        bus.publish("ghost_town", payload="nothing")
    except Exception as e:
        pytest.fail(f"Publishing to an empty topic raised an exception: {e}")


def test_subscribe_and_receive_kwargs(bus):
    """Verifies a subscriber receives the exact keyword arguments published."""
    mock_handler = MagicMock()

    bus.subscribe("playback_started", mock_handler)
    bus.publish("playback_started", file_path="/fake/book.m4b", duration=3600)

    mock_handler.assert_called_once_with(file_path="/fake/book.m4b", duration=3600)


def test_multiple_subscribers_same_topic(bus):
    """Verifies that an event broadcasts to all registered listeners."""
    handler_one = MagicMock()
    handler_two = MagicMock()

    bus.subscribe("download_progress", handler_one)
    bus.subscribe("download_progress", handler_two)

    bus.publish("download_progress", percent=50.5)

    handler_one.assert_called_once_with(percent=50.5)
    handler_two.assert_called_once_with(percent=50.5)


def test_unsubscribe(bus):
    """Verifies that unsubscribing successfully stops events from routing to the handler."""
    mock_handler = MagicMock()

    bus.subscribe("system_shutdown", mock_handler)
    bus.unsubscribe("system_shutdown", mock_handler)

    bus.publish("system_shutdown", reason="user_quit")

    # The handler should not have been called
    mock_handler.assert_not_called()
