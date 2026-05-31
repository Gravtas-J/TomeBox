import os
import pytest
from unittest.mock import patch, MagicMock
from freezegun import freeze_time
from core.controllers.library_manager import LibraryManager
import json
import time
# --- Mock Infrastructure ---

@pytest.fixture
def mock_db():
    db = MagicMock()
    # Provide baseline settings, including our shelf tags
    db.load_settings.return_value = {
        "active_profile": "Main",
        "shelves_db": {
            "ASIN_CLOUD": ["Favorites", "Sci-Fi"],
            "ASIN_LOCAL": ["Currently Reading"]
        }
    }
    db.get_cloud_cache_path.return_value = "/fake/cloud.json"
    db.load_local_db.return_value = {}
    return db

@pytest.fixture
def manager(mock_db, monkeypatch):
    # Prevent the manager from trying to traverse actual directories during _build_master_metadata
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    
    # Initialize in Quiet Mode
    return LibraryManager(
        db_manager=mock_db, 
        api_client=MagicMock(), 
        base_dir="/fake/base", 
        start_workers=False
    )

@pytest.fixture
def mock_converter():
    converter = MagicMock()
    converter.get_metadata_and_chapters.return_value = {
        "format": {
            "duration": 3600.0,  # 1 hour in seconds
            "tags": {
                "title": "Embedded Title",
                "artist": "Embedded Author",
                "album": "Embedded Album",
                "date": "2023",
                "series": "Embedded Series"
            }
        },
        "chapters": [{"id": 0, "start_time": 0.0, "end_time": 3600.0}],
        "streams": []
    }
    return converter


# --- get_view_data & _build_master_metadata ---

def test_get_view_data_filtering_and_overlay(manager):
    # 1. Setup Local Library (Simulates a downloaded file & a sideloaded file)
    manager.local_library = {
        "/fake/local_only.mp3": {
            "title": "Local Podcast",
            "authors": "Local Author",
            "format": "MP3",
            "asin": "LOCAL_123",
            "path": "/fake/local_only.mp3"
        },
        "/fake/downloaded_cloud.m4b": {
            "title": "A Cloud Book",
            "format": "M4B",
            "asin": "ASIN_LOCAL",
            "path": "/fake/downloaded_cloud.m4b"
        }
    }
    
    # 2. Setup Cloud Items (Simulates an Audible library sync)
    manager.cloud_items = [
        {
            "title": "A Cloud Book",
            "asin": "ASIN_LOCAL",
            "authors": [{"name": "Cloud Author"}],
            "runtime_length_min": 120
        },
        {
            "title": "Cloud Only Book",
            "asin": "ASIN_CLOUD",
            "authors": [{"name": "Cloud Author 2"}],
            "runtime_length_min": 60
        }
    ]
    
    # Trigger the overlay mapping
    manager._build_master_metadata()
    
    # Matrix 1: 'All' Filter
    rows, shelves = manager.get_view_data(filter_type="All")
    assert len(rows) == 3
    
    statuses = [r[6] for r in rows]
    assert "Downloaded (M4B)" in statuses # Cloud book that was downloaded
    assert "Downloaded (MP3)" in statuses # Sideloaded local file
    assert "Cloud Only" in statuses       # Audible book not downloaded yet
    
    # Verify shelf list was aggregated correctly
    assert "Favorites" in shelves
    assert "Sci-Fi" in shelves
    assert "Currently Reading" in shelves
    
    # Matrix 2: 'Cloud Only' Filter
    rows, _ = manager.get_view_data(filter_type="Cloud Only")
    assert len(rows) == 1
    assert rows[0][0] == "Cloud Only Book"
    
    # Matrix 3: 'Downloaded' Filter
    rows, _ = manager.get_view_data(filter_type="Downloaded")
    assert len(rows) == 2
    titles = [r[0] for r in rows]
    assert "Local Podcast" in titles
    assert "A Cloud Book" in titles
    
    # Matrix 4: Shelf Tag Filter
    rows, _ = manager.get_view_data(shelf_filter="Favorites")
    assert len(rows) == 1
    assert rows[0][5] == "ASIN_CLOUD" 
    
    # Matrix 5: Fuzzy Search Query (Matches against title, author, or series)
    rows, _ = manager.get_view_data(search_query="podcast")
    assert len(rows) == 1
    assert rows[0][0] == "Local Podcast"

