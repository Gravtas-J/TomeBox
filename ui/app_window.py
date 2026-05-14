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
except ImportError:
    messagebox.showerror("Missing Dependency", "Please run: pip install audible requests pillow tkinterdnd2")
    exit()
from core.utils.wake import keep
import pystray
from pystray import MenuItem as item
import sys
import socket
from api.audible_client import AudibleClient

from ui.components.dialogs import open_auth_window, show_achievement_toast, open_pairing_window, open_error_log_window, open_cover_modal
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
from core.controllers.stats_manager import StatsManager, ACHIEVEMENTS
from ui.bookmarks_presenter import BookmarksPresenter
from ui.action_router import ActionRouter
from ui.import_session import ImportSession


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

class UiState:
    def __init__(self, settings):
        # General / System
        self.minimize_to_tray = tk.BooleanVar(value=settings.get("minimize_to_tray", True))
        self.palette = tk.StringVar(value=settings.get("classic_palette", "light"))
        self.auth_bytes = tk.StringVar(value="") 
        self.locale = tk.StringVar(value="us")
        
        # Library View
        self.lib_count = tk.StringVar(value="Books found: 0")
        self.search = tk.StringVar()
        self.filter = tk.StringVar(value="All")
        self.shelf_filter = tk.StringVar(value="All Shelves")
        self.sort = tk.StringVar(value=settings.get("sort_pref", "Date Added (Newest)"))
        self.dl_status = tk.StringVar(value="Idle")
        self.dl_progress = tk.DoubleVar()
        self.error_btn = tk.StringVar(value="Errors (0)")
        self.api_health = tk.StringVar(value="API: Online")
        
        # Player Bar
        self.playback_progress = tk.DoubleVar()
        self.playback_speed = tk.StringVar(value="1.0x")  
        self.volume = tk.DoubleVar(value=100.0)
        self.timer_countdown = tk.StringVar(value="")
        self.voice_boost = tk.BooleanVar(value=settings.get("voice_boost", False))
        self.skip_silence = tk.BooleanVar(value=settings.get("skip_silence", False))
