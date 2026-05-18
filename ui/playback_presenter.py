import os
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from core.utils.process_runner import ProcessRunner

class PlaybackPresenter:
    def __init__(self, app):
        self.app = app
        self.view = None
    def set_view(self, view):
        self.view = view
    # --- Callbacks ---
    def on_playback_error(self, error_code):
        def update():
            self.stop_audio()
            if error_code == "NO_AUDIO":
                messagebox.showerror(
                    "Playback Failed", 
                    "No audio stream found in this title.\n\nThe file may be corrupted, or the DRM decryption failed during download. Try deleting and re-downloading the file."
                )
            else:
                messagebox.showerror("Playback Error", f"An unexpected playback error occurred.\nError Code: {error_code}")
        self.app.root.after(0, update)

    def on_playback_tick(self, current_time, total_time, real_time_delta):
        def update_ui():
            percent = (current_time / total_time) * 100 if total_time > 0 else 0
            self.app.ui_state.playback_progress.set(percent)
            
            curr_str = self.format_time(current_time)
            dur_str = self.format_time(total_time)
            self.view.time_label.config(text=f"{curr_str} / {dur_str}")

            self.app.session_listen_buffer += real_time_delta
            if self.app.session_listen_buffer >= 60.0:
                self.app.stats_manager.add_stat("seconds_listened", self.app.session_listen_buffer)
                self.app.session_listen_buffer = 0.0

            now = time.time()
            if now - getattr(self.app, '_last_disk_save_time', 0.0) > 10:
                self.save_playback_state()
                self.app._last_disk_save_time = now
                
        self.app.root.after(0, update_ui)

    # --- State & Loading ---
    def save_playback_state(self):
        state = self.app.playback.get_current_state()
        if state:
            state["file_path"] = self.app.file_path
            self.app.library_manager.save_playback_state(state, self.app.active_profile)

    def sync_playhead_from_remote(self, abs_position):
        try:
            # --- 1. ANTI-FREEZE DEADZONE ---
            # Calculate where the desktop playhead currently is in absolute time
            current_abs_time = self.app.playback.current_play_time
            if self.app.playback.chapters:
                start_offset = float(self.app.playback.chapters[self.app.playback.current_chapter_idx].get("start_time", 0))
                current_abs_time += start_offset
            
            # If the incoming sync is within 5 seconds of our current position, ignore it!
            # This stops the app from constantly restarting FFplay when the web UI polls.
            if abs(current_abs_time - abs_position) < 5.0:
                return

            # --- 2. THE SEEK & MATH FIX ---
            if self.app.playback.seek_to_absolute(abs_position):
                # Update the UI based on CHAPTER progress, not whole-book progress
                if hasattr(self.app, 'progress_bar') and self.app.playback.chapters:
                    found_chap = self.app.playback.current_chapter_idx
                    chapter = self.app.playback.chapters[found_chap]
                    
                    self.app.playback.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
                    
                    self.update_info()
                    curr_str = self.format_time(self.app.playback.current_play_time)
                    dur_str = self.format_time(self.app.playback.chapter_duration)
                    self.view.time_label.config(text=f"{curr_str} / {dur_str}")
                    
                    percent = (self.app.playback.current_play_time / self.app.playback.chapter_duration) * 100 if self.app.playback.chapter_duration > 0 else 0
                    self.app.ui_state.playback_progress.set(percent)
                    
        except Exception as e:
            self.app.logger.error(f"Failed to sync remote playhead: {e}")

    def cue_last_played(self):
        last_path = self.app.settings.get(f"last_played_{self.app.active_profile}")
        if last_path and last_path in self.app.library_manager.local_library and os.path.exists(last_path):
            self.load_specific_file(last_path)

    def load_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b")])
        if filepath:
            self.load_specific_file(filepath)

    def load_specific_file(self, filepath):
        self.app.file_path = filepath
        is_encrypted = filepath.endswith(".aax") or filepath.endswith(".aaxc")
        
        self.app.playback.chapters = []
        self.app.playback.current_chapter_idx = 0
        self.app.playback.current_play_time = 0.0
        self.app.playback.chapter_duration = 0.0
        self.update_info() 
        
        self.app.ui_state.dl_status.set("Analyzing...")
        self.app.root.update()
        
        local_data = self.app.library_manager.local_library.get(filepath, {})
        
        if hasattr(self.view, 'player_cover_lbl'):
            asin = local_data.get("asin")
            cover_path = None
            if asin:
                cp = os.path.join(self.app.covers_dir, f"{asin}.jpg")
                if os.path.exists(cp): cover_path = cp
                    
            if cover_path:
                try:
                    from PIL import Image, ImageTk
                    thumb = Image.open(cover_path)
                    thumb.thumbnail((45, 45), Image.Resampling.LANCZOS)
                    thumb_photo = ImageTk.PhotoImage(thumb)
                    self.view.player_cover_lbl.config(image=thumb_photo, width=45, height=45)
                    self.view.player_cover_lbl.image = thumb_photo 
                except Exception:
                    self.view.player_cover_lbl.config(image="", width=0, height=0)
            else:
                self.view.player_cover_lbl.config(image="", width=0, height=0)
            if hasattr(self.view, 'btn_compact'):
                self.view.btn_compact.config(state=tk.NORMAL)
                
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.app.file_path)
            if not success:
                self.app.ui_state.dl_status.set("Verification Failed")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.app.file_path = ""
                return

        cached_chapters = local_data.get("chapters")
        
        if cached_chapters:
            self.app.playback.chapters = cached_chapters
        else:
            self.app.ui_state.dl_status.set(f"Extracting chapters: {os.path.basename(self.app.file_path)}")
            self.app.root.update()
            
            self.app.playback.chapters = self.extract_chapters(self.app.file_path)
            
            local_data["chapters"] = self.app.playback.chapters
            self.app.library_manager.local_library[filepath] = local_data
            self.app.library_manager.db.save_local_db(self.app.library_manager.local_library)

        self.app.ui_state.dl_status.set(f"Ready: {os.path.basename(self.app.file_path)}")
        
        if not self.app.playback.chapters:
            self.app.logger.info("No chapters found in file. Generating dummy master chapter.")
            duration_sec = local_data.get("duration_min", 0) * 60
            if duration_sec <= 0:
                try: duration_sec = self.app.converter.get_duration(self.app.file_path)
                except Exception: duration_sec = 86400 
            self.app.playback.chapters = [{"id": 0, "start_time": "0.000000", "end_time": str(duration_sec), "tags": {"title": "Full Audiobook"}}]
            
        abs_pos = None
        if "progress" in local_data and self.app.active_profile in local_data["progress"]:
            abs_pos = local_data["progress"][self.app.active_profile]
        elif "last_position" in local_data:
            abs_pos = local_data["last_position"]
            
        if abs_pos is not None:
            found_chap = 0
            for i, chap in enumerate(self.app.playback.chapters):
                start = float(chap.get("start_time", 0))
                end = float(chap.get("end_time", 0))
                if start <= abs_pos < end:
                    found_chap = i
                    break
                if i == len(self.app.playback.chapters) - 1 and abs_pos >= end:
                    found_chap = i
            self.app.playback.current_chapter_idx = found_chap
            self.app.playback.current_play_time = max(0.0, abs_pos - float(self.app.playback.chapters[found_chap].get("start_time", 0)))
        else:
            self.app.playback.current_chapter_idx = local_data.get("last_chapter", 0)
            self.app.playback.current_play_time = local_data.get("last_time", 0.0)
        
        if self.app.playback.current_chapter_idx >= len(self.app.playback.chapters):
            self.app.playback.current_chapter_idx = 0
            self.app.playback.current_play_time = 0.0
            
        self.update_info()
        chapter = self.app.playback.chapters[self.app.playback.current_chapter_idx]
        self.app.playback.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        
        curr_str = self.format_time(self.app.playback.current_play_time)
        dur_str = self.format_time(self.app.playback.chapter_duration)
        self.view.time_label.config(text=f"{curr_str} / {dur_str}")
        percent = (self.app.playback.current_play_time / self.app.playback.chapter_duration) * 100 if self.app.playback.chapter_duration > 0 else 0
        self.app.ui_state.playback_progress.set(percent)

        self.app.metadata_manager.fetch_display_metadata(filepath)
        self.app.bookmarks_presenter.refresh_bookmarks_ui()

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        local_data = self.app.library_manager.local_library.get(filepath, {})
        auth_bytes = self.app.ui_state.auth_bytes.get().strip()
        
        drm_flags = self.app.api_client.get_drm_flags(
            filepath=filepath, local_data=local_data, active_profile=self.app.active_profile, 
            auth_bytes=auth_bytes, data_dir=self.app.db.data_dir, logger=self.app.logger
        )
        cmd.extend(drm_flags)
        cmd.extend(["-i", filepath, "-t", "0.1", "-f", "null", "-"])
        
        try:
            result = ProcessRunner.run_blocking(cmd)
            if result.returncode != 0: return False, result.stderr if result.stderr else "FFmpeg rejected the file."
            return True, ""
        except FileNotFoundError: return False, "FFmpeg is missing!"
        except Exception as e: return False, str(e)

    def extract_chapters(self, filepath):
        metadata = self.app.converter.get_metadata_and_chapters(filepath)
        return metadata.get("chapters", [])

    # --- Core Actions ---
    def master_play(self, event=None):
        if self.app.current_view_mode == "list":
            selected = self.app.library_tree.focus()
            if not selected:
                if self.app.file_path: self.play_chapter()
                else: messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self.app.library_tree.item(selected)
        else:
            if not getattr(self.app, '_selected_grid_item', None):
                if self.app.file_path: self.play_chapter()
                else: messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self.app._selected_grid_item

        title = item['values'][0]
        status = item['values'][6]  

        if "Downloaded" not in status:
            messagebox.showinfo("Cloud Only", "This title has not been downloaded yet.")
            return

        is_playlist = False
        local_path = None
        for path, data in self.app.library_manager.local_library.items():
            if data.get("title") == title:
                local_path = path
                is_playlist = data.get("is_playlist", False)
                break

        if not local_path or (not is_playlist and not os.path.exists(local_path)):
            messagebox.showerror("File Error", "The audio file could not be found on your disk.")
            return

        if self.app.file_path == local_path:
            self.play_chapter()
            return

        self.stop_audio()
        self.app.metadata_manager.fetch_display_metadata(local_path)
        self.app.handle_action_on_selected("play")

    def play_chapter(self):
        if not self.app.file_path or not self.app.playback.chapters: return
        
        chapter = self.app.playback.chapters[self.app.playback.current_chapter_idx]
        self.app.playback.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        self.update_info()
        
        self.app.playback.is_paused = False
        self.resume_playback()

    def resume_playback(self):
        local_data = self.app.library_manager.local_library.get(self.app.file_path, {})
        is_playlist = local_data.get("is_playlist", False)
        
        if is_playlist and self.app.playback.chapters:
            chapter = self.app.playback.chapters[self.app.playback.current_chapter_idx]
            # Safely fall back to alternative keys if the dictionary structure varies
            target_path = chapter.get("file_path") or chapter.get("path") or self.app.file_path
            self.app.playback.file_path = target_path
            self.app.playback.is_playlist = True
        else:
            self.app.playback.file_path = self.app.file_path
            self.app.playback.is_playlist = False

        # --- INFINITE LOOP GUARD ---
        # If the path is a folder or missing entirely, STOP immediately instead of looping
        if not self.app.playback.file_path or not os.path.isfile(self.app.playback.file_path):
            self.app.logger.error(f"Playback Failed: Target is not a valid file -> {self.app.playback.file_path}")
            self.stop_audio()
            return

        drm_flags = self.app.api_client.get_drm_flags(self.app.file_path, local_data, self.app.active_profile, self.app.ui_state.auth_bytes.get().strip(), self.app.db.data_dir) if self.app.file_path.endswith((".aax", ".aaxc")) else None
        
        self.app.playback.set_speed(float(self.app.ui_state.playback_speed.get().replace("x", "")))
        self.app.playback.set_volume(int(self.app.ui_state.volume.get()))
        
        self.app.playback.play(
            voice_boost=self.app.ui_state.voice_boost.get(),
            skip_silence=self.app.ui_state.skip_silence.get(),
            drm_flags=drm_flags
        )

    def pause_audio(self):
        if self.app.playback.is_playing:
            self.app.playback.pause()
            self.app.playback.is_playing = False
            self.app.playback.is_paused = True
            
            curr_str = self.format_time(self.app.playback.current_play_time)
            dur_str = self.format_time(self.app.playback.chapter_duration)
            self.view.time_label.config(text=f"{curr_str} / {dur_str}")
            self.save_playback_state()

    def stop_audio(self):
        self.app.playback.stop()
        self.app.playback.is_playing = False
        self.app.playback.is_paused = False
        self.save_playback_state()

    def seek_audio(self, offset):
        result = self.app.playback.seek(offset)
        
        if result == "NEXT_CHAPTER":
            self.next_chapter()
            return
        
        self.update_info() 
        
        if result == "RESTART_PLAYBACK":
            self.resume_playback()
            
        if self.app.playback.is_paused:
            curr_str = self.format_time(self.app.playback.current_play_time)
            dur_str = self.format_time(self.app.playback.chapter_duration)
            self.view.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.app.playback.current_play_time / self.app.playback.chapter_duration) * 100 if self.app.playback.chapter_duration > 0 else 0
            self.app.ui_state.playback_progress.set(percent)

    def on_progress_click(self, event):
        if not hasattr(self.view, 'progress_bar') or self.app.playback.chapter_duration <= 0: 
            return
        
        # Calculate true X relative to the widget's absolute screen position
        click_x = event.x_root - self.view.progress_bar.winfo_rootx()
        bar_width = self.view.progress_bar.winfo_width()
        
        if bar_width > 0:
            percent = click_x / bar_width
            target_time = self.app.playback.chapter_duration * percent
            offset = target_time - self.app.playback.current_play_time
            self.seek_audio(offset)

    def on_speed_change(self, event=None):
        speed_val = float(self.app.ui_state.playback_speed.get().replace("x", ""))
        self.app.playback.set_speed(speed_val)
        if self.app.playback.is_playing:
            self.pause_audio()
            self.app.playback.is_paused = False
            self.resume_playback()

    def on_volume_change(self, event=None):
        self.app.playback.set_volume(int(self.app.ui_state.volume.get()))
        if os.name != 'nt' and self.app.playback.is_playing:
            self.pause_audio()
            self.app.playback.is_paused = False
            self.resume_playback()

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def next_chapter(self):
        self.save_playback_state()
        self.stop_audio()
        
        if self.app.playback.next_chapter():
            if self.app.sleep_mode == "chapters":
                self.app.sleep_chapters_remaining -= 1
                if self.app.sleep_chapters_remaining <= 0:
                    self.app.sleep_mode = None
                    self.view.timer_btn.config(text="Sleep: Off")
                    self.app.logger.info("Sleep timer (chapters) finished. Pausing playback.")
                    
                    self.app.playback.is_paused = True
                    self.update_info()
                    curr_str = self.format_time(self.app.playback.current_play_time)
                    dur_str = self.format_time(self.app.playback.chapter_duration)
                    self.view.time_label.config(text=f"{curr_str} / {dur_str}")
                    self.app.ui_state.playback_progress.set(0)
                    return
                else:
                    self.view.timer_btn.config(text=f"Sleep: {self.app.sleep_chapters_remaining} ch")

            self.app.playback.is_paused = False
            self.update_info()
            self.app.root.after(200, self.resume_playback)
        else:
            self.app.stats_manager.add_stat("books_finished", 1)
            self.view.info_label.config(text="Finished Book")

    def prev_chapter(self):
        self.save_playback_state()
        self.stop_audio() 
        
        self.app.playback.prev_chapter()
        self.app.playback.is_paused = False
        self.update_info()
        self.resume_playback()

    def update_info(self):
        if self.app.playback.chapters:
            title = self.app.playback.chapters[self.app.playback.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.app.playback.current_chapter_idx + 1}")
            self.view.info_label.config(text=f"Playing:\n{title}")

    # --- Sleep Timer ---
    def set_sleep_timer(self, mode, value=0):
        if self.app._sleep_timer_id is not None:
            self.app.root.after_cancel(self.app._sleep_timer_id)
        if hasattr(self.app, 'sleep_menu_popup') and self.app.sleep_menu_popup.winfo_exists():
            self.app.sleep_menu_popup.destroy()

        try: val = int(value)
        except ValueError: return

        if mode == "off" or val <= 0:
            self.app.sleep_mode = None
            self.view.timer_btn.config(text="Sleep: Off")
            return
            
        self.app.sleep_mode = mode
        if mode == "time":
            self.app.sleep_timer_seconds = val * 60
            self.view.timer_btn.config(text=f"Sleep: {self.format_time(self.app.sleep_timer_seconds)}")
            self.sleep_timer_tick()
        elif mode == "chapters":
            self.app.sleep_chapters_remaining = val
            text = "End of Chapter" if val == 1 else f"Sleep: {val} ch"
            self.view.timer_btn.config(text=text)

    def sleep_timer_tick(self):
        if self.app.sleep_mode != "time": return
        if self.app.sleep_timer_seconds <= 0:
            self.app.sleep_mode = None
            self.view.timer_btn.config(text="Sleep: Off")
            if self.app.playback.is_playing:
                self.app.logger.info("Sleep timer finished. Pausing.")
                self.pause_audio()
            return
            
        self.app.sleep_timer_seconds -= 1
        self.view.timer_btn.config(text=f"Sleep: {self.format_time(self.app.sleep_timer_seconds)}")
        self.app._sleep_timer_id = self.app.root.after(1000, self.sleep_timer_tick)

    def on_sleep_timer_set(self, event=None):
        val = self.app.sleep_time_var.get()
        if self.app._sleep_timer_id is not None:
            self.app.root.after_cancel(self.app._sleep_timer_id)
            
        if val == "Off":
            self.app.sleep_timer_active = False
            self.app.ui_state.timer_countdown.set("")
            return
            
        mins = int(val.replace("m", ""))
        self.app.sleep_timer_seconds = mins * 60
        self.app.sleep_timer_active = True
        self.app.ui_state.timer_countdown.set(self.format_time(self.app.sleep_timer_seconds))
        self.sleep_timer_tick()