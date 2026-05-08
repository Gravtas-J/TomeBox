from core.utils.logger import setup_logger
from core.utils.thread_pool import AppThreadPool
from core.utils.process_runner import ProcessRunner
import subprocess
import json
import threading
import os
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import traceback
import requests
import io
from PIL import Image, ImageTk
import csv
import httpx
import time
try:
    import audible
    from tkinterdnd2 import DND_FILES, TkinterDnD
    from wakepy import keep
except ImportError:
    messagebox.showerror("Missing Dependency", "Please run: pip install audible requests pillow tkinterdnd2 wakepy")
    exit()
import pystray
from pystray import MenuItem as item
import sys
import socket
from api.audible_client import AudibleClient

from ui.components.dialogs import open_auth_window, open_chapter_window, open_sleep_menu, open_achievements_window, show_achievement_toast, open_pairing_window, open_error_log_window
from ui.components.theme import apply_theme
from ui.components.menu_bar import setup_menu_bar
from ui.components.player_bar import setup_player_bar
from ui.components.library_view import setup_library_view
from ui.components.sidebar import setup_sidebar

from core.utils.paths import get_resource_path, parse_dnd_paths

from core.utils.paths import get_resource_path

from core.database import DatabaseManager
from core.converter import AudioConverter
from core.player import AudioPlayer
from core.exporter import LibraryExporter
from core.controllers.library_manager import LibraryManager
from core.controllers.playback_controller import PlaybackController
from core.controllers.download_manager import DownloadManager
from core.controllers.metadata_manager import MetadataManager
from core.controllers.conversion_manager import ConversionManager
from core.controllers.system_manager import SystemManager
from core.controllers.stats_manager import StatsManager

mac_paths = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin"
os.environ["PATH"] = f"{os.environ.get('PATH', '')}{os.pathsep}{mac_paths}"
bundled_bin_dir = get_resource_path("bin")
# 2. Inject bundled PyInstaller binaries and restore +x permissions
if hasattr(sys, '_MEIPASS'):
    bin_path = os.path.join(sys._MEIPASS, 'bin')
    os.environ["PATH"] = f"{sys._MEIPASS}{os.pathsep}{bin_path}{os.pathsep}{os.environ.get('PATH', '')}"
    
    try:
        for binary in ['ffmpeg', 'ffplay']:
            b_path = os.path.join(bin_path, binary)
            if os.path.exists(b_path): 
                os.chmod(b_path, 0o755)
    except Exception:
        pass

if os.path.exists(bundled_bin_dir):
    os.environ["PATH"] = f"{bundled_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
