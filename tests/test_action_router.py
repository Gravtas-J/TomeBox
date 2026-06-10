from unittest.mock import MagicMock

import pytest

from core.events import EventBus
from ui.action_router import ActionRouter


@pytest.fixture
def mock_app():
    app = MagicMock()

    app.ui_state.dl_status.set = MagicMock()
    app.ui_state.dl_progress.set = MagicMock()
    app.queue_ui_elements = {}

    def safe_after(delay, func, *args):
        # Allow nested calls up to 3 levels deep before breaking the circuit
        depth = getattr(safe_after, "depth", 0)
        if depth > 3:
            return "timer_id"

        safe_after.depth = depth + 1
        try:
            func(*args)
        finally:
            safe_after.depth -= 1
        return "timer_id"

    app.root.after.side_effect = safe_after

    app.library_manager._is_importing = False
    app.library_manager.import_queue.empty.return_value = True
    app.download_manager.is_processing = False
    app.converter.current_process = None

    return app


@pytest.fixture
def router(mock_app):
    # Use an isolated event bus for testing so state doesn't leak
    test_bus = EventBus()
    return ActionRouter(mock_app, event_bus=test_bus)


def test_reset_ui_if_idle_when_idle(router, mock_app):
    router.reset_ui_if_idle()

    mock_app.ui_state.dl_status.set.assert_called_with("Idle")
    mock_app.ui_state.dl_progress.set.assert_called_with(0)


def test_reset_ui_if_idle_when_busy(router, mock_app):
    mock_app.download_manager.is_processing = True

    router.reset_ui_if_idle()

    mock_app.ui_state.dl_status.set.assert_not_called()


def test_update_global_status(router, mock_app):
    router.update_global_status("Downloading...")
    mock_app.ui_state.dl_status.set.assert_called_with("Downloading...")

    # An empty string should trigger the idle reset logic
    router.update_global_status("")
    mock_app.ui_state.dl_status.set.assert_called_with("Idle")


def test_download_progress_event_updates_global_and_queue(router, mock_app):
    # Setup a fake queue UI row
    prog_var_mock = MagicMock()
    status_var_mock = MagicMock()
    mock_app.queue_ui_elements["123"] = {
        "prog_var": prog_var_mock,
        "status_var": status_var_mock,
    }

    # Publish to the bus exactly how the DownloadManager would
    router.event_bus.publish(
        "download.progress", asin="123", percent=50.0, is_global=True
    )

    # Assert global header updated
    mock_app.ui_state.dl_progress.set.assert_called_with(50.0)

    # Assert specific queue row updated
    prog_var_mock.set.assert_called_with(50.0)
    status_var_mock.set.assert_called_with("50%")


def test_remove_queue_ui_row(router, mock_app):
    frame_mock = MagicMock()
    mock_app.queue_ui_elements["task_1"] = {"frame": frame_mock}

    router.remove_queue_ui_row("task_1")

    # Assert the Tkinter frame was destroyed and removed from the dict
    frame_mock.destroy.assert_called_once()
    assert "task_1" not in mock_app.queue_ui_elements

    # Assert the drawer closes itself when the last item is removed
    mock_app.import_session.toggle_queue_drawer.assert_called_with(False)


def test_conversion_complete_event(router, mock_app, monkeypatch):
    messagebox_mock = MagicMock()
    monkeypatch.setattr("ui.action_router.messagebox", messagebox_mock)

    router.event_bus.publish("conversion.complete", message="Merge successful")

    messagebox_mock.showinfo.assert_called_with(
        "Conversion Success", "Merge successful"
    )


def test_task_error_event(router, mock_app):
    # Setup the failed tasks list as empty
    mock_app.failed_tasks = []

    router.event_bus.publish(
        "conversion.error",
        filepath="book.mp3",
        action="split",
        error_msg="FFmpeg failed",
    )

    assert len(mock_app.failed_tasks) == 1
    assert mock_app.failed_tasks[0]["path"] == "book.mp3"

    # Assert the error button was activated
    mock_app.ui_state.error_btn.set.assert_called_with("Errors (1)")
    mock_app.error_btn.config.assert_called_with(state="normal")


def test_download_complete_event_post_actions(router, mock_app):
    # Test auto-play post action
    router.event_bus.publish(
        "download.complete", filepath="book1.aax", title="Book 1", post_action="play"
    )

    mock_app.stats_manager.add_stat.assert_called_with("books_downloaded", 1)
    mock_app.library_presenter.refresh_library_ui.assert_called()
    mock_app.playback_presenter.load_specific_file.assert_called_with("book1.aax")
    mock_app.playback_presenter.master_play.assert_called()

    # Test auto-convert post action
    router.event_bus.publish(
        "download.complete", filepath="book2.aax", title="Book 2", post_action="convert"
    )
    mock_app.start_convert_thread.assert_called()


