import pytest
from unittest.mock import MagicMock, patch
from ui.auth_controller import AuthController

@pytest.fixture
def mock_app():
    app = MagicMock()
    app.auth_save_path = "/mock/auth.json"
    app.settings = {}
    
    app.cloud_server_controller = MagicMock()

    # Mock Tkinter UI State
    app.ui_state.auth_bytes.set = MagicMock()
    app.ui_state.filter.set = MagicMock()
    app.ui_state.shelf_filter.set = MagicMock()
    app.ui_state.search.set = MagicMock()
    
    # Force Tkinter thread updates to execute instantly in tests
    app.root.after.side_effect = lambda delay, func, *args: func(*args)
    
    return app

@pytest.fixture
def auth_controller(mock_app):
    return AuthController(mock_app)

def test_auto_load_auth_success(auth_controller, mock_app):
    """Verifies UI state resets and cloud fetches when auth successfully loads."""
    mock_app.api_client.load_auth_from_file.return_value = True
    mock_app.api_client.get_activation_bytes.return_value = "deadbeef"
    
    auth_controller.auto_load_auth()
    
    mock_app.api_client.load_auth_from_file.assert_called_with("/mock/auth.json")
    mock_app.ui_state.auth_bytes.set.assert_called_with("deadbeef")
    mock_app.cloud_server_controller.fetch_cloud_library.assert_called_once()
    mock_app.ui_state.filter.set.assert_called_with("All")

def test_auto_load_auth_failure(auth_controller, mock_app):
    """Verifies it gracefully skips cloud sync if no auth is found."""
    mock_app.api_client.load_auth_from_file.return_value = False
    
    auth_controller.auto_load_auth()
    mock_app.fetch_cloud_library.assert_not_called()

@patch("ui.auth_controller.filedialog.askopenfilename")
def test_load_auth_file_prompt_success(mock_askopen, auth_controller, mock_app):
    """Verifies manual JSON file loading saves the auth and syncs."""
    mock_askopen.return_value = "/selected/auth.json"
    mock_app.api_client.load_auth_from_file.return_value = True
    mock_app.api_client.get_activation_bytes.return_value = "beefdead"
    
    auth_controller.load_auth_file_prompt()
    
    mock_app.api_client.load_auth_from_file.assert_called_with("/selected/auth.json")
    mock_app.api_client.save_auth_to_file.assert_called_with("/mock/auth.json")
    mock_app.cloud_server_controller.fetch_cloud_library.assert_called_once()

def test_start_browser_login_thread(auth_controller, mock_app):
    """Verifies the UI button disables and fires the background thread."""
    btn_mock = MagicMock()
    mock_app.browser_login_btn = btn_mock
    mock_app.ui_state.locale.get.return_value = "au"
    
    auth_controller.start_browser_login_thread()
    
    btn_mock.config.assert_called_with(text="Connecting...", state="disabled")
    mock_app.thread_pool.submit.assert_called_once_with(auth_controller.browser_login_worker, "au")

def test_switch_profile(auth_controller, mock_app):
    """Verifies that switching a profile completely resets session state and caches."""
    mock_app.profile_combo.get.return_value = "Wife_Profile"
    mock_app.db.get_auth_path.return_value = "/new/auth.json"
    mock_app.library_manager.load_cloud_cache.return_value = ["book1"]
    
    auth_controller.switch_profile()
    
    # Check Settings updated
    assert mock_app.active_profile == "Wife_Profile"
    assert mock_app.settings["active_profile"] == "Wife_Profile"
    mock_app.db.save_settings.assert_called_once_with(mock_app.settings)
    
    # Check Auth Wiped
    assert mock_app.api_client.auth is None
    mock_app.ui_state.auth_bytes.set.assert_any_call("")
    
    # Check Caches mapped
    assert mock_app.library_manager.cloud_items == ["book1"]
    mock_app.refresh_library_ui.assert_called_once()