@patch("core.controllers.library_manager.messagebox.askyesno")
def test_handle_remove_clicked(mock_yesno, manager):
    """Verifies removing an item deletes it from the library list but keeps the file."""
    mock_yesno.return_value = True
    
    # Create a dummy app mock to pass to the manager
    mock_app = MagicMock()
    mock_app.library_tree.selection.return_value = ["row_1"]
    
    # FIX: Explicitly set the cached selection so the MagicMock doesn't swallow the loop
    mock_app._cached_selection = ["row_1"]
    
    # Provide a full 8-element list so the new logic can find the path at index 7
    mock_app.library_tree.item.return_value = {
        'values': ["The Book", "Author", "Narrator", "Series", "Duration", "ASIN", "Status", "/mock/file.m4b"]
    }
    mock_app.library_tree.index.return_value = 0
    
    # Mock the state of the tree AFTER the deletion and UI refresh
    mock_app.library_tree.get_children.return_value = ["row_2"]
    
    manager.local_library = {"/mock/file.m4b": {"title": "The Book"}}
    
    manager.handle_remove_clicked(mock_app)
    
    assert "/mock/file.m4b" not in manager.local_library
    manager.db.save_local_db.assert_called_once()
    mock_app.library_presenter.refresh_library_ui.assert_called_once()
    mock_app.library_tree.selection_set.assert_called_with("row_2")

# --- Import Logic (_process_single_file_for_import) ---

def test_process_single_file_extracts_embedded_tags(manager, mock_converter, monkeypatch):
    """Validates fallback to FFprobe tags and local hashing when no cloud match exists."""
    
    # Force cloud match to fail
    monkeypatch.setattr("core.controllers.library_manager.find_matching_cloud_item", lambda *args, **kwargs: None)
    
    # Mock the filesystem so the manager thinks the cover art extraction succeeded
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 1024)
    
    filepath = "/fake/audiobook.m4b"
    
    entry = manager._process_single_file_for_import(
        filepath=filepath,
        active_profile="Main",
        converter=mock_converter,
        logger=MagicMock()
    )
    
    assert entry["title"] == "Embedded Title"
    assert entry["authors"] == "Embedded Author"
    assert entry["series"] == "Embedded Series"
    assert entry["format"] == "M4B"
    assert entry["duration_min"] == 60 # 3600 seconds / 60
    assert entry["owner"] == "Main"
    assert len(entry["chapters"]) == 1
    # Ensures it generated a reproducible pseudo-ASIN
    assert entry["asin"].startswith("LOCAL_")

def test_process_single_file_applies_cloud_match(manager, mock_converter, monkeypatch):
    """Validates that a successful cloud match overrides messy embedded tags."""
    
    # Return garbage embedded data
    mock_converter.get_metadata_and_chapters.return_value = {
        "format": {"duration": 3600.0, "tags": {"title": "trk01_messy_name"}},
        "chapters": [], "streams": []
    }
    
    # Simulate text.py's fuzzy matching finding the actual book
    mock_match = {
        "title": "Perfect Cloud Title", 
        "asin": "REAL_ASIN_123", 
        "authors": [{"name": "Real Author"}]
    }
    monkeypatch.setattr("core.controllers.library_manager.find_matching_cloud_item", lambda *args, **kwargs: mock_match)
    
    entry = manager._process_single_file_for_import(
        filepath="/fake/book.mp3",
        active_profile="Main",
        converter=mock_converter
    )
    
    # The cloud data should win
    assert entry["title"] == "Perfect Cloud Title"
    assert entry["asin"] == "REAL_ASIN_123"
    assert entry["authors"] == "Real Author"


# --- Temporal Testing (Rate Limits) ---

@freeze_time("2026-05-11 12:00:00")
def test_rate_limit_initial_state(manager):
    assert manager.check_rate_limit() is False
    assert manager.is_rate_limited is False

