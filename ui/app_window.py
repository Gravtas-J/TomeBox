import os
import threading
import tkinter as tk
import webbrowser
import shutil
from tkinter import filedialog, messagebox, ttk

import faulthandler
import time
import logging

from PIL import Image

from core.utils.logger import setup_logger
from core.utils.process_runner import ProcessRunner
from core.utils.thread_pool import AppThreadPool

try:
    from tkinterdnd2 import DND_FILES
except ImportError:
    messagebox.showerror(
        "Missing Dependency",
        "Please run: pip install audible requests pillow tkinterdnd2",
    )
    exit()
import sys

import pystray
from pystray import MenuItem as item

from api.audible_client import AudibleClient
from core.controllers.conversion_manager import ConversionManager
from core.controllers.download_manager import DownloadManager
from core.controllers.library_manager import LibraryManager
from core.controllers.metadata_manager import MetadataManager
from core.controllers.playback_controller import PlaybackController
from core.controllers.stats_manager import ACHIEVEMENTS, StatsManager
from core.controllers.system_manager import SystemManager
from core.converter import AudioConverter
from core.database import DatabaseManager
from core.exporter import LibraryExporter
from core.utils.image_cache import ImageCache
from core.utils.paths import get_resource_path
from ui.action_router import ActionRouter
from ui.auth_controller import AuthController
from ui.bookmarks_presenter import BookmarksPresenter
from ui.cloud_server_controller import CloudServerController
from ui.components.dialogs import (
    open_chapter_window,
    open_cover_modal,
    open_error_log_window,
    open_sleep_menu,
    show_achievement_toast,
)
from ui.components.library_view import setup_library_view
from ui.components.menu_bar import setup_menu_bar
from ui.components.player_bar import PlayerBarView
from ui.components.sidebar import setup_sidebar
from ui.import_session import ImportSession
from ui.library_presenter import LibraryPresenter
from ui.palette_controller import PaletteController
from ui.playback_presenter import PlaybackPresenter

mac_paths = "/opt/homebrew/bin:/usr/local/bin:/opt/local/bin"
os.environ["PATH"] = f"{os.environ.get('PATH', '')}{os.pathsep}{mac_paths}"
bundled_bin_dir = get_resource_path("bin")
# 2. Inject bundled PyInstaller binaries and restore +x permissions
if hasattr(sys, "_MEIPASS"):
    bin_path = os.path.join(sys._MEIPASS, "bin")
    os.environ["PATH"] = (
        f"{sys._MEIPASS}{os.pathsep}{bin_path}{os.pathsep}{os.environ.get('PATH', '')}"
    )

    try:
        for binary in ["ffmpeg", "ffplay"]:
            b_path = os.path.join(bin_path, binary)
            if os.path.exists(b_path):
                os.chmod(b_path, 0o755)
    except Exception:
        pass

if os.path.exists(bundled_bin_dir):
    os.environ["PATH"] = f"{bundled_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

import faulthandler, threading, time, os, logging

class _FreezeWatchdog:
    def __init__(self, root, log_dir, threshold=5.0):
        self.root = root
        self.dump_path = os.path.join(log_dir, "freeze_dump.txt")
        self.threshold = threshold
        self._last_beat = time.time()
        self._dumped = False
        self.root.after(1000, self._beat)                      # heartbeat on main thread
        threading.Thread(target=self._watch, daemon=True).start()

    def _beat(self):
        self._last_beat = time.time()
        self._dumped = False
        self.root.after(1000, self._beat)

    def _watch(self):
        while True:
            time.sleep(1.0)
            lag = time.time() - self._last_beat
            if lag > self.threshold and not self._dumped:
                self._dumped = True
                logging.getLogger("TomeBox").error(
                    "[WATCHDOG] main thread blocked %.1fs — dumping stacks to %s",
                    lag, self.dump_path)
                try:
                    with open(self.dump_path, "a", encoding="utf-8") as fh:
                        fh.write(f"\n=== main thread blocked {lag:.1f}s @ {time.ctime()} ===\n")
                        faulthandler.dump_traceback(file=fh)   # ALL threads, even the wedged one
                except Exception:
                    pass