def test_download_batch_finish_event(router, mock_app):
    # Setup a fake UI queue with one download task and one import task
    mock_app.queue_ui_elements["dl_123"] = {"frame": MagicMock()}
    mock_app.queue_ui_elements["import_456"] = {"frame": MagicMock()}

    router.event_bus.publish("download.batch_finish")

    mock_app.dl_all_btn.config.assert_called_with(state="normal")

    # Assert download rows are cleared, but import rows are left alone
    assert "dl_123" not in mock_app.queue_ui_elements
    assert "import_456" in mock_app.queue_ui_elements


def test_import_status_and_progress(router, mock_app):
    # Setup the queue row
    status_mock = MagicMock()
    prog_mock = MagicMock()
    mock_app.queue_ui_elements["import_999"] = {
        "status_var": status_mock,
        "prog_var": prog_mock,
    }

    router.on_import_status("import_999", "Merging files...")
    status_mock.set.assert_called_with("Merging files...")
    mock_app.ui_state.dl_status.set.assert_called_with("Merging files...")

    router.on_import_progress("import_999", 75.0)
    prog_mock.set.assert_called_with(75.0)


def test_import_finished_success(router, mock_app):
    mock_app.library_manager.canceled_tasks = set()

    # 1. Keep a distinct reference to the mock so we can check it even after the dictionary deletes it
    status_mock = MagicMock()
    mock_app.queue_ui_elements["import_777"] = {
        "frame": MagicMock(),
        "status_var": status_mock,
    }

    router.on_import_finished(
        "C:/fake/path", added_count=2, total_found=2, task_id="import_777"
    )

    mock_app.system_manager.remove_pending_import.assert_called_with(
        mock_app.db.data_dir, "C:/fake/path"
    )
    mock_app.ui_state.dl_status.set.assert_called_with("Successfully imported 2 files.")

    # 2. Check our saved reference, and assert the dictionary actually deleted the row!
    status_mock.set.assert_called_with("Complete")
    assert "import_777" not in mock_app.queue_ui_elements


def test_import_queue_empty_event(router, mock_app):
    router.event_bus.publish("library.queue.empty")

    # Use assert_any_call because the instant 3000ms timer resets it to "Idle" right after this
    mock_app.ui_state.dl_status.set.assert_any_call("All queued imports completed.")
    mock_app.root.bell.assert_called_once()


def test_book_start_and_complete(router, mock_app):
    router.on_book_start("sub_task_1", "The Hobbit")
    mock_app.import_session.add_queue_ui_row.assert_called_with(
        "sub_task_1", "The Hobbit"
    )

    # Keep a distinct reference to the mock
    status_mock = MagicMock()
    mock_app.queue_ui_elements["sub_task_1"] = {
        "frame": MagicMock(),
        "status_var": status_mock,
    }
    router.on_book_complete("sub_task_1", success=True)

    # Check the reference and ensure it was cleaned up
    status_mock.set.assert_called_with("Complete")
    assert "sub_task_1" not in mock_app.queue_ui_elements


def test_scrape_apply_complete_reloads_cover(router, mock_app, monkeypatch):
    # Mock messagebox to prevent Tkinter from crashing the test!
    monkeypatch.setattr("ui.action_router.messagebox", MagicMock())

    # Simulate the user currently listening to the book being scraped
    mock_app.file_path = "C:/audio/book.m4b"

    # Publish the event (is_manual=False will trigger our mock instead of a real popup)
    router.event_bus.publish(
        "metadata.apply_complete",
        filepath="C:/audio/book.m4b",
        title="New Title",
        is_manual=False,
    )

    mock_app.library_presenter.cover_cache.clear.assert_called_once()
    mock_app.library_presenter.refresh_library_ui.assert_called()
    mock_app.metadata_manager.fetch_display_metadata.assert_called_with(
        "C:/audio/book.m4b"
    )
    # Because they are listening to it, it should auto-reload the specific file
    mock_app.playback_presenter.load_specific_file.assert_called_with(
        "C:/audio/book.m4b"
    )


def test_scrape_error_handling(router, mock_app):
    # Test Rate Limit
    router.event_bus.publish("metadata.error", error_msg="HTTP 429 Too Many Requests")
    mock_app.update_api_health.assert_called_with("Rate Limited", is_error=True)
    mock_app.ui_state.dl_status.set.assert_called_with(
        "Audible API rate limit reached. Pausing scrape for 60s."
    )

    # Test Offline/Timeout
    router.event_bus.publish("metadata.error", error_msg="Timeout occurred")
    mock_app.update_api_health.assert_called_with("Offline", is_error=True)

    # Test Generic Error
    router.event_bus.publish("metadata.error", error_msg="Malformed JSON")
    mock_app.update_api_health.assert_called_with("Error", is_error=True)


def test_display_metadata_ready_ignores_unselected_items(router, mock_app):
    # If the user clicked away to a different book, the incoming metadata shouldn't overwrite the sidebar
    mock_app._selected_local_path = "C:/audio/currently_viewing.m4b"

    router.event_bus.publish(
        "metadata.display_ready",
        filepath="C:/audio/background_scrape.m4b",
        cover_path="C:/covers/123.jpg",
        authors="John Doe",
        msg="",
    )

    mock_app.author_label.config.assert_not_called()