class AAXManagerApp:
    def __init__(self, root, base_dir):
        self.root = root
        self.root.title("TomeBox")
        self.root.geometry("1550x850")
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.on_file_drop)
        self.base_dir = base_dir  
        self.current_sort_col = "Title"  
        self.current_sort_descending = False

        # 1. Initialize Database Manager FIRST
        self.db = DatabaseManager(self.base_dir)
        self.api_client = AudibleClient()
        self.library_manager = LibraryManager(self.db, self.api_client, self.base_dir)

        self.library_manager.on_queue_empty_cb = self._on_import_queue_empty

        # 2. Setup Assets (Icons are in the ui folder)
        icon_ico = get_resource_path("ui", "tomebox.ico")
        icon_png = get_resource_path("ui", "tomebox.png")


        
        
        def apply_taskbar_icon():
            try:
                # Force the OS to acknowledge the window exists first
                self.root.update_idletasks() 
                
                # Now apply the icon
                self.root.iconbitmap(icon_ico)
            except Exception as e:
                print(f"Icon error: {e}")
        
        if os.path.exists(icon_png):
            try:
                icon_img = tk.PhotoImage(file=icon_png)
                self.root.iconphoto(True, icon_img)
            except Exception: pass

        # 3. Load Settings and Global Paths
        self.settings = self.db.load_settings()
        self.logger = setup_logger(self.base_dir, debug_mode=self.settings.get("debug_mode", False))
        self.logger.info("=== TomeBox Application Started ===")
        self.covers_dir = os.path.join(self.base_dir, "covers")
        os.makedirs(self.covers_dir, exist_ok=True)
        self.root.after(200, apply_taskbar_icon)
        # 4. Apply Profile Variables
        self.active_profile = self.settings.get("active_profile", "Main")
        self.minimize_to_tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        
        # Use the DB manager to get paths instead of calculating them here
        self.auth_save_path = self.db.get_auth_path(self.active_profile)
        self.cloud_cache_path = self.db.get_cloud_cache_path(self.active_profile)
        self.converter = AudioConverter(self.write_log)
        self.thread_pool = AppThreadPool(logger=self.logger)

        self.stats_manager = StatsManager(
            self.db, 
            callbacks={"on_achievement": lambda title, desc: self.root.after(0, lambda: show_achievement_toast(self, title, desc))}
        )
        self.download_manager = DownloadManager(
            api_client=self.api_client,
            logger=self.logger,
            library_manager=self.library_manager,
            thread_pool=self.thread_pool,  
            callbacks={
                "on_status": self._on_dl_status,
                "on_progress": self._on_dl_progress,
                "on_complete": self._on_dl_complete,
                "on_batch_finish": self._on_dl_batch_finish
            }
        )
        self.metadata_manager = MetadataManager(
            api_client=self.api_client,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            callbacks={
                "on_search_complete": self._on_scrape_search_results,
                "on_apply_complete": self._on_scrape_apply_complete,
                "on_display_ready": self._on_display_metadata_ready,
                "on_error": self._on_scrape_error
            }
        )
        self.conversion_manager = ConversionManager(
            converter=self.converter,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            thread_pool=self.thread_pool,
            get_drm_flags_cb=lambda path: self.api_client.get_drm_flags(
                path, self.library_manager.local_library.get(path, {}), self.active_profile, self.auth_bytes.get().strip(), self.db.data_dir, self.logger
            ),
            callbacks={
                "on_status": lambda msg: self.root.after(0, self.update_global_status, msg),
                "on_progress": lambda pct: self.root.after(0, self.update_global_progress, pct),
                "on_complete": lambda msg: self.root.after(0, lambda: messagebox.showinfo("Conversion Success", msg)),
                "on_error": self._on_task_error, 
                "on_refresh_required": lambda: self.root.after(0, self.refresh_library_ui)
            }
        )
        self.playback = PlaybackController(
            logger=self.logger,
            on_tick_cb=self._on_playback_tick,
            on_chapter_end_cb=lambda: self.root.after(0, self.next_chapter),
            on_error_cb=self._on_playback_error
        )
        self.system_manager = SystemManager(logger=self.logger)
        self.system_manager.enforce_single_instance(on_wake_callback=lambda: self.root.after(0, self.bring_to_front))

        self.file_path = ""
        self.auth_bytes = tk.StringVar(value="")
        self.locale = tk.StringVar(value="us")
        self.chapters = []
        self.current_chapter_idx = 0
        self.player_process = None

        self.debug_mode = tk.BooleanVar(value=False)
        self.dl_progress_var = tk.DoubleVar()
        self.dl_status_var = tk.StringVar(value="Idle")

        self.root.after(100, self.check_dependencies)
        
        try:
            icon_path = get_resource_path("ui", "tomebox.ico")
            if os.path.exists(icon_path):
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, icon_img) # "True" applies it to all future dialog windows too
        except Exception as e:
            self.logger.warning(f"Could not load app icon: {e}")
        self.build_context_menu()
        # UI & View State
        self.current_view_mode = "list"
        self._selected_grid_item = None
        self._last_selected_card_frame = None
        self._current_filtered_data = []
        self._last_canvas_width = 0
        self._resize_timer = None
        self.cover_cache = {}
        
        # Window / Dialog State
        self.chapter_win = None
        self.sleep_menu_popup = None
        self.current_cover_photo = None
        
        # File & Playback State
        self.file_path = ""
        self.chapters = []
        self.current_chapter_idx = 0
        self.current_play_time = 0.0
        self.chapter_duration = 0.0
        self.is_playing = False
        self.is_paused = False
        
        # Sleep Timer State
        self.sleep_mode = None
        self.sleep_timer_seconds = 0
        self.sleep_chapters_remaining = 0
        self._sleep_timer_id = None
        self.sleep_timer_active = False
        
        # Directory Paths
        self.default_download_dir = self.settings.get("download_dir", self.base_dir)
        
        # Background Workers
        self._last_disk_save_time = 0.0
        
        # Timers & UI Flags
        self.tray_icon = None
        self._sleep_timer_id = None
        self._resize_timer = None
        self.browser_login_btn = None

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.handle_window_close)
        self.setup_tray_icon()
        self.root.after(500, self.auto_load_auth)
        self.root.after(900000, self.run_background_sync)
        dl_dir = self.settings.get("download_folder") or self.settings.get("download_dir")
        lib_paths = list(self.library_manager.local_library.keys())
        
        threading.Thread(
            target=self.system_manager.cleanup_orphaned_files, 
            args=(dl_dir, lib_paths),
            daemon=True
        ).start()
        threading.Thread(
            target=self.library_manager.monitor_local_files, 
            args=(self.logger, lambda: self.root.after(0, self.refresh_library_ui)), 
            daemon=True
        ).start()

        if "stats" not in self.settings:
            self.settings["stats"] = {
                "seconds_listened": 0, 
                "books_finished": 0, 
                "books_downloaded": 0, 
                "unlocked_achievements": []
            }
        
        self.session_listen_buffer = 0.0
        
        self.achievements = {
            "first_dl": {"title": "System Integration Complete", "desc": "Download your first audiobook.", "type": "books_downloaded", "threshold": 1},
            "hoarder_1": {"title": "Spatial Expansion", "desc": "Download 10 audiobooks.", "type": "books_downloaded", "threshold": 10},
            "first_finish": {"title": "Core Consumed", "desc": "Finish an audiobook.", "type": "books_finished", "threshold": 1},
            "finish_5": {"title": "Path Advancement", "desc": "Finish 5 audiobooks.", "type": "books_finished", "threshold": 5},
            "listen_10h": {"title": "Mana Cultivator", "desc": "Listen for 10 total hours.", "type": "seconds_listened", "threshold": 36000},
            "listen_50h": {"title": "Dao of the Tome", "desc": "Listen for 50 total hours.", "type": "seconds_listened", "threshold": 180000}
        }
        self.root.after(1500, self._prompt_resume_imports)

    def _on_playback_error(self, error_code):
        """Catches player thread crashes and pushes a visible alert to the user."""
        def update():
            self.stop_audio()
            
            if error_code == "NO_AUDIO":
                messagebox.showerror(
                    "Playback Failed", 
                    "No audio stream found in this title.\n\nThe file may be corrupted, or the DRM decryption failed during download. Try deleting and re-downloading the file."
                )
            else:
                messagebox.showerror(
                    "Playback Error", 
                    f"An unexpected playback error occurred.\nError Code: {error_code}"
                )
        self.root.after(0, update)

    def _on_import_status(self, task_id, msg):
        self.root.after(0, lambda: self._on_dl_status(task_id, msg, is_global=False))
        self.root.after(0, lambda: self.update_global_status(msg))

    def _on_import_progress(self, task_id, pct):
        self.root.after(0, lambda: self._on_dl_progress(task_id, pct, is_global=False))
        self.root.after(0, lambda: self.update_global_progress(pct))

    def remove_queue_ui_row(self, task_id):
        if task_id in self.queue_ui_elements:
            self.queue_ui_elements[task_id]["frame"].destroy()
            del self.queue_ui_elements[task_id]
        
        # If the queue is entirely empty, naturally close the drawer
        if not self.queue_ui_elements:
            self.toggle_queue_drawer(False)

    def _schedule_row_removal(self, task_id):
        self.root.after(3000, lambda: self.remove_queue_ui_row(task_id))

    def cancel_task(self, task_id):
        """Unified method to cancel either an active import OR an active download from the queue drawer."""
        if str(task_id).startswith("import_"):
            self.library_manager.cancel_import(task_id)
            
            # Only terminate the FFmpeg process if this specific task is the one currently running
            if getattr(self.library_manager, 'active_task_id', None) == task_id:
                self.converter.cancel()
                
            self._on_dl_status(task_id, "Canceling...", is_global=False)
        else:
            self.download_manager.cancel_download(task_id)

    def _prompt_resume_imports(self):
        pending = self.system_manager.load_pending_imports(self.db.data_dir)
        import os
        valid_pending = [p for p in pending if os.path.exists(p["path"])]
        
        if not valid_pending:
            if pending: 
                self.system_manager.clear_all_pending_imports(self.db.data_dir)
            return

        if messagebox.askyesno("Interrupted Imports Found", 
                               f"TomeBox recovered {len(valid_pending)} interrupted import tasks from a previous session.\n\n"
                               "Would you like to resume importing them now?"):
            self.dl_status_var.set("Resuming interrupted imports...")
            for task in valid_pending:
                path = task["path"]
                is_folder = task["is_folder"]
                if is_folder:
                    self.library_manager.import_folder(
                        folder_path=path, converter=self.converter, active_profile=self.active_profile,
                        on_status_cb=lambda msg: self.root.after(0, self.dl_status_var.set, msg),
                        on_complete_cb=lambda c, t=0, p=path: self._on_import_finished(p, c, t),
                        logger=self.logger, on_progress_cb=lambda pct: self.root.after(0, lambda: self.dl_progress_var.set(pct))
                    )
                else:
                    self.library_manager.import_files(
                        file_paths=[path], converter=self.converter, active_profile=self.active_profile,
                        on_status_cb=lambda msg: self.root.after(0, self.dl_status_var.set, msg),
                        on_complete_cb=lambda c, t=0, p=path: self._on_import_finished(p, c, t),
                        logger=self.logger
                        # Removed on_progress_cb here too!
                    )
        else:
            self.system_manager.clear_all_pending_imports(self.db.data_dir)

    def clear_sidebar(self):
        """Wipes the side panel when selection is lost or deleted."""
        if hasattr(self, 'author_label'):
            self.author_label.config(text="")
        if hasattr(self, 'cover_label'):
            self.cover_label.config(image="", text="No Cover Art")
        self.current_cover_photo = None
        
        if hasattr(self, 'bm_tree'):
            for row in self.bm_tree.get_children():
                self.bm_tree.delete(row)

    def _on_import_finished(self, path, added_count, total_found=0, task_id=None):
        self.system_manager.remove_pending_import(self.db.data_dir, path)
        self._on_import_complete(added_count, total_found)
        if task_id:
            if task_id in getattr(self.library_manager, 'canceled_tasks', set()):
                status = "Canceled"
            else:
                status = "Complete" if added_count > 0 else "Finished"
            self._on_dl_status(task_id, status, is_global=False)
            self._schedule_row_removal(task_id)
        
    def _on_import_queue_empty(self):
        """Triggered only when the entire import queue is finished."""
        def update():
            self.update_global_status("All queued imports completed.")
            self.root.bell()
            self.root.after(3000, self.reset_ui_if_idle)
        self.root.after(0, update)
        
    def _on_task_error(self, filepath, action_type, error_msg):
        """Catches errors from background threads and pushes them to the log."""
        def update():
            self.failed_tasks.append({
                "path": filepath, 
                "action": action_type, 
                "error": error_msg
            })
            
            # Wake up the Error button
            self.error_btn_var.set(f"Errors ({len(self.failed_tasks)})")
            self.error_btn.config(state=tk.NORMAL)
            
            # Bounce the status text momentarily so they notice
            self.dl_status_var.set(f"Task failed: {os.path.basename(filepath)}")
            self.root.after(4000, lambda: self.reset_ui_if_idle())
        self.root.after(0, update)

    def open_error_log(self):
        """Bridge method to open the Error Log popup from dialogs.py"""
        open_error_log_window(self)

    def ensure_download_folder(self):
        """
        Confirms or sets the download folder before starting downloads.
        Returns the chosen folder path, or None if the user cancels entirely.
        """
        folder = self.settings.get("download_folder")
        if folder and os.path.isdir(folder):
            return folder
        
        # Step 1: Confirm they want to proceed
        proceed = messagebox.askyesno(
            "Set Download Folder",
            "No download folder is set. Would you like to choose one now?\n\n"
            "Click 'Yes' to pick a folder, or 'No' to cancel the download."
        )
        if not proceed:
            return None
        
        # Step 2: Show folder picker
        folder = filedialog.askdirectory(title="Select Download Folder")
        
        # Step 3: User closed the picker without choosing — offer fallback
        if not folder:
            default_folder = os.path.join(os.path.expanduser("~"), "Downloads", "TomeBox")
            
            use_default = messagebox.askyesno(
                "Use Default Location?",
                f"No folder was selected.\n\n"
                f"Would you like to download to the default location?\n\n"
                f"{default_folder}\n\n"
                f"Click 'Yes' to use this location, or 'No' to cancel the download."
            )
            
            if not use_default:
                return None
            
            os.makedirs(default_folder, exist_ok=True)
            folder = default_folder
            
            # Confirm where it landed
            messagebox.showinfo(
                "Download Folder Set",
                f"Audiobooks will be saved to:\n\n{folder}\n\n"
                f"You can change this later via File → Set Download Folder."
            )
        
        # Save for next time
        self.settings["download_folder"] = folder
        self.db.save_settings(self.settings)
        return folder

    def _on_scrape_search_results(self, filepath, products):
        """Called when the Audible search returns results."""
        def update():
            self.reset_ui_if_idle()
            if not products:
                messagebox.showinfo("No Results", "No matches found for that title.")
                return
            self.show_scrape_results(filepath, products)
        self.root.after(0, update)

    def _on_scrape_apply_complete(self, filepath, title):
        """Called when FFmpeg finishes embedding tags."""
        def update():
            self.reset_ui_if_idle()
            
            # 1. Clear the image cache so Grid View is forced to load the new cover from disk
            self.cover_cache.clear()
            
            # 2. Refresh the list/grid views with the new ASINs and Titles
            self.refresh_library_ui()
            
            # 3. Force the right-hand side panel to immediately load the new cover and author data
            self.metadata_manager.fetch_display_metadata(filepath)
            
            # 4. Reload the player if the user is currently listening to the file we just tagged
            if self.file_path == filepath:
                self.load_specific_file(filepath)
                
            messagebox.showinfo("Success", "Metadata scraped and applied!")
            
        self.root.after(0, update)

    def update_api_health(self, message, is_error=False):
        """Thread-safe update of the API health status label."""
        def update():
            if hasattr(self, 'api_health_var'):
                self.api_health_var.set(f"API: {message}")
            if is_error:
                # Auto-reset the health indicator back to Online after the 60s cooldown expires
                self.root.after(60000, lambda: self.api_health_var.set("API: Online"))
        self.root.after(0, update)

    def _on_scrape_error(self, err_msg):
        """Catches metadata scrape errors, maps them to plain text, and updates the UI."""
        def update():
            self.reset_ui_if_idle()
            
            err_lower = err_msg.lower()
            
            # Map technical errors to user-friendly messages
            if "rate limit" in err_lower or "429" in err_lower:
                user_msg = "Audible API rate limit reached. Pausing scrape for 60s."
                self.update_api_health("Rate Limited", is_error=True)
                
            elif "timeout" in err_lower or "unavailable" in err_lower:
                user_msg = "Metadata server unresponsive. Using local tags."
                self.update_api_health("Offline", is_error=True)
                
            else:
                user_msg = "Failed to fetch metadata. Using local tags."
                self.update_api_health("Error", is_error=True)
                
            # Update the main task status bar to ensure the user sees it, 
            # rather than throwing a disruptive messagebox popup.
            self.dl_status_var.set(user_msg)
            
            # Optionally, log the error trace silently to the log file via our new logger method
            if hasattr(self, 'logger'):
                self.logger.error(f"UI caught scrape error: {err_msg}")
                
        self.root.after(0, update)

    def _on_dl_status(self, asin, status_text, is_global=False):
        """Routes status text updates to either the global header or the specific queue row."""
        def update():
            if is_global:
                self.update_global_status(status_text)
            elif asin in self.queue_ui_elements:
                self.queue_ui_elements[asin]["status_var"].set(status_text)
        self.root.after(0, update)

    def _on_dl_progress(self, asin, percent, is_global=False):
        """Routes progress bar updates."""
        def update():
            if is_global:
                self.update_global_progress(percent)
            if asin in self.queue_ui_elements:
                self.queue_ui_elements[asin]["prog_var"].set(percent)
                self.queue_ui_elements[asin]["status_var"].set(f"{int(percent)}%")
        self.root.after(0, update)

    def _on_dl_complete(self, filepath, title, post_action):
        """Called when a file successfully finishes saving to disk."""
        def update():
            self.stats_manager.add_stat("books_downloaded", 1)
            self.refresh_library_ui()
            
            if post_action in ["play", "convert"]:
                self.load_specific_file(filepath)
                if post_action == "play":
                    self.root.after(500, self.play_chapter)
                elif post_action == "convert":
                    self.root.after(500, self.start_convert_thread)
        self.root.after(0, update)

    def _on_dl_batch_finish(self):
        """Called when the entire queue goes idle."""
        def update():
            self.update_global_status("All downloads completed.")
            if hasattr(self, 'dl_all_btn'):
                self.dl_all_btn.config(state=tk.NORMAL)
            self.root.after(3000, self.reset_ui_if_idle)
            
            # Cleanup download rows when the batch finishes
            for task_id in list(self.queue_ui_elements.keys()):
                if not str(task_id).startswith("import_"):
                    self.remove_queue_ui_row(task_id)
        self.root.after(0, update)

    def _on_playback_tick(self, current_time, total_time, real_time_delta):
        """Called twice a second by the PlaybackController."""
        def update_ui():
            # Update Progress Bar & Labels
            percent = (current_time / total_time) * 100 if total_time > 0 else 0
            self.progress_var.set(percent)
            
            curr_str = self.format_time(current_time)
            dur_str = self.format_time(total_time)
            self.time_label.config(text=f"{curr_str} / {dur_str}")

            # Achievement Tracking
            self.session_listen_buffer += real_time_delta
            if self.session_listen_buffer >= 60.0:
                self.stats_manager.add_stat("seconds_listened", self.session_listen_buffer)
                self.session_listen_buffer = 0.0

            # Database Saving (Every ~10 seconds)
            now = time.time()
            if now - self._last_disk_save_time > 10:
                self.save_playback_state()
                self._last_disk_save_time = now
                
        # Push the update to the main Tkinter thread safely
        self.root.after(0, update_ui)

    def _on_import_complete(self, added_count, total_found=0):
        def update():
            if added_count > 0:
                self.refresh_library_ui()
                self.dl_status_var.set(f"Successfully imported {added_count} files.")
            elif total_found > 0:
                self.dl_status_var.set("Files already in library.")
            else:
                self.dl_status_var.set("No valid audiobooks found to import.")
                
        self.root.after(0, update)

    def toggle_web_server(self):
        def on_started():
            self.server_running = True
            # Toggle the start/stop label
            self.root.after(0, lambda: self.file_menu.entryconfigure("Enable Web Server", label="Disable Web Server"))
            # Enable the pairing info button
            self.root.after(0, lambda: self.file_menu.entryconfigure("Show Pairing Info", state=tk.NORMAL))
            # Auto-show the pairing window on start
            self.root.after(0, lambda: open_pairing_window(self))
            
        def on_stopped():
            self.server_running = False
            # Toggle the start/stop label back
            self.root.after(0, lambda: self.file_menu.entryconfigure("Disable Web Server", label="Enable Web Server"))
            # Disable the pairing info button
            self.root.after(0, lambda: self.file_menu.entryconfigure("Show Pairing Info", state=tk.DISABLED))
            
            self.root.after(0, lambda: messagebox.showinfo("Server Stopped", "The companion server has been safely disabled."))
            
        def on_error(title, msg):
            self.root.after(0, lambda: messagebox.showerror(title, msg))

        self.system_manager.toggle_web_server(
            app_instance=self,
            on_started_cb=on_started,
            on_stopped_cb=on_stopped,
            on_error_cb=on_error
        )
    def on_file_drop(self, event):
        from tkinter import messagebox
        import os
        
        # 1. Parse the dropped string using our robust regex parser
        dropped_paths = parse_dnd_paths(event.data)

        if not dropped_paths:
            self.dl_status_var.set("No valid paths found.")
            return

        # NEW: Consent Warning for dragged folders
        has_folders = any(os.path.isdir(p) for p in dropped_paths)
        if has_folders:
            if not messagebox.askyesno(
                "Auto-Merge Warning",
                "You are importing folders. If multiple audio files belonging to the same book are found, TomeBox will automatically merge them into a new .m4b file on your hard drive.\n\nDo you wish to proceed?"
            ):
                self.dl_status_var.set("Import cancelled.")
                return

        self.dl_status_var.set("Processing dropped items...")
        # 2. Route the paths to the correct importer
        for path in dropped_paths:
            is_folder = os.path.isdir(path)
            task_id = f"import_{time.time()}_{os.path.basename(path)}"
            self.system_manager.add_pending_import(self.db.data_dir, path, is_folder)
            
            self.toggle_queue_drawer(True)
            self.add_queue_ui_row(task_id, f"Importing: {os.path.basename(path)}")
            
            if is_folder:
                self.library_manager.import_folder(
                    folder_path=path, converter=self.converter, active_profile=self.active_profile,
                    on_status_cb=lambda msg, tid=task_id: self._on_import_status(tid, msg),
                    on_complete_cb=lambda c, t=0, p=path, tid=task_id: self._on_import_finished(p, c, t, tid),
                    logger=self.logger, on_progress_cb=lambda pct, tid=task_id: self._on_import_progress(tid, pct),
                    task_id=task_id
                )
            elif os.path.isfile(path):
                self.library_manager.import_files(
                    file_paths=[path], converter=self.converter, active_profile=self.active_profile,
                    on_status_cb=lambda msg, tid=task_id: self._on_import_status(tid, msg),
                    on_complete_cb=lambda c, t=0, p=path, tid=task_id: self._on_import_finished(p, c, t, tid),
                    logger=self.logger,
                    task_id=task_id
                )


    def add_local_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b *.mp3")])
        if not filepath: return
        
        import time
        import os
        
        task_id = f"import_{time.time()}_{os.path.basename(filepath)}"
        self.system_manager.add_pending_import(self.db.data_dir, filepath, False)
        
        self.toggle_queue_drawer(True)
        self.add_queue_ui_row(task_id, f"Importing: {os.path.basename(filepath)}")
        
        self.library_manager.import_files(
            file_paths=[filepath], converter=self.converter, active_profile=self.active_profile,
            on_status_cb=lambda msg, tid=task_id: self._on_import_status(tid, msg),
            on_complete_cb=lambda c, t=0, p=filepath, tid=task_id: self._on_import_finished(p, c, t, tid),
            logger=self.logger,
            task_id=task_id
        )

        
    def import_folder(self):
        """Prompts the user to select a folder and recursively imports all audio files."""
        folder = filedialog.askdirectory(title="Select Folder Containing Audiobooks")
        if not folder:
            return
        if not messagebox.askyesno(
            "Auto-Merge Warning",
            "TomeBox will now scan this folder.\n\nIf multiple audio files belonging to the same audiobook are found, they will be automatically merged into a new, single .m4b file on your hard drive.\n\nDo you wish to proceed?"
        ):
            return
            
        import time
        import os
        
        task_id = f"import_{time.time()}_{os.path.basename(folder)}"
        self.system_manager.add_pending_import(self.db.data_dir, folder, True)
        
        self.toggle_queue_drawer(True)
        self.add_queue_ui_row(task_id, f"Importing Folder: {os.path.basename(folder)}")
        
        self.library_manager.import_folder(
            folder_path=folder, converter=self.converter, active_profile=self.active_profile,
            on_status_cb=lambda msg, tid=task_id: self._on_import_status(tid, msg),
            on_complete_cb=lambda c, t=0, p=folder, tid=task_id: self._on_import_finished(p, c, t, tid),
            logger=self.logger, 
            on_progress_cb=lambda pct, tid=task_id: self._on_import_progress(tid, pct),
            task_id=task_id
        )

    def bring_to_front(self):
        # 1. Un-hide it if it was minimized to the system tray
        self.root.deiconify()
        
        # 2. Lift it above other windows
        self.root.lift()
        
        # 3. Force it to the absolute top, then release the lock so the user can click other things again
        self.root.attributes('-topmost', True)
        self.root.after_idle(self.root.attributes, '-topmost', False)

    def setup_tray_icon(self):
        import sys
        # macOS strictly forbids background tray loops and uses the Dock instead.
        if sys.platform == 'darwin':
            self.logger.info("macOS detected. Skipping system tray initialization.")
            return

        try:
            icon_path = get_resource_path("ui", "tomebox.ico")
            
            if not os.path.exists(icon_path):
                self.logger.warning(f"System tray icon not found at: {icon_path}")
                return
                
            image = Image.open(icon_path)
            
            menu = pystray.Menu(
                item('Show TomeBox', self.show_window_from_tray, default=True),
                item('Quit', self.quit_from_tray)
            )
            
            self.tray_icon = pystray.Icon("TomeBox", image, "TomeBox", menu)
            
            # Run the tray icon loop in a background thread so it doesn't block Tkinter (Safe on Windows/Linux)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self.logger.info(f"Failed to initialize system tray: {e}")

    def thread_safe_ask_directory(self):
        """Safely opens a directory dialog from a background thread."""
        result = [None]
        event = threading.Event()
        
        def _ask():
            # Force the main window to the front before opening the dialog
            self.root.attributes('-topmost', True)
            result[0] = filedialog.askdirectory(parent=self.root, title="Select TomeBox Location")
            self.root.attributes('-topmost', False)
            event.set()
            
        self.root.after(0, _ask)
        event.wait() # Block the calling background thread until the user clicks OK/Cancel
        return result[0]

    def thread_safe_ask_file(self):
        """Safely opens a file dialog from a background thread."""
        result = [None]
        event = threading.Event()
        
        def _ask():
            self.root.attributes('-topmost', True)
            result[0] = filedialog.askopenfilename(
                parent=self.root, 
                title="Select Audiobook File",
                filetypes=[("Audiobooks", "*.m4b *.mp3 *.aaxc *.aax")]
            )
            self.root.attributes('-topmost', False)
            event.set()
            
        self.root.after(0, _ask)
        event.wait()
        return result[0]

    def hide_window_to_tray(self):
        # Withdraw hides the window from the taskbar and screen
        self.root.withdraw()
        
    def show_window_from_tray(self, icon, item):
        # Must be passed back to the main Tkinter thread using .after
        self.root.after(0, self.root.deiconify)
        
    def quit_from_tray(self, icon, item):
        icon.stop()
        self.root.after(0, self.on_closing)

    def open_support_link(self):
        import webbrowser
        self.logger.info("Opening Buy Me a Coffee link...")
        webbrowser.open("https://buymeacoffee.com/ProblematicSyntax")

    def add_new_profile(self):
        new_name = simpledialog.askstring("New Profile", "Enter a name for the new profile:")
        if new_name and new_name not in self.profiles_list:
            self.profiles_list.append(new_name)
            self.settings["profiles"] = self.profiles_list
            self.profile_combo.config(values=self.profiles_list)
            self.profile_combo.set(new_name)
            self.switch_profile()

    def switch_profile(self, event=None):
        selected = self.profile_combo.get()
        self.active_profile = selected
        self.settings["active_profile"] = selected
        self.db.save_settings(self.settings)
        
        self.auth_save_path = get_resource_path( "data", f"auth_{self.active_profile}.json")
        self.cloud_cache_path = get_resource_path( "data", f"cloud_{self.active_profile}.json")
        
        # Clear current session
        self.api_client.auth = None
        self.auth_bytes.set("")
        self.library_manager.cloud_items = self.load_cloud_cache()
        
        # Try to load the new profile's auth file
        self.auto_load_auth()
        self.refresh_library_ui()

    def check_dependencies(self):
        import shutil
        import webbrowser
        
        ffmpeg_installed = shutil.which("ffmpeg") is not None
        ffplay_installed = shutil.which("ffplay") is not None
        
        if not ffmpeg_installed or not ffplay_installed:
            self.logger.warning("WARNING: FFmpeg or FFplay not found in system PATH.")
            
            msg = (
                "FFmpeg is missing from your system.\n\n"
                "TomeBox requires FFmpeg to play, convert, and split audiobooks. "
                "Without it, you will only be able to download files.\n\n"
                "Would you like to open the official FFmpeg download page now?"
            )
            
            # Force the window to the top and attach the dialog to prevent Mac freezing
            self.root.attributes('-topmost', True)
            user_wants_link = messagebox.askyesno("Missing Dependency: FFmpeg", msg, parent=self.root)
            self.root.attributes('-topmost', False)
            
            if user_wants_link:
                self.logger.info("Opening FFmpeg download page in browser...")
                webbrowser.open("https://ffmpeg.org/download.html")

    def run_background_sync(self):
        self.thread_pool.submit(
            self.library_manager.silent_cloud_sync, 
            self.logger, 
            lambda msg: self.root.after(0, lambda: self.dl_status_var.set(msg)), 
            lambda: self.root.after(0, self.refresh_library_ui)
        )
        # Schedule the next check in 15 minutes (900000 milliseconds)
        self.root.after(900000, self.run_background_sync)

    def build_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        
        # Playback Controls
        self.context_menu.add_command(label="▶ Play", command=self.master_play)
        self.context_menu.add_separator()


        # File Operations 
        self.context_menu.add_command(label="⬇️ Download", command=lambda: self.handle_action_on_selected("download"))
        self.context_menu.add_command(label="🔄 Convert", command=lambda: self.handle_action_on_selected("convert"))
        self.context_menu.add_command(label="🔍 Scrape Metadata", command=lambda: self.handle_action_on_selected("scrape"))

    def show_context_menu(self, event):
        # If we are in the list view, select the item under the cursor first
        if self.current_view_mode == "list":
            item = self.library_tree.identify_row(event.y)
            if item:
                self.library_tree.selection_set(item)
                self.library_tree.focus(item)
                self.on_item_select() # Update the side panel preview

        # Pop the menu at the exact screen coordinates of the mouse click
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def on_chapter_select(self, tree):
        selected = tree.focus()
        if not selected:
            return
            
        
        target_idx = tree.index(selected)

        if 0 <= target_idx < len(self.chapters):
            # Close the window
            if hasattr(self, 'chapter_win') and self.chapter_win:
                self.chapter_win.destroy()
            
            # Stop current playback and save state
            self.stop_audio()
                
            # Set the new target
            self.current_chapter_idx = target_idx
            self.current_play_time = 0.0
            
            self.play_chapter()

    def on_item_select(self, event=None):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                self.clear_sidebar()
                self._selected_local_path = None
                return
            item = self.library_tree.item(selected)
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]
        else:
            if not self._selected_grid_item:
                self.clear_sidebar()
                self._selected_local_path = None
                return
            item = self._selected_grid_item
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]

        if hasattr(self, 'author_label'):
            self.author_label.config(text=authors)
        
        cover_path = None
        covers_dir = self.covers_dir
        
        if asin and asin != "Unknown":
            padded_asin = str(asin).zfill(10)
            test_path_padded = os.path.join(covers_dir, f"{padded_asin}.jpg")
            test_path_raw = os.path.join(covers_dir, f"{asin}.jpg")
            
            if os.path.exists(test_path_padded):
                cover_path = test_path_padded
            elif os.path.exists(test_path_raw):
                cover_path = test_path_raw
                
        local_path = None
        for p, d in self.library_manager.local_library.items():
            if d.get("title") == title:
                local_path = p
                if not cover_path:
                    test_local = os.path.splitext(p)[0] + "_cover.jpg"
                    if os.path.exists(test_local):
                        cover_path = test_local
                break

        # Track the strictly selected path
        self._selected_local_path = local_path

        if cover_path and hasattr(self, 'cover_label'):
            try:
                from PIL import Image, ImageTk
                img = Image.open(cover_path)
                img.thumbnail((400, 400), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.cover_label.config(image=photo, text="")
                self.current_cover_photo = photo 
            except Exception:
                self.cover_label.config(image="", text=title)
        elif hasattr(self, 'cover_label'):
            self.cover_label.config(image="", text=title)
            
        self.refresh_bookmarks_ui()

    def manage_shelves_prompt(self):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Please select an audiobook to tag.")
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item:
                messagebox.showwarning("Selection Required", "Please select an audiobook to tag.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        asin = item['values'][4]

        if not asin or asin == "Unknown":
            messagebox.showerror("Error", "Cannot tag an orphaned file without an ASIN. Please scrape its metadata first.")
            return

        if "shelves_db" not in self.settings:
            self.settings["shelves_db"] = {}

        current_shelves = self.settings["shelves_db"].get(asin, [])
        current_shelves_str = ", ".join(current_shelves)

        new_shelves_str = simpledialog.askstring(
            "Manage Shelves",
            f"Enter custom shelves for:\n{title}\n\n(Separate multiple tags with commas)",
            initialvalue=current_shelves_str
        )

        if new_shelves_str is not None:
            tags = [t.strip() for t in new_shelves_str.split(",") if t.strip()]
            self.settings["shelves_db"][asin] = tags
            self.db.save_settings(self.settings)
            self.refresh_library_ui()

    def save_tray_setting(self):
        self.settings["minimize_to_tray"] = self.minimize_to_tray_var.get()
        self.db.save_settings(self.settings)

    def on_filter_change(self):

        if self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()

    def handle_window_close(self):
        if self.minimize_to_tray_var.get():
            self.hide_window_to_tray()
        else:
            if self.tray_icon:
                self.tray_icon.stop()
            self.on_closing()
    
    def on_closing(self):
        try:
            self.logger.info("Initiating shutdown sequence...")
            
            # 1. Save our place in the audiobook and database
            self.save_playback_state()

            # 2. Trigger the aggressive stop command on our playback controller
            if self.playback:
                self.playback.stop()

            # 3. Flag the web server thread to shut down gracefully
            if self.system_manager:
                self.system_manager.stop_server_sync()
                
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
            
        finally:
            # 4. Hide the UI instantly so the app feels snappy and responsive
            self.root.withdraw()
            
            # 5. The Nuclear Option: Kill the process tree.
            import os
            import subprocess
            if os.name == 'nt':
                # /T flag = "Tree Kill" (Kills this process and everything it spawned)
                ProcessRunner.run_async(['taskkill', '/F', '/T', '/PID', str(os.getpid())])
            else:
                # Mac/Linux immediate hard exit
                os._exit(0)

    def save_playback_state(self):
        state = self.playback.get_current_state()
        if state:
            self.library_manager.save_playback_state(state, self.active_profile)
    
    def sync_playhead_from_remote(self, abs_position):
        """Called by the web server when the phone updates the current book's time."""
        try:
            # Let the playback controller handle the chapter/time math
            if self.playback.seek_to_absolute(abs_position):
                
                # Keep local UI variables synced with the controller's new reality
                self.current_chapter_idx = self.playback.current_chapter_idx
                self.current_play_time = self.playback.current_play_time
                
                # Visually move the progress bar on the PC screen
                if hasattr(self, 'progress_var') and self.chapters:
                    total_duration = float(self.chapters[-1].get("end_time", 0))
                    if total_duration > 0:
                        self.progress_var.set((abs_position / total_duration) * 100)
                        
        except Exception as e:
            self.logger.error(f"Failed to sync remote playhead: {e}")
            
    def cue_last_played(self):
        last_path = self.settings.get(f"last_played_{self.active_profile}")
        if last_path and last_path in self.library_manager.local_library and os.path.exists(last_path):
            self.load_specific_file(last_path)
    
    def set_download_folder(self):
        directory = filedialog.askdirectory(title="Select Default Download Folder")
        if directory:
            self.default_download_dir = directory
            self.settings["download_folder"] = directory
            self.db.save_settings(self.settings)
            messagebox.showinfo("Folder Saved", f"Default download folder updated to:\n{directory}")

    def cancel_all_downloads(self):
        if messagebox.askyesno("Cancel All", "Cancel all active and pending downloads?"):
            self.download_manager.cancel_all()
            self.logger.info("User initiated Cancel All Downloads.")

    def cancel_download(self, asin):
        self.download_manager.cancel_download(asin)

    def start_download_all(self):
        # We check the library manager instead of local_library directly
        local_titles = {data["title"] for path, data in self.library_manager.local_library.items()}
        missing_items = [{"asin": item.get("asin"), "title": item.get("title", "Unknown")} 
                         for item in self.library_manager.cloud_items 
                         if item.get("title") not in local_titles]

        if not missing_items:
            messagebox.showinfo("Up to Date", "Your local library already has all cloud items.")
            return

        save_dir = self.ensure_download_folder()
        if not save_dir:
            return
        if messagebox.askyesno("Download All", f"Queue {len(missing_items)} missing audiobooks?"):
            self.dl_all_btn.config(state=tk.DISABLED)
            self.toggle_queue_drawer(True)
            
            for item in missing_items:
                self.add_queue_ui_row(item["asin"], item["title"])
                
            self.download_manager.queue_batch(missing_items, save_dir)

    def apply_classic_palette(self, palette_name):
        apply_theme(self, palette_name)

    def toggle_library_view(self):
        if self.current_view_mode == "list":
            self.current_view_mode = "grid"
            self.view_btn.config(text="List View")
            
            # Hide list elements
            self.library_tree.grid_remove()
            self.h_scroll.grid_remove()
            
            if self.library_manager.cloud_items or self.library_manager.local_library:
                self.grid_canvas.grid(row=0, column=0, sticky="nsew")
            
            # Route vertical scrollbar to the grid
            self.v_scroll.config(command=self.grid_canvas.yview)
            self.grid_canvas.config(yscrollcommand=self.v_scroll.set)
        else:
            self.current_view_mode = "list"
            self.view_btn.config(text="Grid View")
            
            # Hide grid elements
            self.grid_canvas.grid_remove()
            
            if self.library_manager.cloud_items or self.library_manager.local_library:
                self.library_tree.grid(row=0, column=0, sticky="nsew")
                self.h_scroll.grid(row=1, column=0, sticky="ew")
            
            # Route vertical scrollbar back to the list
            self.v_scroll.config(command=self.library_tree.yview)
            self.library_tree.config(yscrollcommand=self.v_scroll.set)
            
        self.refresh_library_ui()

    def on_canvas_resize(self, event):

        if hasattr(self, 'grid_window_id'):
            self.grid_canvas.itemconfig(self.grid_window_id, width=event.width)
        if getattr(self, '_last_canvas_width', None) == event.width:
            return
        self._last_canvas_width = event.width
        if self._resize_timer is not None:
            self.root.after_cancel(self._resize_timer)
        self._resize_timer = self.root.after(200, self.draw_grid_view)

    def draw_grid_view(self):
        if self.current_view_mode != "grid": return
        
        for widget in self.grid_inner.winfo_children():
            widget.destroy()


        style = ttk.Style()
        default_bg = style.lookup("TFrame", "background") or "#f0f0f0"
        default_fg = style.lookup("TLabel", "foreground") or "#000000"
        select_bg = "#4a90e2" 

        self.grid_canvas.config(bg=default_bg)
        self.grid_inner.config(bg=default_bg)
        
        canvas_width = self.grid_canvas.winfo_width()
        cols = max(1, canvas_width // 190)

        for i in range(20): 
            self.grid_inner.columnconfigure(i, weight=0)
        for i in range(cols):
            self.grid_inner.columnconfigure(i, weight=1)
        
        for idx, row_data in enumerate(getattr(self, '_current_filtered_data', [])):
            title, authors, series_str, duration_str, asin, status, row_path = row_data

            outer_card = tk.Frame(self.grid_inner, bg=default_bg)
            outer_card.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)

            card = tk.Frame(outer_card, bg=default_bg, width=170, height=240, bd=0, highlightthickness=0)
            card.pack_propagate(False) 
            card.pack(padx=2, pady=2) 
            
            is_missing_file = "Downloaded" in status and row_path and not os.path.exists(row_path)
            is_missing_duration = duration_str in ["0h 0m", "N/A", ""]
            
            if is_missing_file:
                warning_lbl = tk.Label(card, text="⚠️ File Missing", bg="#ff4444", fg="#ffffff", font=("Segoe UI", 8, "bold"))
                warning_lbl.pack(side=tk.TOP, fill="x")
            elif is_missing_duration:
                warning_lbl = tk.Label(card, text="⚠️ No Duration", bg="#ffaa00", fg="#000000", font=("Segoe UI", 8, "bold"))
                warning_lbl.pack(side=tk.TOP, fill="x")

            img_obj = None
            if asin in self.cover_cache:
                img_obj = self.cover_cache[asin]
            else:
                cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                if os.path.exists(cover_path):
                    try:
                        img = Image.open(cover_path)
                        img.thumbnail((150, 150))
                        img_obj = ImageTk.PhotoImage(img)
                        self.cover_cache[asin] = img_obj 
                    except: pass
                
            img_label = tk.Label(card, image=img_obj, text="No Cover" if not img_obj else "", bg=default_bg, fg=default_fg, bd=0, highlightthickness=0, takefocus=0)
            img_label.pack(pady=(5, 0))
            
            display_title = title[:45] + "..." if len(title) > 45 else title
            
            text_color = "#ff4444" if is_missing_file else ("#ffaa00" if is_missing_duration else default_fg)
            text_label = tk.Label(card, text=display_title, bg=default_bg, fg=text_color, font=("Segoe UI", 9), wraplength=150, justify="center", bd=0, highlightthickness=0, takefocus=0)
            text_label.pack(pady=(5, 0))
            
            def on_card_click(e, oc=outer_card, t=title, a=asin, s=status):
                # Safely check if a previous card exists before trying to un-highlight it
                last_card = getattr(self, '_last_selected_card_frame', None)
                if last_card is not None and last_card.winfo_exists():
                    last_card.config(bg=default_bg)
                
                oc.config(bg=select_bg)
                self._last_selected_card_frame = oc 
                self._selected_grid_item = {'values': [t, "", "", "", a, s, ""]} 
                self.on_item_select()
                
            def on_card_double_click(e, oc=outer_card, t=title, a=asin, s=status):
                on_card_click(e, oc, t, a, s)
                self.master_play()

            outer_card.bind("<Button-1>", on_card_click)
            outer_card.bind("<Double-1>", on_card_double_click)
            card.bind("<Button-1>", on_card_click)
            card.bind("<Double-1>", on_card_double_click)
            img_label.bind("<Button-1>", on_card_click)
            img_label.bind("<Double-1>", on_card_double_click)
            text_label.bind("<Button-1>", on_card_click)
            text_label.bind("<Double-1>", on_card_double_click)

        self.grid_inner.update_idletasks()
        self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all"))

    def toggle_queue_visibility(self):
        current_panes = self.main_paned.panes()
        queue_str = str(self.queue_frame)
        
        if queue_str in current_panes:
            self.main_paned.forget(self.queue_frame)
        else:
            self.main_paned.add(self.queue_frame, weight=0)

    def toggle_queue_drawer(self, show=True):
        current_panes = self.main_paned.panes()
        queue_str = str(self.queue_frame)
        
        if show and queue_str not in current_panes:
            self.main_paned.add(self.queue_frame, weight=0)
        elif not show and queue_str in current_panes:
            self.main_paned.forget(self.queue_frame)

    def add_queue_ui_row(self, task_id, title):
        row_frame = tk.Frame(self.queue_inner, bg="#1c1c1c")
        row_frame.pack(fill="x", pady=2, padx=5)

        title_lbl = ttk.Label(row_frame, text=title[:40] + ("..." if len(title) > 40 else ""), width=35, anchor="w")
        title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        prog_var = tk.DoubleVar()
        prog_bar = ttk.Progressbar(row_frame, variable=prog_var, maximum=100, length=200)
        prog_bar.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 10))

        status_var = tk.StringVar(value="Waiting...")
        status_lbl = ttk.Label(row_frame, textvariable=status_var, width=15, anchor="w")
        status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        # The unified task_id now properly binds to the cancel button
        cancel_btn = ttk.Button(row_frame, text="✕", command=lambda a=task_id: self.cancel_task(a))
        cancel_btn.pack(side=tk.RIGHT)

        self.queue_ui_elements[task_id] = {
            "frame": row_frame,
            "prog_var": prog_var,
            "status_var": status_var
        }

    def refresh_library_ui(self, *args):
        # 1. Clear the current UI
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        search_query = self.search_var.get()
        current_filter = self.filter_var.get()
        current_shelf = getattr(self, 'shelf_filter_var', tk.StringVar(value="All Shelves")).get()

        # 2. Ask the Controller for the data
        filtered_rows, shelf_list = self.library_manager.get_view_data(
            search_query=search_query,
            filter_type=current_filter,
            shelf_filter=current_shelf
        )

        self._current_filtered_data = filtered_rows

        # 3. Repopulate the shelf filter dropdown
        if hasattr(self, 'shelf_combo'):
            self.shelf_combo['values'] = shelf_list

        # 4. Handle Empty State
        is_completely_empty = (not self.library_manager.cloud_items) and (not self.library_manager.local_library)

        if is_completely_empty:
            self.library_tree.grid_remove()
            self.h_scroll.grid_remove()
            self.grid_canvas.grid_remove()
            self.v_scroll.grid_remove()
            self.empty_state_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        else:
            self.empty_state_frame.grid_remove()
            self.v_scroll.grid(row=0, column=1, sticky="ns")
            
            if self.current_view_mode == "list":
                self.grid_canvas.grid_remove()
                self.library_tree.grid(row=0, column=0, sticky="nsew")
                self.h_scroll.grid(row=1, column=0, sticky="ew")

                # Configure the health warning tag colors
                self.library_tree.tag_configure('warning', foreground='#ffaa00') # Orange for missing metadata
                self.library_tree.tag_configure('error', foreground='#ff4444')   # Red for missing files

                for row in filtered_rows:
                    title, authors, series_str, duration_str, asin, status, row_path = row
                    
                    # Evaluate Health
                    tags = ()
                    is_missing_file = "Downloaded" in status and row_path and not os.path.exists(row_path)
                    is_missing_duration = duration_str in ["0h 0m", "N/A", ""]

                    if is_missing_file:
                        tags = ('error',)
                    elif is_missing_duration:
                        tags = ('warning',)

                    self.library_tree.insert("", "end", values=row, tags=tags)

                if hasattr(self, 'current_sort_col') and hasattr(self, 'current_sort_descending'):
                    self.sort_treeview(self.library_tree, self.current_sort_col, self.current_sort_descending)
            else:
                self.library_tree.grid_remove()
                self.h_scroll.grid_remove()
                self.grid_canvas.grid(row=0, column=0, sticky="nsew")
                self.draw_grid_view()
        total_books = len(self.library_manager.local_library)
        formats = {}
        
        for path, data in self.library_manager.local_library.items():
            fmt = data.get("format", "UNKNOWN").upper()
            formats[fmt] = formats.get(fmt, 0) + 1
            
        self.lib_count_var.set(f"Books found: {total_books}")
        
        if formats:
            tooltip_text = "\n".join([f"{f}: {c}" for f, c in sorted(formats.items())])
        else:
            tooltip_text = "Library is empty."
            
        if hasattr(self, 'lib_count_tooltip'):
            self.lib_count_tooltip.text = tooltip_text

    def handle_action_on_selected(self, action_type):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item:
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        asin = item['values'][4]

        local_path = None
        for path, data in self.library_manager.local_library.items():
            if data["title"] == title:
                local_path = path
                break

        if local_path:
            if not os.path.exists(local_path):
                messagebox.showerror("File Missing", "The file was deleted or moved. Please remove it from the list and re-download.")
                return
                
            if action_type == "scrape":
                self.start_scrape_thread(local_path)
                return
                
            self.load_specific_file(local_path)
            if action_type == "play":
                self.play_chapter()
            elif action_type == "convert":
                self.start_convert_thread()
        else:
            if action_type == "download" or messagebox.askyesno("Download Required", f"'{title}' is not downloaded.\n\nDownload it now?"):
                save_dir = self.ensure_download_folder()
                if not save_dir:
                    return
                self.add_queue_ui_row(asin, title)
                self.download_manager.queue_download(asin, title, save_dir, post_action=action_type)
    def match_to_audible_prompt(self):
        """Opens the manual match dialog for the currently selected library item."""
        from ui.components.dialogs import open_match_to_audible_window
        
        # Get the selected file
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Please select a local file to match.")
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item:
                messagebox.showwarning("Selection Required", "Please select a local file to match.")
                return
            item = self._selected_grid_item
        
        title = item['values'][0]
        
        # Find the actual filepath for this title
        filepath = None
        for path, data in self.library_manager.local_library.items():
            if data.get("title") == title:
                filepath = path
                break
        
        if not filepath:
            messagebox.showinfo(
                "Cloud-Only Item",
                "This item is in your Audible library but hasn't been downloaded yet.\n\n"
                "Use 'Download Selected' or 'Download Missing' to fetch it."
            )
            return
        
        open_match_to_audible_window(self, filepath)
        
    def start_scrape_thread(self, filepath):
        data = self.library_manager.local_library.get(filepath, {})
        current_title = data.get("title", os.path.basename(filepath))
        
        query = simpledialog.askstring("Search Catalog", "Enter book title or author to search:", initialvalue=current_title)
        if not query: return
        
        self.dl_status_var.set("Searching catalogs (Audible & Google)...")
        self.metadata_manager.search_catalog(filepath, query)

    def show_scrape_results(self, filepath, products):
        popup = tk.Toplevel(self.root)
        popup.title("Select Correct Book")
        popup.geometry("600x300")
        popup.transient(self.root)
        
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        popup.configure(bg=bg_color)
        
        listbox = tk.Listbox(popup, width=80, height=12)
        listbox.pack(padx=10, pady=10, fill="both", expand=True)
        
        for p in products:
            title = p.get("title", "")
            raw_authors = p.get("authors", [])
            authors = ", ".join([a.get("name", "") for a in raw_authors])
            source = p.get("source", "Audible")
            listbox.insert(tk.END, f"[{source}] {title} | {authors} ({p.get('asin')})")
            
        def on_select():
            sel = listbox.curselection()
            if not sel: return
            selected_asin = products[sel[0]].get("asin")
            popup.destroy()
            self.dl_status_var.set("Fetching and embedding metadata...")
            self.metadata_manager.apply_scraped_metadata(filepath, selected_asin)
            
        ttk.Button(popup, text="Apply Metadata", command=on_select).pack(pady=(0, 10))

    def sort_treeview(self, tree, col, descending):
        data = [(tree.set(child, col), child) for child in tree.get_children('')]
        
        def sort_key(item):
            val = item[0]
            if "h " in val and "m" in val:
                try:
                    parts = val.split("h ")
                    h = int(parts[0])
                    m = int(parts[1].replace("m", ""))
                    return h * 60 + m
                except ValueError:
                    pass
            return val.lower()

        data.sort(key=sort_key, reverse=descending)
        self.current_sort_col = col
        self.current_sort_descending = descending
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        tree.heading(col, command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def setup_ui(self):
        setup_menu_bar(self)
        setup_player_bar(self)
        import sys
        if sys.platform == 'darwin':
            try:
                # 1. Enable the native Mac "Settings/Preferences" menu item
                from ui.components.dialogs import open_auth_window
                self.root.createcommand('::tk::mac::ShowPreferences', lambda: open_auth_window(self))
                
                # 2. Hide the default Tkinter "Run Widget Demo" help menu
                self.root.createcommand('tk::mac::ShowHelp', lambda: None)
            except Exception as e:
                if hasattr(self, 'logger'): self.logger.error(f"Failed to bind Mac menus: {e}")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main_vbox = tk.Frame(self.root)
        main_vbox.pack(fill="both", expand=True, padx=10, pady=10)
        main_vbox.rowconfigure(0, weight=1)
        main_vbox.columnconfigure(0, weight=1)

        self.top_split = ttk.PanedWindow(main_vbox, orient=tk.HORIZONTAL)
        self.top_split.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        left_panel = tk.Frame(self.top_split)
        self.right_panel = tk.Frame(self.top_split)

        self.top_split.add(left_panel, weight=3)
        self.top_split.add(self.right_panel, weight=1)

        bottom_panel = tk.Frame(main_vbox)
        bottom_panel.grid(row=1, column=0, sticky="ew")

        setup_library_view(self, left_panel)
        setup_sidebar(self, self.right_panel)

    def toggle_sidebar_visibility(self):
        """Hides or reveals the right-hand info and bookmarks panel."""
        current_panes = self.top_split.panes()
        
        # Check if the panel is currently visible in the window
        if str(self.right_panel) in current_panes:
            self.top_split.forget(self.right_panel)
        else:
            self.top_split.add(self.right_panel, weight=1)

    def open_web_ui(self):
        """Opens the localhost web UI in the user's default browser."""
        import webbrowser
        
        # Auto-start the server if it's not already running
        if not getattr(self, 'server_running', False):
            self.toggle_web_server()
        
        # Small delay to give the server a moment to bind the port before browser opens
        self.root.after(500, lambda: webbrowser.open("http://127.0.0.1:8000/desktop"))
        
    def export_csv_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV File", "*.csv")],
            title="Export Library to CSV"
        )
        if not output_file:
            return

        try:
            LibraryExporter.export_csv(output_file, self.library_manager.local_library, self.library_manager.cloud_items)
            messagebox.showinfo("Export Successful", f"Library successfully exported to:\n{output_file}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to write CSV:\n{e}")

    def export_html_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML Document", "*.html")],
            title="Export Library to HTML"
        )
        if not output_file:
            return

        try:
            LibraryExporter.export_html(output_file, self.library_manager.local_library, self.library_manager.cloud_items)
            import webbrowser
            webbrowser.open(output_file)
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to generate HTML:\n{e}")

    def write_log(self, message):
        """Bridge method routing legacy log calls into the standard logger."""
        if hasattr(self, 'logger'):
            self.logger.info(message)
        else:
            print(message)

    def auto_load_auth(self):
        self.logger.info("DEBUG: auto_load_auth fired from startup timer.")
        if self.api_client.load_auth_from_file(self.auth_save_path):
            activation_bytes = self.api_client.get_activation_bytes()
            self.auth_bytes.set(activation_bytes)
            self.logger.info(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
            
            # Reset filters before fetch so UI shows everything when worker completes
            self.filter_var.set("All")
            self.shelf_filter_var.set("All Shelves")
            self.search_var.set("")
            
            self.fetch_cloud_library()
        else:
            self.logger.info("No saved session found. Please log in.")

    def load_auth_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Auth File", "*.json")], title="Select Audible Auth File")
        if not filepath: return

        self.logger.info(f"Loading auth from external file: {filepath}")
        try:
            if self.api_client.load_auth_from_file(filepath):
                activation_bytes = self.api_client.get_activation_bytes()
                self.auth_bytes.set(activation_bytes)
                self.logger.info(f"Activation Bytes Received: {activation_bytes}")
                self.api_client.save_auth_to_file(self.auth_save_path)
                
                messagebox.showinfo("Success", "Auth file loaded! You can now fetch your library.")
                self.fetch_cloud_library()
        except Exception as e:
            self.logger.error(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Error", "Could not load auth file. Check the log.")

    def start_browser_login_thread(self):
        if self.browser_login_btn and self.browser_login_btn.winfo_exists():
            self.browser_login_btn.config(text="Connecting...", state=tk.DISABLED)
        self.thread_pool.submit(self.browser_login_worker, self.locale.get())

    def browser_login_worker(self, locale):
        self.logger.info(f"Starting external browser login for region: {locale}")
        
        def custom_login_callback(login_url):
            self.logger.info("Opening default web browser...")
            webbrowser.open(login_url)
            
            result = [None]
            event = threading.Event()
            
            def ask_user_for_url():
                msg = (
                    "1. Your web browser should have opened.\n"
                    "2. Log in to Amazon / Audible.\n"
                    "3. Once logged in, you will land on a blank or 'Page Not Found' error page.\n\n"
                    "4. Copy the ENTIRE URL from your browser's address bar and paste it below:"
                )
                res = simpledialog.askstring("Audible Login Authorization", msg, parent=self.root)
                result[0] = res
                event.set()
                
            self.root.after(0, ask_user_for_url)
            event.wait()
            
            if not result[0]:
                raise Exception("Authentication cancelled by user.")
                
            return result[0].strip()

        try:
            self.logger.info("Waiting for user to complete browser login and paste URL...")
            if self.api_client.login_with_browser(locale, custom_login_callback):
                activation_bytes = self.api_client.get_activation_bytes()
                
                self.root.after(0, self.auth_bytes.set, activation_bytes)
                self.logger.info(f"Activation Bytes Received: {activation_bytes}")
                
                self.api_client.save_auth_to_file(self.auth_save_path)
                self.logger.info(f"Session saved locally to {self.auth_save_path}")

                self.root.after(0, lambda: messagebox.showinfo("Success", "Connected to Audible!"))
                self.filter_var.set("All")
                self.shelf_filter_var.set("All Shelves")
                self.search_var.set("")
                self.root.after(0, self.fetch_cloud_library)
                
        except Exception as e:
            error_trace = traceback.format_exc()
            self.logger.error("ERROR DURING LOGIN:")
            self.logger.error(error_trace)
            self.root.after(0, lambda: messagebox.showerror("Login Failed", str(e)))
            
        finally:
            self.logger.info("Login thread terminated.")
            def restore_btn():
                if self.browser_login_btn and self.browser_login_btn.winfo_exists():
                    self.browser_login_btn.config(text="Login via Browser", state=tk.NORMAL)
            self.root.after(0, restore_btn)

    def fetch_cloud_library(self):
        self.logger.info("DEBUG: fetch_cloud_library method started executing.")
        
        if not self.api_client.auth:
            self.logger.info("DEBUG: fetch_cloud_library aborted - self.api_client.auth is missing or None.")
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        self.logger.info("DEBUG: self.api_client.auth verified. Launching fetch_library_worker thread...")
        
        self.dl_status_var.set("Fetching data from Amazon... Please wait.")
        
        self.thread_pool.submit(self.fetch_library_worker)

    def fetch_library_worker(self):
        try:
            self.logger.info("Querying Audible Library API...")
            
            # 1. Delegate entirely to the LibraryManager (this handles the API call AND saving the cache)
            self.library_manager.fetch_cloud_library()
            
            self.logger.info(f"Successfully retrieved {len(self.library_manager.cloud_items)} library items.")

            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda: self.reset_ui_if_idle())

            self.metadata_manager.sync_missing_covers(
                on_complete_cb=lambda: self.root.after(0, lambda: self.refresh_library_ui() if self.current_view_mode == 'grid' else None)
            )
            
        except httpx.ConnectError:
            self.logger.error("Network offline during library sync.")
            self.root.after(0, lambda: messagebox.showerror("Connection Error", "Could not connect to Audible servers. Check your internet connection."))
        except Exception as e:
            # 2. Safely catch auth/API errors without relying on a specific audible package exception
            if "401" in str(e) or "unauthorized" in str(e).lower() or "Not authenticated" in str(e):
                self.logger.error(f"Audible API rejected the request: {e}")
                self.root.after(0, lambda: messagebox.showerror("Audible API Error", "Your session may have expired. Please log in again via Settings."))
            else:
                self.logger.error(f"Unhandled exception in library worker: {e}\n{traceback.format_exc()}")
                self.root.after(0, lambda: messagebox.showerror("Library Error", "An unexpected error occurred while fetching your library."))
        finally:
            self.root.after(0, self.reset_ui_if_idle)

    def remove_local_file(self):
        selected_items = self.library_tree.selection()
        if not selected_items: 
            return
        
        if not messagebox.askyesno("Remove Files", f"Remove {len(selected_items)} selected item(s) from your local library list?\n\n(This only removes them from the list, it does not delete the actual files from your hard drive.)"):
            return

        removed_count = 0
        for item_id in selected_items:
            item = self.library_tree.item(item_id)
            title = item['values'][0]
            
            local_path = None
            for path, data in self.library_manager.local_library.items():
                if data["title"] == title:
                    local_path = path
                    break
            
            if local_path and local_path in self.library_manager.local_library:
                del self.library_manager.local_library[local_path]
                removed_count += 1
                
        if removed_count > 0:
            self.db.save_local_db(self.library_manager.local_library)
            self.refresh_library_ui()
            
            # Wipe panel after deletion
            self.clear_sidebar()
            self._selected_local_path = None
        else:
            messagebox.showinfo("Cloud Only", "This title is not currently in your downloaded local library.")

    def _on_display_metadata_ready(self, filepath, cover_path, authors, error_text):
        """Updates the side panel when the user clicks a book."""
        def update():
            # Strictly reject updates if the fetched path isn't the currently selected path
            if getattr(self, '_selected_local_path', None) != filepath:
                return
                
            self.author_label.config(text=authors)
            if cover_path and os.path.exists(cover_path):
                try:
                    img = Image.open(cover_path)
                    img.thumbnail((400, 400))
                    photo = ImageTk.PhotoImage(img)
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                except Exception:
                    self.cover_label.config(image="", text="Image Error")
            else:
                self.cover_label.config(image="", text=error_text)
        self.root.after(0, update)

    def set_sleep_timer(self, mode, value=0):

        if self._sleep_timer_id is not None:
            self.root.after_cancel(self._sleep_timer_id)
            
        if hasattr(self, 'sleep_menu_popup') and self.sleep_menu_popup.winfo_exists():
            self.sleep_menu_popup.destroy()

        try:
            val = int(value)
        except ValueError:
            return

        if mode == "off" or val <= 0:
            self.sleep_mode = None
            self.timer_btn.config(text="Sleep: Off")
            return
            
        self.sleep_mode = mode
        
        if mode == "time":
            self.sleep_timer_seconds = val * 60
            self.timer_btn.config(text=f"Sleep: {self.format_time(self.sleep_timer_seconds)}")
            self.sleep_timer_tick()
            
        elif mode == "chapters":
            self.sleep_chapters_remaining = val
            text = "End of Chapter" if val == 1 else f"Sleep: {val} ch"
            self.timer_btn.config(text=text)

    def sleep_timer_tick(self):
        if self.sleep_mode != "time":
            return
            
        if self.sleep_timer_seconds <= 0:
            self.sleep_mode = None
            self.timer_btn.config(text="Sleep: Off")
            
            if self.is_playing:
                self.logger.info("Sleep timer (minutes) finished. Pausing playback.")
                self.pause_audio()
            return
            
        self.sleep_timer_seconds -= 1
        self.timer_btn.config(text=f"Sleep: {self.format_time(self.sleep_timer_seconds)}")
        
        self._sleep_timer_id = self.root.after(1000, self.sleep_timer_tick)

    def on_sleep_timer_set(self, event=None):
        val = self.sleep_time_var.get()

        if self._sleep_timer_id is not None:
            self.root.after_cancel(self._sleep_timer_id)
            
        if val == "Off":
            self.sleep_timer_active = False
            self.timer_countdown_var.set("")
            return
            
        mins = int(val.replace("m", ""))
        self.sleep_timer_seconds = mins * 60
        self.sleep_timer_active = True

        self.timer_countdown_var.set(self.format_time(self.sleep_timer_seconds))
        
        self.sleep_timer_tick()

    def _on_grid_scroll(self, event):
        if self.current_view_mode != "grid":
            return

        if str(self.grid_canvas) not in str(event.widget):
            return

        num = getattr(event, 'num', 0)
        delta = getattr(event, 'delta', 0)

        if num == 4 or delta > 0:
            self.grid_canvas.yview_scroll(-1, "units")
        # Scroll Down
        elif num == 5 or delta < 0:
            self.grid_canvas.yview_scroll(1, "units")

    def master_play(self, event=None):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                if self.file_path:
                    self.play_chapter()
                else:
                    messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item or not self._selected_grid_item:
                if self.file_path:
                    self.play_chapter()
                else:
                    messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        status = item['values'][5]  

        if "Downloaded" not in status:
            messagebox.showinfo("Cloud Only", "This title has not been downloaded yet.")
            return

        local_path = None
        for path, data in self.library_manager.local_library.items():
            if data.get("title") == title:
                local_path = path
                break

        if not local_path or not os.path.exists(local_path):
            messagebox.showerror("File Error", "The audio file could not be found on your disk.")
            return

        if self.file_path == local_path:
            self.play_chapter()
            return

        self.stop_audio()

        self.metadata_manager.fetch_display_metadata(local_path) # Use local_path in master_play
        
        self.handle_action_on_selected("play")

    def load_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b")])
        if filepath:
            self.load_specific_file(filepath)

    def load_specific_file(self, filepath):
        self.file_path = filepath
        is_encrypted = filepath.endswith(".aax") or filepath.endswith(".aaxc")
        
        # --- 1. Clear ghost state immediately to prevent UI hanging on the old book ---
        self.chapters = []
        self.current_chapter_idx = 0
        self.current_play_time = 0.0
        self.chapter_duration = 0.0
        self.update_info() 
        
        self.dl_status_var.set("Analyzing...")
        self.root.update()
        
        local_data = self.library_manager.local_library.get(filepath, {})
        
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.file_path)
            if not success:
                self.dl_status_var.set("Verification Failed")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.file_path = ""
                return

        # --- 2. Database Chapter Caching ---
        cached_chapters = local_data.get("chapters")
        
        if cached_chapters:
            # Instant load from cache
            self.chapters = cached_chapters
        else:
            # First time load: Run ffprobe and cache the result
            self.dl_status_var.set(f"Extracting chapters: {os.path.basename(self.file_path)}")
            self.root.update()
            
            self.chapters = self.extract_chapters(self.file_path)
            
            local_data["chapters"] = self.chapters
            self.library_manager.local_library[filepath] = local_data
            self.library_manager.db.save_local_db(self.library_manager.local_library)

        self.dl_status_var.set(f"Ready: {os.path.basename(self.file_path)}")
        
        # --- 3. Moved dummy chapter generation here so it evaluates properly ---
        if not self.chapters:
            self.logger.info("No chapters found in file. Generating dummy master chapter.")
            duration_sec = local_data.get("duration_min", 0) * 60
            
            if duration_sec <= 0:
                try:
                    duration_sec = self.converter.get_duration(self.file_path)
                except Exception:
                    duration_sec = 86400 
                    
            self.chapters = [{
                "id": 0,
                "start_time": "0.000000",
                "end_time": str(duration_sec),
                "tags": {"title": "Full Audiobook"}
            }]
            
        # --- 4. Resume time-syncing logic ---
        abs_pos = None
        if "progress" in local_data and self.active_profile in local_data["progress"]:
            abs_pos = local_data["progress"][self.active_profile]
        elif "last_position" in local_data:
            abs_pos = local_data["last_position"]
            
        if abs_pos is not None:
            found_chap = 0
            for i, chap in enumerate(self.chapters):
                start = float(chap.get("start_time", 0))
                end = float(chap.get("end_time", 0))
                if start <= abs_pos < end:
                    found_chap = i
                    break
                if i == len(self.chapters) - 1 and abs_pos >= end:
                    found_chap = i
                    
            self.current_chapter_idx = found_chap
            self.current_play_time = max(0.0, abs_pos - float(self.chapters[found_chap].get("start_time", 0)))
        else:
            self.current_chapter_idx = local_data.get("last_chapter", 0)
            self.current_play_time = local_data.get("last_time", 0.0)
        
        if self.current_chapter_idx >= len(self.chapters):
            self.current_chapter_idx = 0
            self.current_play_time = 0.0
            
        self.update_info()
        
        chapter = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)

        self.metadata_manager.fetch_display_metadata(filepath)
        self.refresh_bookmarks_ui()

    def add_bookmark(self):
        if not self.file_path:
            messagebox.showwarning("No File", "Please load an audiobook first.")
            return

        was_playing = self.is_playing
        if was_playing:
            self.pause_audio()

        current_time = self.current_play_time
        chapter_idx = self.current_chapter_idx

        abs_time = current_time
        if self.chapters:
            abs_time += float(self.chapters[chapter_idx].get("start_time", 0))

        note = simpledialog.askstring("Add Bookmark", f"Add a note for {self.format_time(current_time)}:")

        if was_playing:
            self.is_paused = False
            self.resume_playback()
            
        if not note: return 

        local_data = self.library_manager.local_library.get(self.file_path, {})
        if "bookmarks" not in local_data:
            local_data["bookmarks"] = []
            
        local_data["bookmarks"].append({
            "chapter_idx": chapter_idx,
            "time": current_time,
            "abs_time": abs_time,
            "note": note
        })
        
        self.db.save_local_db(self.library_manager.local_library)
        self.refresh_bookmarks_ui()

    def refresh_bookmarks_ui(self):
        if not hasattr(self, 'bm_tree'): return
        
        for row in self.bm_tree.get_children():
            self.bm_tree.delete(row)
            
        target_path = getattr(self, '_selected_local_path', None)
        if not target_path: return
        
        local_data = self.library_manager.local_library.get(target_path, {})
        bookmarks = local_data.get("bookmarks", [])

        bookmarks.sort(key=lambda x: x.get("abs_time", 0))
        
        for idx, bm in enumerate(bookmarks):
            chap_idx = bm.get("chapter_idx", 0)

            chap_title = f"Chapter {chap_idx + 1}"
            
            # Use loaded chapters if it's the active file, otherwise use generic title
            if target_path == self.file_path and self.chapters and chap_idx < len(self.chapters):
                chap_title = self.chapters[chap_idx].get("tags", {}).get("title", chap_title)
                
            t_str = self.format_time(bm.get("time", 0))
            display_time = f"{chap_title} - {t_str}"

            self.bm_tree.insert("", "end", iid=str(idx), values=(display_time, bm.get("note", "")))

    def jump_to_bookmark(self, event=None):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        target_path = getattr(self, '_selected_local_path', None)
        if not target_path: return
        
        bookmarks = self.library_manager.local_library.get(target_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            bm = bookmarks[idx]
            
            # Load the file if jumping to a bookmark for a book not currently playing
            if target_path != self.file_path:
                self.load_specific_file(target_path)
            
            self.stop_audio()
            self.current_chapter_idx = bm.get("chapter_idx", 0)
            self.current_play_time = bm.get("time", 0.0)
            
            self.play_chapter()

    def delete_bookmark(self):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        target_path = getattr(self, '_selected_local_path', None)
        if not target_path: return
        
        bookmarks = self.library_manager.local_library.get(target_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            del bookmarks[idx]
            self.db.save_local_db(self.library_manager.local_library)
            self.refresh_bookmarks_ui()

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        
        
        local_data = self.library_manager.local_library.get(filepath, {})
        auth_bytes = self.auth_bytes.get().strip()
        
        drm_flags = self.api_client.get_drm_flags(
            filepath=filepath, 
            local_data=local_data, 
            active_profile=self.active_profile, 
            auth_bytes=auth_bytes, 
            data_dir=self.db.data_dir, 
            logger=self.logger
        )
        cmd.extend(drm_flags)
        
        
        cmd.extend(["-i", filepath, "-t", "0.1", "-f", "null", "-"])
        try:
            result = ProcessRunner.run_blocking(cmd)
            if result.returncode != 0:
                return False, result.stderr if result.stderr else "FFmpeg rejected the file."
            return True, ""
        except FileNotFoundError:
            return False, "FFmpeg is missing!"
        except Exception as e:
            return False, str(e)
        
    def start_convert_thread(self):
        if not self.chapters:
            messagebox.showinfo("No Chapters Found", "This file does not contain chapter markers. Defaulting to single file conversion.")
            split_choice = False
        else:
            split_choice = messagebox.askyesnocancel(
                "Conversion Options",
                "Do you want to split this audiobook into individual chapters?\n\n"
                "Yes = Split into multiple files (Export only)\n"
                "No = Keep as a single .m4b file\n"
                "Cancel = Abort"
            )

        if split_choice is None:
            return

        if split_choice:
            output_dir = filedialog.askdirectory(title=f"Select Folder to Extract Chapters For: {os.path.basename(self.file_path)}")
            if not output_dir: 
                return
            self.dl_status_var.set("Splitting into chapters... Please wait.")
            self.conversion_manager.split_book(self.file_path, output_dir, self.chapters)
        else:
            output_file = filedialog.asksaveasfilename(
                defaultextension=".m4b", 
                filetypes=[("M4B Audiobook", "*.m4b")], 
                initialfile=os.path.basename(self.file_path).replace(".aaxc", ".m4b").replace(".aax", ".m4b")
            )
            if not output_file: 
                return
            self.dl_status_var.set("Converting to .m4b... Please wait.")
            self.conversion_manager.convert_single(self.file_path, output_file, self.chapters)

    def start_convert_all_thread(self):
        to_convert = [path for path, data in self.library_manager.local_library.items() if data.get("format", "").upper() in ["AAX", "AAXC"]]
        
        if not to_convert:
            messagebox.showinfo("Convert All", "No AAX or AAXC files found to convert.")
            return
            
        required_bytes = sum(os.path.getsize(p) for p in to_convert if os.path.exists(p))
        if not self.system_manager.has_enough_disk_space(self.base_dir, required_bytes + (500 * 1024 * 1024)):
            required_gb = required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage", 
                f"Batch conversion requires at least {required_gb:.2f} GB of free space on your drive.\n\n"
                "Please free up space and try again."
            )
            return
            
        if not messagebox.askyesno("Convert All", f"Found {len(to_convert)} files to convert.\nThis will process sequentially in the background. Proceed?"):
            return
            
        self.conversion_manager.convert_batch(to_convert)

    def extract_chapters(self, filepath):
        metadata = self.converter.get_metadata_and_chapters(filepath)
        return metadata.get("chapters", [])

    def play_chapter(self):
        if not self.file_path or not self.chapters: return
        
        # 1. Update UI Info
        chapter = self.chapters[self.current_chapter_idx]
        self.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        self.update_info()

        # 2. Load the state into the controller
        self.playback.load_file(
            filepath=self.file_path,
            chapters=self.chapters,
            start_chapter_idx=self.current_chapter_idx,
            start_time=self.current_play_time
        )
        
        self.is_paused = False
        self.resume_playback()

    def resume_playback(self):
        drm_flags = self.api_client.get_drm_flags(self.file_path, self.library_manager.local_library.get(self.file_path, {}), self.active_profile, self.auth_bytes.get().strip(), self.db.data_dir) if self.file_path.endswith((".aax", ".aaxc")) else None
        
        # Make sure the controller has the latest UI settings before playing
        self.playback.set_speed(float(self.playback_speed.get().replace("x", "")))
        self.playback.set_volume(int(self.volume_var.get()))
        
        # Tell the controller to spin up FFplay
        self.playback.play(
            voice_boost=self.voice_boost_var.get(),
            skip_silence=self.skip_silence_var.get(),
            drm_flags=drm_flags
        )
        
        self.is_playing = True

    def pause_audio(self):
        if self.is_playing:
            self.playback.pause()
            self.is_playing = False
            self.is_paused = True
            
            # Sync the PC's memory with where the controller stopped
            self.current_play_time = self.playback.current_play_time
            
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            
            self.save_playback_state()

    def stop_audio(self):
        self.playback.stop()
        self.is_playing = False
        self.is_paused = False
        self.current_play_time = self.playback.current_play_time
        self.save_playback_state()

    def cancel_active_task(self):
        """Global button: Cancels all active imports, conversions, and downloads."""
        self.converter.cancel()
        self.library_manager.cancel_import() # None = Cancel All
        self.system_manager.clear_all_pending_imports(self.db.data_dir)
        self.download_manager.cancel_all()
        
        self.update_global_status("Cancelling all tasks...")
        self.update_global_progress(0)
        
        # Mark all UI rows as Canceling
        for task_id in list(self.queue_ui_elements.keys()):
            self._on_dl_status(task_id, "Canceling...", is_global=False)
            self._schedule_row_removal(task_id)
            
        self.root.after(2000, lambda: self.update_global_status("All tasks cancelled."))
        self.root.after(5000, self.reset_ui_if_idle)

    def seek_audio(self, offset):
        result = self.playback.seek(offset)
        
        if result == "NEXT_CHAPTER":
            self.next_chapter()
            return # next_chapter handles playback and UI resumption natively
            
        # Keep local UI state completely synced in case the controller crossed a chapter boundary
        self.current_chapter_idx = self.playback.current_chapter_idx
        self.current_play_time = self.playback.current_play_time
        self.chapter_duration = self.playback.chapter_duration
        
        # Update the Title/Info label in case the chapter changed
        self.update_info() 
        
        if result == "RESTART_PLAYBACK":
            self.resume_playback()
            
        # If paused, update the UI visually (if playing, the background tick will handle it)
        if self.is_paused:
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
            self.progress_var.set(percent)

    def on_progress_click(self, event):
        if not hasattr(self, 'chapter_duration') or self.chapter_duration <= 0:
            return
            
        # Calculate percentage based on where the mouse clicked relative to the width
        click_x = event.x
        bar_width = self.progress_bar.winfo_width()
        
        if bar_width > 0:
            percent = click_x / bar_width
            target_time = self.chapter_duration * percent
            
            # Since your seek method takes an offset, we calculate the difference
            offset = target_time - self.current_play_time
            self.seek_audio(offset)

    def on_speed_change(self, event=None):
        speed_val = float(self.playback_speed.get().replace("x", ""))
        self.playback.set_speed(speed_val)
        
        # FFplay requires a restart to change speed mid-stream
        if self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()

    def on_volume_change(self, event=None):
        self.playback.set_volume(int(self.volume_var.get()))
        # Only restart if we are on Mac/Linux (Windows changes it dynamically via pycaw)
        if os.name != 'nt' and self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def next_chapter(self):
        self.save_playback_state()
        
        # 1. Ask the controller to advance its internal state
        if self.playback.next_chapter():
            
            # 2. Sync the UI's variables to match the controller's new reality
            self.current_chapter_idx = self.playback.current_chapter_idx
            self.current_play_time = self.playback.current_play_time
            self.chapter_duration = self.playback.chapter_duration
            
            # 3. Handle Chapter Sleep Timer
            if self.sleep_mode == "chapters":
                self.sleep_chapters_remaining -= 1
                if self.sleep_chapters_remaining <= 0:
                    self.sleep_mode = None
                    self.timer_btn.config(text="Sleep: Off")
                    self.logger.info("Sleep timer (chapters) finished. Pausing playback.")
                    
                    self.is_paused = True
                    self.update_info()
                    curr_str = self.format_time(self.current_play_time)
                    dur_str = self.format_time(self.chapter_duration)
                    self.time_label.config(text=f"{curr_str} / {dur_str}")
                    self.progress_var.set(0)
                    return
                else:
                    self.timer_btn.config(text=f"Sleep: {self.sleep_chapters_remaining} ch")

            # 4. Resume playing the newly loaded chapter
            self.is_paused = False
            self.update_info()
            self.resume_playback()
            
        else:
            # Controller reported False (we were on the last chapter)
            self.stop_audio()
            self.stats_manager.add_stat("books_finished", 1)
            self.info_label.config(text="Finished Book")

    def prev_chapter(self):
        self.save_playback_state()
        
        # 1. Ask the controller to revert its state
        self.playback.prev_chapter()
        
        # 2. Sync the UI's variables
        self.current_chapter_idx = self.playback.current_chapter_idx
        self.current_play_time = self.playback.current_play_time
        self.chapter_duration = self.playback.chapter_duration
        
        # 3. Resume playing
        self.is_paused = False
        self.update_info()
        self.resume_playback()

    def update_info(self):
        if self.chapters:
            title = self.chapters[self.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")

    def reset_ui_if_idle(self):
        """Checks all background workers and only sets Idle if completely inactive."""
        is_importing = getattr(self.library_manager, '_is_importing', False) or not self.library_manager.import_queue.empty()
        is_downloading = getattr(self.download_manager, 'is_processing', False)
        is_converting = getattr(self.converter, 'current_process', None) is not None
        
        if not is_importing and not is_downloading and not is_converting:
            self.dl_status_var.set("Idle")
            self.dl_progress_var.set(0)

    def update_global_status(self, msg):
        if msg in ["Idle", ""]:
            self.reset_ui_if_idle()
        else:
            self.dl_status_var.set(msg)

    def update_global_progress(self, pct):
        if pct == 0:
            self.reset_ui_if_idle()
        else:
            self.dl_progress_var.set(pct)    