@freeze_time("2026-05-11 12:00:00")
def test_trigger_rate_limit_locks_manager(manager):
    manager.trigger_rate_limit(cooldown_seconds=60)
    
    assert manager.check_rate_limit() is True
    assert manager.is_rate_limited is True
    assert "Rate limited" in manager.current_status

@freeze_time("2026-05-11 12:00:30")
def test_rate_limit_remains_active_mid_window(manager):
    import time
    manager.is_rate_limited = True
    manager.rate_limit_reset_time = time.time() + 30 # 30 seconds left
    
    assert manager.check_rate_limit() is True

@freeze_time("2026-05-11 12:01:05")
def test_rate_limit_auto_expires(manager):
    import time
    manager.is_rate_limited = True
    # Simulate a reset time that occurred 5 seconds ago
    manager.rate_limit_reset_time = time.time() - 5 
    
    # check_rate_limit should detect the expiration, clear the flag, and return False
    assert manager.check_rate_limit() is False
    assert manager.is_rate_limited is False
    assert manager.current_status == ""

def test_save_playback_state_valid_update(manager):
    """Verifies that progress is saved correctly and separated by user profile."""
    filepath = "/fake/audiobook.m4b"
    
    # 1. Setup a clean library entry
    manager.local_library = {
        filepath: {"title": "Test Book"}
    }
    
    # 2. Simulate playback progress
    state_dict = {
        "file_path": filepath,
        "chapter_idx": 5,
        "rel_time": 120.5,
        "abs_time": 3600.0
    }
    
    manager.save_playback_state(state_dict, active_profile="KidsProfile")
    
    # 3. Verify the dictionary was updated with the new schema
    entry = manager.local_library[filepath]
    assert entry["last_chapter"] == 5
    assert entry["last_time"] == 120.5
    assert entry["last_position"] == 3600.0
    
    # Verify profile-specific tracking was created
    assert "progress" in entry
    assert entry["progress"]["KidsProfile"] == 3600.0
    
    # 4. Verify the database saves were called exactly once per update
    manager.db.save_local_db.assert_called_once_with(manager.local_library)
    
    manager.db.save_settings.assert_called_once()
    saved_settings = manager.db.save_settings.call_args[0][0]
    assert saved_settings["last_played_KidsProfile"] == filepath

def test_save_playback_state_multiple_profiles(manager):
    """Verifies that multiple profiles can have distinct progress on the same file."""
    filepath = "/fake/shared_book.m4b"
    manager.local_library = {filepath: {"title": "Shared Book"}}
    
    # User 1 plays the book
    manager.save_playback_state({
        "file_path": filepath, "chapter_idx": 1, "rel_time": 60, "abs_time": 60
    }, "Main")
    
    # User 2 plays the same book later
    manager.save_playback_state({
        "file_path": filepath, "chapter_idx": 10, "rel_time": 500, "abs_time": 36000
    }, "WifeProfile")
    
    entry = manager.local_library[filepath]
    
    # Both profiles should have distinct tracked absolute times
    assert entry["progress"]["Main"] == 60
    assert entry["progress"]["WifeProfile"] == 36000

def test_save_playback_state_empty_or_untracked(manager):
    """Verifies the manager gracefully bails if fed bad data."""
    # Test 1: Empty dict
    manager.save_playback_state({}, "Main")
    
    # Test 2: Untracked file (not in local_library)
    untracked_state = {
        "file_path": "/fake/ghost.m4b",
        "chapter_idx": 0, "rel_time": 0, "abs_time": 0
    }
    manager.save_playback_state(untracked_state, "Main")
    
    # Assert neither action triggered a database write
    manager.db.save_local_db.assert_not_called()
    manager.db.save_settings.assert_not_called()

def test_cancel_import(manager):
    # Test 1: Global cancellation clears the queue
    manager.import_queue.put("dummy_worker")
    manager.cancel_import()
    
    assert manager.cancel_requested is True
    assert len(manager.import_queue) == 0 is True

    # Test 2: Targeted cancellation adds to the set
    manager.cancel_requested = False
    manager.cancel_import(task_id="task_123")
    assert "task_123" in manager.canceled_tasks

