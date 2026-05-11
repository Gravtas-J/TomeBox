import tkinter as tk
import time
import pytest
from unittest.mock import MagicMock
from ui.components.dialogs import open_match_to_audible_window
from core.events import EventBus

@pytest.fixture
def mock_app():
    """Provides a headless Tkinter instance and a mocked app environment."""
    app = MagicMock()
    app.root = tk.Tk()
    
    app.library_manager.local_library = {
        "/fake/file.m4b": {"title": "Test Book", "authors": "Test Author"}
    }
    
    app.metadata_manager.event_bus = EventBus()
    yield app
    app.root.destroy()

def find_widget_by_text(parent, text):
    """Recursively walks the Tkinter widget tree to find a specific button."""
    try:
        if parent.cget("text") == text:
            return parent
    except tk.TclError:
        pass 
        
    for child in parent.winfo_children():
        result = find_widget_by_text(child, text)
        if result:
            return result
    return None

def test_search_button_race_condition_and_unlock(mock_app):
    """Verifies that the search button disables itself to prevent spamming, and unlocks on completion."""
    
    open_match_to_audible_window(mock_app, "/fake/file.m4b")
    
    dialog_window = mock_app.root.winfo_children()[0] 
    search_btn = find_widget_by_text(dialog_window, "Search")
    
    assert search_btn is not None, "Could not find the Search button in the UI tree."

    # 1. Wait out the 100ms auto-search that triggers when the window opens
    time.sleep(0.15)
    mock_app.root.update()
    
    # Resolve the auto-search so the button unlocks for our actual test
    mock_app.metadata_manager.event_bus.publish("metadata.search_complete", filepath="/fake/file.m4b", products=[])
    
    # Poll until Tkinter processes the .after(0) unlock callback
    for _ in range(10):
        mock_app.root.update()
        if str(search_btn.cget("state")) == tk.NORMAL:
            break
        time.sleep(0.05)
        
    assert str(search_btn.cget("state")) == tk.NORMAL
    
    # Reset our mock counter so we can test the double-click cleanly
    mock_app.metadata_manager.search_catalog.reset_mock()

    # 2. Simulate a rapid double-click
    search_btn.invoke()
    search_btn.invoke()
    
    # Verify it only triggered once and immediately locked
    mock_app.metadata_manager.search_catalog.assert_called_once_with("/fake/file.m4b", "Test Book Test Author")
    assert str(search_btn.cget("state")) == tk.DISABLED
    
    # 3. Simulate completion
    mock_app.metadata_manager.event_bus.publish("metadata.search_complete", filepath="/fake/file.m4b", products=[])
    
    # Poll until unlocked
    for _ in range(10):
        mock_app.root.update()
        if str(search_btn.cget("state")) == tk.NORMAL:
            break
        time.sleep(0.05)
        
    assert str(search_btn.cget("state")) == tk.NORMAL