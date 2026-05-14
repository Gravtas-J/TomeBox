import os
import sys
import pytest
from unittest.mock import MagicMock

# If you haven't moved this to a root conftest.py, keep the path injection here
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from api.audible_client import AudibleClient

@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_tomebox.db"

class SyncThreadPool:
    def __init__(self, *args, **kwargs):
        pass

    def submit(self, fn, *args, **kwargs):
        class SyncFuture:
            def __init__(self):
                try:
                    self._result = fn(*args, **kwargs)
                    self._exception = None
                except Exception as e:
                    self._result = None
                    self._exception = e

            def result(self):
                if self._exception:
                    raise self._exception
                return self._result

            def add_done_callback(self, callback):
                callback(self)

        return SyncFuture()

    def shutdown(self, wait=True):
        pass

@pytest.fixture
def fake_thread_pool(monkeypatch):
    pool = SyncThreadPool()
    # Intercepts instantiations of AppThreadPool across the app
    monkeypatch.setattr("core.utils.thread_pool.AppThreadPool", lambda *a, **kw: pool)
    return pool

@pytest.fixture
def fake_logger(monkeypatch):
    logs = []
    
    class MockLogger:
        def __call__(self, msg, *args, **kwargs):
            logs.append(msg)
        def info(self, msg, *args, **kwargs):
            logs.append(f"INFO: {msg}")
        def error(self, msg, *args, **kwargs):
            logs.append(f"ERROR: {msg}")
        def warning(self, msg, *args, **kwargs):
            logs.append(f"WARNING: {msg}")
        def debug(self, msg, *args, **kwargs):
            logs.append(f"DEBUG: {msg}")

    # Replaces the initialized logger instance in the module
    monkeypatch.setattr("core.utils.logger.logger", MockLogger())
    return logs

@pytest.fixture
def fake_api_client():
    mock_client = MagicMock(spec=AudibleClient)
    
    # Sensible defaults to prevent NoneType attribute errors in unmocked controllers
    mock_client.is_authenticated.return_value = True
    mock_client.fetch_library.return_value = []
    mock_client.fetch_product_metadata.return_value = {}
    mock_client.search_catalog.return_value = []
    mock_client.get_download_license.return_value = ("mock_url", "mock_voucher")
    mock_client.get_drm_flags.return_value = ("-activation_bytes", "00000000")
    mock_client.get_activation_bytes.return_value = "00000000"
    
    return mock_client

@pytest.fixture(autouse=True)
def mock_tkinter_dialogs(monkeypatch):
    """
    Globally suppresses all Tkinter popups during testing and auto-answers them.
    Because autouse=True, this automatically applies to all 200+ tests without needing to be imported.
    """
    import tkinter.messagebox as mb
    import tkinter.simpledialog as sd
    import tkinter.filedialog as fd

    # Auto-click "OK" on alerts
    monkeypatch.setattr(mb, "showinfo", MagicMock(return_value="ok"))
    monkeypatch.setattr(mb, "showwarning", MagicMock(return_value="ok"))
    monkeypatch.setattr(mb, "showerror", MagicMock(return_value="ok"))
    
    # Auto-click "Yes" on confirmation prompts
    monkeypatch.setattr(mb, "askyesno", MagicMock(return_value=True))

    # Auto-fill text inputs (like Profile creation or URL pasting)
    monkeypatch.setattr(sd, "askstring", MagicMock(return_value="Mocked_Input_String"))

    # Auto-select dummy files/folders for file explorers
    monkeypatch.setattr(fd, "askopenfilename", MagicMock(return_value="/mock/path/file.m4b"))
    monkeypatch.setattr(fd, "askdirectory", MagicMock(return_value="/mock/path/folder"))

@pytest.fixture(autouse=True)
def prevent_tkinter_windows(monkeypatch):
    """
    Globally prevents any test from accidentally spawning a real Tkinter window.
    Intercepts Tk() and Toplevel() calls and replaces them with mocks.
    """
    import tkinter as tk
    
    # Create a dummy root mock that won't crash when .after() or .withdraw() is called
    dummy_root = MagicMock()
    dummy_root.after.side_effect = lambda delay, func, *args: func(*args)
    dummy_root.after_idle.side_effect = lambda func, *args: func(*args)
    
    monkeypatch.setattr(tk, "Tk", lambda *args, **kwargs: dummy_root)
    monkeypatch.setattr(tk, "Toplevel", lambda *args, **kwargs: MagicMock())