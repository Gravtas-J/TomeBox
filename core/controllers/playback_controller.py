import os
import threading
import time

from core.events import default_bus
from core.player import AudioPlayer


class PlaybackController:
    def __init__(
        self,
        logger,
        on_tick_cb=None,
        on_chapter_end_cb=None,
        on_error_cb=None,
        player_factory=None,
        event_bus=None,
    ):
        self.logger = logger
        self.event_bus = event_bus or default_bus

        # Subscribe legacy UI callbacks
        if on_tick_cb:
            self.event_bus.subscribe(
                "playback.tick",
                lambda **kw: on_tick_cb(
                    kw.get("current_time"), kw.get("total_time"), kw.get("duration")
                ),
            )
        if on_chapter_end_cb:
            self.event_bus.subscribe(
                "playback.chapter_end", lambda **kw: on_chapter_end_cb()
            )
        if on_error_cb:
            self.event_bus.subscribe(
                "playback.error", lambda **kw: on_error_cb(kw.get("error_msg"))
            )

        # Core Audio Player injection
        player_cls = player_factory or AudioPlayer
        self.player = player_cls(
            logger=self.logger,
            on_complete_cb=self._handle_player_complete,
            on_error_cb=self._handle_error,
        )

        # Playback State
        self.file_path = None
        self.chapters = []
        self.current_chapter_idx = 0
        self.current_play_time = 0.0
        self.chapter_duration = 0.0
        self.playback_speed = 1.0
        self.volume = 100
        self.audio_device = "System Default"
        self.is_playlist = False

        # Status Flags
        self.is_playing = False
        self.is_paused = False

        # Internal Threading (Using Session IDs to prevent Zombie Threads)
        self._last_tick_time = 0
        self._tick_session = 0

    def set_audio_device(self, device_name):
        self.audio_device = device_name

    def load_file(self, filepath, chapters, start_chapter_idx, start_time):
        """Loads a new file and sets the initial state without playing."""
        self.stop()
        self.file_path = filepath
        self.chapters = chapters
        self.current_chapter_idx = start_chapter_idx
        self.current_play_time = start_time

        if self.chapters and self.current_chapter_idx < len(self.chapters):
            ch = self.chapters[self.current_chapter_idx]
            self.chapter_duration = float(ch.get("end_time", 0)) - float(
                ch.get("start_time", 0)
            )
        else:
            self.chapter_duration = 0.0

    def play(self, voice_boost, skip_silence, drm_flags=None):
        if not self.file_path or not self.chapters:
            return
        if self.current_chapter_idx >= len(self.chapters):
            return

        chapter = self.chapters[self.current_chapter_idx]
        base_start = float(chapter.get("start_time", 0))
        base_end = float(chapter.get("end_time", 0))
        self.chapter_duration = base_end - base_start
        if (
                self.current_chapter_idx >= len(self.chapters) - 1
                and self.current_play_time >= self.chapter_duration
        ):
            self.current_chapter_idx = 0
            self.current_play_time = 0.0
            chapter = self.chapters[0]
            base_start = float(chapter.get("start_time", 0))
            base_end = float(chapter.get("end_time", 0))
            self.chapter_duration = base_end - base_start
            self.logger.info("Restarting finished book from the beginning.")
            self.event_bus.publish("playback.book_replayed", file_path=self.file_path)
        if getattr(self, "is_playlist", False):
            actual_start_time = self.current_play_time
        else:
            actual_start_time = base_start + self.current_play_time

        remaining_duration = self.chapter_duration - self.current_play_time
        if remaining_duration <= 0:
            remaining_duration = 999999

        # --- THE SHIELD ---
        # Drop the flag BEFORE spawning the new process to protect against overlap
        if self.is_playing:
            self.is_playing = False

        success = self.player.play(
            filepath=self.file_path,
            start_time=actual_start_time,
            remaining_duration=remaining_duration,
            speed=self.playback_speed,
            volume=self.volume,
            voice_boost=voice_boost,
            skip_silence=skip_silence,
            drm_flags=drm_flags,
            audio_device=self.audio_device,
        )

        if not success:
            return

        self.is_playing = True
        self.is_paused = False
        self._last_tick_time = time.time()
        self._play_start_time = time.time()
        self._terminal_segment = self.current_chapter_idx >= len(self.chapters) - 1

        self._tick_session += 1
        threading.Thread(
            target=self._tick_loop, args=(self._tick_session,), daemon=True
        ).start()

    def pause(self):
        if self.is_playing:
            self.is_playing = False  # Shield Up
            self.player.stop()
            self.is_paused = True
            self._tick_session += 1
            self.current_play_time = max(0.0, self.current_play_time - 1.5)

    def stop(self):
        self.is_playing = False  # Shield Up
        self.is_paused = False
        self._tick_session += 1
        self.player.stop()

    def seek(self, offset_seconds):
        if not self.file_path or not self.chapters:
            return False

        new_time = self.current_play_time + offset_seconds

        if new_time < 0:
            deficit = abs(new_time)
            while deficit > 0 and self.current_chapter_idx > 0:
                self.current_chapter_idx -= 1
                ch = self.chapters[self.current_chapter_idx]
                self.chapter_duration = float(ch.get("end_time", 0)) - float(
                    ch.get("start_time", 0)
                )

                if deficit >= self.chapter_duration:
                    deficit -= self.chapter_duration
                    if deficit == 0:
                        self.current_play_time = 0.0
                else:
                    self.current_play_time = self.chapter_duration - deficit
                    deficit = 0
            if deficit > 0:
                self.current_play_time = 0.0

        elif new_time >= self.chapter_duration:
            return "NEXT_CHAPTER"
        else:
            self.current_play_time = new_time

        was_playing = self.is_playing
        if was_playing:
            self.is_playing = False  # Shield Up
            self.player.stop()
            self.is_paused = False
            self._tick_session += 1
            return "RESTART_PLAYBACK"

        return "SUCCESS"

    def set_speed(self, speed_float):
        self.playback_speed = speed_float

    def set_volume(self, volume_int):
        self.volume = volume_int
        if os.name == "nt":
            self.player.set_volume(volume_int)

    def _tick_loop(self, session_id):
        """Background thread updating the time."""
        # The thread will auto-terminate if is_playing is False, OR if the session ID is no longer the active one
        while self.is_playing and getattr(self, "_tick_session", None) == session_id:
            now = time.time()
            dt = (now - self._last_tick_time) * self.playback_speed
            self._last_tick_time = now
            self.current_play_time += dt

            # Fire event with the CORRECT payloads mapped to the ActionRouter UI receiver
            self.event_bus.publish(
                "playback.tick",
                current_time=self.current_play_time,
                total_time=self.chapter_duration,
                duration=dt,
            )
            time.sleep(0.1)

    def _handle_player_complete(self):
        """Called automatically when FFplay finishes the current chunk."""
        if not self.is_playing:
            return
        if time.time() - getattr(self, "_play_start_time", 0) < 1.5:
            if getattr(self, "_terminal_segment", False) and self.chapters:
                self.is_playing = False
                self._tick_session += 1
                self.event_bus.publish("playback.chapter_end")
                return
            self.is_playing = False
            self.logger.error(
                "Playback engine crashed instantly. Halting to prevent infinite loop."
            )
            self.event_bus.publish(
                "playback.error",
                error_msg="Audio engine failed to start. Check your audio device settings or file integrity.",
            )
            return

        self.is_playing = False
        self._tick_session += 1
        self.event_bus.publish("playback.chapter_end")

    def _handle_error(self, msg):
        self.is_playing = False
        self._tick_session += 1
        self.event_bus.publish("playback.error", error_msg=msg)

    def next_chapter(self):
        """Advances state to the next chapter. Returns True if successful, False if at the end."""
        if not self.chapters or self.current_chapter_idx >= len(self.chapters) - 1:
            return False

        self.current_chapter_idx += 1
        self.current_play_time = 0.0

        ch = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(ch.get("end_time", 0)) - float(
            ch.get("start_time", 0)
        )
        return True

    def prev_chapter(self):
        """Reverts to the previous chapter, or restarts the current one."""
        if not self.chapters:
            return

        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1

        self.current_play_time = 0.0

        ch = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(ch.get("end_time", 0)) - float(
            ch.get("start_time", 0)
        )

    def get_current_state(self):
        """Returns the calculated playback state for saving."""
        if not self.file_path:
            return None

        abs_time = self.current_play_time
        if self.chapters and self.current_chapter_idx < len(self.chapters):
            abs_time = (
                float(self.chapters[self.current_chapter_idx].get("start_time", 0))
                + self.current_play_time
            )

        return {
            "file_path": self.file_path,
            "chapter_idx": self.current_chapter_idx,
            "rel_time": self.current_play_time,
            "abs_time": abs_time,
        }

    def seek_to_absolute(self, abs_position):
        """Calculates and sets chapter index and relative time from an absolute position."""
        if self.is_playing:
            return False  # Don't interrupt active playback on the host machine

        if not self.chapters:
            return False

        for idx, ch in enumerate(self.chapters):
            start = float(ch.get("start_time", 0))
            end = float(ch.get("end_time", 0))

            if start <= abs_position < end:
                self.current_chapter_idx = idx
                self.current_play_time = abs_position - start
                self.chapter_duration = end - start
                return True

        # Catch-all if position somehow overshoots the last chapter
        if abs_position > float(self.chapters[-1].get("end_time", 0)):
            self.current_chapter_idx = len(self.chapters) - 1
            start = float(self.chapters[-1].get("start_time", 0))
            end = float(self.chapters[-1].get("end_time", 0))
            self.current_play_time = end - start
            self.chapter_duration = end - start
            return True

        return False
