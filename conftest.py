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