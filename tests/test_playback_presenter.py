import pytest
import os
import time
from unittest.mock import MagicMock, patch
from ui.playback_presenter import PlaybackPresenter

@pytest.fixture
def mock_app():
    app = MagicMock()
    
    # Mock UI State Variables
    app.ui_state.playback_speed.get.return_value = "1.0x"
    app.ui_state.volume.get.return_value = 100
    app.ui_state.voice_boost.get.return_value = False
    app.ui_state.skip_silence.get.return_value = False
    
    # Mock the Core Playback Controller
    app.playback.chapters = [{"id": 0, "start_time": "0.0", "end_time": "100.0", "tags": {"title": "Test Chapter"}}]
    app.playback.current_chapter_idx = 0
    app.playback.current_play_time = 10.0
    app.playback.chapter_duration = 100.0
    app.playback.is_playing = False
    app.playback.is_paused = False
    
    # Mock Library & Database
    app.library_manager.local_library = {}
    app.active_profile = "Main"
    app.db.data_dir = "/mock/data"
    app.covers_dir = "/mock/covers"
    
    # Session tracking vars
    app.session_listen_buffer = 0.0
    app._last_disk_save_time = 0.0
    app.sleep_mode = None
    app._sleep_timer_id = None
    
    # Instantly execute Tkinter UI updates
    app.root.after.side_effect = lambda delay, func, *args: func(*args)
    
    return app

@pytest.fixture
def presenter(mock_app):
    p = PlaybackPresenter(mock_app)
    # Inject a mocked view with all the UI labels/buttons
    view = MagicMock()
    view.progress_bar.winfo_rootx.return_value = 0
    view.progress_bar.winfo_width.return_value = 200 # 200px wide progress bar
    p.set_view(view)
    return p

def test_format_time(presenter):
    """Verifies seconds convert to HH:MM:SS or MM:SS."""
    assert presenter.format_time(45) == "00:45"
    assert presenter.format_time(125) == "02:05"
    assert presenter.format_time(3665) == "01:01:05"

def test_on_playback_error(presenter, mock_app):
    """Verifies errors trigger the UI EventBus popup and stop the player."""
    mock_app.playback.is_playing = True # <--- Added so stop_audio() actually fires
    
    presenter.on_playback_error("NO_AUDIO")
    
    mock_app.playback.stop.assert_called()
    
    # Check the correct Action Router EventBus path
    mock_app.action_router.event_bus.publish.assert_called_once()
    call_args = mock_app.action_router.event_bus.publish.call_args
    
    assert call_args[0][0] == "ui.show_error"
    assert "No audio stream found" in call_args[1]["message"]

@patch("time.time")
def test_on_playback_tick(mock_time, presenter, mock_app):
    """Verifies the UI updates its progress, saves stats at 60s, and saves disk at 10s."""
    mock_time.return_value = 100.0
    mock_app._last_disk_save_time = 85.0 # 15 seconds ago (should trigger save)
    
    presenter.on_playback_tick(current_time=25.0, total_time=100.0, real_time_delta=60.0)
    
    # 1. UI Updates
    mock_app.ui_state.playback_progress.set.assert_called_with(25.0)
    presenter.view.time_label.config.assert_called_with(text="00:25 / 01:40")
    
    # 2. Stats Manager trigger (buffered >= 60)
    mock_app.stats_manager.add_stat.assert_called_with("seconds_listened", 60.0)
    assert mock_app.session_listen_buffer == 0.0
    
    # 3. Disk Save trigger (time elapsed > 10s)
    mock_app.library_manager.save_playback_state.assert_called_once()

def test_sync_playhead_from_remote(presenter, mock_app):
    """Verifies remote web sync requests are respected, but ignored if too close to current time."""
    mock_app.playback.current_play_time = 10.0
    
    # 1. Ignore if within 5 seconds (Deadzone)
    presenter.sync_playhead_from_remote(12.0)
    mock_app.playback.seek_to_absolute.assert_not_called()
    
    # 2. Execute if outside 5 seconds
    mock_app.playback.seek_to_absolute.return_value = True
    presenter.sync_playhead_from_remote(50.0)
    mock_app.playback.seek_to_absolute.assert_called_with(50.0)
    presenter.view.time_label.config.assert_called() # UI updated