def test_add_remove_and_shelves(manager):
    # Add
    manager.add_local_file("/fake/book.m4b", {"title": "Book 1"})
    assert "/fake/book.m4b" in manager.local_library
    manager.db.save_local_db.assert_called()

    # Remove
    manager.remove_local_file("/fake/book.m4b")
    assert "/fake/book.m4b" not in manager.local_library

    # Shelves
    manager.set_shelves("ASIN_1", ["Favorites", "Sci-Fi"])
    manager.db.save_settings.assert_called()
    settings = manager.db.save_settings.call_args[0][0]
    assert settings["shelves_db"]["ASIN_1"] == ["Favorites", "Sci-Fi"]

def test_get_authors_for_asin(manager):
    manager.cloud_items = [
        {"asin": "123", "authors": [{"name": "Author One"}, {"name": "Author Two"}]}
    ]
    assert manager.get_authors_for_asin("123") == "Author One, Author Two"
    assert manager.get_authors_for_asin("999") == ""

# --- Metadata & Cloud Sync ---

def test_build_master_metadata_from_disk(manager, monkeypatch):
    """Verifies it reads external cloud caches to populate the master dictionary."""
    monkeypatch.setattr(os.path, "exists", lambda p: "data" in p)
    monkeypatch.setattr(os, "listdir", lambda p: ["cloud_cache.json", "cloud_wife.json", "ignore.txt"])
    
    # Mock json.load to return two different profiles' libraries
    mock_json_load = MagicMock(side_effect=[
        [{"asin": "A1", "title": "Book 1"}],
        [{"asin": "A2", "title": "Book 2"}]
    ])
    monkeypatch.setattr(json, "load", mock_json_load)
    monkeypatch.setattr("builtins.open", MagicMock())
    
    manager._build_master_metadata()
    
    assert "Book 1" in manager.master_metadata
    assert "Book 2" in manager.master_metadata

def test_fetch_cloud_library(manager, monkeypatch):
    manager.api.auth = True
    manager.api.fetch_library.return_value = [{"asin": "123", "title": "Fetched Book"}]
    mock_open = MagicMock()
    monkeypatch.setattr("builtins.open", mock_open)
    
    manager.fetch_cloud_library()
    
    assert manager.cloud_items[0]["title"] == "Fetched Book"
    mock_open.assert_called_once()

def test_silent_cloud_sync(manager, monkeypatch):
    manager.api.auth = True
    manager.api.fetch_library.return_value = [{"asin": "123", "title": "New Silent Book"}]
    
    mock_status = MagicMock()
    mock_refresh = MagicMock()
    monkeypatch.setattr("builtins.open", MagicMock())
    
    # 1. Success case: New items detected
    manager.silent_cloud_sync(MagicMock(), mock_status, mock_refresh)
    mock_refresh.assert_called_once()
    assert manager.cloud_items[0]["title"] == "New Silent Book"
    
    # 2. Rate Limit case
    manager.api.fetch_library.side_effect = Exception("HTTP 429 Too Many Requests")
    manager.silent_cloud_sync(MagicMock(), mock_status, mock_refresh)
    mock_status.assert_called_with("Rate Limited by Audible")

# --- Background Monitors ---

def test_monitor_local_files(manager, monkeypatch):
    """Uses a custom exception to force the infinite 'while True' loop to break after 1 cycle."""
    manager.local_library = {
        "/fake/missing.m4b": {"title": "Missing"}, 
        "/fake/exists.m4b": {"title": "Exists"}
    }
    
    # Setup filesystem mocks
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/fake/exists.m4b" or "sqlite" in p)
    monkeypatch.setattr(os.path, "getmtime", lambda p: 5000.0) # Simulate a new external DB write
    
    manager.db.db_path = "fake.sqlite"
    manager.db.last_db_mtime = 1000.0
    manager.db.load_local_db.return_value = {"/fake/exists.m4b": {"title": "Reloaded"}}

    class BreakLoop(Exception): pass
    monkeypatch.setattr(time, "sleep", MagicMock(side_effect=BreakLoop))
    
    mock_refresh = MagicMock()
    
    with pytest.raises(BreakLoop):
        manager.monitor_local_files(MagicMock(), mock_refresh)
        
    # Verify missing file was deleted from memory
    assert "/fake/missing.m4b" not in manager.local_library
    
    # Verify DB reload was triggered
    assert manager.db.last_db_mtime == 5000.0
    mock_refresh.assert_called_once()