class UiState:
    def __init__(self, settings):
        # General / System
        self.minimize_to_tray = tk.BooleanVar(
            value=settings.get("minimize_to_tray", True)
        )
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
    def file_path(self):
        return getattr(self, "_active_book_path", "")

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
        self.palette_controller = PaletteController(self)
        self.playback_presenter = PlaybackPresenter(self)
        self.auth_controller = AuthController(self)
        self.library_presenter = LibraryPresenter(self)
        self.cloud_server_controller = CloudServerController(self)

        self.root.dnd_bind("<<Drop>>", self.import_session.on_file_drop)
        self.base_dir = base_dir

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
            except Exception:
                pass

        # 3. Load Settings and Global Paths
        self.settings = self.db.load_settings()
        if self.settings.get("compact_player", False):
            self.settings["compact_player"] = False
            self.db.save_settings(self.settings)
        self.ui_state = UiState(self.settings)
        self.logger = setup_logger(
            self.base_dir, debug_mode=self.settings.get("debug_mode", False)
        )
        def _report_tk_exception(exc, val, tb):
            import traceback
            self.logger.error(
                "[TK-EXC] Unhandled exception in Tk callback:\n"
                + "".join(traceback.format_exception(exc, val, tb))
            )
        self.root.report_callback_exception = _report_tk_exception
        

        _tb_log = logging.getLogger("TomeBox")

        def _log_uncaught(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb); return
            _tb_log.error("UNCAUGHT (main)", exc_info=(exc_type, exc_value, exc_tb))
        sys.excepthook = _log_uncaught

        def _log_uncaught_thread(args):
            if issubclass(args.exc_type, SystemExit):
                return
            _tb_log.error("UNCAUGHT (thread=%s)", getattr(args.thread, "name", "?"),
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        threading.excepthook = _log_uncaught_thread

        self._freeze_watchdog = _FreezeWatchdog(self.root, os.path.join(self.base_dir, "logs"))

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
            callbacks={
                "on_achievement": lambda title, desc: self.root.after(
                    0, lambda: show_achievement_toast(self, title, desc)
                )
            },
        )
        self.download_manager = DownloadManager(
            api_client=self.api_client,
            logger=self.logger,
            library_manager=self.library_manager,
            thread_pool=self.thread_pool,
            callbacks={},
        )
        self.metadata_manager = MetadataManager(
            api_client=self.api_client,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            thread_pool=self.thread_pool,
            callbacks={},
        )
        self.conversion_manager = ConversionManager(
            converter=self.converter,
            library_manager=self.library_manager,
            logger=self.logger,
            covers_dir=self.covers_dir,
            thread_pool=self.thread_pool,
            get_drm_flags_cb=lambda path: self.api_client.get_drm_flags(
                path,
                self.library_manager.local_library.get(path, {}),
                self.active_profile,
                self.ui_state.auth_bytes.get().strip(),
                self.db.data_dir,
                self.logger,
            ),
            callbacks={},
        )
        self.playback = PlaybackController(
            logger=self.logger,
            on_tick_cb=self.playback_presenter.on_playback_tick,
            on_chapter_end_cb=lambda: self.root.after(
                0, self.playback_presenter.next_chapter
            ),
            on_error_cb=self.playback_presenter.on_playback_error,
        )
        # Load saved audio device
        saved_device = self.settings.get("audio_device", "System Default")
        self.playback.set_audio_device(saved_device)

        self.system_manager = SystemManager(logger=self.logger)
        self.system_manager.enforce_single_instance(
            on_wake_callback=lambda: self.root.after(0, self.bring_to_front)
        )

        self.player_process = None
        self.failed_tasks = []

        self.root.after(100, self.check_dependencies)

        try:
            icon_path = get_resource_path("ui", "tomebox.ico")
            if os.path.exists(icon_path):
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(
                    True, icon_img
                )  # "True" applies it to all future dialog windows too
        except Exception as e:
            self.logger.warning(f"Could not load app icon: {e}")
        self.build_context_menu()
        # UI & View State
        self.current_view_mode = "list"
        self._selected_grid_item = None

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
        self.import_root = self.settings.get(
            "download_folder", self.default_download_dir
        )
        if not self.import_root:
            # Fallback to the user's download directory, or the app base directory
            self.import_root = self.settings.get(
                "download_folder", self.default_download_dir
            )
            self.settings["import_root"] = self.import_root
            self.db.save_settings(self.settings)

        # Background Workers
        self._last_disk_save_time = 0.0

        # Timers & UI Flags
        self.tray_icon = None
        self.browser_login_btn = None

        self.image_cache = ImageCache(max_size=100)

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.handle_window_close)
        self.root.bind("<F2>", lambda event: self.handle_action_on_selected("edit"))
        for key in (
            "<Up>",
            "<Down>",
            "<Left>",
            "<Right>",
            "<Prior>",
            "<Next>",
            "<Home>",
            "<End>",
        ):
            self.root.bind(key, self.library_presenter.handle_keyboard_scroll)

        self.root.bind("<Key>", self.library_presenter.handle_alpha_jump, add="+")
        self.root.bind("<Delete>", self.handle_global_delete)

        self.root.bind("<Control-a>", self.library_presenter.handle_select_all)
        self.root.bind("<Command-a>", self.library_presenter.handle_select_all)

        def _focus_search():
            self.search_entry.focus_force()
            self.search_entry.icursor(tk.END)

        self.root.after(200, _focus_search)

        self.setup_tray_icon()
        self.root.after(500, self.auth_controller.auto_load_auth)
        self.root.after(900000, self.cloud_server_controller.run_background_sync)
        self.root.after(
            3000,
            lambda: self.library_manager.run_background_library_scan(
                self.converter,
                self.active_profile,
                self.logger,
                self.thread_pool,
                on_refresh_cb=lambda: self.root.after(
                    0, self.library_presenter.refresh_library_ui
                ),
            ),
        )
        dl_dir = self.settings.get("download_folder") or self.settings.get(
            "download_dir"
        )
        lib_paths = list(self.library_manager.local_library.keys())

        threading.Thread(
            target=self.system_manager.cleanup_orphaned_files,
            args=(dl_dir, lib_paths),
            daemon=True,
        ).start()
        threading.Thread(
            target=self.library_manager.monitor_local_files,
            args=(
                self.logger,
                lambda: self.root.after(0, self.library_presenter.refresh_library_ui),
            ),
            daemon=True,
        ).start()

        if "stats" not in self.settings:
            self.settings["stats"] = {
                "seconds_listened": 0,
                "books_finished": 0,
                "books_downloaded": 0,
                "unlocked_achievements": [],
            }

        self.session_listen_buffer = 0.0

        self.achievements = ACHIEVEMENTS
        self.root.after(1500, self.import_session._prompt_resume_imports)

    def cancel_task(self, task_id):
        """Unified method to cancel either an active import OR an active download from the queue drawer."""
        if str(task_id).startswith("import_"):
            self.library_manager.cancel_import(task_id)

            # Only terminate the FFmpeg process if this specific task is the one currently running
            if getattr(self.library_manager, "active_task_id", None) == task_id:
                self.converter.cancel()

            self.action_router.on_dl_status(task_id, "Canceling...", is_global=False)
        else:
            self.download_manager.cancel_download(task_id)

    def toggle_pause_task(self, task_id, btn):
        """Toggles the pause state for a specific task in the queue."""
        # If the button shows play, it's currently paused
        is_paused = btn.cget("text") == "▶"

        if str(task_id).startswith("import_"):
            # Placeholder for the upcoming LibraryManager refactor
            if is_paused:
                self.library_manager.resume_import(task_id)
                btn.config(text="⏸")
            else:
                self.library_manager.pause_import(task_id)
                btn.config(text="▶")
        else:
            # Download Manager routing
            if is_paused:
                self.download_manager.resume_download(task_id)
                btn.config(text="⏸")
            else:
                self.download_manager.pause_download(task_id)
                btn.config(text="▶")

    def clear_sidebar(self):
        """Wipes the side panel when selection is lost or deleted."""
        if hasattr(self, "author_label"):
            self.author_label.config(text="")
        if hasattr(self, "cover_label"):
            self.cover_label.config(image="", text="No Cover Art")
        self.current_cover_photo = None

        if hasattr(self, "bm_tree"):
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
            "Click 'Yes' to pick a folder, or 'No' to cancel the download.",
        )
        if not proceed:
            return None

        # Step 2: Show folder picker
        folder = filedialog.askdirectory(title="Select Download Folder")

        # Step 3: User closed the picker without choosing — offer fallback
        if not folder:
            default_folder = os.path.join(
                os.path.expanduser("~"), "Downloads", "TomeBox"
            )

            use_default = messagebox.askyesno(
                "Use Default Location?",
                f"No folder was selected.\n\n"
                f"Would you like to download to the default location?\n\n"
                f"{default_folder}\n\n"
                f"Click 'Yes' to use this location, or 'No' to cancel the download.",
            )

            if not use_default:
                return None

            os.makedirs(default_folder, exist_ok=True)
            folder = default_folder

            # Confirm where it landed
            messagebox.showinfo(
                "Download Folder Set",
                f"Audiobooks will be saved to:\n\n{folder}\n\n"
                f"You can change this later via File → Set Download Folder.",
            )

        # Save for next time
        self.settings["download_folder"] = folder
        self.db.save_settings(self.settings)
        return folder

    def update_api_health(self, message, is_error=False):
        """Thread-safe update of the API health status label."""

        def update():
            if hasattr(self, "api_health_var"):
                self.ui_state.api_health.set(f"API: {message}")
            if is_error:
                # Auto-reset the health indicator back to Online after the 60s cooldown expires
                self.root.after(
                    60000, lambda: self.ui_state.api_health.set("API: Online")
                )

        self.root.after(0, update)

    def bring_to_front(self):
        # 1. Un-hide it if it was minimized to the system tray
        self.root.deiconify()

        # 2. Lift it above other windows
        self.root.lift()

        # 3. Force it to the absolute top, then release the lock so the user can click other things again
        self.root.attributes("-topmost", True)
        self.root.after_idle(self.root.attributes, "-topmost", False)

    def setup_tray_icon(self):
        import sys

        # macOS strictly forbids background tray loops and uses the Dock instead.
        if sys.platform == "darwin":
            self.logger.info("macOS detected. Skipping system tray initialization.")
            return

        try:
            icon_path = get_resource_path("ui", "tomebox.ico")

            if not os.path.exists(icon_path):
                self.logger.warning(f"System tray icon not found at: {icon_path}")
                return

            image = Image.open(icon_path)

            menu = pystray.Menu(
                item("Show TomeBox", self.show_window_from_tray, default=True),
                item("Quit", self.quit_from_tray),
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
            self.root.attributes("-topmost", True)
            result[0] = filedialog.askdirectory(
                parent=self.root, title="Select TomeBox Location"
            )
            self.root.attributes("-topmost", False)
            event.set()

        self.root.after(0, _ask)
        event.wait()  # Block the calling background thread until the user clicks OK/Cancel
        return result[0]

    def thread_safe_ask_file(self):
        """Safely opens a file dialog from a background thread."""
        result = [None]
        event = threading.Event()

        def _ask():
            self.root.attributes("-topmost", True)
            result[0] = filedialog.askopenfilename(
                parent=self.root,
                title="Select Audiobook File",
                filetypes=[("Audiobooks", "*.m4b *.mp3 *.aaxc *.aax")],
            )
            self.root.attributes("-topmost", False)
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

        self.logger.info("Opening Buy Me a Coffee link...")
        webbrowser.open("https://buymeacoffee.com/ProblematicSyntax")

    def check_dependencies(self):
        

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
            self.root.attributes("-topmost", True)
            user_wants_link = messagebox.askyesno(
                "Missing Dependency: FFmpeg", msg, parent=self.root
            )
            self.root.attributes("-topmost", False)

            if user_wants_link:
                self.logger.info("Opening FFmpeg download page in browser...")
                webbrowser.open("https://ffmpeg.org/download.html")

    def build_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)

        # Playback Controls
        self.context_menu.add_command(
            label="▶ Play", command=self.playback_presenter.master_play
        )
        self.context_menu.add_separator()

        # File Operations
        self.context_menu.add_command(
            label="⬇️ Download",
            command=lambda: self.handle_action_on_selected("download"),
        )
        self.context_menu.add_command(
            label="🔄 Convert",
            command=lambda: self.handle_action_on_selected("convert"),
        )
        self.context_menu.add_command(
            label="🔍 Scrape Metadata",
            command=lambda: self.handle_action_on_selected("scrape"),
        )
        self.context_menu.add_command(
            label="✏️ Edit Metadata",
            command=lambda: self.handle_action_on_selected("edit"),
        )

        # --- NEW: Safe Extraction Lambda ---
        def open_location():
            import os

            path = None
            if self.current_view_mode == "list":
                selected = self.library_tree.selection()
                if selected:
                    vals = self.library_tree.item(selected[0], "values")
                    if len(vals) > 7:
                        path = vals[7]
            else:
                grid_item = getattr(self, "_selected_grid_item", None)
                if grid_item:
                    vals = grid_item.get("values", [])
                    if len(vals) > 7:
                        path = vals[7]

            if not path:
                path = getattr(self, "_selected_local_path", None)

            # Resolve virtual playlist paths to their actual folder
            if path:
                local_data = self.library_manager.local_library.get(path, {})
                if local_data.get("is_playlist"):
                    chapters = local_data.get("chapters", [])
                    if chapters and chapters[0].get("file_path"):
                        path = os.path.dirname(chapters[0]["file_path"])
                    else:
                        path = os.path.dirname(path)

            if path:
                self.system_manager.open_file_location(path)

        self.context_menu.add_command(
            label="📁 Open File Location", command=open_location
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="🔗 Match Local File",
            command=lambda: self.handle_action_on_selected("match_local"),
        )

    def show_context_menu(self, event):
        # 0x0004 is Windows/Linux Ctrl. 0x20000 is Mac Command.
        if hasattr(event, "state") and (
            event.state & 0x0004 or getattr(event, "state", 0) & 0x20000
        ):
            return
        path = None
        if self.current_view_mode == "list":
            item = self.library_tree.identify_row(event.y)
            # Treeview already has native protection: it only selects if 'not in selection()'
            if item and item not in self.library_tree.selection():
                self.library_tree.selection_set(item)
                self.library_tree.focus(item)
                self.on_item_select()

            selected = self.library_tree.selection()
            if selected:
                vals = self.library_tree.item(selected[0], "values")
                if len(vals) > 7:
                    path = vals[7]
        else:
            idx = self.grid_canvas.get_index_at(event.x, event.y)

            if idx is not None:
                item_data = self.grid_canvas.data[idx]
                asin = item_data.get("asin", "")
                path = item_data.get("path", "")
                fp = asin if asin and asin != "Unknown" else path

                # Only force a new selection if it isn't already in the batch!
                if fp not in self.grid_canvas.active_asins:
                    self._selected_grid_item = {
                        "values": [
                            item_data.get("title", ""),
                            item_data.get("authors", ""),
                            item_data.get("narrator", ""),
                            item_data.get("series", ""),
                            item_data.get("duration_str", ""),
                            item_data.get("asin", ""),
                            item_data.get("status", ""),
                            item_data.get("path", ""),
                        ]
                    }
                    self.on_item_select()

            # Grab the path of the primary selected item to determine which context menu to show
            grid_item = getattr(self, "_selected_grid_item", None)
            if grid_item:
                vals = grid_item.get("values", [])
                if len(vals) > 7:
                    path = vals[7]

        if not path:
            path = getattr(self, "_selected_local_path", None)

        import os

        # Resolve virtual playlist paths before doing the existence check
        if path:
            local_data = self.library_manager.local_library.get(path, {})
            if local_data.get("is_playlist"):
                chapters = local_data.get("chapters", [])
                if chapters and chapters[0].get("file_path"):
                    path = os.path.dirname(chapters[0]["file_path"])
                else:
                    path = os.path.dirname(path)

        if path and os.path.exists(path):
            self.context_menu.entryconfig("📁 Open File Location", state=tk.NORMAL)
        else:
            self.context_menu.entryconfig("📁 Open File Location", state=tk.DISABLED)

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
            if hasattr(self, "chapter_win") and self.chapter_win:
                self.chapter_win.destroy()

            # Stop current playback and save state
            self.playback_presenter.stop_audio()

            # Set the new target
            self.playback.current_chapter_idx = target_idx
            self.playback.current_play_time = 0.0

            self.playback_presenter.play_chapter()

    def on_item_select(self, event=None):
        if self.current_view_mode == "list":
            self._cached_selection = self.library_tree.selection()
            selected = self.library_tree.focus()
            if not selected:
                self.clear_sidebar()
                self._selected_local_path = None
                return
            item = self.library_tree.item(selected)
            title = item["values"][0]
            authors = item["values"][1]
            asin = item["values"][5]
        else:
            if not self._selected_grid_item:
                self.clear_sidebar()
                self._selected_local_path = None
                return

            if getattr(self.grid_canvas, "batch_selection", None):
                self._selected_grid_items = self.grid_canvas.batch_selection

                all_fps = set()
                for item in self._selected_grid_items:
                    raw = item.get("asin", "")
                    fp = raw if raw and raw != "Unknown" else item.get("path", "")
                    all_fps.add(fp)
                self.grid_canvas.set_active_asins(all_fps)
            else:
                # Normal click: Wipe the batch and highlight just the one book
                self._selected_grid_items = None
                asin = self._selected_grid_item["values"][5]
                path = self._selected_grid_item["values"][7]
                fp = asin if asin and asin != "Unknown" else path
                self.grid_canvas.set_active_asins({fp})

            item = self._selected_grid_item
            title = item["values"][0]
            authors = item["values"][1]
            asin = item["values"][5]

        if hasattr(self, "author_label"):
            self.author_label.config(text=authors)
        series_text = item["values"][3]
        if series_text and series_text.strip():
            self.series_label.config(text=series_text)
        else:
            self.series_label.config(text="")
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

        if cover_path and hasattr(self, "cover_label"):
            try:
                from PIL import Image, ImageTk

                img = Image.open(cover_path)
                img.thumbnail((400, 400), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.cover_label.config(image=photo, text="")
                self.current_cover_photo = photo

                self.cover_label.bind(
                    "<Button-1>",
                    lambda e, a=asin, t=title, p=cover_path: open_cover_modal(
                        self, a, t, explicit_path=p
                    ),
                )

            except Exception:
                self.cover_label.config(image="", text=title)
                self.cover_label.unbind(
                    "<Button-1>"
                )  # Unbind to prevent crashes on broken images
        elif hasattr(self, "cover_label"):
            self.cover_label.config(image="", text=title)
            self.cover_label.unbind("<Button-1>")  # Unbind if there is no cover

        self.bookmarks_presenter.refresh_bookmarks_ui()

    def manage_shelves_prompt(self):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning(
                    "Selection Required", "Please select an audiobook to tag."
                )
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item:
                messagebox.showwarning(
                    "Selection Required", "Please select an audiobook to tag."
                )
                return
            item = self._selected_grid_item

        title = item["values"][0]
        asin = item["values"][5]

        if not asin or asin == "Unknown":
            messagebox.showerror(
                "Error",
                "Cannot tag an orphaned file without an ASIN. Please scrape its metadata first.",
            )
            return

        from ui.components.dialogs import open_shelf_management_window

        open_shelf_management_window(self, title, asin)

    def save_tray_setting(self):
        self.settings["minimize_to_tray"] = self.ui_state.minimize_to_tray.get()
        self.db.save_settings(self.settings)

    def on_filter_change(self):

        if self.playback.is_playing:
            self.playback_presenter.pause_audio()
            self.playback.is_paused = False
            self.playback_presenter.resume_playback()

    def handle_global_delete(self, event):
        import tkinter as tk
        from tkinter import ttk

        # If the user is typing in a text box, let them delete text!
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return

        # Otherwise, proceed with book removal
        self.library_manager.handle_remove_clicked(self)

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
            self.playback_presenter.save_playback_state()

            if getattr(self.library_manager, "_playback_dirty", False):
                self.library_manager.db.save_local_db(self.library_manager.local_library)
                self.library_manager._playback_dirty = False
                
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
            import time

            try:
                if hasattr(self, "db") and hasattr(self.db, "conn") and self.db.conn:
                    self.db.conn.close()
                elif hasattr(self, "library_manager") and hasattr(
                    self.library_manager, "db"
                ):
                    self.library_manager.db.conn.close()
                time.sleep(0.2)  # Give the OS a moment to flush the WAL file to disk
            except Exception as e:
                print(f"Error closing database gracefully: {e}")
            if os.name == "nt":
                # /T flag = "Tree Kill" (Kills this process and everything it spawned)
                ProcessRunner.run_async(
                    ["taskkill", "/F", "/T", "/PID", str(os.getpid())]
                )
            else:
                # Mac/Linux immediate hard exit
                os._exit(0)

    def set_download_folder(self):
        self.root.attributes("-topmost", True)
        directory = filedialog.askdirectory(
            parent=self.root, title="Select Default Download Folder"
        )
        self.root.attributes("-topmost", False)

        if directory:
            self.default_download_dir = directory
            self.settings["download_folder"] = directory
            self.import_root = directory

            self.db.save_settings(self.settings)
            messagebox.showinfo(
                "Folder Saved",
                f"Default download folder updated to:\n{directory}",
                parent=self.root,
            )

    def cancel_all_downloads(self):
        if messagebox.askyesno(
            "Cancel All", "Cancel all active and pending downloads?"
        ):
            self.download_manager.cancel_all()
            self.logger.info("User initiated Cancel All Downloads.")

    def cancel_download(self, asin):
        self.download_manager.cancel_download(asin)

    def start_download_all(self):
        # We check the library manager instead of local_library directly
        local_titles = {
            data["title"] for path, data in self.library_manager.local_library.items()
        }
        missing_items = [
            {"asin": item.get("asin"), "title": item.get("title", "Unknown")}
            for item in self.library_manager.cloud_items
            if item.get("title") not in local_titles
        ]

        if not missing_items:
            messagebox.showinfo(
                "Up to Date", "Your local library already has all cloud items."
            )
            return

        save_dir = self.ensure_download_folder()
        if not save_dir:
            return
        if messagebox.askyesno(
            "Download All", f"Queue {len(missing_items)} missing audiobooks?"
        ):
            self.dl_all_btn.config(state=tk.DISABLED)
            self.import_session.toggle_queue_drawer(True)

            for item in missing_items:
                self.import_session.add_queue_ui_row(item["asin"], item["title"])

            self.download_manager.queue_batch(missing_items, save_dir)

    def _collect_selected_paths(self):
        paths = []
        if self.current_view_mode == "list":
            selected = getattr(self, "library_tree", None)
            if selected:
                for item_id in selected.selection():
                    vals = selected.item(item_id).get("values", [])
                    if len(vals) > 7 and vals[7]:
                        paths.append(vals[7])
        else:
            if getattr(self, "_selected_grid_items", None):
                for item in self._selected_grid_items:
                    path = item.get("path") or (item.get("values", []) and item["values"][7])
                    if path:
                        paths.append(path)
            elif getattr(self, "_selected_grid_item", None):
                vals = self._selected_grid_item.get("values", [])
                if len(vals) > 7 and vals[7]:
                    paths.append(vals[7])
        
        return list(dict.fromkeys(paths))

    def edit_selected_metadata(self):
        from tkinter import messagebox
        from ui.components.dialogs import open_manual_metadata_window, open_bulk_metadata_window
        if self.metadata_manager.is_applying:
            messagebox.showinfo(
                "Edit in progress",
                "A metadata edit is still finishing. Give it a second and try again.",
            )
            return
        paths = self._collect_selected_paths()
        valid_paths = [p for p in paths if p in self.library_manager.local_library]

        if not valid_paths:
            messagebox.showwarning(
                "Selection Required", 
                "Please select at least one downloaded title to edit.",
                parent=self.root
            )
            return

        if len(valid_paths) == 1:
            open_manual_metadata_window(self, valid_paths[0])
        else:
            open_bulk_metadata_window(self, valid_paths)

    def handle_action_on_selected(self, action_type):
        if action_type == "edit":
            self.edit_selected_metadata()
            return

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
        self._selected_grid_items = None
        title = item["values"][0]
        asin = item["values"][5]

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
                messagebox.showerror(
                    "File Missing",
                    "The file was deleted or moved. Please remove it from the list and re-download.",
                )
                return

            if action_type == "scrape":
                self.start_scrape_thread(local_path)
                return
            
            elif action_type == "convert":
                if is_playlist:
                    messagebox.showinfo(
                        "Not Applicable",
                        "Playlists are already split into individual files.",
                    )
                    return
                self.start_convert_thread(target_path=local_path)
                return

            self.playback_presenter.load_specific_file(local_path)
            if action_type == "play":
                self.playback_presenter.play_chapter()
            elif action_type == "convert":
                self.start_convert_thread()
        else:
            if action_type == "download" or messagebox.askyesno(
                "Download Required", f"'{title}' is not downloaded.\n\nDownload it now?"
            ):
                save_dir = self.ensure_download_folder()
                if not save_dir:
                    return
                self.import_session.add_queue_ui_row(asin, title)
                self.download_manager.queue_download(
                    asin, title, save_dir, post_action=action_type
                )

    def match_to_audible_prompt(self):
        """Opens the manual match dialog for the currently selected library item."""
        from ui.components.dialogs import open_match_to_audible_window

        # Get the selected file
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning(
                    "Selection Required", "Please select a local file to match."
                )
                return
            item = self.library_tree.item(selected)
        else:
            if not self._selected_grid_item:
                messagebox.showwarning(
                    "Selection Required", "Please select a local file to match."
                )
                return
            item = self._selected_grid_item

        title = item["values"][0]

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
                "Use 'Download Selected' or 'Download Missing' to fetch it.",
            )
            return

        open_match_to_audible_window(self, filepath)

    def start_scrape_thread(self, filepath):
        """Re-routes the scraper button to the new unified Match UI."""
        from ui.components.dialogs import open_match_to_audible_window

        open_match_to_audible_window(self, filepath)

    def toggle_compact_mode(self):
        if not getattr(self, "file_path", None):
            return
        is_compact = self.settings.get("compact_player", False)
        new_state = not is_compact
        self.settings["compact_player"] = new_state
        if hasattr(self, "db"):
            self.db.save_settings(self.settings)
        self._apply_compact_state(new_state)

    def _apply_compact_state(self, is_compact):
        import os

        from PIL import Image, ImageOps, ImageTk

        if is_compact:
            self._saved_menu = self.root.cget("menu")
            self.root.config(menu="")

            self._was_zoomed = self.root.state() == "zoomed"
            if self._was_zoomed:
                self.root.state("normal")
            self.root.resizable(False, False)
            if not getattr(self, "_booting_compact", False):
                self._pre_compact_geom = self.root.geometry()

            self._hidden_pack_slaves = []
            self._hidden_grid_slaves = []

            for widget in self.root.pack_slaves():
                if widget != self.player_bar.play_frame and widget != getattr(
                    self, "compact_cover_lbl", None
                ):
                    self._hidden_pack_slaves.append((widget, widget.pack_info()))
                    widget.pack_forget()

            for widget in self.root.grid_slaves():
                if widget != self.player_bar.play_frame and widget != getattr(
                    self, "compact_cover_lbl", None
                ):
                    self._hidden_grid_slaves.append((widget, widget.grid_info()))
                    widget.grid_forget()

            style = ttk.Style()
            bg_color = style.lookup("TFrame", "background") or "#2b2b2b"
            if bg_color == "":
                bg_color = "#2b2b2b"

            if not hasattr(self, "compact_cover_lbl"):
                self.compact_cover_lbl = tk.Label(self.root, bg=bg_color)
            else:
                self.compact_cover_lbl.config(bg=bg_color)

            self.compact_cover_lbl.pack(side=tk.TOP, fill="both", expand=True)

            self.player_bar.apply_compact_layout()

            cover_path = None
            if hasattr(self, "file_path") and self.file_path:
                local_data = self.library_manager.local_library.get(self.file_path, {})
                asin = local_data.get("asin")
                if asin:
                    cp = os.path.join(self.covers_dir, f"{asin}.jpg")
                    if os.path.exists(cp):
                        cover_path = cp

            if cover_path:
                try:
                    img = Image.open(cover_path).convert("RGB")
                    img = ImageOps.fit(
                        img,
                        (450, 450),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                    photo = ImageTk.PhotoImage(img)
                    self.compact_cover_lbl.config(image=photo)
                    self.compact_cover_lbl.image = photo
                except Exception:
                    self.compact_cover_lbl.config(image="")
            else:
                self.compact_cover_lbl.config(image="")

            self.root.geometry("450x610")

        else:
            if hasattr(self, "_saved_menu") and self._saved_menu:
                self.root.config(menu=self._saved_menu)
            self.root.resizable(True, True)

            if hasattr(self, "compact_cover_lbl"):
                self.compact_cover_lbl.pack_forget()

            if hasattr(self, "_hidden_pack_slaves"):
                for widget, info in self._hidden_pack_slaves:
                    try:
                        widget.pack(**info)
                    except Exception:
                        pass
            if hasattr(self, "_hidden_grid_slaves"):
                for widget, info in self._hidden_grid_slaves:
                    try:
                        widget.grid(**info)
                    except Exception:
                        pass

            self.player_bar.apply_standard_layout()

            if hasattr(self, "_pre_compact_geom"):
                self.root.geometry(self._pre_compact_geom)

            if getattr(self, "_was_zoomed", False):
                self.root.state("zoomed")

    def setup_ui(self):
        setup_menu_bar(self)
        self.player_bar = PlayerBarView(
            parent_root=self.root,
            ui_state=self.ui_state,
            playback_presenter=self.playback_presenter,
            bookmarks_presenter=self.bookmarks_presenter,
            settings=self.settings,
            callbacks={
                "toggle_compact": self.toggle_compact_mode,
                "open_chapter": lambda: open_chapter_window(self),
                "open_sleep": lambda: open_sleep_menu(self),
                "on_filter_change": self.on_filter_change,
            },
        )
        import sys

        if sys.platform == "darwin":
            try:
                # 1. Enable the native Mac "Settings/Preferences" menu item
                from ui.components.dialogs import open_auth_window

                self.root.createcommand(
                    "::tk::mac::ShowPreferences", lambda: open_auth_window(self)
                )

                # 2. Hide the default Tkinter "Run Widget Demo" help menu
                self.root.createcommand("tk::mac::ShowHelp", lambda: None)
            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.error(f"Failed to bind Mac menus: {e}")

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

        # Auto-start the server if it's not already running
        if not getattr(self, "server_running", False):
            self.cloud_server_controller.toggle_web_server()

        self.root.after(500, lambda: webbrowser.open("http://127.0.0.1:8000/desktop"))

    def export_csv_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV File", "*.csv")],
            title="Export Library to CSV",
        )
        if not output_file:
            return

        try:
            LibraryExporter.export_csv(
                output_file,
                self.library_manager.local_library,
                self.library_manager.cloud_items,
            )
            messagebox.showinfo(
                "Export Successful", f"Library successfully exported to:\n{output_file}"
            )
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to write CSV:\n{e}")

    def export_html_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML Document", "*.html")],
            title="Export Library to HTML",
        )
        if not output_file:
            return

        try:
            LibraryExporter.export_html(
                output_file,
                self.library_manager.local_library,
                self.library_manager.cloud_items,
            )

            webbrowser.open(output_file)
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to generate HTML:\n{e}")

    def write_log(self, message):
        """Bridge method routing legacy log calls into the standard logger."""
        if hasattr(self, "logger"):
            self.logger.info(message)
        else:
            print(message)

    def match_local_file_to_cloud(self, title, asin):
        """Manually associates a local file on the hard drive with a Cloud-only item."""
        filepath = filedialog.askopenfilename(
            title=f"Select local file for: {title}",
            filetypes=[("Audiobooks", "*.m4b *.mp3 *.aax *.aaxc")],
        )
        if not filepath:
            return

        # 1. Grab the richest metadata available from the cloud cache
        cloud_data = None
        for cloud_item in self.library_manager.cloud_items:
            if cloud_item.get("asin") == asin or cloud_item.get("title") == title:
                cloud_data = cloud_item
                break

        if not cloud_data:
            cloud_data = {"title": title, "asin": asin}

        # 2. Parse Authors
        raw_authors = cloud_data.get("authors", [])
        authors_str = "Unknown Author"
        if raw_authors:
            authors_str = ", ".join(
                [a.get("name", "") for a in raw_authors if isinstance(a, dict)]
            )

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
        self.metadata_manager.sync_missing_covers(
            on_complete_cb=lambda: self.root.after(
                0, self.library_presenter.refresh_library_ui
            )
        )
        self.library_presenter.refresh_library_ui()
        messagebox.showinfo(
            "Match Successful", f"Successfully linked '{title}' to:\n\n{filepath}"
        )

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
            
            probe_data = self.converter.get_metadata_and_chapters(target_path)
            db_chapters = probe_data.get("chapters", [])

            if db_chapters:
                local_data["chapters"] = db_chapters
                self.library_manager.local_library[target_path] = local_data
                self.library_manager.db.save_local_db(
                    self.library_manager.local_library
                )

            self.ui_state.dl_status.set("Idle")

        # Identify if the chapters are real or just the auto-generated dummy
        has_real_chapters = False
        if db_chapters:
            if len(db_chapters) > 1:
                has_real_chapters = True
            elif (
                len(db_chapters) == 1
                and db_chapters[0].get("tags", {}).get("title") != "Full Audiobook"
            ):
                has_real_chapters = True

        if not has_real_chapters:
            messagebox.showinfo(
                "No Chapters Found",
                "This file does not contain chapter markers to split.",
            )
            return

        # --- DIRECT TO SPLIT ---
        output_dir = filedialog.askdirectory(
            title=f"Select Folder to Extract Chapters For: {os.path.basename(target_path)}"
        )
        if not output_dir:
            return

        self.ui_state.dl_status.set("Splitting into chapters... Please wait.")
        self.conversion_manager.split_book(target_path, output_dir, db_chapters)

    def start_convert_all_thread(self):
        to_convert = [
            path
            for path, data in self.library_manager.local_library.items()
            if data.get("format", "").upper() in ["AAX", "AAXC"]
        ]

        if not to_convert:
            messagebox.showinfo("Convert All", "No AAX or AAXC files found to convert.")
            return

        required_bytes = sum(
            os.path.getsize(p) for p in to_convert if os.path.exists(p)
        )
        if not self.system_manager.has_enough_disk_space(
            self.base_dir, required_bytes + (500 * 1024 * 1024)
        ):
            required_gb = required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage",
                f"Batch conversion requires at least {required_gb:.2f} GB of free space on your drive.\n\n"
                "Please free up space and try again.",
            )
            return

        if not messagebox.askyesno(
            "Convert All",
            f"Found {len(to_convert)} files to convert.\nThis will process sequentially in the background. Proceed?",
        ):
            return

        self.conversion_manager.convert_batch(to_convert)

    def cancel_active_task(self):
        """Global button: Cancels all active imports, conversions, and downloads."""
        self.converter.cancel()
        self.library_manager.cancel_import()  # None = Cancel All
        self.system_manager.clear_all_pending_imports(self.db.data_dir)
        self.download_manager.cancel_all()

        self.action_router.update_global_status("Cancelling all tasks...")
        self.action_router.update_global_progress(0)

        # Mark all UI rows as Canceling
        for task_id in list(self.queue_ui_elements.keys()):
            self.action_router.on_dl_status(task_id, "Canceling...", is_global=False)
            self.action_router._schedule_row_removal(task_id)

        self.root.after(
            2000,
            lambda: self.action_router.update_global_status("All tasks cancelled."),
        )
        self.root.after(5000, self.action_router.reset_ui_if_idle)