class AAXManagerApp:
    @property
    def file_path(self): return getattr(self, '_active_book_path', "")
    @file_path.setter
    def file_path(self, val): 
        self._active_book_path = val
        self.playback.file_path = val
    def __init__(self, root, base_dir):
        self.root = root
        self.root.title("TomeBox")
        self.root.geometry("1550x850")
        self.root.drop_target_register(DND_FILES)

        self.action_router = ActionRouter(self)
        self.bookmarks_presenter = BookmarksPresenter(self)
        self.import_session = ImportSession(self)

        self.root.dnd_bind('<<Drop>>', self.import_session.on_file_drop)
        self.base_dir = base_dir  
        self.current_sort_col = "Title"  
        self.current_sort_descending = False

        # 1. Initialize Database Manager FIRST
        self.db = DatabaseManager(self.base_dir)
        self.api_client = AudibleClient()
        self.library_manager = LibraryManager(self.db, self.api_client, self.base_dir)

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
        self.ui_state = UiState(self.settings)
        self.logger = setup_logger(self.base_dir, debug_mode=self.settings.get("debug_mode", False))
        self.logger.info("=== TomeBox Application Started ===")
        self.covers_dir = os.path.join(self.base_dir, "covers")
        os.makedirs(self.covers_dir, exist_ok=True)
        self.root.after(200, apply_taskbar_icon)
        # 4. Apply Profile Variables
        self.active_profile = self.settings.get("active_profile", "Main")
        
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
            callbacks={}
        )
        self.metadata_manager = MetadataManager(
            api_client=self.api_client,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            thread_pool=self.thread_pool,
            callbacks={}
        )
        self.conversion_manager = ConversionManager(
            converter=self.converter,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            thread_pool=self.thread_pool,
            get_drm_flags_cb=lambda path: self.api_client.get_drm_flags(
                path, self.library_manager.local_library.get(path, {}), self.active_profile, self.ui_state.auth_bytes.get().strip(), self.db.data_dir, self.logger
            ),
            callbacks={}
        )
        self.playback = PlaybackController(
            logger=self.logger,
            on_tick_cb=self._on_playback_tick,
            on_chapter_end_cb=lambda: self.root.after(0, self.next_chapter),
            on_error_cb=self._on_playback_error
        )
        # Load saved audio device
        saved_device = self.settings.get("audio_device", "System Default")
        self.playback.set_audio_device(saved_device)

        self.system_manager = SystemManager(logger=self.logger)
        self.system_manager.enforce_single_instance(on_wake_callback=lambda: self.root.after(0, self.bring_to_front))

        self.player_process = None
        self.failed_tasks = []        

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
        
        
        # Sleep Timer State
        self.sleep_mode = None
        self.sleep_timer_seconds = 0
        self.sleep_chapters_remaining = 0
        self._sleep_timer_id = None
        self.sleep_timer_active = False
        
        # Directory Paths
        self.default_download_dir = self.settings.get("download_dir", self.base_dir)
        
        # Security: The download directory doubles as the safe network import boundary
        self.import_root = self.settings.get("download_folder", self.default_download_dir)
        if not self.import_root:
            # Fallback to the user's download directory, or the app base directory
            self.import_root = self.settings.get("download_folder", self.default_download_dir)
            self.settings["import_root"] = self.import_root
            self.db.save_settings(self.settings)
        
        # Background Workers
        self._last_disk_save_time = 0.0

        # Timers & UI Flags
        self.tray_icon = None
        self.browser_login_btn = None

        

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.handle_window_close)

        def _focus_search():
            self.search_entry.focus_force() 
            self.search_entry.icursor(tk.END) 
        self.root.after(200, _focus_search)

        self.setup_tray_icon()
        self.root.after(500, self.auto_load_auth)
        self.root.after(900000, self.run_background_sync)
        self.root.after(3000, lambda: self.library_manager.run_background_library_scan(
            self.converter, self.active_profile, self.logger, self.thread_pool, 
            on_refresh_cb=lambda: self.root.after(0, self.refresh_library_ui)
        ))
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
        
        self.achievements = ACHIEVEMENTS
        self.root.after(1500, self.import_session._prompt_resume_imports)

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

    def cancel_task(self, task_id):
        """Unified method to cancel either an active import OR an active download from the queue drawer."""
        if str(task_id).startswith("import_"):
            self.library_manager.cancel_import(task_id)
            
            # Only terminate the FFmpeg process if this specific task is the one currently running
            if getattr(self.library_manager, 'active_task_id', None) == task_id:
                self.converter.cancel()
                
            self.action_router.on_dl_status(task_id, "Canceling...", is_global=False)
        else:
            self.download_manager.cancel_download(task_id)

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

    def update_api_health(self, message, is_error=False):
        """Thread-safe update of the API health status label."""
        def update():
            if hasattr(self, 'api_health_var'):
                self.ui_state.api_health.set(f"API: {message}")
            if is_error:
                # Auto-reset the health indicator back to Online after the 60s cooldown expires
                self.root.after(60000, lambda: self.ui_state.api_health.set("API: Online"))
        self.root.after(0, update)

    def _on_playback_tick(self, current_time, total_time, real_time_delta):
        """Called twice a second by the PlaybackController."""
        def update_ui():
            # Update Progress Bar & Labels
            percent = (current_time / total_time) * 100 if total_time > 0 else 0
            self.ui_state.playback_progress.set(percent)
            
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
            try:
                self.refresh_library_ui()
            except Exception as e:
                import traceback; traceback.print_exc()
            if added_count > 0:
                self.ui_state.dl_status.set(f"Successfully imported {added_count} files.")
            elif total_found > 0:
                self.ui_state.dl_status.set("Files already in library.")
            else:
                self.ui_state.dl_status.set("No valid audiobooks found to import.")
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
        
    def add_firewall_rule_prompt(self):
        import os
        if os.name != 'nt':
            messagebox.showinfo("Not Applicable", "Firewall management is only automated on Windows.")
            return
            
        # Check if it's already there before bothering them with a UAC prompt
        if self.system_manager._is_firewall_rule_installed():
            messagebox.showinfo("Already Installed", "The 'TomeBox Web Server' firewall rule is already active on your system.")
            return
            
        if messagebox.askyesno(
            "Add Firewall Rule", 
            "This will require Administrator privileges to add the 'TomeBox Web Server' rule to Windows Defender Firewall.\n\n"
            "This allows your mobile device to communicate with the TomeBox companion server over your local Wi-Fi network.\n\n"
            "Do you want to continue?"
        ):
            success = self.system_manager._add_firewall_rule()
            if success:
                messagebox.showinfo("Success", "Firewall rule added successfully.")
            else:
                messagebox.showerror("Action Failed", "Failed to add the firewall rule. You may have declined the admin prompt.")

    def remove_firewall_rule_prompt(self):
        import os
        if os.name != 'nt':
            messagebox.showinfo("Not Applicable", "Firewall management is only automated on Windows.")
            return
            
        if messagebox.askyesno(
            "Remove Firewall Rule", 
            "This will require Administrator privileges to remove the 'TomeBox Web Server' rule from Windows Defender Firewall.\n\n"
            "If you restart the Web Server later, you will be prompted to approve the rule again.\n\n"
            "Do you want to continue?"
        ):
            success = self.system_manager.remove_firewall_rule()
            if success:
                messagebox.showinfo("Success", "Firewall rule removed successfully.")
            else:
                messagebox.showerror("Action Failed", "Failed to remove the firewall rule. You may have declined the admin prompt, or the rule did not exist.")

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
        self.ui_state.auth_bytes.set("")
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
            lambda msg: self.root.after(0, lambda: self.ui_state.dl_status.set(msg)), 
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
        self.context_menu.add_command(label="✏️ Edit Metadata", command=lambda: self.handle_action_on_selected("edit"))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🔗 Match Local File", command=lambda: self.handle_action_on_selected("match_local"))

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

        if 0 <= target_idx < len(self.playback.chapters):
            # Close the window
            if hasattr(self, 'chapter_win') and self.chapter_win:
                self.chapter_win.destroy()
            
            # Stop current playback and save state
            self.stop_audio()
                
            # Set the new target
            self.playback.current_chapter_idx = target_idx
            self.playback.current_play_time = 0.0
            
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
                
                self.cover_label.bind("<Button-1>", lambda e, a=asin, t=title, p=cover_path: open_cover_modal(self, a, t, explicit_path=p))
                
            except Exception:
                self.cover_label.config(image="", text=title)
                self.cover_label.unbind("<Button-1>") # Unbind to prevent crashes on broken images
        elif hasattr(self, 'cover_label'):
            self.cover_label.config(image="", text=title)
            self.cover_label.unbind("<Button-1>") # Unbind if there is no cover
            
        self.bookmarks_presenter.refresh_bookmarks_ui()

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

        from ui.components.dialogs import open_shelf_management_window
        open_shelf_management_window(self, title, asin)

    def save_tray_setting(self):
        self.settings["minimize_to_tray"] = self.ui_state.minimize_to_tray.get()
        self.db.save_settings(self.settings)

    def on_filter_change(self):

        if self.playback.is_playing:
            self.pause_audio()
            self.playback.is_paused = False
            self.resume_playback()

    def handle_window_close(self):
        if self.ui_state.minimize_to_tray.get():
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
            import time
            try:
                if hasattr(self, 'db') and hasattr(self.db, 'conn') and self.db.conn:
                    self.db.conn.close()
                elif hasattr(self, 'library_manager') and hasattr(self.library_manager, 'db'):
                    self.library_manager.db.conn.close()
                time.sleep(0.2) # Give the OS a moment to flush the WAL file to disk
            except Exception as e:
                print(f"Error closing database gracefully: {e}")
            if os.name == 'nt':
                # /T flag = "Tree Kill" (Kills this process and everything it spawned)
                ProcessRunner.run_async(['taskkill', '/F', '/T', '/PID', str(os.getpid())])
            else:
                # Mac/Linux immediate hard exit
                os._exit(0)

    def save_playback_state(self):
        state = self.playback.get_current_state()
        if state:
            state["file_path"] = self.file_path
            self.library_manager.save_playback_state(state, self.active_profile)
    
    def sync_playhead_from_remote(self, abs_position):
        """Called by the web server when the phone updates the current book's time."""
        try:
            # Let the playback controller handle the chapter/time math
            if self.playback.seek_to_absolute(abs_position):
                
                
                # Visually move the progress bar on the PC screen
                if hasattr(self, 'progress_var') and self.playback.chapters:
                    total_duration = float(self.playback.chapters[-1].get("end_time", 0))
                    if total_duration > 0:
                        self.ui_state.playback_progress.set((abs_position / total_duration) * 100)
                        
        except Exception as e:
            self.logger.error(f"Failed to sync remote playhead: {e}")
            
    def cue_last_played(self):
        last_path = self.settings.get(f"last_played_{self.active_profile}")
        if last_path and last_path in self.library_manager.local_library and os.path.exists(last_path):
            self.load_specific_file(last_path)
    
    def set_download_folder(self):
        self.root.attributes('-topmost', True)
        directory = filedialog.askdirectory(parent=self.root, title="Select Default Download Folder")
        self.root.attributes('-topmost', False)
        
        if directory:
            self.default_download_dir = directory
            self.settings["download_folder"] = directory
            self.import_root = directory 
            
            self.db.save_settings(self.settings)
            messagebox.showinfo("Folder Saved", f"Default download folder updated to:\n{directory}", parent=self.root)

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
            self.import_session.toggle_queue_drawer(True)
            
            for item in missing_items:
                self.add_queue_ui_row(item["asin"], item["title"])
                
            self.download_manager.queue_batch(missing_items, save_dir)

    def apply_classic_palette(self, palette_name):
        apply_theme(self, palette_name)

    def toggle_library_view(self):
        if self.current_view_mode == "list":
            self.current_view_mode = "grid"
            self.view_btn.config(text="List View")
            
            if hasattr(self, 'cover_toggle'):
                self.cover_toggle.pack(side=tk.RIGHT, padx=5)
            if hasattr(self, 'sort_label'):
                self.sort_label.pack(side=tk.LEFT, padx=(10, 5))
                self.sort_combo.pack(side=tk.LEFT)
            
            self.library_tree.grid_remove()
            self.h_scroll.grid_remove()
            
            if self.library_manager.cloud_items or self.library_manager.local_library:
                self.grid_canvas.grid(row=0, column=0, sticky="nsew")
            
            self.v_scroll.config(command=self.grid_canvas.yview)
            self.grid_canvas.config(yscrollcommand=self.v_scroll.set)
        else:
            self.current_view_mode = "list"
            self.view_btn.config(text="Grid View")
            
            if hasattr(self, 'cover_toggle'):
                self.cover_toggle.pack_forget()
            if hasattr(self, 'sort_label'):
                self.sort_label.pack_forget()
                self.sort_combo.pack_forget()
            
            self.grid_canvas.grid_remove()
            
            if self.library_manager.cloud_items or self.library_manager.local_library:
                self.library_tree.grid(row=0, column=0, sticky="nsew")
                self.h_scroll.grid(row=1, column=0, sticky="ew")
            
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
            title, authors, series_str, duration_str, asin, status, row_path, date_str = row_data

            outer_card = tk.Frame(self.grid_inner, bg=default_bg)
            outer_card.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)

            card = tk.Frame(outer_card, bg=default_bg, width=170, height=240, bd=0, highlightthickness=0)
            card.pack_propagate(False) 
            card.pack(padx=2, pady=2) 
            
            is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
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
                
            
            img_label = tk.Label(card, image=img_obj, text="No Cover" if not img_obj else "", bg=default_bg, fg=default_fg, bd=0, highlightthickness=0, takefocus=0, cursor="hand2")
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

    def refresh_library_ui(self, *args):
        # 1. Clear the current UI
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        search_query = self.ui_state.search.get()
        current_filter = self.ui_state.filter.get()
        current_shelf = self.ui_state.shelf_filter.get()

        # 2. Ask the Controller for the data
        filtered_rows, shelf_list = self.library_manager.get_view_data(
            search_query=search_query,
            filter_type=current_filter,
            shelf_filter=current_shelf
        )

        if hasattr(self, 'sort_var'):
            sort_pref = self.ui_state.sort.get()
            
            def get_sort_key(row):
                title, authors, series_str, duration_str, asin, status, row_path, date_str = row
                
                if sort_pref == "Title (A-Z)":
                    return title.lower()
                elif sort_pref == "Author (A-Z)":
                    return authors.lower()
                else: # Date Added
                    # Check local database for physical files
                    if row_path and row_path in self.library_manager.local_library:
                        return self.library_manager.local_library[row_path].get("date_added", 0)
                    # Cloud-only items drop to the bottom of the "Newest" list
                    return 0 
                    
            is_reverse = sort_pref == "Date Added (Newest)"
            filtered_rows.sort(key=get_sort_key, reverse=is_reverse)


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
                    title, authors, series_str, duration_str, asin, status, row_path, date_str = row
                    
                    # Evaluate Health
                    tags = ()
                    is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
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
            
        self.ui_state.lib_count.set(f"Books found: {total_books}")
        
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
        is_playlist = False
        for path, data in self.library_manager.local_library.items():
            if data["title"] == title:
                local_path = path
                is_playlist = data.get("is_playlist", False)
                break
        
        if action_type == "match_local":
            self.match_local_file_to_cloud(title, asin)
            return
        
        if local_path:
            # Bypass file check for virtual playlists
            if not is_playlist and not os.path.exists(local_path):
                messagebox.showerror("File Missing", "The file was deleted or moved. Please remove it from the list and re-download.")
                return
                
            if action_type == "scrape":
                self.start_scrape_thread(local_path)
                return
            elif action_type == "edit":
                from ui.components.dialogs import open_manual_metadata_window
                open_manual_metadata_window(self, local_path)
                return
            elif action_type == "convert":
                if is_playlist:
                    messagebox.showinfo("Not Applicable", "Playlists are already split into individual files.")
                    return
                self.start_convert_thread(target_path=local_path)
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
                self.import_session.add_queue_ui_row(asin, title)
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
        """Re-routes the scraper button to the new unified Match UI."""
        from ui.components.dialogs import open_match_to_audible_window
        open_match_to_audible_window(self, filepath)

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

            if val == "N/A":
                return "0000-00-00"
                
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

    def match_local_file_to_cloud(self, title, asin):
        """Manually associates a local file on the hard drive with a Cloud-only item."""
        filepath = filedialog.askopenfilename(
            title=f"Select local file for: {title}",
            filetypes=[("Audiobooks", "*.m4b *.mp3 *.aax *.aaxc")]
        )
        if not filepath:
            return
            
        # 1. Grab the richest metadata available from the cloud cache
        cloud_data = None
        for item in self.library_manager.cloud_items:
            if item.get("asin") == asin or item.get("title") == title:
                cloud_data = item
                break
        
        if not cloud_data:
            cloud_data = {"title": title, "asin": asin}
            
        # 2. Parse Authors
        raw_authors = cloud_data.get("authors", [])
        authors_str = "Unknown Author"
        if raw_authors:
            authors_str = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
        # 3. Parse Series
        series_str = ""
        raw_series = cloud_data.get("series", [])
        if raw_series:
            series_parts = []
            for s in raw_series:
                s_title = s.get("title", "").strip()
                s_seq = str(s.get("sequence", "")).strip()
                if s_title:
                    if s_seq and s_seq != "None":
                        series_parts.append(f"{s_title}, Book {s_seq}")
                    else:
                        series_parts.append(s_title)
            if series_parts:
                series_str = " / ".join(series_parts)

        # 4. Map data to the file
        local_data = self.library_manager.local_library.get(filepath, {})
        local_data["title"] = cloud_data.get("title", title)
        local_data["asin"] = asin
        local_data["authors"] = authors_str
        if series_str:
            local_data["series"] = series_str
            
        local_data["format"] = os.path.splitext(filepath)[1].replace(".", "").upper()
        local_data["path"] = filepath
        
        duration_min = cloud_data.get("runtime_length_min")
        if duration_min:
            local_data["duration_min"] = duration_min
            
        # 5. Save to database
        self.library_manager.local_library[filepath] = local_data
        self.library_manager.db.save_local_db(self.library_manager.local_library)
        
        # 6. Fetch the cover art and refresh UI
        self.metadata_manager.sync_missing_covers(on_complete_cb=lambda: self.root.after(0, self.refresh_library_ui))
        self.refresh_library_ui()
        messagebox.showinfo("Match Successful", f"Successfully linked '{title}' to:\n\n{filepath}")

    def auto_load_auth(self):
        self.logger.info("DEBUG: auto_load_auth fired from startup timer.")
        if self.api_client.load_auth_from_file(self.auth_save_path):
            activation_bytes = self.api_client.get_activation_bytes()
            self.ui_state.auth_bytes.set(activation_bytes)
            self.logger.info(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
            
            # Reset filters before fetch so UI shows everything when worker completes
            self.ui_state.filter.set("All")
            self.ui_state.shelf_filter.set("All Shelves")
            self.ui_state.search.set("")
            
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
                self.ui_state.auth_bytes.set(activation_bytes)
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
        self.thread_pool.submit(self.browser_login_worker, self.ui_state.locale.get())

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
                
                self.root.after(0, self.ui_state.auth_bytes.set, activation_bytes)
                self.logger.info(f"Activation Bytes Received: {activation_bytes}")
                
                self.api_client.save_auth_to_file(self.auth_save_path)
                self.logger.info(f"Session saved locally to {self.auth_save_path}")

                self.root.after(0, lambda: messagebox.showinfo("Success", "Connected to Audible!"))
                self.ui_state.filter.set("All")
                self.ui_state.shelf_filter.set("All Shelves")
                self.ui_state.search.set("")
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
        
        self.ui_state.dl_status.set("Fetching data from Amazon... Please wait.")
        
        self.thread_pool.submit(self.fetch_library_worker)

    def fetch_library_worker(self):
        try:
            self.logger.info("Querying Audible Library API...")
            
            # 1. Delegate entirely to the LibraryManager (this handles the API call AND saving the cache)
            self.library_manager.fetch_cloud_library()
            
            self.logger.info(f"Successfully retrieved {len(self.library_manager.cloud_items)} library items.")

            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda: self.action_router.reset_ui_if_idle())

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
            self.root.after(0, self.action_router.reset_ui_if_idle)

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
            
            if self.playback.is_playing:
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
            self.ui_state.timer_countdown.set("")
            return
            
        mins = int(val.replace("m", ""))
        self.sleep_timer_seconds = mins * 60
        self.sleep_timer_active = True

        self.ui_state.timer_countdown.set(self.format_time(self.sleep_timer_seconds))
        
        self.sleep_timer_tick()

    def _on_global_scroll(self, event):
        """A universal scroll handler that intelligently scrolls whatever canvas the mouse is hovering over."""
        widget = event.widget
        
        # Safeguard for older OS/Tkinter versions that pass widget paths as strings
        if isinstance(widget, str):
            try:
                widget = self.root.nametowidget(widget)
            except Exception:
                return

        target_canvas = None
        current = widget
        
        # Walk up the widget hierarchy to find a scrollable Canvas
        while current:
            # Do NOT hijack scrolling if hovering over native scrollable widgets
            if isinstance(current, (ttk.Treeview, tk.Text, tk.Listbox)):
                return
                
            if isinstance(current, tk.Canvas):
                # Ensure we only scroll the main library grid if it is the actively visible view
                if hasattr(self, 'grid_canvas') and current == self.grid_canvas:
                    if self.current_view_mode != "grid":
                        return 
                target_canvas = current
                break
            
            try:
                current = current.master
            except AttributeError:
                break

        if not target_canvas:
            return

        num = getattr(event, 'num', 0)
        delta = getattr(event, 'delta', 0)

        # Standardized directional math for Windows (-120/+120) and macOS (-1/+1)
        if num == 4 or delta > 0:
            target_canvas.yview_scroll(-1, "units")
        elif num == 5 or delta < 0:
            target_canvas.yview_scroll(1, "units")

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

        is_playlist = False
        local_path = None
        for path, data in self.library_manager.local_library.items():
            if data.get("title") == title:
                local_path = path
                is_playlist = data.get("is_playlist", False)
                break

        if not local_path:
            messagebox.showerror("File Error", "The audio file could not be found on your disk.")
            return

        if not is_playlist and not os.path.exists(local_path):
            messagebox.showerror("File Error", "The audio file could not be found on your disk.")
            return

        if self.file_path == local_path:
            self.play_chapter()
            return

        self.stop_audio()

        self.metadata_manager.fetch_display_metadata(local_path) # Use local_path in master_play
        
        self.handle_action_on_selected("play")

    def manage_library_folders_prompt(self):
        """Opens a UI dialog to manage the background scanner's watched folders."""
        win = tk.Toplevel(self.root)
        win.title("Manage Library Folders")
        win.geometry("500x350")
        win.transient(self.root)
        
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        win.configure(bg=bg_color)
        
        main_frame = ttk.Frame(win, padding=15)
        main_frame.pack(fill="both", expand=True)
        
        ttk.Label(main_frame, text="Watched Library Folders", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Label(main_frame, text="TomeBox will automatically scan these folders for new audiobooks in the background.", font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 10))
        
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        folder_listbox = tk.Listbox(list_frame, bg="#2b2b2b", fg="white", selectbackground="#4a90e2")
        folder_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=folder_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        folder_listbox.config(yscrollcommand=scrollbar.set)
        
        # Load current folders
        current_folders = self.settings.get("library_folders", [])
        for f in current_folders:
            folder_listbox.insert(tk.END, f)
            
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x")
        
        def add_folder():
            folder = filedialog.askdirectory(parent=win, title="Select Library Folder")
            if folder and folder not in folder_listbox.get(0, tk.END):
                folder_listbox.insert(tk.END, os.path.normpath(folder))
                
        def remove_folder():
            selected = folder_listbox.curselection()
            if selected:
                folder_listbox.delete(selected[0])
                
        def save_folders():
            folders = list(folder_listbox.get(0, tk.END))
            self.settings["library_folders"] = folders
            self.db.save_settings(self.settings)
            
            # Trigger an immediate scan of the new folders!
            self.library_manager.run_background_library_scan(
                self.converter, self.active_profile, self.logger, self.thread_pool, 
                on_refresh_cb=lambda: self.root.after(0, self.refresh_library_ui)
            )
            win.destroy()
            
        ttk.Button(btn_frame, text="Add Folder", command=add_folder).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Remove Selected", command=remove_folder).pack(side=tk.LEFT)
        
        ttk.Button(btn_frame, text="Save & Scan", command=save_folders).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 5))

    def load_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b")])
        if filepath:
            self.load_specific_file(filepath)

    def load_specific_file(self, filepath):
        self.file_path = filepath
        is_encrypted = filepath.endswith(".aax") or filepath.endswith(".aaxc")
        
        # --- 1. Clear ghost state immediately to prevent UI hanging on the old book ---
        self.playback.chapters = []
        self.playback.current_chapter_idx = 0
        self.playback.current_play_time = 0.0
        self.playback.chapter_duration = 0.0
        self.update_info() 
        
        self.ui_state.dl_status.set("Analyzing...")
        self.root.update()
        
        local_data = self.library_manager.local_library.get(filepath, {})
        
        if hasattr(self, 'player_cover_lbl'):
            asin = local_data.get("asin")
            cover_path = None
            if asin:
                cp = os.path.join(self.covers_dir, f"{asin}.jpg")
                if os.path.exists(cp):
                    cover_path = cp
                    
            if cover_path:
                try:
                    from PIL import Image, ImageTk
                    thumb = Image.open(cover_path)
                    thumb.thumbnail((45, 45), Image.Resampling.LANCZOS)
                    thumb_photo = ImageTk.PhotoImage(thumb)
                    self.player_cover_lbl.config(image=thumb_photo, width=45, height=45)
                    self.player_cover_lbl.image = thumb_photo # Prevent garbage collection
                except Exception:
                    self.player_cover_lbl.config(image="", width=0, height=0)
            else:
                self.player_cover_lbl.config(image="", width=0, height=0)
            if hasattr(self, 'btn_compact'):
                self.btn_compact.config(state=tk.NORMAL)
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.file_path)
            if not success:
                self.ui_state.dl_status.set("Verification Failed")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.file_path = ""
                return

        # --- 2. Database Chapter Caching ---
        cached_chapters = local_data.get("chapters")
        
        if cached_chapters:
            # Instant load from cache
            self.playback.chapters = cached_chapters
        else:
            # First time load: Run ffprobe and cache the result
            self.ui_state.dl_status.set(f"Extracting chapters: {os.path.basename(self.file_path)}")
            self.root.update()
            
            self.playback.chapters = self.extract_chapters(self.file_path)
            
            local_data["chapters"] = self.playback.chapters
            self.library_manager.local_library[filepath] = local_data
            self.library_manager.db.save_local_db(self.library_manager.local_library)

        self.ui_state.dl_status.set(f"Ready: {os.path.basename(self.file_path)}")
        
        # --- 3. Moved dummy chapter generation here so it evaluates properly ---
        if not self.playback.chapters:
            self.logger.info("No chapters found in file. Generating dummy master chapter.")
            duration_sec = local_data.get("duration_min", 0) * 60
            
            if duration_sec <= 0:
                try:
                    duration_sec = self.converter.get_duration(self.file_path)
                except Exception:
                    duration_sec = 86400 
                    
            self.playback.chapters = [{
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
            for i, chap in enumerate(self.playback.chapters):
                start = float(chap.get("start_time", 0))
                end = float(chap.get("end_time", 0))
                if start <= abs_pos < end:
                    found_chap = i
                    break
                if i == len(self.playback.chapters) - 1 and abs_pos >= end:
                    found_chap = i
                    
            self.playback.current_chapter_idx = found_chap
            self.playback.current_play_time = max(0.0, abs_pos - float(self.playback.chapters[found_chap].get("start_time", 0)))
        else:
            self.playback.current_chapter_idx = local_data.get("last_chapter", 0)
            self.playback.current_play_time = local_data.get("last_time", 0.0)
        
        if self.playback.current_chapter_idx >= len(self.playback.chapters):
            self.playback.current_chapter_idx = 0
            self.playback.current_play_time = 0.0
            
        self.update_info()
        
        chapter = self.playback.chapters[self.playback.current_chapter_idx]
        self.playback.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        
        curr_str = self.format_time(self.playback.current_play_time)
        dur_str = self.format_time(self.playback.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")
        percent = (self.playback.current_play_time / self.playback.chapter_duration) * 100 if self.playback.chapter_duration > 0 else 0
        self.ui_state.playback_progress.set(percent)

        self.metadata_manager.fetch_display_metadata(filepath)
        self.bookmarks_presenter.refresh_bookmarks_ui()

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        
        
        local_data = self.library_manager.local_library.get(filepath, {})
        auth_bytes = self.ui_state.auth_bytes.get().strip()
        
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
        
    def start_convert_thread(self, target_path=None):
        # Fallback for backwards compatibility
        if not target_path:
            target_path = self.file_path
            
        if not target_path:
            return

        local_data = self.library_manager.local_library.get(target_path, {})
        db_chapters = local_data.get("chapters", [])
        
        # Safely extract chapters on the fly if the DB doesn't have them yet
        if not db_chapters:
            self.ui_state.dl_status.set(f"Extracting chapters: {os.path.basename(target_path)}")
            self.root.update()
            
            db_chapters = self.extract_chapters(target_path)
            
            if db_chapters:
                local_data["chapters"] = db_chapters
                self.library_manager.local_library[target_path] = local_data
                self.library_manager.db.save_local_db(self.library_manager.local_library)
            
            self.ui_state.dl_status.set("Idle")

        # Identify if the chapters are real or just the auto-generated dummy
        has_real_chapters = False
        if db_chapters:
            if len(db_chapters) > 1:
                has_real_chapters = True
            elif len(db_chapters) == 1 and db_chapters[0].get("tags", {}).get("title") != "Full Audiobook":
                has_real_chapters = True

        if not has_real_chapters:
            messagebox.showinfo("No Chapters Found", "This file does not contain chapter markers to split.")
            return

        # --- DIRECT TO SPLIT ---
        output_dir = filedialog.askdirectory(title=f"Select Folder to Extract Chapters For: {os.path.basename(target_path)}")
        if not output_dir: 
            return
            
        self.ui_state.dl_status.set("Splitting into chapters... Please wait.")
        self.conversion_manager.split_book(target_path, output_dir, db_chapters)

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
        if not self.file_path or not self.playback.chapters: return
        
        # 1. Update UI Info
        chapter = self.playback.chapters[self.playback.current_chapter_idx]
        self.playback.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
        self.update_info()
        
        # 2. Resume playback
        self.playback.is_paused = False
        self.resume_playback()

    def resume_playback(self):
        local_data = self.library_manager.local_library.get(self.file_path, {})
        is_playlist = local_data.get("is_playlist", False)
        
        if is_playlist and self.playback.chapters:
            chapter = self.playback.chapters[self.playback.current_chapter_idx]
            physical_file = chapter.get("file_path", self.file_path)
            self.playback.file_path = physical_file
            self.playback.is_playlist = True
        else:
            self.playback.file_path = self.file_path
            self.playback.is_playlist = False

        drm_flags = self.api_client.get_drm_flags(self.file_path, local_data, self.active_profile, self.ui_state.auth_bytes.get().strip(), self.db.data_dir) if self.file_path.endswith((".aax", ".aaxc")) else None
        
        # Make sure the controller has the latest UI settings before playing
        self.playback.set_speed(float(self.ui_state.playback_speed.get().replace("x", "")))
        self.playback.set_volume(int(self.ui_state.volume.get()))
        
        # Tell the controller to spin up FFplay
        self.playback.play(
            voice_boost=self.ui_state.voice_boost.get(),
            skip_silence=self.ui_state.skip_silence.get(),
            drm_flags=drm_flags
        )
        
        self.playback.is_playing = True

    def pause_audio(self):
        if self.playback.is_playing:
            self.playback.pause()
            self.playback.is_playing = False
            self.playback.is_paused = True
            
            curr_str = self.format_time(self.playback.current_play_time)
            dur_str = self.format_time(self.playback.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            
            self.save_playback_state()

    def stop_audio(self):
        self.playback.stop()
        self.playback.is_playing = False
        self.playback.is_paused = False
        self.save_playback_state()

    def cancel_active_task(self):
        """Global button: Cancels all active imports, conversions, and downloads."""
        self.converter.cancel()
        self.library_manager.cancel_import() # None = Cancel All
        self.system_manager.clear_all_pending_imports(self.db.data_dir)
        self.download_manager.cancel_all()
        
        self.action_router.update_global_status("Cancelling all tasks...")
        self.action_router.update_global_progress(0)
        
        # Mark all UI rows as Canceling
        for task_id in list(self.queue_ui_elements.keys()):
            self.action_router.on_dl_status(task_id, "Canceling...", is_global=False)
            self.action_router._schedule_row_removal(task_id)
            
        self.root.after(2000, lambda: self.action_router.update_global_status("All tasks cancelled."))
        self.root.after(5000, self.action_router.reset_ui_if_idle)

    def seek_audio(self, offset):
        result = self.playback.seek(offset)
        
        if result == "NEXT_CHAPTER":
            self.next_chapter()
            return # next_chapter handles playback and UI resumption natively
        
        # Update the Title/Info label in case the chapter changed
        self.update_info() 
        
        if result == "RESTART_PLAYBACK":
            self.resume_playback()
            
        # If paused, update the UI visually (if playing, the background tick will handle it)
        if self.playback.is_paused:
            curr_str = self.format_time(self.playback.current_play_time)
            dur_str = self.format_time(self.playback.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.playback.current_play_time / self.playback.chapter_duration) * 100 if self.playback.chapter_duration > 0 else 0
            self.ui_state.playback_progress.set(percent)

    def on_progress_click(self, event):
        if not hasattr(self, 'chapter_duration') or self.playback.chapter_duration <= 0:
            return
            
        # Calculate percentage based on where the mouse clicked relative to the width
        click_x = event.x
        bar_width = self.progress_bar.winfo_width()
        
        if bar_width > 0:
            percent = click_x / bar_width
            target_time = self.playback.chapter_duration * percent
            
            # Since your seek method takes an offset, we calculate the difference
            offset = target_time - self.playback.current_play_time
            self.seek_audio(offset)

    def on_speed_change(self, event=None):
        speed_val = float(self.ui_state.playback_speed.get().replace("x", ""))
        self.playback.set_speed(speed_val)
        
        # FFplay requires a restart to change speed mid-stream
        if self.playback.is_playing:
            self.pause_audio()
            self.playback.is_paused = False
            self.resume_playback()

    def on_volume_change(self, event=None):
        self.playback.set_volume(int(self.ui_state.volume.get()))
        # Only restart if we are on Mac/Linux (Windows changes it dynamically via pycaw)
        if os.name != 'nt' and self.playback.is_playing:
            self.pause_audio()
            self.playback.is_paused = False
            self.resume_playback()

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def next_chapter(self):
        self.save_playback_state()
        
        if self.playback.next_chapter():
            if self.sleep_mode == "chapters":
                self.sleep_chapters_remaining -= 1
                if self.sleep_chapters_remaining <= 0:
                    self.sleep_mode = None
                    self.timer_btn.config(text="Sleep: Off")
                    self.logger.info("Sleep timer (chapters) finished. Pausing playback.")
                    
                    self.playback.is_paused = True
                    self.update_info()
                    curr_str = self.format_time(self.playback.current_play_time)
                    dur_str = self.format_time(self.playback.chapter_duration)
                    self.time_label.config(text=f"{curr_str} / {dur_str}")
                    self.ui_state.playback_progress.set(0)
                    return
                else:
                    self.timer_btn.config(text=f"Sleep: {self.sleep_chapters_remaining} ch")

            self.playback.is_paused = False
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

        
        # 3. Resume playing
        self.playback.is_paused = False
        self.update_info()
        self.resume_playback()

    def update_info(self):
        if self.playback.chapters:
            title = self.playback.chapters[self.playback.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.playback.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")
