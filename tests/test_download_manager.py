import os
import pytest
from unittest.mock import MagicMock
from core.controllers.download_manager import DownloadManager

# --- Mock Infrastructure ---

@pytest.fixture(autouse=True)
def mock_wake(monkeypatch):
    """Mocks the wakepy context manager so tests don't crash or hang."""
    class DummyKeep:
        def running(self):
            class Context:
                def __enter__(self): pass
                def __exit__(self, exc_type, exc_val, exc_tb): pass
            return Context()
    monkeypatch.setattr("core.controllers.download_manager.keep", DummyKeep())

@pytest.fixture
def mock_downloader(monkeypatch):
    """Replaces AudiobookDownloader to bypass network streams."""
    mock_dl = MagicMock()
    
    # Default behavior: Immediately complete without checking flags
    def default_download(asin, title, save_dir, progress_callback, check_cancel_callback):
        if progress_callback:
            progress_callback(100.0)
        return (f"/fake/{asin}.mp3", None, None, ".mp3")
        
    mock_dl.download_item.side_effect = default_download
    monkeypatch.setattr("core.controllers.download_manager.AudiobookDownloader", lambda *args, **kwargs: mock_dl)
    return mock_dl

@pytest.fixture
def manager(fake_thread_pool, fake_api_client, mock_downloader):
    mock_lib_mgr = MagicMock()
    mock_lib_mgr.local_library = {}
    mock_lib_mgr.db = MagicMock()

    callbacks = {
        "on_status": MagicMock(),
        "on_progress": MagicMock(),
        "on_complete": MagicMock(),
        "on_batch_finish": MagicMock()
    }

    return DownloadManager(
        api_client=fake_api_client,
        logger=MagicMock(),
        library_manager=mock_lib_mgr,
        callbacks=callbacks,
        thread_pool=fake_thread_pool
    )


# --- Tests ---

def test_queue_deduplication(manager, mock_downloader):
    # Submit the same ASIN multiple times in a single batch
    items = [
        {"asin": "111", "title": "Book 1"},
        {"asin": "111", "title": "Book 1 Duplicate"}
    ]
    
    manager.queue_batch(items, "/fake/dir")
    
    # Because SyncThreadPool executes instantly, the queue is already empty.
    # We verify it only downloaded once.
    assert mock_downloader.download_item.call_count == 1
    # Check that it executed the first occurrence
    assert mock_downloader.download_item.call_args[1]["title"] == "Book 1"

def test_queue_ordering_is_fifo(manager, mock_downloader):
    call_order = []
    
    def tracking_download(asin, title, *args, **kwargs):
        call_order.append(asin)
        return (f"/fake/{asin}.mp3", None, None, ".mp3")
        
    mock_downloader.download_item.side_effect = tracking_download
    
    items = [
        {"asin": "A", "title": "First"},
        {"asin": "B", "title": "Second"},
        {"asin": "C", "title": "Third"}
    ]
    
    manager.queue_batch(items, "/fake/dir")
    
    assert call_order == ["A", "B", "C"]

def test_mid_stream_cancellation(manager, mock_downloader):
    def fake_cancellable_download(asin, title, save_dir, progress_callback, check_cancel_callback):
        # Simulate reaching 50%
        progress_callback(50.0)
        
        # Trip the cancellation flag from the "outside"
        manager.cancel_download(asin)
        
        # The real downloader checks this callback and raises an Exception if True
        if check_cancel_callback():
            raise Exception("Download canceled by user.")
            
        return (f"/fake/{asin}.mp3", None, None, ".mp3")
        
    mock_downloader.download_item.side_effect = fake_cancellable_download
    
    # Mock the event bus to capture the status updates
    manager.event_bus.publish = MagicMock()
    
    manager.queue_download("123", "Cancel Me", "/fake/dir")
    
    # The worker catches the exception and signals "Failed" over the event bus
    manager.event_bus.publish.assert_any_call("download.status", asin="123", status="Failed", is_global=False)
    
    # Ensure it didn't write to the library database
    assert len(manager.library_manager.local_library) == 0

def test_aaxc_decryption_pass(manager, mock_downloader, monkeypatch):
    # Setup mock to return an encrypted .aaxc payload
    mock_downloader.download_item.side_effect = None
    mock_downloader.download_item.return_value = ("/fake/book.aaxc", "key123", "iv456", ".aaxc")
    
    # Mock ProcessRunner so we don't actually invoke FFmpeg
    mock_run_blocking = MagicMock()
    class MockCompletedProcess:
        returncode = 0
        stderr = ""
    mock_run_blocking.return_value = MockCompletedProcess()
    monkeypatch.setattr("core.controllers.download_manager.ProcessRunner.run_blocking", mock_run_blocking)
    
    # Mock OS paths to pretend FFmpeg successfully created the file
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 1024)
    monkeypatch.setattr(os, "remove", MagicMock()) # Prevent deletion of fake files
    
    manager.queue_download("999", "Encrypted Book", "/fake/dir")
    
    # 1. Verify FFmpeg was called with the correct decryption arguments
    mock_run_blocking.assert_called_once()
    cmd = mock_run_blocking.call_args[0][0]
    
    assert "ffmpeg" in cmd
    assert "-audible_key" in cmd
    assert "key123" in cmd
    assert "-audible_iv" in cmd
    assert "iv456" in cmd
    assert "/fake/book.aaxc" in cmd
    assert "/fake/book.m4b" in cmd
    
    # 2. Verify the library database was updated with the decrypted M4B, NOT the AAXC
    lib = manager.library_manager.local_library
    assert "/fake/book.m4b" in lib
    assert "/fake/book.aaxc" not in lib
    assert lib["/fake/book.m4b"]["format"] == "M4B"
    assert lib["/fake/book.m4b"]["audible_key"] == "key123"