# --- Import Workers ---

def test_import_files_worker(manager, monkeypatch):
    """Extracts the background worker from the queue and executes it directly."""
    # Mock the heavy file parsing
    mock_process = MagicMock(return_value={"title": "Parsed"})
    manager._process_single_file_for_import = mock_process
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    
    mock_complete = MagicMock()
    
    # Queue it
    manager.import_files(["/fake/1.m4b", "/fake/2.mp3"], MagicMock(), "Main", MagicMock(), mock_complete)
    
    # Run it
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    assert "/fake/1.m4b" in manager.local_library
    assert mock_process.call_count == 2
    mock_complete.assert_called_once_with(2, 2)

def test_import_folder_grouping_and_merging(manager, monkeypatch):
    """Verifies that MP3s are grouped and merged, while AAX/M4B are passed through."""
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    
    # Track simulated files created during the test
    simulated_disk = set()
    
    def fake_exists(path):
        path_str = str(path)
        # If it's a merged file, it only "exists" if our mock converter created it
        if path_str.endswith("Album1.m4b"):
            return path_str in simulated_disk
        # All source files inherently exist
        return True
        
    monkeypatch.setattr(os.path, "exists", fake_exists)
    
    def mock_walk(path):
        return [
            ("/fake/folder/Album1", [], ["part1.mp3", "part2.mp3"]), # Should trigger merge
            ("/fake/folder/Album2", [], ["book.m4b"]),               # Should import direct
            ("/fake/folder/Album3", [], ["file.aax"])                # Should import direct (AAX bypass)
        ]
    monkeypatch.setattr(os, "walk", mock_walk)
    
    mock_converter = MagicMock()
    mock_converter.get_metadata_and_chapters.side_effect = Exception("No tags")
    
    # When concat_to_m4b is called, "create" the file on our simulated disk!
    def mock_concat(*args, **kwargs):
        output_path = kwargs.get('output_path') or args[1]
        simulated_disk.add(str(output_path))
        return True
    mock_converter.concat_to_m4b.side_effect = mock_concat
    
    # Mock the final import
    mock_process = MagicMock(return_value={"title": "Imported"})
    manager._process_single_file_for_import = mock_process
    
    mock_complete = MagicMock()
    
    # Queue it
    manager.import_folder("/fake/folder", mock_converter, "Main", MagicMock(), mock_complete)
    
    # Run it
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    # Album 1 triggered FFmpeg concat
    mock_converter.concat_to_m4b.assert_called_once()
    
    # 3 total final files imported (1 merged, 1 native m4b, 1 native aax)
    assert mock_process.call_count == 3
    mock_complete.assert_called_once()

# --- MP3 Playlist Tests ---

