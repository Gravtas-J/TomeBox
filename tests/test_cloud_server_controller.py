import pytest
import httpx
from unittest.mock import MagicMock
from ui.cloud_server_controller import CloudServerController

@pytest.fixture
def mock_app():
    app = MagicMock()
    app.ui_state.dl_status.set = MagicMock()
    app.root.after.side_effect = lambda delay, func, *args: func(*args)
    return app

@pytest.fixture
def cloud_controller(mock_app):
    return CloudServerController(mock_app)

def test_fetch_cloud_library_not_logged_in(cloud_controller, mock_app):
    """If auth is None, it should bounce the user without spawning a thread."""
    mock_app.api_client.auth = None
    
    cloud_controller.fetch_cloud_library()
    
    mock_app.thread_pool.submit.assert_not_called()

def test_fetch_cloud_library_success(cloud_controller, mock_app):
    """If logged in, it should spawn the worker."""
    mock_app.api_client.auth = "ValidAuth"
    
    cloud_controller.fetch_cloud_library()
    
    mock_app.ui_state.dl_status.set.assert_called_with("Fetching data from Amazon... Please wait.")
    mock_app.thread_pool.submit.assert_called_once_with(cloud_controller.fetch_library_worker)

def test_fetch_library_worker_network_error(cloud_controller, mock_app):
    """Verifies httpx connection errors are caught safely."""
    # Force the library manager to throw a connection error when called
    mock_app.library_manager.fetch_cloud_library.side_effect = httpx.ConnectError("Offline")
    
    cloud_controller.fetch_library_worker()
    
    # Assert UI cleanup still ran
    mock_app.action_router.reset_ui_if_idle.assert_called()
    mock_app.logger.error.assert_called()

def test_fetch_library_worker_success(cloud_controller, mock_app):
    """Verifies UI refreshes and missing covers are synced after a good pull."""
    mock_app.library_manager.cloud_items = [1, 2, 3]
    
    cloud_controller.fetch_library_worker()
    
    mock_app.library_manager.fetch_cloud_library.assert_called_once()
    mock_app.library_presenter.refresh_library_ui.assert_called_once()
    mock_app.metadata_manager.sync_missing_covers.assert_called_once()
    mock_app.action_router.reset_ui_if_idle.assert_called()