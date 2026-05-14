import pytest
import os
from unittest.mock import MagicMock
from ui.app_window import AAXManagerApp

@pytest.fixture
def mock_app(monkeypatch):
    """Creates a headless, mocked version of the main app class for logic testing."""
    app = MagicMock()
    
    # Setup mock library manager
    app.library_manager = MagicMock()
    app.library_manager.local_library = {}
    app.library_manager.cloud_items = [
        {
            "asin": "CLOUD_123",
            "title": "Cloud Title",
            "authors": [{"name": "Cloud Author"}],
            "series": [{"title": "Cloud Series", "sequence": "2"}],
            "runtime_length_min": 300
        }
    ]
    
    # Mock the UI popups so tests don't hang waiting for user input
    monkeypatch.setattr("ui.app_window.filedialog.askopenfilename", lambda **kw: "/real/local/file.m4b")
    monkeypatch.setattr("ui.app_window.messagebox.showinfo", MagicMock())
    
    # Bind the specific method we want to test to our mock object
    app.match_local_file_to_cloud = AAXManagerApp.match_local_file_to_cloud.__get__(app)
    return app

def test_match_local_file_to_cloud_mapping(mock_app):
    """Verifies that cloud JSON arrays are properly flattened and mapped to the local file DB."""
    
    # Initial state: The local file exists in the DB but has bad/missing data
    mock_app.library_manager.local_library = {"/real/local/file.m4b": {"title": "Unknown File"}}
    
    # Execute mapping
    mock_app.match_local_file_to_cloud("Cloud Title", "CLOUD_123")
    
    # Verify the local DB was mapped correctly
    updated_data = mock_app.library_manager.local_library["/real/local/file.m4b"]
    
    assert updated_data["title"] == "Cloud Title"
    assert updated_data["asin"] == "CLOUD_123"
    assert updated_data["authors"] == "Cloud Author"
    assert updated_data["series"] == "Cloud Series, Book 2"
    assert updated_data["duration_min"] == 300
    assert updated_data["format"] == "M4B"
    assert updated_data["path"] == "/real/local/file.m4b"
    
    # Verify DB save and cover sync triggers fired
    mock_app.library_manager.db.save_local_db.assert_called_once()
    mock_app.metadata_manager.sync_missing_covers.assert_called_once()
    mock_app.library_presenter.refresh_library_ui.assert_called()