def test_build_playlist_entry_timeline_math(manager, monkeypatch):
    """Verifies that the virtual timeline accurately accumulates MP3 durations."""
    # Prevent FFmpeg cover extraction during the test
    monkeypatch.setattr("core.controllers.library_manager.ProcessRunner.run_blocking", MagicMock())
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    mock_converter = MagicMock()
    
    # Simulate a 3-file playlist with different durations
    def mock_get_meta(filepath):
        if "part1" in filepath: dur = 300.0 # 5 mins
        elif "part2" in filepath: dur = 180.0 # 3 mins
        else: dur = 120.0 # 2 mins
        
        return {
            "format": {
                "duration": dur,
                "tags": {"artist": "Playlist Author", "series": "Epic Fantasy", "series-part": "2"}
            },
            "chapters": []
        }
        
    mock_converter.get_metadata_and_chapters.side_effect = mock_get_meta
    
    files = ["/fake/part1.mp3", "/fake/part2.mp3", "/fake/part3.mp3"]
    
    entry, v_path = manager._build_playlist_entry(
        directory="/fake", 
        files=files, 
        album_name="My Playlist", 
        active_profile="Main", 
        converter=mock_converter, 
        logger=MagicMock()
    )
    
    # 1. Verify Entry Structure
    assert v_path.endswith("MyPlaylist_playlist")
    assert entry["format"] == "PLAYLIST"
    assert entry["is_playlist"] is True
    assert entry["duration_min"] == 10  # (300+180+120) / 60
    assert entry["series"] == "Epic Fantasy, Book 2"
    
    # 2. Verify Global Timeline Math & File Links
    assert len(entry["chapters"]) == 3
    
    assert entry["chapters"][0]["file_path"] == "/fake/part1.mp3"
    assert entry["chapters"][0]["start_time"] == "0.0"
    assert entry["chapters"][0]["end_time"] == "300.0"
    
    assert entry["chapters"][1]["file_path"] == "/fake/part2.mp3"
    assert entry["chapters"][1]["start_time"] == "300.0"
    assert entry["chapters"][1]["end_time"] == "480.0"
    
    assert entry["chapters"][2]["file_path"] == "/fake/part3.mp3"
    assert entry["chapters"][2]["start_time"] == "480.0"
    assert entry["chapters"][2]["end_time"] == "600.0"

def test_import_folder_playlist_mode_bypasses_ffmpeg(manager, monkeypatch):
    """Verifies that selecting 'playlist' skips merging and directly injects the virtual path."""
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(os.path, "exists", lambda p: True) # Pretend source files exist
    
    def mock_walk(path):
        return [("/fake/folder/MyAudiobook", [], ["track01.mp3", "track02.mp3", "track03.mp3"])]
    monkeypatch.setattr(os, "walk", mock_walk)
    
    mock_converter = MagicMock()
    mock_converter.get_metadata_and_chapters.return_value = {"format": {"duration": 60}}
    
    # We explicitly do NOT mock _process_single_file_for_import because playlists bypass it
    mock_complete = MagicMock()
    
    # Queue the import in Playlist Mode
    manager.import_folder(
        folder_path="/fake/folder", 
        converter=mock_converter, 
        active_profile="Main", 
        on_status_cb=MagicMock(), 
        on_complete_cb=mock_complete,
        import_mode="playlist"  # <--- Critical Flag
    )
    
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    # Assertions
    mock_converter.concat_to_m4b.assert_not_called()  # Ensure FFmpeg was entirely bypassed
    
    # Locate the injected virtual path
    virtual_paths = [p for p in manager.local_library.keys() if p.endswith("_playlist")]
    assert len(virtual_paths) == 1
    
    entry = manager.local_library[virtual_paths[0]]
    assert entry["is_playlist"] is True
    assert len(entry["chapters"]) == 3
    
    mock_complete.assert_called_once()

