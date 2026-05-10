import os
import pytest
from unittest.mock import MagicMock, call
from core.controllers.conversion_manager import ConversionManager
from core.converter import AudioConverter

# --- Mock Infrastructure ---

@pytest.fixture(autouse=True)
def mock_wake(monkeypatch):
    """Mocks the wakepy context manager to prevent sleep-prevention errors during testing."""
    class DummyKeep:
        def running(self):
            class Context:
                def __enter__(self): pass
                def __exit__(self, *args): pass
            return Context()
    monkeypatch.setattr("core.controllers.conversion_manager.keep", DummyKeep())

@pytest.fixture
def sync_pool():
    """Executes threaded worker functions instantly on the main thread."""
    class Pool:
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)
    return Pool()

@pytest.fixture
def manager(sync_pool):
    """Provides a fully wired ConversionManager with mocked dependencies."""
    mock_lib_mgr = MagicMock()
    mock_lib_mgr.local_library = {}
    mock_lib_mgr.get_authors_for_asin.return_value = "Test Author"
    
    callbacks = {
        "on_status": MagicMock(),
        "on_progress": MagicMock(),
        "on_complete": MagicMock(),
        "on_error": MagicMock(),
        "on_refresh_required": MagicMock()
    }
    
    # We use a real AudioConverter so we can verify the actual FFmpeg command string it builds
    converter = AudioConverter(logger=MagicMock())
    converter.get_duration = MagicMock(return_value=3600.0) # 1 hour fake duration
    
    return ConversionManager(
        converter=converter,
        library_manager=mock_lib_mgr,
        logger=MagicMock(),
        covers_dir="/fake/covers",
        callbacks=callbacks,
        get_drm_flags_cb=lambda path: ["-activation_bytes", "deadbeef"],
        thread_pool=sync_pool
    )

# --- Tests ---

def test_convert_single_flow_and_original_deletion(manager, monkeypatch):
    """Verifies FFmpeg command construction and the built-in original file deletion."""
    
    # Mock ProcessRunner so it succeeds instantly without looking for an actual FFmpeg binary
    mock_run_async = MagicMock()
    class MockProcess:
        returncode = 0
        stdout = []
        def wait(self): pass
        def terminate(self): pass
    mock_run_async.return_value = MockProcess()
    monkeypatch.setattr("core.converter.ProcessRunner.run_async", mock_run_async)
    
    # Mock OS functions to simulate a successful file write and prevent real deletions
    mock_remove = MagicMock()
    monkeypatch.setattr(os, "remove", mock_remove)
    monkeypatch.setattr(os, "replace", MagicMock())
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    
    input_file = "/fake/input.aax"
    output_file = "/fake/output.m4b"
    manager.library_manager.local_library[input_file] = {"title": "Test Book", "asin": "123"}
    
    manager.convert_single(input_file, output_file, chapters=[])
    
    # 1. Verify the FFmpeg command was constructed with the correct flags
    mock_run_async.assert_called_once()
    cmd = mock_run_async.call_args[0][0]
    
    assert "ffmpeg" in cmd
    assert "-activation_bytes" in cmd
    assert "deadbeef" in cmd
    assert "-i" in cmd
    assert input_file in cmd
    assert "-metadata" in cmd
    assert "title=Test Book" in cmd
    
    # 2. Verify original-file deletion (Built-in behavior of convert_single)
    # Check that os.remove was called on the original input file
    assert call(input_file) in mock_remove.call_args_list
    
    # Verify the library was updated
    assert output_file in manager.library_manager.local_library

def test_split_book_preserves_original(manager, monkeypatch):
    """Verifies that splitting a book does NOT delete the source file."""
    mock_remove = MagicMock()
    monkeypatch.setattr(os, "remove", mock_remove)
    monkeypatch.setattr(os, "makedirs", MagicMock())
    
    # Mock the converter's internal split logic so we only test the manager's orchestration
    manager.converter.split_into_chapters = MagicMock(return_value=True)
    
    input_file = "/fake/big_book.m4b"
    manager.split_book(input_file, "/fake/outdir", [{"start_time": 0, "end_time": 10}])
    
    manager.converter.split_into_chapters.assert_called_once()
    
    # Assert os.remove was never called on the input file
    if mock_remove.called:
        for args_call in mock_remove.call_args_list:
            assert args_call[0][0] != input_file, "Original file was erroneously deleted during split!"

def test_batch_error_accumulation(manager, monkeypatch):
    """Feeds 5 paths, simulates failures on 2 of them, and verifies exactly 2 errors are caught."""
    monkeypatch.setattr(os, "remove", MagicMock())
    monkeypatch.setattr(os, "replace", MagicMock())
    monkeypatch.setattr(os.path, "exists", lambda p: True) # Pretend all 5 paths exist on disk
    
    paths = [f"/fake/path{i}.aax" for i in range(1, 6)]
    
    # We hijack the converter to raise an exception ONLY on paths 2 and 4
    def mock_convert_to_m4b(*args, **kwargs):
        input_path = kwargs.get("input_path")
        if input_path in ["/fake/path2.aax", "/fake/path4.aax"]:
            raise Exception("Simulated FFmpeg Crash")
        return True
        
    manager.converter.convert_to_m4b = MagicMock(side_effect=mock_convert_to_m4b)
    
    manager.convert_batch(paths)
    
    # Verify the exact error string was passed back to the UI callback
    manager.on_complete.assert_called_with(
        "Batch conversion finished with 2 error(s).\n\nCheck the Errors window for details."
    )
    
    # Verify the specific error UI callback fired twice
    assert manager.on_error.call_count == 2