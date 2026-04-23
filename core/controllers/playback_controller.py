import os
import time
import threading
from core.player import AudioPlayer

class PlaybackController:
    def __init__(self, logger, on_tick_cb, on_chapter_end_cb, on_error_cb):
        self.logger = logger
        
        # Callbacks to update the UI
        self.on_tick_cb = on_tick_cb
        self.on_chapter_end_cb = on_chapter_end_cb
        
        # Core Audio Player
        self.player = AudioPlayer(
            logger=self.logger,
            on_complete_cb=self._handle_player_complete,
            on_error_cb=on_error_cb
        )
        
        # Playback State
        self.file_path = None
        self.chapters = []
        self.current_chapter_idx = 0
        self.current_play_time = 0.0
        self.chapter_duration = 0.0
        self.playback_speed = 1.0
        self.volume = 100
        
        # Status Flags
        self.is_playing = False
        self.is_paused = False
        
        # Internal Threading
        self._last_tick_time = 0
        self._monitor_active = False

    def load_file(self, filepath, chapters, start_chapter_idx, start_time):
        """Loads a new file and sets the initial state without playing."""
        self.stop()
        self.file_path = filepath
        self.chapters = chapters
        self.current_chapter_idx = start_chapter_idx
        self.current_play_time = start_time
        
        if self.chapters and self.current_chapter_idx < len(self.chapters):
            ch = self.chapters[self.current_chapter_idx]
            self.chapter_duration = float(ch.get("end_time", 0)) - float(ch.get("start_time", 0))
        else:
            self.chapter_duration = 0.0

    def play(self, voice_boost, skip_silence, drm_flags=None):
        """Starts or resumes playback of the currently loaded file."""
        if not self.file_path or not self.chapters:
            return

        if self.current_chapter_idx >= len(self.chapters):
            return

        chapter = self.chapters[self.current_chapter_idx]
        base_start = float(chapter.get("start_time", 0))
        actual_start_time = base_start + self.current_play_time
        remaining_duration = self.chapter_duration - self.current_play_time
        
        self.player.play(
            filepath=self.file_path,
            start_time=actual_start_time,
            remaining_duration=remaining_duration,
            speed=self.playback_speed,
            volume=self.volume,
            voice_boost=voice_boost,
            skip_silence=skip_silence,
            drm_flags=drm_flags
        )
        
        self.is_playing = True
        self.is_paused = False
        self._last_tick_time = time.time()
        
        if not self._monitor_active:
            self._monitor_active = True
            threading.Thread(target=self._tick_loop, daemon=True).start()

    def pause(self):
        if self.is_playing:
            self.player.stop()
            self.is_playing = False
            self.is_paused = True
            self._monitor_active = False
            
            # Rewind slightly on pause to catch context on resume
            self.current_play_time = max(0.0, self.current_play_time - 1.5)

    def stop(self):
        self.is_playing = False
        self.is_paused = False
        self._monitor_active = False
        self.player.stop()

    def seek(self, offset_seconds):
        if not self.file_path or not self.chapters:
            return False # Cannot seek

        new_time = self.current_play_time + offset_seconds
        
        if new_time < 0:
            new_time = 0.0
        elif new_time >= self.chapter_duration:
            return "NEXT_CHAPTER" # Signal the UI to handle chapter transition
            
        self.current_play_time = new_time
        
        # If currently playing, we must restart the FFplay process at the new time
        was_playing = self.is_playing
        if was_playing:
            self.pause()
            self.is_paused = False
            return "RESTART_PLAYBACK" # Signal UI to call play() again with current flags
            
        return "SUCCESS"

    def set_speed(self, speed_float):
        self.playback_speed = speed_float

    def set_volume(self, volume_int):
        self.volume = volume_int
        if os.name == 'nt':
            self.player.set_volume(volume_int)

    def _tick_loop(self):
        """Background thread that tracks time and pings the UI."""
        while self._monitor_active and self.is_playing:
            now = time.time()
            delta = now - self._last_tick_time
            self._last_tick_time = now
            
            real_time_delta = delta * self.playback_speed
            self.current_play_time += real_time_delta
            
            if self.current_play_time > self.chapter_duration:
                self.current_play_time = self.chapter_duration
                
            # Fire the callback so the UI can update progress bars and DB
            if self.on_tick_cb:
                self.on_tick_cb(self.current_play_time, self.chapter_duration, real_time_delta)
                
            time.sleep(0.5)

    def _handle_player_complete(self):
        """Called internally when the FFplay process exits normally (chapter end)."""
        self.is_playing = False
        self._monitor_active = False
        if self.on_chapter_end_cb:
            self.on_chapter_end_cb()
    
    def next_chapter(self):
        """Advances state to the next chapter. Returns True if successful, False if at the end."""
        if not self.chapters or self.current_chapter_idx >= len(self.chapters) - 1:
            return False
            
        self.current_chapter_idx += 1
        self.current_play_time = 0.0
        
        ch = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(ch.get("end_time", 0)) - float(ch.get("start_time", 0))
        return True

    def prev_chapter(self):
        """Reverts to the previous chapter, or restarts the current one."""
        if not self.chapters: 
            return
            
        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            
        self.current_play_time = 0.0
        
        ch = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(ch.get("end_time", 0)) - float(ch.get("start_time", 0))