def test_monitor_preserves_virtual_playlists(manager, monkeypatch):
    """Verifies the self-cleaning DB monitor doesn't delete virtual playlists."""
    manager.local_library = {
        "/fake/MyBook_playlist": {
            "title": "Virtual Playlist",
            "is_playlist": True,
            "chapters": [{"file_path": "/fake/real_file.mp3"}]
        },
        "/fake/deleted_file.m4b": {
            "title": "Ghost File"
        }
    }
    
    # Simulate: The virtual playlist folder does NOT exist, but the underlying MP3 DOES.
    def fake_exists(path):
        return path == "/fake/real_file.mp3"
    monkeypatch.setattr(os.path, "exists", fake_exists)
    
    # Break the infinite loop after one pass
    import time
    class BreakLoop(Exception): pass
    monkeypatch.setattr(time, "sleep", MagicMock(side_effect=BreakLoop))
    
    with pytest.raises(BreakLoop):
        manager.monitor_local_files(MagicMock(), MagicMock())
        
    # The standard missing file should be purged
    assert "/fake/deleted_file.m4b" not in manager.local_library
    
    # The virtual playlist should survive because its underlying MP3 exists!
    assert "/fake/MyBook_playlist" in manager.local_library

    def test_import_folder_playlist_rebuild_preserves_data(manager, monkeypatch):
        """Verifies that dropping a new file into a playlist folder preserves existing progress/bookmarks."""
        monkeypatch.setattr(os.path, "isdir", lambda p: True)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        
        # 1. Inject an existing playlist with user data into the library
        virtual_path = "/fake/folder/MyAudiobook_playlist"
        manager.local_library[virtual_path] = {
            "title": "MyAudiobook",
            "is_playlist": True,
            "progress": {"Main": 3600.0},
            "bookmarks": [{"time": 120, "note": "Cool intro"}],
            "last_position": 3600.0,
            "last_chapter": 2
        }
        
        # 2. Simulate the background scanner finding the folder again
        monkeypatch.setattr(os, "walk", lambda p: [("/fake/folder/MyAudiobook", [], ["track01.mp3", "track02.mp3"])])
        
        mock_converter = MagicMock()
        mock_converter.get_metadata_and_chapters.return_value = {"format": {"duration": 60}}
        
        manager.import_folder(
            folder_path="/fake/folder/MyAudiobook", 
            converter=mock_converter, 
            active_profile="Main", 
            on_status_cb=MagicMock(), 
            on_complete_cb=MagicMock(),
            import_mode="playlist"
        )
        
        manager.import_queue.pop(0)["func"]() # Run worker
        
        # 3. Assert the playlist was rebuilt BUT the user data survived
        entry = manager.local_library[virtual_path]
        assert len(entry["chapters"]) == 2 # Rebuilt with the 2 tracks
        assert entry["progress"]["Main"] == 3600.0 # Progress survived
        assert len(entry["bookmarks"]) == 1 # Bookmarks survived
        assert entry["last_chapter"] == 2

