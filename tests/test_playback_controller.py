import pytest
from unittest.mock import MagicMock
from core.controllers.playback_controller import PlaybackController

# --- Mock Infrastructure ---

class MockPlayer:
    """Records calls and simulates basic AudioPlayer state for the controller."""
    def __init__(self, logger, on_complete_cb=None, on_error_cb=None):
        self.on_complete_cb = on_complete_cb
        self.on_error_cb = on_error_cb
        self.call_log = []
        self.is_playing = False
        
    def play(self, filepath, start_time, remaining_duration, speed, volume, voice_boost, skip_silence, drm_flags, audio_device):
        self.call_log.append(("play", filepath, start_time))
        self.is_playing = True
        return True
        
    def stop(self):
        self.call_log.append(("stop",))
        self.is_playing = False
        
    def set_volume(self, volume):
        self.call_log.append(("set_volume", volume))

@pytest.fixture
def controller():
    ctrl = PlaybackController(
        logger=MagicMock(),
        on_tick_cb=MagicMock(),
        on_chapter_end_cb=MagicMock(),
        on_error_cb=MagicMock(),
        player_factory=MockPlayer
    )
    
    # Load standard test chapters
    # Chapter 0: 0s to 60s (Length: 60)
    # Chapter 1: 60s to 180s (Length: 120)
    # Chapter 2: 180s to 200s (Length: 20)
    chapters = [
        {"id": 0, "start_time": 0.0, "end_time": 60.0},
        {"id": 1, "start_time": 60.0, "end_time": 180.0},
        {"id": 2, "start_time": 180.0, "end_time": 200.0}
    ]
    
    # Use the controller's own load method to set up clean state
    ctrl.load_file("/fake/book.m4b", chapters, start_chapter_idx=0, start_time=0.0)
    return ctrl

# --- Seek & Cascading Tests ---

def test_seek_forward_signals_ui(controller):
    # Controller handles forward cascades by returning "NEXT_CHAPTER"
    controller.current_chapter_idx = 0
    controller.current_play_time = 50.0  
    
    # Seek +30s (past the 60s duration of chapter 0)
    result = controller.seek(30.0)
    
    assert result == "NEXT_CHAPTER"
    # State should remain untouched waiting for the UI to call next_chapter()
    assert controller.current_chapter_idx == 0
    assert controller.current_play_time == 50.0

def test_seek_backward_cascades_internally(controller):
    controller.current_chapter_idx = 1
    controller.chapter_duration = 120.0
    controller.current_play_time = 10.0  # 10s into Chapter 1
    
    # Seek -30s. Eats the 10s of Ch 1, leaving 20s deficit.
    # Falls back to Ch 0 (duration 60s). 60 - 20 = 40s.
    result = controller.seek(-30.0)
    
    assert result == "SUCCESS"
    assert controller.current_chapter_idx == 0
    assert controller.current_play_time == 40.0

def test_seek_backward_multi_chapter_cascade(controller):
    controller.current_chapter_idx = 2
    controller.chapter_duration = 20.0
    controller.current_play_time = 5.0 # 5s into Chapter 2
    
    # Seek -100s.
    # Eats 5s of Ch 2 (95s deficit).
    # Falls back to Ch 1 (duration 120s). 120 - 95 = 25s.
    result = controller.seek(-100.0)
    
    assert result == "SUCCESS"
    assert controller.current_chapter_idx == 1
    assert controller.current_play_time == 25.0

def test_seek_backward_past_start_clamps_to_zero(controller):
    controller.current_chapter_idx = 0
    controller.chapter_duration = 60.0
    controller.current_play_time = 10.0
    
    # Seek way past the beginning of the book
    result = controller.seek(-500.0)
    
    assert result == "SUCCESS"
    assert controller.current_chapter_idx == 0
    assert controller.current_play_time == 0.0

def test_seek_restarts_playback_if_playing(controller):
    controller.current_chapter_idx = 0
    controller.chapter_duration = 60.0
    controller.current_play_time = 10.0
    controller.is_playing = True  # Simulate active playback
    
    result = controller.seek(10.0)
    
    assert result == "RESTART_PLAYBACK"
    assert controller.current_play_time == 20.0
    assert controller.is_paused is False

# --- Boundary Conditions & Absolute Seek ---

def test_seek_to_absolute_standard(controller):
    # Absolute 70s should be 10s into Chapter 1
    success = controller.seek_to_absolute(70.0)
    
    assert success is True
    assert controller.current_chapter_idx == 1
    assert controller.current_play_time == 10.0
    assert controller.chapter_duration == 120.0

def test_seek_to_absolute_overshoot(controller):
    # Book is 200s total. Try seeking to 300s.
    success = controller.seek_to_absolute(300.0)
    
    assert success is True
    # Should clamp to the end of the final chapter
    assert controller.current_chapter_idx == 2
    assert controller.current_play_time == 20.0