def test_cue_last_played(presenter, mock_app):
    mock_app.settings = {f"last_played_Main": "/mock/last.m4b"}
    mock_app.library_manager.local_library = {"/mock/last.m4b": {}}
    
    with patch("os.path.exists", return_value=True), \
         patch.object(presenter, "load_specific_file") as mock_load:
         
        presenter.cue_last_played()
        mock_load.assert_called_with("/mock/last.m4b")

@patch("ui.playback_presenter.ProcessRunner.run_blocking")
@patch("os.path.exists", return_value=True)
def test_load_specific_file(mock_exists, mock_run, presenter, mock_app):
    """Verifies file loading extracts chapters, checks DRM, and updates position."""
    mock_run.return_value.returncode = 0 # Simulate successful DRM verification
    
    mock_app.library_manager.local_library = {
        "/mock/book.aax": {
            "progress": {"Main": 50.0},
            "chapters": [{"id": 0, "start_time": "0.0", "end_time": "100.0"}]
        }
    }
    
    presenter.load_specific_file("/mock/book.aax")
    
    assert mock_app.file_path == "/mock/book.aax"
    assert mock_app.playback.current_play_time == 50.0 # Restored progress
    mock_app.metadata_manager.fetch_display_metadata.assert_called_with("/mock/book.aax")

@patch("os.path.isfile", return_value=True)
def test_resume_playback(mock_isfile, presenter, mock_app):
    """Verifies unpausing gathers all parameters and calls the core player."""
    mock_app.file_path = "/mock/book.m4b"
    
    presenter.resume_playback()
    
    mock_app.playback.set_speed.assert_called_with(1.0)
    mock_app.playback.set_volume.assert_called_with(100)
    mock_app.playback.play.assert_called_once()

def test_pause_and_stop_audio(presenter, mock_app):
    mock_app.playback.is_playing = True
    
    presenter.pause_audio()
    mock_app.playback.pause.assert_called()
    assert mock_app.playback.is_paused is True
    
    presenter.stop_audio()
    mock_app.playback.stop.assert_called()

def test_seek_audio(presenter, mock_app):
    # Standard seek
    mock_app.playback.seek.return_value = "SUCCESS"
    presenter.seek_audio(15.0)
    mock_app.playback.seek.assert_called_with(15.0)
    
    # Seek triggers chapter crossover
    mock_app.playback.seek.return_value = "NEXT_CHAPTER"
    with patch.object(presenter, "next_chapter") as mock_next:
        presenter.seek_audio(1000.0)
        mock_next.assert_called_once()

def test_on_progress_click(presenter, mock_app):
    """Verifies clicking the progress bar calculates the correct target seek time."""
    event = MagicMock()
    event.x_root = 100 # Clicked exactly in the middle of a 200px wide bar
    
    with patch.object(presenter, "seek_audio") as mock_seek:
        presenter.on_progress_click(event)
        
        # 50% of 100.0 duration = 50.0 target time
        # Current time is 10.0. Offset should be 40.0
        mock_seek.assert_called_with(40.0)

def test_speed_and_volume_change_while_playing(presenter, mock_app):
    """Verifies changing speed or volume dynamically restarts the player if active."""
    mock_app.playback.is_playing = True
    
    with patch.object(presenter, "pause_audio") as mock_pause, \
         patch.object(presenter, "resume_playback") as mock_resume:
         
         # Speed
         app_state = mock_app.ui_state.playback_speed.get.return_value = "1.5x"
         presenter.on_speed_change()
         mock_app.playback.set_speed.assert_called_with(1.5)
         mock_pause.assert_called()
         mock_resume.assert_called()

def test_sleep_timer_chapters(presenter, mock_app):
    """Verifies sleep timer tracking by chapter decrements cleanly."""
    mock_app.playback.next_chapter.return_value = True
    app_state = mock_app.sleep_mode = "chapters"
    app_state = mock_app.sleep_chapters_remaining = 1
    
    # Simulate crossing chapter boundary
    presenter.next_chapter()
    
    # Timer should hit 0, turn off, and pause audio
    assert mock_app.sleep_mode is None
    assert mock_app.playback.is_paused is True