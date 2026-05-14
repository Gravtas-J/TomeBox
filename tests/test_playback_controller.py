import pytest
import os
import time
import threading
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

def test_setters(controller, monkeypatch):
    controller.set_audio_device("Headphones")
    assert controller.audio_device == "Headphones"

    controller.set_speed(1.25)
    assert controller.playback_speed == 1.25

    # Test volume targeting Windows behavior
    monkeypatch.setattr(os, "name", "nt")
    controller.set_volume(80)
    assert controller.volume == 80
    assert controller.player.call_log[-1] == ("set_volume", 80)

def test_load_file_edge_cases(controller):
    # Load out of bounds chapter
    controller.load_file("/fake.m4b", controller.chapters, 99, 0)
    assert controller.chapter_duration == 0.0

    # Load with completely empty chapters list
    controller.load_file("/fake.m4b", [], 0, 0)
    assert controller.chapter_duration == 0.0

def test_play_pause_stop(controller, monkeypatch):
    # Prevent the actual daemon thread from launching
    monkeypatch.setattr(threading.Thread, "start", MagicMock())

    # 1. PLAY
    controller.play(voice_boost=True, skip_silence=False, drm_flags=[])
    assert controller.is_playing is True
    assert controller.is_paused is False
    assert ("play", "/fake/book.m4b", 0.0) in controller.player.call_log

    # 2. PAUSE
    # (Advances time manually to test the 1.5s pause rewind feature)
    controller.current_play_time = 10.0
    controller.pause()
    assert controller.is_playing is False
    assert controller.is_paused is True
    assert controller.current_play_time == 8.5 
    assert ("stop",) in controller.player.call_log

    # 3. STOP
    controller.stop()
    assert controller.is_playing is False

def test_play_edge_cases(controller):
    # Attempt to play with no file loaded
    controller.file_path = None
    controller.play(False, False)
    assert controller.is_playing is False

    # Attempt to play while at an out-of-bounds index
    controller.file_path = "/fake.m4b"
    controller.current_chapter_idx = 99
    controller.play(False, False)
    assert controller.is_playing is False

# --- Edge Cases for Math & Navigation ---

def test_seek_edge_cases(controller):
    # Seek exact boundary hit (deficit drops to exactly 0)
    controller.current_chapter_idx = 1
    controller.chapter_duration = 120.0
    controller.current_play_time = 0.0
    
    assert controller.seek(-60.0) == "SUCCESS"
    assert controller.current_chapter_idx == 0
    assert controller.current_play_time == 0.0

    # Attempt to seek with no file/chapters
    controller.file_path = None
    assert controller.seek(10.0) is False

def test_seek_to_absolute_edge_cases(controller):
    # Host stability: Refuse absolute background seeking if actively playing
    controller.is_playing = True
    assert controller.seek_to_absolute(50.0) is False
    controller.is_playing = False

    # Attempt absolute seek with empty chapters
    controller.chapters = []
    assert controller.seek_to_absolute(50.0) is False

def test_tick_loop(controller, monkeypatch):
    """Tricks the time loop into running exactly one tick to verify speed math."""
    from unittest.mock import MagicMock
    import time
    import pytest
    
    controller.is_playing = True
    controller._monitor_active = True
    controller._last_tick_time = 1000.0
    controller.playback_speed = 2.0
    controller.current_play_time = 0.0
    controller.chapter_duration = 100.0
    
    # Spy on the event bus
    controller.event_bus = MagicMock()
    
    monkeypatch.setattr(time, "time", lambda: 1001.0)
    
    class BreakLoop(Exception): pass
    monkeypatch.setattr(time, "sleep", MagicMock(side_effect=BreakLoop))
    
    with pytest.raises(BreakLoop):
        controller._tick_loop(controller._tick_session)
        
    assert controller.current_play_time == 2.0
    
    # Assert the event bus broadcasted the tick
    controller.event_bus.publish.assert_called_once_with(
        "playback.tick", 
        current_time=2.0, 
        total_time=100.0, 
        duration=2.0
    )

def test_chapter_navigation_and_hooks(controller):
    # Next Chapter
    assert controller.next_chapter() is True
    assert controller.current_chapter_idx == 1
    assert controller.chapter_duration == 120.0

    # Hit the end of the book limit
    controller.current_chapter_idx = 2
    assert controller.next_chapter() is False

    # Prev Chapter
    controller.prev_chapter()
    assert controller.current_chapter_idx == 1

    # Hit the beginning of the book limit
    controller.current_chapter_idx = 0
    controller.prev_chapter()
    assert controller.current_chapter_idx == 0

    # Player Complete Hook (Fired by FFplay exit)
    from unittest.mock import MagicMock
    
    # Spy on the event bus
    controller.event_bus = MagicMock()
    controller.is_playing = True
    
    # Fire the completion hook
    controller._handle_player_complete()
    
    # Verify internal state changed
    assert controller.is_playing is False
    
    # Verify it broadcasted the completion to the rest of the app
    controller.event_bus.publish.assert_called_once_with("playback.chapter_end")

def test_get_current_state(controller):
    # Active file state
    controller.current_chapter_idx = 1
    controller.current_play_time = 30.0
    
    state = controller.get_current_state()
    assert state["chapter_idx"] == 1
    assert state["rel_time"] == 30.0
    # 60s (start of ch 1) + 30s relative
    assert state["abs_time"] == 90.0 

    # No file loaded
    controller.file_path = None
    assert controller.get_current_state() is None