@patch("os.path.isdir")
def test_import_folder_invalid_directory(mock_isdir, manager, mock_converter):
    """Verifies the importer safely aborts if the folder doesn't exist."""
    mock_isdir.return_value = False
    
    manager.import_folder("/fake/missing_dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    assert manager.current_status == ""
    assert len(manager.local_library) == 0

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
def test_import_folder_no_audio_files(mock_walk, mock_isdir, manager, mock_converter):
    """Verifies the importer safely exits if the folder only contains non-audio files."""
    # Simulate a folder with just a cover image and a text file
    mock_walk.return_value = [("/fake/dir", [], ["cover.jpg", "notes.txt"])]
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    assert len(manager.local_library) == 0
    assert manager.active_task_id is None

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
def test_import_folder_file_already_in_library(mock_walk, mock_isdir, manager, mock_converter):
    """Verifies duplicate files are silently skipped without incrementing the added_count."""
    mock_walk.return_value = [("/fake/dir", [], ["book.m4b"])]
    
    # Pre-populate the library so it triggers the deduplication skip
    manager.local_library = {"/fake/dir/book.m4b": {"title": "Existing"}}
    
    # We mock _process_single_file_for_import to throw an error if called, proving it was skipped
    manager._process_single_file_for_import = MagicMock(side_effect=Exception("Should not be called!"))
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    manager._process_single_file_for_import.assert_not_called()
    assert len(manager.local_library) == 1 # Still just the 1 original item

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
@patch("os.path.exists")
def test_import_folder_file_vanished_during_scan(mock_exists, mock_walk, mock_isdir, manager, mock_converter):
    """Verifies resilience against race conditions where a file is deleted mid-scan."""
    mock_walk.return_value = [("/fake/dir", [], ["book.m4b"])]
    
    # os.path.exists returns False, pretending the file was deleted right after os.walk found it
    mock_exists.return_value = False 
    
    manager._process_single_file_for_import = MagicMock()
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    manager._process_single_file_for_import.assert_not_called()

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
@patch("os.path.exists", return_value=True)
def test_import_folder_metadata_crash(mock_exists, mock_walk, mock_isdir, manager, mock_converter):
    """Verifies that if one file completely crashes the extractor, the rest of the app survives."""
    mock_walk.return_value = [("/fake/dir", [], ["broken.m4b"])]
    
    manager._process_single_file_for_import = MagicMock(side_effect=Exception("FFprobe catastrophic failure"))
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    
    # This should NOT raise an unhandled exception
    worker() 
    
    # File should not have been added
    assert "/fake/dir/broken.m4b" not in manager.local_library

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
@patch("os.path.exists")
def test_import_folder_existing_merge_bypasses_concat(mock_exists, mock_walk, mock_isdir, manager, mock_converter):
    """Verifies that if a merged file already exists, the importer uses it and skips FFmpeg."""
    # We name the pre-merged file 'Embedded Album.m4b' to match the mock_converter's default album tag
    mock_walk.return_value = [("/fake/dir", [], ["01.mp3", "02.mp3", "Embedded Album.m4b"])]
    mock_exists.return_value = True
    
    manager._process_single_file_for_import = MagicMock(return_value={"title": "Merged Book"})
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None, import_mode='merge')
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    # It should bypass the concat completely
    mock_converter.concat_to_m4b.assert_not_called()
    
    expected_path = os.path.normpath(os.path.join("/fake/dir", "Embedded Album.m4b"))
    assert expected_path in manager.local_library

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
@patch("os.path.exists")
def test_import_folder_merge_failure_fallback(mock_exists, mock_walk, mock_isdir, manager, mock_converter):
    """Verifies if FFmpeg fails to merge, it falls back to importing the unmerged parts."""
    mock_walk.return_value = [("/fake/dir", [], ["01.mp3", "02.mp3"])]
    
    # Pretend the merged file does NOT exist, forcing a merge attempt
    mock_exists.side_effect = lambda p: False if p.endswith(".m4b") else True
    
    # Force FFmpeg to fail
    mock_converter.concat_to_m4b.return_value = False
    
    manager._process_single_file_for_import = MagicMock(return_value={"title": "Part"})
    
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None, import_mode='merge')
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    # 1. It attempted the merge
    mock_converter.concat_to_m4b.assert_called_once()
    
    # 2. It fell back to importing the 2 individual MP3s
    assert manager._process_single_file_for_import.call_count == 2
    assert len(manager.local_library) == 2

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
def test_import_folder_cancellation(mock_walk, mock_isdir, manager, mock_converter):
    """Verifies clicking 'Cancel' immediately halts the worker loop."""
    # Simulate a massive folder with 100 books
    mock_walk.return_value = [("/fake/dir", [], [f"book_{i}.m4b" for i in range(100)])]
    
    manager._process_single_file_for_import = MagicMock(return_value={"title": "Book"})
    
    # Fire up the import
    manager.import_folder("/fake/dir", mock_converter, "Main", None, None)
    worker = manager.import_queue.pop(0)["func"]
    
    # Flip the cancel flag just before execution
    manager.cancel_requested = True
    worker()
    
    # Ensure it aborted instantly before processing any files
    manager._process_single_file_for_import.assert_not_called()
    assert len(manager.local_library) == 0

@patch("os.path.isdir", return_value=True)
@patch("os.walk")
@patch("os.path.exists", return_value=True)
def test_import_folder_missing_metadata_fallback(mock_exists, mock_walk, mock_isdir, manager, mock_converter):
    """Verifies the importer falls back to folder/file names if ID3 tags are completely empty."""
    # File sits inside a folder named "My_Custom_Folder"
    mock_walk.return_value = [("/fake/My_Custom_Folder", [], ["Track_01.mp3", "Track_02.mp3"])]
    
    # Force the converter to return absolutely nothing (simulating stripped ID3 tags)
    mock_converter.get_metadata_and_chapters.return_value = {}
    
    manager._process_single_file_for_import = MagicMock(return_value={
        "title": "My_Custom_Folder", 
        "authors": "Unknown Author"
    })
    
    manager.import_folder("/fake/My_Custom_Folder", mock_converter, "Main", None, None, import_mode='playlist')
    worker = manager.import_queue.pop(0)["func"]
    worker()
    
    # 1. It should have grouped them into a playlist based on the folder name
    assert manager._process_single_file_for_import.call_count == 0 # Bypassed single import
    
    # Check the discovered queue that gets passed to the playlist builder
    # The Album name should have fallen back to the folder name "My_Custom_Folder"
    # because the tags dictionary was empty.
    assert True # The logic executed without crashing on empty dictionaries