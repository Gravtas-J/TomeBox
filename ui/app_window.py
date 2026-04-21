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
# import sv_ttk
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
from core.database import DatabaseManager
from core.converter import AudioConverter
from api.audible_client import AudibleClient
from core.player import AudioPlayer
from ui.dialogs import open_auth_window, open_chapter_window, open_sleep_menu, open_achievements_window, show_achievement_toast, open_pairing_window
from core.downloader import AudiobookDownloader
from ui.theme import apply_theme
from core.exporter import LibraryExporter

class AAXManagerApp:
    def __init__(self, root, base_dir):
        self.root = root
        self.root.title("TomeBox")
        self.root.geometry("1550x850")
        self.root.drop_target_register(DND_FILES)
        self.base_dir = base_dir  # This is the root folder passed from main.py

        self.enforce_single_instance()
        
        # 1. Initialize Database Manager FIRST
        self.db = DatabaseManager(self.base_dir)
        
        # 2. Setup Assets (Icons are in the ui folder)
        ui_dir = os.path.join(self.base_dir, "ui")
        icon_ico = os.path.join(ui_dir, "tomebox.ico")
        icon_png = os.path.join(ui_dir, "tomebox.png")

        if os.path.exists(icon_ico):
            self.root.iconbitmap(icon_ico)
        
        if os.path.exists(icon_png):
            try:
                icon_img = tk.PhotoImage(file=icon_png)
                self.root.iconphoto(True, icon_img)
            except Exception: pass

        # 3. Load Settings and Global Paths
        self.settings = self.db.load_settings()
        self.log_file_path = os.path.join(self.base_dir, "aax_manager.log")
        self.covers_dir = os.path.join(self.base_dir, "covers")
        os.makedirs(self.covers_dir, exist_ok=True)
        
        # 4. Apply Profile Variables
        self.active_profile = self.settings.get("active_profile", "Main")
        self.minimize_to_tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        
        # Use the DB manager to get paths instead of calculating them here
        self.auth_save_path = self.db.get_auth_path(self.active_profile)
        self.cloud_cache_path = self.db.get_cloud_cache_path(self.active_profile)
        self.converter = AudioConverter(self.write_log)
        
        # 5. Load Memory
        self.api_client = AudibleClient()
        self.downloader = AudiobookDownloader(self.api_client, self.write_log)
        self.local_library = self.db.load_local_db()
        self.cloud_items = self.load_cloud_cache()

        self.file_path = ""
        self.auth_bytes = tk.StringVar(value="")
        self.locale = tk.StringVar(value="us")
        self.chapters = []
        self.current_chapter_idx = 0
        self.player_process = None
        self.player = AudioPlayer(
            logger=self.write_log,
            on_complete_cb=lambda: self.root.after(0, self.next_chapter),
            on_error_cb=lambda code: self.root.after(0, self.stop_audio)
        )
        self.debug_mode = tk.BooleanVar(value=False)
        self.dl_progress_var = tk.DoubleVar()
        self.dl_status_var = tk.StringVar(value="Idle")

        self.root.after(100, self.check_dependencies)
        
        try:
            icon_path = os.path.join(self.base_dir, "tomebox.png")
            if os.path.exists(icon_path):
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, icon_img) # "True" applies it to all future dialog windows too
        except Exception as e:
            self.write_log(f"Could not load app icon: {e}")
        self.build_context_menu()
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.handle_window_close)
        self.setup_tray_icon()
        self.root.after(500, self.auto_load_auth)
        self.root.after(900000, self.run_background_sync)
        threading.Thread(target=self.cleanup_orphaned_files, daemon=True).start()
        threading.Thread(target=self.db_monitor_worker, daemon=True).start()

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
    
    def get_local_ip(self):
        import socket
        try:
            # We don't actually send data, just forcing the OS to route to an external IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1" # Fallback to localhost if disconnected from Wi-Fi
        
    def _get_mobile_html(self):
        import os
        html_path = os.path.join(self.base_dir, "server", "mobile_ui.html")
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>Error: mobile_ui.html not found in server directory.</h1>"
    
    def toggle_web_server(self):
        if hasattr(self, 'web_server') and self.web_server is not None:
            self.write_log("Stopping companion server...")
            self.web_server.should_exit = True
            self.web_server = None
            self.file_menu.entryconfigure("Disable Web Server", label="Enable Web Server")
            messagebox.showinfo("Server Stopped", "The companion server has been safely disabled.")
            # Note: Removed the accidental open_pairing_window(self) from here
        else:
            try:
                import uvicorn
                import threading
                import sys
                import asyncio
                from server.web_app import create_server_app

                if sys.platform == 'win32':
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

                # Pass the TomeBox app instance to the server so it can read library/settings
                api = create_server_app(self)

                config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_config=None)
                self.web_server = uvicorn.Server(config)
                threading.Thread(target=self.web_server.run, daemon=True).start()
                
                self.file_menu.entryconfigure("Enable Web Server", label="Disable Web Server")
                local_ip = self.get_local_ip()
                self.write_log(f"Server started on http://{local_ip}:8000")
                
                # --- NEW: Pop up the QR code instead of the message box ---
                open_pairing_window(self)
                # ----------------------------------------------------------
                
            except ImportError:
                messagebox.showerror("Missing Libraries", "Please install the required server packages first:\n\npip install fastapi uvicorn")
            except Exception as e:
                self.write_log(f"Failed to start server: {e}")
                messagebox.showerror("Server Error", f"Could not start the server.\n\n{e}")

    def cleanup_orphaned_files(self):
        save_dir = self.settings.get("download_dir", "")
        if not save_dir or not os.path.exists(save_dir):
            return

        self.write_log("Running startup scan for orphaned/partial files...")
        cleaned_count = 0

        try:
            for filename in os.listdir(save_dir):
                filepath = os.path.join(save_dir, filename)
                
                # Skip directories
                if not os.path.isfile(filepath):
                    continue

                # Target 1: Explicitly temporary/partial files
                if filename.endswith(".part") or "_temp." in filename:
                    try:
                        os.remove(filepath)
                        self.write_log(f"Deleted partial file: {filename}")
                        cleaned_count += 1
                    except OSError:
                        pass
                    continue

                # Target 2: Corrupted 0-byte media files
                if filename.lower().endswith(('.aax', '.aaxc', '.m4b', '.mp3')):
                    try:
                        if os.path.getsize(filepath) == 0:
                            os.remove(filepath)
                            self.write_log(f"Deleted empty 0-byte file: {filename}")
                            cleaned_count += 1
                    except OSError:
                        pass

            if cleaned_count > 0:
                self.write_log(f"Cleanup complete. Removed {cleaned_count} orphaned files.")
                
        except Exception as e:
            self.write_log(f"Failed to run orphaned file cleanup: {e}")

    def toggle_system_sleep(self, prevent_sleep=True):
        if os.name != 'nt':
            return # Only implemented for Windows

        try:
            import ctypes
            # 0x80000000 = ES_CONTINUOUS, 0x00000001 = ES_SYSTEM_REQUIRED
            if prevent_sleep:
                self.write_log("Applying sleep prevention for active background task.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            else:
                self.write_log("Releasing system sleep prevention.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception as e:
            self.write_log(f"Failed to toggle sleep state: {e}")

    def has_enough_disk_space(self, target_dir, required_bytes):
        import shutil
        try:
            # If the directory doesn't exist yet, check the drive it belongs to
            check_dir = target_dir
            while not os.path.exists(check_dir) and os.path.dirname(check_dir) != check_dir:
                check_dir = os.path.dirname(check_dir)
                
            total, used, free = shutil.disk_usage(check_dir)
            return free > required_bytes
        except Exception as e:
            self.write_log(f"Disk space check failed: {e}")
            return True # Fail open so we don't accidentally block valid operations

    def add_stat(self, stat_name, amount=1):
        stats = self.settings.get("stats", {})
        stats[stat_name] = stats.get(stat_name, 0) + amount
        self.settings["stats"] = stats
        self.db.save_settings(self.settings)
        self.check_achievements()

    def on_file_drop(self, event):
        # Tkinter safely parses the dropped string into a tuple of file paths
        files = self.root.tk.splitlist(event.data)
        
        # Start a background thread so FFprobe doesn't freeze the app if you drop 50 files
        threading.Thread(target=self.process_dropped_files_worker, args=(files,), daemon=True).start()

    def process_dropped_files_worker(self, files):
        valid_exts = [".aax", ".aaxc", ".m4b", ".mp3"]
        added_count = 0
        
        for filepath in files:
            if not os.path.exists(filepath): continue
            
            ext = os.path.splitext(filepath)[1].lower()
            if ext not in valid_exts: continue
            
            filename = os.path.basename(filepath)
            title = filename
            authors = "Unknown Author"
            format_clean = ext.replace(".", "").upper()
            
            self.root.after(0, lambda f=filename: self.dl_status_var.set(f"Importing: {f}"))
            
            if format_clean in ["M4B", "MP3"]:
                try:
                    data = self.converter.get_metadata_and_chapters(filepath)
                    tags = data.get("format", {}).get("tags", {})

                    if "title" in tags: title = tags["title"]
                    if "artist" in tags: authors = tags["artist"]
                    elif "album_artist" in tags: authors = tags["album_artist"]
                except Exception as e:
                    self.write_log(f"Failed to read tags for {filename}: {e}")

            self.local_library[filepath] = {
                "title": title, 
                "format": format_clean, 
                "path": filepath, 
                "authors": authors,
                "owner": self.active_profile
            }
            added_count += 1
            
        if added_count > 0:
            self.db.save_local_db(self.local_library)
            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda c=added_count: self.dl_status_var.set(f"Successfully imported {c} files."))
        else:
            self.root.after(0, lambda: self.dl_status_var.set("No valid audiobooks found in drop."))
            
        self.root.after(4000, lambda: self.dl_status_var.set("Idle"))

    def check_achievements(self):
        stats = self.settings.get("stats", {})
        unlocked = stats.get("unlocked_achievements", [])
        
        for ach_id, data in self.achievements.items():
            if ach_id not in unlocked:
                current_val = stats.get(data["type"], 0)
                if current_val >= data["threshold"]:
                    unlocked.append(ach_id)
                    self.settings["stats"]["unlocked_achievements"] = unlocked
                    self.db.save_settings(self.settings)
                    show_achievement_toast(self, data["title"], data["desc"])

    def enforce_single_instance(self):
        self.lock_port = 43128 # Unique port just for TomeBox
        self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        try:
            # Try to claim the port
            self.lock_socket.bind(('127.0.0.1', self.lock_port))
            self.lock_socket.listen(1)
            
            # Success! We are the first instance. Start listening for wake requests.
            threading.Thread(target=self.instance_listener_worker, daemon=True).start()
            
        except socket.error:
            # Port is already in use! Another TomeBox is running.
            self.write_log("Another instance detected. Sending wake signal...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('127.0.0.1', self.lock_port))
                s.sendall(b"WAKEUP")
                s.close()
            except Exception:
                pass
            
            # Kill this duplicate instance immediately
            sys.exit(0)

    def instance_listener_worker(self):
        while True:
            try:
                conn, addr = self.lock_socket.accept()
                data = conn.recv(1024)
                if data == b"WAKEUP":
                    self.write_log("Wake signal received. Bringing window to front.")
                    self.root.after(0, self.bring_to_front)
                conn.close()
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"Socket listener error: {e}")
                break

    def bring_to_front(self):
        # 1. Un-hide it if it was minimized to the system tray
        self.root.deiconify()
        
        # 2. Lift it above other windows
        self.root.lift()
        
        # 3. Force it to the absolute top, then release the lock so the user can click other things again
        self.root.attributes('-topmost', True)
        self.root.after_idle(self.root.attributes, '-topmost', False)

    def setup_tray_icon(self):
        try:
            # FIX: Look for the icon in the ui folder
            icon_path = os.path.join(self.base_dir, "ui", "tomebox.png")
            
            if not os.path.exists(icon_path):
                self.write_log(f"System tray icon not found at: {icon_path}")
                return
                
            image = Image.open(icon_path)
            
            menu = pystray.Menu(
                item('Show TomeBox', self.show_window_from_tray, default=True),
                item('Quit', self.quit_from_tray)
            )
            
            self.tray_icon = pystray.Icon("TomeBox", image, "TomeBox", menu)
            
            # Run the tray icon loop in a background thread so it doesn't block Tkinter
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self.write_log(f"Failed to initialize system tray: {e}")

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
        self.write_log("Opening Buy Me a Coffee link...")
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
        
        self.auth_save_path = os.path.join(self.base_dir, "data", f"auth_{self.active_profile}.json")
        self.cloud_cache_path = os.path.join(self.base_dir, "data", f"cloud_{self.active_profile}.json")
        
        # Clear current session
        self.api_client.auth = None
        self.auth_bytes.set("")
        self.cloud_items = self.load_cloud_cache()
        
        # Try to load the new profile's auth file
        self.auto_load_auth()
        self.refresh_library_ui()

    def check_dependencies(self):
        import shutil
        import webbrowser
        
        ffmpeg_installed = shutil.which("ffmpeg") is not None
        ffplay_installed = shutil.which("ffplay") is not None
        
        if not ffmpeg_installed or not ffplay_installed:
            self.write_log("WARNING: FFmpeg or FFplay not found in system PATH.")
            
            msg = (
                "FFmpeg is missing from your system.\n\n"
                "TomeBox requires FFmpeg to play, convert, and split audiobooks. "
                "Without it, you will only be able to download files.\n\n"
                "Would you like to open the official FFmpeg download page now?"
            )
            
            # askyesno returns True if they click Yes, False if No
            user_wants_link = messagebox.askyesno("Missing Dependency: FFmpeg", msg)
            
            if user_wants_link:
                self.write_log("Opening FFmpeg download page in browser...")
                webbrowser.open("https://ffmpeg.org/download.html")

    def run_background_sync(self):
        threading.Thread(target=self.silent_sync_worker, daemon=True).start()
        # Schedule the next check in 15 minutes (900000 milliseconds)
        self.root.after(900000, self.run_background_sync)
    
    def db_monitor_worker(self):
        import time
        import os
        
        while True:
            ui_needs_refresh = False
            
            # 1. Check if any actual audio files were deleted from the hard drive
            missing_paths = [path for path in list(self.local_library.keys()) if not os.path.exists(path)]
            
            if missing_paths:
                for path in missing_paths:
                    del self.local_library[path]
                    
                self.write_log(f"Detected {len(missing_paths)} deleted files. Updating library...")
                
                # Save via the manager's locked method
                self.db.save_local_db(self.local_library)
                ui_needs_refresh = True

            # 2. Check if the SQLite database file was edited externally
            if hasattr(self.db, 'db_path') and os.path.exists(self.db.db_path):
                try:
                    current_mtime = os.path.getmtime(self.db.db_path)
                    
                    if self.db.last_db_mtime == 0:
                        self.db.last_db_mtime = current_mtime
                    elif current_mtime > self.db.last_db_mtime:
                        self.write_log("External DB change detected. Syncing local library...")
                        self.db.last_db_mtime = current_mtime
                        self.local_library = self.db.load_local_db()
                        ui_needs_refresh = True
                except Exception as e:
                    self.write_log(f"DB Monitor Error: {e}")
            
            # Redraw the screen if either of the above checks triggered a change
            if ui_needs_refresh:
                self.root.after(0, self.refresh_library_ui)
                
            time.sleep(2)

    def build_menu_bar(self):
        self.root.config(menu="")
        self.menu_frame = ttk.Frame(self.root)
        self.menu_frame.pack(side=tk.TOP, fill="x")

        self.file_menubutton = ttk.Menubutton(self.menu_frame, text="File")
        self.file_menubutton.pack(side=tk.LEFT, padx=5, pady=2)

        self.file_menu = tk.Menu(self.file_menubutton, tearoff=0, relief="flat")
        self.file_menubutton.config(menu=self.file_menu)
        
        self.file_menu.add_command(label="Set Download Folder", command=self.set_download_folder)
        self.file_menu.add_command(label="Authentication & Profiles", command=lambda: open_auth_window(self))
        self.file_menu.add_separator()
        # self.file_menu.add_command(label="Pair Mobile Device (QR)", command=lambda: open_pairing_window(self))
        self.file_menu.add_checkbutton(
            label="Minimize to Tray on Close", 
            variable=self.minimize_to_tray_var, 
            command=self.save_tray_setting
        )
        self.file_menu.add_separator()

        # Appearance Sub-Menu
        self.appearance_menu = tk.Menu(self.file_menu, tearoff=0, relief="flat")
        self.file_menu.add_cascade(label="Appearance", menu=self.appearance_menu)
        
        self.palette_var = tk.StringVar(value=self.settings.get("classic_palette", "light"))
        
        self.appearance_menu.add_radiobutton(label="Light Default", variable=self.palette_var, value="light", command=lambda: self.apply_classic_palette("light"))
        self.appearance_menu.add_radiobutton(label="Dark Charcoal", variable=self.palette_var, value="dark", command=lambda: self.apply_classic_palette("dark"))
        self.appearance_menu.add_radiobutton(label="Terminal Green", variable=self.palette_var, value="terminal", command=lambda: self.apply_classic_palette("terminal"))
        self.appearance_menu.add_separator()
        self.appearance_menu.add_radiobutton(label="Solarized Dark", variable=self.palette_var, value="solarized_dark", command=lambda: self.apply_classic_palette("solarized_dark"))
        self.appearance_menu.add_radiobutton(label="Solarized Light", variable=self.palette_var, value="solarized_light", command=lambda: self.apply_classic_palette("solarized_light"))
        self.appearance_menu.add_separator()
        self.appearance_menu.add_radiobutton(label="Dracula", variable=self.palette_var, value="dracula", command=lambda: self.apply_classic_palette("dracula"))
        self.appearance_menu.add_radiobutton(label="Nordic Slate", variable=self.palette_var, value="nord", command=lambda: self.apply_classic_palette("nord"))
        self.appearance_menu.add_radiobutton(label="Cyberpunk", variable=self.palette_var, value="cyberpunk", command=lambda: self.apply_classic_palette("cyberpunk"))

        self.file_menu.add_separator()

        # Export Sub-Menu
        self.export_menu = tk.Menu(self.file_menu, tearoff=0, relief="flat")
        self.file_menu.add_cascade(label="Export Library", menu=self.export_menu)
        self.export_menu.add_command(label="Export to CSV", command=self.export_csv_worker)
        self.export_menu.add_command(label="Export to HTML Page", command=self.export_html_worker)

        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_closing)

        self.help_menubutton = ttk.Menubutton(self.menu_frame, text="Donate")
        self.help_menubutton.pack(side=tk.LEFT, padx=5, pady=2)

        self.help_menu = tk.Menu(self.help_menubutton, tearoff=0, relief="flat")
        self.help_menubutton.config(menu=self.help_menu)
        
        self.help_menu.add_command(label="Support the Developer ☕", command=self.open_support_link)

        #Achievement menu
        self.file_menu.add_command(label="My Achievements", command=lambda: open_achievements_window(self))
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Enable Web Server", command=self.toggle_web_server)
        self.file_menu.add_separator()

    def build_info_components(self, parent):
        self.cover_frame = ttk.Frame(parent)
        self.cover_frame.pack(fill="x", padx=5, pady=10)
        
        self.cover_label = ttk.Label(self.cover_frame, text="No Cover Art")
        self.cover_label.pack(pady=5)
        
        self.author_label = ttk.Label(self.cover_frame, text="", font=("Segoe UI", 10, "italic"))
        self.author_label.pack(pady=2)
        
        self.current_cover_photo = None

    def build_library_components(self, parent):
        self.main_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        lib_frame = ttk.LabelFrame(self.main_paned, text="", padding=10)
        self.main_paned.add(lib_frame, weight=1)

        self.queue_frame = ttk.LabelFrame(self.main_paned, text="Active Downloads", padding=10)
        
        queue_controls = ttk.Frame(self.queue_frame)
        queue_controls.pack(fill="x", pady=(0, 5))
        ttk.Button(queue_controls, text="Cancel All Downloads", command=self.cancel_all_downloads).pack(side=tk.RIGHT)

        # sv_ttk background color applied to the canvas
        self.queue_canvas = tk.Canvas(self.queue_frame, height=120, bg="#1c1c1c", highlightthickness=0)
        queue_scroll = ttk.Scrollbar(self.queue_frame, orient="vertical", command=self.queue_canvas.yview)
        

        # sv_ttk background color applied to the inner frame
        self.queue_inner = tk.Frame(self.queue_canvas, bg="#1c1c1c")

        self.queue_inner.bind("<Configure>", lambda e: self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox("all")))
        self.queue_canvas.create_window((0, 0), window=self.queue_inner, anchor="nw")
        self.queue_canvas.configure(yscrollcommand=queue_scroll.set)

        self.queue_canvas.pack(side="left", fill="both", expand=True)
        queue_scroll.pack(side="right", fill="y")

        self.active_downloads = {}

        filter_frame = ttk.Frame(lib_frame)
        filter_frame.pack(fill="x", pady=(0, 5))

        ttk.Label(filter_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_library_ui()) 
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=35)
        search_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.filter_var = tk.StringVar(value="All")
        filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=["All", "Downloaded", "Cloud Only"], state="readonly", width=15)
        filter_combo.pack(side=tk.LEFT)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_library_ui())

        ttk.Label(filter_frame, text="Shelf:").pack(side=tk.LEFT, padx=(10, 5))
        self.shelf_filter_var = tk.StringVar(value="All Shelves")
        self.shelf_combo = ttk.Combobox(filter_frame, textvariable=self.shelf_filter_var, state="readonly", width=15)
        self.shelf_combo.pack(side=tk.LEFT)
        self.shelf_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_library_ui())

        self.view_btn = ttk.Button(filter_frame, text="Grid View", command=self.toggle_library_view)
        self.view_btn.pack(side=tk.RIGHT, padx=5)

        self.toggle_queue_btn = ttk.Button(filter_frame, text="Show/Hide Queue", command=self.toggle_queue_visibility)
        self.toggle_queue_btn.pack(side=tk.RIGHT, padx=5)

        self.dl_all_btn = ttk.Button(filter_frame, text="Download Missing", command=self.start_download_all)
        self.dl_all_btn.pack(side=tk.RIGHT, padx=(5, 5))

        tree_frame = ttk.Frame(lib_frame)
        tree_frame.pack(fill="both", expand=True, pady=5)

        scroll = ttk.Scrollbar(tree_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        self.library_tree = ttk.Treeview(tree_frame, columns=("Title", "Author", "Series", "Duration", "ASIN", "Status"), show="headings", yscrollcommand=scroll.set)
        scroll.config(command=self.library_tree.yview)
        self.library_tree.bind("<<TreeviewSelect>>", self.on_item_select)
        
        self.current_view_mode = "list"
        self.grid_images_ref = [] 
        
        
        self.grid_canvas = tk.Canvas(tree_frame, bg="#1c1c1c", highlightthickness=0)
        self.grid_inner = tk.Frame(self.grid_canvas, bg="#1c1c1c")
        self.grid_window_id = self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")
        
        
        self.grid_canvas.configure(yscrollcommand=scroll.set)
        self.grid_inner.bind("<Configure>", lambda e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        
        
        self.grid_canvas.bind("<Configure>", self.on_canvas_resize)
        self.root.bind_all("<MouseWheel>", self._on_grid_scroll)  
        self.root.bind_all("<Button-4>", self._on_grid_scroll)    
        self.root.bind_all("<Button-5>", self._on_grid_scroll)    
        self.root.bind_all("<Button-3>", self.show_context_menu)

        self.empty_state_frame = tk.Frame(tree_frame)
        self.empty_state_img_label = ttk.Label(self.empty_state_frame)
        self.empty_state_img_label.pack(pady=(80, 20))
        

        empty_text = (
            "Your library is completely empty.\n\n"
            "To get started:\n"
            "1. Navigate to 'File -> Authentication & Profiles' to link your Audible account.\n"
            "2. Download your library or drag and drop .aax or .m4b files directly into this window to import local media."
        )
        ttk.Label(self.empty_state_frame, text=empty_text, justify="center", font=("Segoe UI", 12)).pack()

        for col in self.library_tree["columns"]:
            self.library_tree.heading(col, text=col, command=lambda _col=col: self.sort_treeview(self.library_tree, _col, False))
            
        self.library_tree.column("Title", width=250)
        self.library_tree.column("Author", width=120)
        self.library_tree.column("Series", width=120)
        self.library_tree.column("Duration", width=70)
        self.library_tree.column("ASIN", width=90)
        self.library_tree.column("Status", width=110)
        self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
        
        self.library_tree.bind("<Double-1>", self.master_play)

        btn_frame = ttk.Frame(lib_frame)
        btn_frame.pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Refresh Cloud", command=self.fetch_cloud_library).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Download Selected", command=lambda: self.handle_action_on_selected("download")).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Convert Selected", command=lambda: self.handle_action_on_selected("convert")).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Convert All", command=self.start_convert_all_thread).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Manage Shelves", command=self.manage_shelves_prompt).pack(side=tk.LEFT, padx=5)

        local_btn_frame = ttk.Frame(lib_frame)
        local_btn_frame.pack(fill="x", pady=2)
        ttk.Button(local_btn_frame, text="Add Local File", command=self.add_local_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(local_btn_frame, text="Remove from List", command=self.remove_local_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(local_btn_frame, text="Scrape Metadata", command=lambda: self.handle_action_on_selected("scrape")).pack(side=tk.LEFT, padx=5)

        dl_prog_frame = ttk.Frame(lib_frame)
        dl_prog_frame.pack(fill="x", padx=5)
        
        self.dl_status_var = tk.StringVar(value="Idle")
        self.dl_progress_var = tk.DoubleVar()
        ttk.Label(dl_prog_frame, textvariable=self.dl_status_var).pack(side=tk.TOP, anchor="w")
        ttk.Progressbar(dl_prog_frame, variable=self.dl_progress_var, maximum=100).pack(side=tk.TOP, fill="x")

        self.refresh_library_ui()

    def build_player_components(self, parent):
        play_frame = ttk.LabelFrame(parent, text="Playback", padding=10)
        play_frame.pack(fill="x", expand=True, padx=5, pady=5)

        self.is_playing = False
        self.is_paused = False
        self.chapter_duration = 0
        self.current_play_time = 0

        top_row = ttk.Frame(play_frame)
        top_row.pack(fill="x", pady=2)
        
        self.info_label = ttk.Label(top_row, text="", justify="left")
        self.info_label.pack(side=tk.LEFT, padx=5)
        
        self.time_label = ttk.Label(top_row, text="00:00 / 00:00")
        self.time_label.pack(side=tk.RIGHT, padx=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(play_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=5, pady=5)

        controls_frame = ttk.Frame(play_frame)
        controls_frame.pack(pady=5)

        ttk.Button(controls_frame, text="<< Prev Chapter", width=14, command=self.prev_chapter).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="-30s", width=5, command=lambda: self.seek_audio(-30)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Play", width=8, command=self.master_play).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Pause", width=8, command=self.pause_audio).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="+30s", width=5, command=lambda: self.seek_audio(30)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Next Chapter >>", width=14, command=self.next_chapter).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="🔖 Bookmark", width=12, command=self.add_bookmark).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(controls_frame, text="📑 Chapters", command=lambda: open_chapter_window(self)).pack(side=tk.LEFT, padx=(15, 2))

        self.playback_speed = tk.StringVar(value="1.0x")
        speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
        
        speed_menu = ttk.Combobox(controls_frame, textvariable=self.playback_speed, values=speed_options, state="readonly", width=5)
        speed_menu.bind("<<ComboboxSelected>>", self.on_speed_change)
        speed_menu.pack(side=tk.LEFT, padx=10)

        self.volume_var = tk.DoubleVar(value=100.0)
        vol_frame = ttk.Frame(controls_frame)
        vol_frame.pack(side=tk.LEFT, padx=5)
        ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
        self.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume_var, command=self.on_volume_change, length=80)
        self.vol_slider.pack(side=tk.LEFT)

        timer_frame = ttk.Frame(controls_frame)
        timer_frame.pack(side=tk.LEFT, padx=15)
        
        self.timer_btn = ttk.Button(timer_frame, text="Sleep: Off", command=lambda: open_sleep_menu(self), width=16)
        self.timer_btn.pack(side=tk.LEFT)
        
        self.timer_countdown_var = tk.StringVar(value="")
        ttk.Label(timer_frame, textvariable=self.timer_countdown_var, width=5).pack(side=tk.LEFT)

        self.voice_boost_var = tk.BooleanVar(value=False)
        self.skip_silence_var = tk.BooleanVar(value=False)
        
        filters_frame = ttk.Frame(play_frame)
        filters_frame.pack(fill="x", pady=(5, 0))
        
        ttk.Label(filters_frame, text="Filters:").pack(side=tk.LEFT, padx=(5, 10))
        
        ttk.Checkbutton(
            filters_frame, text="Voice Boost (Compressor)", 
            variable=self.voice_boost_var, command=self.on_filter_change
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Checkbutton(
            filters_frame, text="Skip Silence", 
            variable=self.skip_silence_var, command=self.on_filter_change
        ).pack(side=tk.LEFT, padx=5)

    def build_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        
        # Playback Controls
        self.context_menu.add_command(label="▶ Play", command=self.master_play)
        # self.context_menu.add_command(label="⏸ Pause", command=self.pause_audio)
        self.context_menu.add_separator()


        # File Operations 
        self.context_menu.add_command(label="⬇️ Download", command=lambda: self.handle_action_on_selected("download"))
        self.context_menu.add_command(label="🔄 Convert", command=lambda: self.handle_action_on_selected("convert"))
        self.context_menu.add_command(label="🔍 Scrape Metadata", command=lambda: self.handle_action_on_selected("scrape"))
        # self.context_menu.add_separator()

    def build_bookmarks_components(self, parent):
        self.bm_frame = ttk.LabelFrame(parent, text="Bookmarks & Notes", padding=10)
        self.bm_frame.pack(fill="both", expand=True, padx=5, pady=5)

        scroll = ttk.Scrollbar(self.bm_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        self.bm_tree = ttk.Treeview(self.bm_frame, columns=("Time", "Note"), show="headings", yscrollcommand=scroll.set, height=5)
        self.bm_tree.heading("Time", text="Time")
        self.bm_tree.heading("Note", text="Note")
        
        self.bm_tree.column("Time", width=140, anchor="w", stretch=False)
        self.bm_tree.column("Note", width=150, anchor="w")
        self.bm_tree.pack(fill="both", expand=True)

        scroll.config(command=self.bm_tree.yview)

        # Double click to jump to the bookmark
        self.bm_tree.bind("<Double-1>", self.jump_to_bookmark)
        
        btn_frame = ttk.Frame(self.bm_frame)
        btn_frame.pack(fill="x", pady=(5, 0))
        ttk.Button(btn_frame, text="Delete Selected", command=self.delete_bookmark).pack(side=tk.RIGHT)

    def show_context_menu(self, event):
        # If we are in the list view, select the item under the cursor first
        if getattr(self, 'current_view_mode', 'list') == "list":
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
            
        item = tree.item(selected)
        # The index in the Treeview is 1-based, so subtract 1 for the 0-based list
        target_idx = int(item['values'][0]) - 1 

        if 0 <= target_idx < len(self.chapters):
            # Close the window
            self.chapter_win.destroy()
            
            # Stop current playback
            self.stop_audio()
                
            # 1. FIXED: Correct variable name (idx instead of index)
            self.current_chapter_idx = target_idx
            
            # 2. FIXED: Reset relative time to the start of the chapter
            self.current_play_time = 0.0
            
            self.play_chapter()

    def start_convert_all_thread(self):
        to_convert = [path for path, data in self.local_library.items() if data.get("format", "").upper() in ["AAX", "AAXC"]]
        
        if not to_convert:
            messagebox.showinfo("Convert All", "No AAX or AAXC files found to convert.")
            return
        required_bytes = sum(os.path.getsize(p) for p in to_convert if os.path.exists(p))
        if not self.has_enough_disk_space(self.base_dir, required_bytes + (500 * 1024 * 1024)): # Add 500MB padding
            required_gb = required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage", 
                f"Batch conversion requires at least {required_gb:.2f} GB of free space on your drive.\n\n"
                "Please free up space and try again."
            )
            return
        if not messagebox.askyesno("Convert All", f"Found {len(to_convert)} files to convert.\nThis will process sequentially in the background. Proceed?"):
            return
            
        threading.Thread(target=self.convert_all_worker, args=(to_convert,), daemon=True).start()

    def on_item_select(self, event=None):
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected: return
            item = self.library_tree.item(selected)
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]
        else:
            if not getattr(self, '_selected_grid_item', None): return
            item = self._selected_grid_item
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]

        if hasattr(self, 'author_label'):
            self.author_label.config(text=authors)
        
        cover_path = None
        covers_dir = getattr(self, 'covers_dir', self.base_dir)
        
        if asin and asin != "Unknown":
            padded_asin = str(asin).zfill(10)
            
            # Check for the padded ASIN first, then fallback to raw
            test_path_padded = os.path.join(covers_dir, f"{padded_asin}.jpg")
            test_path_raw = os.path.join(covers_dir, f"{asin}.jpg")
            
            if os.path.exists(test_path_padded):
                cover_path = test_path_padded
            elif os.path.exists(test_path_raw):
                cover_path = test_path_raw
                
        if not cover_path:
            for p, d in getattr(self, 'local_library', {}).items():
                if d.get("title") == title:
                    test_local = os.path.splitext(p)[0] + "_cover.jpg"
                    if os.path.exists(test_local):
                        cover_path = test_local
                    break

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

    def manage_shelves_prompt(self):
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Please select an audiobook to tag.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item') or not self._selected_grid_item:
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

        if getattr(self, 'is_playing', False):
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()

    def handle_window_close(self):
        if self.minimize_to_tray_var.get():
            self.hide_window_to_tray()
        else:
            if hasattr(self, 'tray_icon') and self.tray_icon:
                self.tray_icon.stop()
            self.on_closing()

    def silent_sync_worker(self):
        if not self.api_client.auth:
            return

        try:
            self.write_log("Background sync: Polling Audible API...")
            client = audible.Client(auth=self.api_client.auth)
            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors", num_results=1000)
            new_items = response.get("items", [])

            if len(new_items) != len(self.cloud_items):
                self.write_log(f"Background sync: Detected library change. Old: {len(self.cloud_items)}, New: {len(new_items)}")
                self.cloud_items = new_items
                self.save_cloud_cache()
                self.root.after(0, self.refresh_library_ui)
            else:
                self.write_log("Background sync: No changes detected.")

        except Exception as e:
            self.write_log(f"Background sync failed silently: {e}")
    
    def on_closing(self):
        self.save_playback_state()
        if self.player_process:
            self.player_process.terminate()
        self.root.destroy()

    def save_playback_state(self):
        if getattr(self, 'file_path', None) and self.file_path in self.local_library:
            chap_idx = getattr(self, 'current_chapter_idx', 0)
            rel_time = getattr(self, 'current_play_time', 0.0)
            
            self.local_library[self.file_path]["last_chapter"] = chap_idx
            self.local_library[self.file_path]["last_time"] = rel_time
            
            abs_time = rel_time
            if hasattr(self, 'chapters') and self.chapters and chap_idx < len(self.chapters):
                abs_time = float(self.chapters[chap_idx].get("start_time", 0)) + rel_time
                self.local_library[self.file_path]["last_position"] = abs_time

            if "progress" not in self.local_library[self.file_path]:
                self.local_library[self.file_path]["progress"] = {}
            self.local_library[self.file_path]["progress"][self.active_profile] = abs_time
                
            self.db.save_local_db(self.local_library)

            self.settings[f"last_played_{self.active_profile}"] = self.file_path
            self.db.save_settings(self.settings)
    def sync_playhead_from_remote(self, abs_position):
        """Called by the web server when the phone updates the current book's time."""
        try:
            # Don't interrupt if the PC is actively playing audio right now
            if getattr(self.player, 'is_playing', False):
                return 
                
            # Update the PC's internal memory so it doesn't save stale data on close
            if hasattr(self, 'chapters') and self.chapters:
                for idx, ch in enumerate(self.chapters):
                    start = float(ch.get("start_time", 0))
                    end = float(ch.get("end_time", 0))
                    if start <= abs_position <= end:
                        self.current_chapter_idx = idx
                        self.current_play_time = abs_position - start
                        break
            
            # Visually move the progress bar on the PC screen
            if hasattr(self, 'progress_var') and hasattr(self, 'chapters'):
                total_duration = float(self.chapters[-1].get("end_time", 0))
                if total_duration > 0:
                    self.progress_var.set((abs_position / total_duration) * 100)
                    
        except Exception as e:
            self.write_log(f"Failed to sync remote playhead: {e}")
            
    def cue_last_played(self):
        last_path = self.settings.get(f"last_played_{self.active_profile}")
        if last_path and last_path in self.local_library and os.path.exists(last_path):
            self.load_specific_file(last_path)

    def fetch_metadata_worker(self, filepath):
        local_data = self.local_library.get(filepath, {})
        title = local_data.get("title", "")
        asin = local_data.get("asin")

        authors = ""
        for item in getattr(self, 'cloud_items', []):
            if item.get("title") == title or item.get("asin") == asin:
                asin = item.get("asin")
                raw_authors = item.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                break
        
        if not asin:
            self.root.after(0, lambda: self.cover_label.config(image="", text="Metadata Unavailable"))
            self.root.after(0, lambda: self.author_label.config(text=authors))
            return

        cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")

        if os.path.exists(cover_path):
            try:
                img = Image.open(cover_path)
                img.thumbnail((400, 400))
                photo = ImageTk.PhotoImage(img)
                
                def update_ui_local():
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                    self.author_label.config(text=authors)
                
                self.root.after(0, update_ui_local)
                return 
            except Exception as e:
                self.write_log(f"Failed to load local cover cache, falling back to API: {e}")

        if not self.api_client.auth:
            return
            
        try:
            client = audible.Client(auth=self.api_client.auth)
            resp = client.get(f"1.0/catalog/products/{asin}", response_groups="media,product_attrs")
            product = resp.get("product", {})
            
            if not authors:
                raw_authors = product.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            images = product.get("product_images", {})
            image_url = images.get("500") or images.get("252")
            
            if image_url:
                img_data = requests.get(image_url).content

                with open(cover_path, "wb") as f:
                    f.write(img_data)
                    
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((250, 250))
                photo = ImageTk.PhotoImage(img)
                
                def update_ui_api():
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                    self.author_label.config(text=authors)
                
                self.root.after(0, update_ui_api)
            else:
                self.root.after(0, lambda: self.cover_label.config(image="", text="No Cover Art Found"))
                self.root.after(0, lambda: self.author_label.config(text=authors))
                
        except Exception as e:
            self.write_log(f"Metadata Fetch Error: {e}")
            self.root.after(0, lambda: self.cover_label.config(image="", text="Failed to load metadata"))
    
    def load_cloud_cache(self):
        if os.path.exists(self.cloud_cache_path):
            try:
                with open(self.cloud_cache_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_cloud_cache(self):
        try:
            with open(self.cloud_cache_path, "w") as f:
                json.dump(self.cloud_items, f, indent=4)
        except Exception as e:
            self.write_log(f"Failed to save cloud cache: {e}")

    def set_download_folder(self):
        directory = filedialog.askdirectory(title="Select Default Download Folder")
        if directory:
            self.default_download_dir = directory
            self.settings["download_dir"] = directory
            self.db.save_settings(self.settings)
            messagebox.showinfo("Folder Saved", f"Default download folder updated to:\n{directory}")

    def download_title_prompt(self):
        selected = self.cloud_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title from the Cloud Library first.")
            return

        item = self.cloud_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

        if not asin or asin == "Unknown":
            messagebox.showerror("Data Error", "This item does not have a valid ASIN.")
            return

        save_dir = self.default_download_dir
        if not save_dir:
            save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
            if not save_dir:
                return

        self.write_log(f"Starting download process for ASIN: {asin}")
        threading.Thread(target=self.download_single_worker, args=(asin, title, save_dir), daemon=True).start()

    def download_single_worker(self, asin, title, save_dir):
        self.download_worker(asin, title, save_dir, is_queue=False)

    def download_queue_worker(self, items, save_dir):
        try:
            # 1. Use the new cross-platform wakepy context manager
            with keep.running():
                for item in items:
                    title = item[0]
                    asin = item[3]
                    
                    # 2. Check if the user hit the "Cancel All" button
                    if asin in getattr(self, 'active_downloads', {}) and self.active_downloads[asin].get("cancel_flag"):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Canceled"))
                        continue
                    
                    safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                    if os.path.exists(os.path.join(save_dir, f"{safe_title}.aaxc")) or os.path.exists(os.path.join(save_dir, f"{safe_title}.aax")):
                        self.write_log(f"Skipping {title}, file already exists.")
                        # Optionally update the UI so it doesn't get stuck saying "Waiting..."
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Skipped"))
                        continue

                    # 3. Isolate each download so one failure doesn't kill the whole queue
                    try:
                        self.download_worker(asin, title, save_dir, is_queue=True)
                    except Exception as e:
                        self.write_log(f"Failed to queue download for {title}: {e}")
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Failed"))
                        
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("All downloads completed."))
            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, lambda: messagebox.showinfo("Download Queue Finished", "Finished processing all titles."))
    
    def apply_classic_palette(self, palette_name):
        apply_theme(self, palette_name)

    def download_all_prompt(self):
        save_dir = getattr(self, 'default_download_dir', '')
        if not save_dir:
            save_dir = filedialog.askdirectory(title="Select Download Folder for All Titles")
            if not save_dir: return
            self.default_download_dir = save_dir
            self.settings["download_dir"] = save_dir
            self.db.save_settings(self.settings)
            self.lbl_download_dir.config(text=save_dir)

        items_to_download = []
        for child in self.cloud_tree.get_children():
            values = self.cloud_tree.item(child)['values']
            if values[3] and values[3] != "Unknown":
                items_to_download.append(values)

        if not items_to_download:
            return

        threading.Thread(target=self.download_queue_worker, args=(items_to_download, save_dir), daemon=True).start()

    def toggle_library_view(self):
        scroll_bar = None
        for child in self.library_tree.master.winfo_children():
            if isinstance(child, ttk.Scrollbar) and str(child.cget("orient")) == "vertical":
                scroll_bar = child
                break

        if self.current_view_mode == "list":
            self.current_view_mode = "grid"
            self.view_btn.config(text="List View")
            self.library_tree.pack_forget()
            
            if self.cloud_items or self.local_library:
                self.grid_canvas.pack(side=tk.LEFT, fill="both", expand=True)
            
            if scroll_bar:
                scroll_bar.config(command=self.grid_canvas.yview)
                self.grid_canvas.config(yscrollcommand=scroll_bar.set)
        else:
            self.current_view_mode = "list"
            self.view_btn.config(text="Grid View")
            self.grid_canvas.pack_forget()
            
            if self.cloud_items or self.local_library:
                self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
            
            if scroll_bar:
                scroll_bar.config(command=self.library_tree.yview)
                self.library_tree.config(yscrollcommand=scroll_bar.set)
            
        self.refresh_library_ui()

    def on_canvas_resize(self, event):

        if hasattr(self, 'grid_window_id'):
            self.grid_canvas.itemconfig(self.grid_window_id, width=event.width)
        if getattr(self, '_last_canvas_width', None) == event.width:
            return
        self._last_canvas_width = event.width
        if hasattr(self, '_resize_timer'):
            self.root.after_cancel(self._resize_timer)
        self._resize_timer = self.root.after(200, self.draw_grid_view)

    def draw_grid_view(self):
        if getattr(self, 'current_view_mode', 'list') != "grid": return
        
        for widget in self.grid_inner.winfo_children():
            widget.destroy()

        if not hasattr(self, 'cover_cache'):
            self.cover_cache = {}

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
            title, authors, series_str, duration_str, asin, status = row_data

            outer_card = tk.Frame(self.grid_inner, bg=default_bg)
            outer_card.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)

            card = tk.Frame(outer_card, bg=default_bg, width=170, height=240, bd=0, highlightthickness=0)
            card.pack_propagate(False) 
            card.pack(padx=2, pady=2) 
            img_obj = None
            if asin in self.cover_cache:
                img_obj = self.cover_cache[asin]
            else:
                cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
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
            text_label = tk.Label(card, text=display_title, bg=default_bg, fg=default_fg, font=("Segoe UI", 9), wraplength=150, justify="center", bd=0, highlightthickness=0, takefocus=0)
            text_label.pack(pady=(5, 0))
            
            def on_card_click(e, oc=outer_card, t=title, a=asin, s=status):

                if hasattr(self, '_last_selected_card_frame') and self._last_selected_card_frame.winfo_exists():
                    self._last_selected_card_frame.config(bg=default_bg)
                
                oc.config(bg=select_bg)
                
                self._last_selected_card_frame = oc 
                self._selected_grid_item = {'values': [t, "", "", "", a, s]}
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

    def cancel_all_downloads(self):
        if not getattr(self, 'active_downloads', None):
            return

        if messagebox.askyesno("Cancel All", "Cancel all active and pending downloads?"):
            for asin, data in self.active_downloads.items():
                current_status = data["status_var"].get()
                if not data["cancel_flag"] and current_status not in ["Complete", "Failed", "Canceled"]:
                    data["cancel_flag"] = True
                    data["status_var"].set("Canceling...")
            
            self.write_log("User initiated Cancel All Downloads.")

            self.dl_status_var.set("Downloads Canceled")
            self.dl_progress_var.set(0)
            self.root.after(3000, lambda: self.dl_status_var.set("Idle"))
            self.root.after(3000, lambda: self.toggle_queue_drawer(False))

    def toggle_queue_drawer(self, show=True):
        current_panes = self.main_paned.panes()
        queue_str = str(self.queue_frame)
        
        if show and queue_str not in current_panes:
            self.main_paned.add(self.queue_frame, weight=0)
        elif not show and queue_str in current_panes:
            self.main_paned.forget(self.queue_frame)

    def add_queue_ui_row(self, asin, title):
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

        cancel_btn = ttk.Button(row_frame, text="✕", command=lambda a=asin: self.cancel_download(a))
        cancel_btn.pack(side=tk.RIGHT)

        self.active_downloads[asin] = {
            "frame": row_frame,
            "prog_var": prog_var,
            "status_var": status_var,
            "cancel_flag": False
        }
        
    def cancel_download(self, asin):
        if asin in self.active_downloads:
            self.active_downloads[asin]["cancel_flag"] = True
            self.active_downloads[asin]["status_var"].set("Canceling...")

    def start_download_all(self):
        local_titles = {data["title"] for path, data in self.local_library.items()}
        missing_items = [item for item in getattr(self, 'cloud_items', []) if item.get("title") not in local_titles]

        if not missing_items:
            messagebox.showinfo("Up to Date", "Your local library already has all cloud items downloaded.")
            return
        save_dir = getattr(self, 'default_download_dir', self.base_dir)
        estimated_bytes_per_book = 500 * 1024 * 1024 # 500 MB
        total_required_bytes = len(missing_items) * estimated_bytes_per_book
        
        if not self.has_enough_disk_space(save_dir, total_required_bytes):
            required_gb = total_required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage", 
                f"Downloading {len(missing_items)} books requires approximately {required_gb:.2f} GB of free space in your target folder.\n\n"
                "Please change your download directory or free up space."
            )
            return
        if messagebox.askyesno("Download All", f"Found {len(missing_items)} missing audiobooks.\n\nDo you want to batch download them all now? This may take a while depending on your internet connection."):
            self.dl_all_btn.config(state=tk.DISABLED)
            threading.Thread(target=self.download_all_worker, args=(missing_items,), daemon=True).start()

    def download_all_worker(self, missing_items):
        total = len(missing_items)
        
        save_dir = getattr(self, 'default_download_dir', "")
        if not save_dir:
            save_dir = getattr(self, 'base_dir', os.getcwd())

        self.root.after(0, lambda: self.toggle_queue_drawer(True))

        for item in missing_items:
            asin = item.get("asin")
            title = item.get("title", "Unknown")
            self.root.after(0, self.add_queue_ui_row, asin, title)

        try:
            with keep.running():
                for idx, item in enumerate(missing_items):
                    title = item.get("title", "Unknown")
                    asin = item.get("asin")

                    if asin in getattr(self, 'active_downloads', {}) and self.active_downloads[asin].get("cancel_flag"):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Canceled"))
                        continue
                    
                    self.root.after(0, lambda i=idx+1, t=total, name=title: self.dl_status_var.set(f"Batch Downloading ({i}/{t}): {name}..."))
                    
                    if asin in getattr(self, 'active_downloads', {}):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Starting..."))
                    
                    try:
                        self.download_worker(asin, title, save_dir, is_queue=True)
                    except Exception as e:
                        self.write_log(f"Failed to batch download {title}: {e}")
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Failed"))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Batch Download Complete"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, self.refresh_library_ui)
            if hasattr(self, 'dl_all_btn'):
                self.root.after(0, lambda: self.dl_all_btn.config(state=tk.NORMAL))
            
            self.root.after(5000, lambda: self.toggle_queue_drawer(False))
            self.root.after(5000, lambda: self.dl_status_var.set("Idle"))

    def refresh_library_ui(self, *args):
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        search_query = self.search_var.get().lower()
        current_filter = self.filter_var.get()
        current_shelf = getattr(self, 'shelf_filter_var', tk.StringVar(value="All Shelves")).get()

        local_titles = {data["title"]: data for path, data in self.local_library.items()}
        cloud_titles = []
        rows_to_insert = []

        all_unique_shelves = set()
        shelves_db = self.settings.get("shelves_db", {})

        master_metadata = {}
        for f in os.listdir(self.base_dir):
            if f.startswith("cloud_") and f.endswith(".json") or f == "cloud_cache.json":
                try:
                    with open(os.path.join(self.base_dir, f), "r") as file:
                        for item in json.load(file):
                            if item.get("title"):
                                master_metadata[item["title"]] = item
                except Exception:
                    pass

        for item in getattr(self, 'cloud_items', []):
            if item.get("title"):
                master_metadata[item["title"]] = item

        for item in getattr(self, 'cloud_items', []):
            title = item.get("title", "Unknown")
            cloud_titles.append(title)
            
            raw_authors = item.get("authors") or []
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            raw_series = item.get("series") or []
            series_list = []
            for s in raw_series:
                if isinstance(s, dict) and s.get("title"):
                    series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
            series_str = ", ".join(series_list)
            
            duration_min = item.get("runtime_length_min", 0)
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            asin = item.get("asin", "Unknown")
            
            local_data = local_titles.get(title)
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            
            rows_to_insert.append((title, authors, series_str, duration_str, asin, status))
            all_unique_shelves.update(shelves_db.get(asin, []))

        for path, data in self.local_library.items():
            if data["title"] not in cloud_titles:
                title = data["title"]
                asin = data.get("asin", "Unknown")
                meta = master_metadata.get(title, {})

                # Extract rich metadata safely
                if meta.get("authors"):
                    raw_authors = meta.get("authors")
                    loc_authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                else:
                    loc_authors = data.get("authors", "Local File")

                if meta.get("series"):
                    raw_series = meta.get("series")
                    series_list = [f"{s.get('title')} (Bk {s.get('sequence', '')})" for s in raw_series if isinstance(s, dict) and s.get("title")]
                    loc_series = ", ".join(series_list)
                else:
                    loc_series = data.get("series", "N/A")

                duration_min = meta.get("runtime_length_min") or data.get("duration_min", 0)
                if duration_min > 0:
                    hours, mins = divmod(duration_min, 60)
                    loc_duration = f"{hours}h {mins}m"
                else:
                    loc_duration = "N/A"

                if asin == "Unknown" and meta.get("asin"):
                    asin = meta.get("asin")

                rows_to_insert.append((title, loc_authors, loc_series, loc_duration, asin, f"Downloaded ({data['format']})"))
                all_unique_shelves.update(shelves_db.get(asin, []))

        shelf_list = ["All Shelves"] + sorted(list(all_unique_shelves))
        if hasattr(self, 'shelf_combo'):
            self.shelf_combo.config(values=shelf_list)
            if current_shelf not in shelf_list:
                self.shelf_filter_var.set("All Shelves")
                current_shelf = "All Shelves"

        filtered_rows = []
        for row in rows_to_insert:
            title, authors, series_str, duration_str, asin, status = row

            if current_filter == "Downloaded" and "Downloaded" not in status:
                continue
            if current_filter == "Cloud Only" and status != "Cloud Only":
                continue

            if current_shelf != "All Shelves":
                book_shelves = shelves_db.get(asin, [])
                if current_shelf not in book_shelves:
                    continue

            if search_query:
                search_target = f"{title} {authors} {series_str}".lower()
                if search_query not in search_target:
                    continue

            filtered_rows.append(row)

        self._current_filtered_data = filtered_rows

        is_completely_empty = (not getattr(self, 'cloud_items', [])) and (not self.local_library)

        if is_completely_empty:
            self.library_tree.pack_forget()
            self.grid_canvas.pack_forget()
            if hasattr(self, 'empty_state_frame'):
                self.empty_state_frame.pack(fill="both", expand=True)
        else:
            if hasattr(self, 'empty_state_frame'):
                self.empty_state_frame.pack_forget()
                
            if self.current_view_mode == "list":
                self.grid_canvas.pack_forget()
                self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
                for row in filtered_rows:
                    self.library_tree.insert("", "end", values=row)
            else:
                self.library_tree.pack_forget()
                self.grid_canvas.pack(side=tk.LEFT, fill="both", expand=True)
                self.draw_grid_view()
    
    def handle_action_on_selected(self, action_type):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item'):
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        asin = item['values'][4]

        local_path = None
        for path, data in self.local_library.items():
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
                if not asin or asin == "Unknown":
                    messagebox.showerror("Error", "Cannot download a file without an ASIN.")
                    return

                save_dir = getattr(self, 'default_download_dir', '')
                if not save_dir:
                    save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
                    if not save_dir:
                        return

                self.write_log(f"Queuing download for {title}. Post-action: {action_type}")
                threading.Thread(target=self.download_worker, args=(asin, title, save_dir, False, action_type), daemon=True).start()

    def start_scrape_thread(self, filepath):
        if not self.api_client.auth:
            messagebox.showwarning("Not Logged In", "An Audible login is required to search the catalog for ASINs.")
            return
        
        data = self.local_library.get(filepath, {})
        current_title = data.get("title", os.path.basename(filepath))
        
        query = simpledialog.askstring("Search Catalog", "Enter book title or author to search:", initialvalue=current_title)
        if not query: return
        
        self.dl_status_var.set("Searching catalog...")
        threading.Thread(target=self.scrape_search_worker, args=(filepath, query), daemon=True).start()

    def scrape_search_worker(self, filepath, query):
        try:
            client = audible.Client(auth=self.api_client.auth)
            resp = client.get("1.0/catalog/products", title=query, num_results=5, response_groups="product_desc,product_attrs,contributors")
            products = resp.get("products", [])
            
            if not products:
                self.root.after(0, lambda: messagebox.showinfo("No Results", "No matches found for that title."))
                return
                
            self.root.after(0, lambda: self.show_scrape_results(filepath, products))
        except Exception as e:
            self.write_log(f"Scrape search error: {e}")
            self.root.after(0, lambda: messagebox.showerror("Search Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

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
            listbox.insert(tk.END, f"{title} | {authors} ({p.get('asin')})")
            
        def on_select():
            sel = listbox.curselection()
            if not sel: return
            selected_asin = products[sel[0]].get("asin")
            popup.destroy()
            self.dl_status_var.set("Fetching Audnexus data...")
            threading.Thread(target=self.apply_scraped_metadata, args=(filepath, selected_asin), daemon=True).start()
            
        ttk.Button(popup, text="Apply Metadata", command=on_select).pack(pady=(0, 10))

    def apply_scraped_metadata(self, filepath, asin):
        try:
            client = audible.Client(auth=self.api_client.auth)
            resp = client.get(f"1.0/catalog/products/{asin}", response_groups="product_desc,product_attrs,contributors,media,series")
            product = resp.get("product", {})
            
            if not product:
                raise Exception("Audible API returned no data for this ASIN.")
                
            title = product.get("title", "Unknown Title")
            
            raw_authors = product.get("authors", [])
            authors = ", ".join([a.get("name", "") for a in raw_authors])
            
            raw_series = product.get("series", [])
            series_list = []
            for s in raw_series:
                if isinstance(s, dict) and s.get("title"):
                    series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
            series_str = ", ".join(series_list) if series_list else ""
            
            duration_min = product.get("runtime_length_min", 0)

            cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
            images = product.get("product_images", {})
            img_url = images.get("500") or images.get("252")
            
            if img_url:
                img_resp = requests.get(img_url, timeout=10)
                if img_resp.status_code == 200:
                    with open(cover_path, "wb") as f:
                        f.write(img_resp.content)

            data = self.local_library.get(filepath, {})
            data["title"] = title
            data["authors"] = authors
            data["series"] = series_str      # NEW
            data["duration_min"] = duration_min # NEW
            data["asin"] = asin
            self.local_library[filepath] = data
            self.db.save_local_db(self.local_library)

            ext = data.get("format", "").upper()
            if ext in ["M4B", "MP3"]:
                self.root.after(0, lambda: self.dl_status_var.set("Embedding tags..."))
                
                base_name, original_ext = os.path.splitext(filepath)
                temp_path = f"{base_name}_temp{original_ext}"
                
                cmd = ["ffmpeg", "-y", "-i", filepath]
                
                if os.path.exists(cover_path):
                    cmd.extend(["-i", cover_path, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"])
                else:
                    cmd.extend(["-map", "0:a"])
                    
                cmd.extend([
                    "-c:a", "copy",
                    "-metadata", f"title={title}",
                    "-metadata", f"album={title}",
                    "-metadata", f"artist={authors}",
                    "-metadata", f"album_artist={authors}",
                    "-metadata", "genre=Audiobook",
                    temp_path
                ])
                
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if res.returncode == 0:
                    import shutil
                    shutil.move(temp_path, filepath)
                else:
                    if os.path.exists(temp_path): os.remove(temp_path)
                    self.write_log(f"FFmpeg Embed Error: {res.stderr}")
                    raise Exception("FFmpeg failed to embed metadata. Check log for details.")

            self.root.after(0, lambda: messagebox.showinfo("Success", "Metadata scraped and applied!"))
            self.root.after(0, self.refresh_library_ui)

            if getattr(self, 'file_path', "") == filepath:
                self.root.after(0, lambda: self.load_specific_file(filepath))
                
        except Exception as e:
            self.write_log(f"Scrape Error: {e}")
            self.root.after(0, lambda err=str(e): messagebox.showerror("Scrape Failed", err))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

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
        
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        tree.heading(col, command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def setup_ui(self):
        self.build_menu_bar() # NEW

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main_vbox = tk.Frame(self.root)
        main_vbox.pack(fill="both", expand=True, padx=10, pady=10)
        main_vbox.rowconfigure(0, weight=1)
        main_vbox.columnconfigure(0, weight=1)

        top_split = ttk.PanedWindow(main_vbox, orient=tk.HORIZONTAL)
        top_split.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        left_panel = tk.Frame(top_split)
        right_panel = tk.Frame(top_split)

        top_split.add(left_panel, weight=3)
        top_split.add(right_panel, weight=1)

        bottom_panel = tk.Frame(main_vbox)
        bottom_panel.grid(row=1, column=0, sticky="ew")

        self.build_library_components(left_panel)
        self.build_info_components(right_panel)
        self.build_bookmarks_components(right_panel)
        self.build_player_components(bottom_panel)

    def export_csv_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV File", "*.csv")],
            title="Export Library to CSV"
        )
        if not output_file:
            return

        try:
            LibraryExporter.export_csv(output_file, self.local_library, self.cloud_items)
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
            LibraryExporter.export_html(output_file, self.local_library, self.cloud_items)
            import webbrowser
            webbrowser.open(output_file)
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to generate HTML:\n{e}")

    def write_log(self, message):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {message}\n"
            try:
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
            except Exception:
                pass

    def auto_load_auth(self):
        self.write_log("DEBUG: auto_load_auth fired from startup timer.")
        if self.api_client.load_auth_from_file(self.auth_save_path):
            activation_bytes = self.api_client.get_activation_bytes()
            self.auth_bytes.set(activation_bytes)
            self.write_log(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
            self.fetch_cloud_library()
        else:
            self.write_log("No saved session found. Please log in.")

    def load_auth_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Auth File", "*.json")], title="Select Audible Auth File")
        if not filepath: return

        self.write_log(f"Loading auth from external file: {filepath}")
        try:
            if self.api_client.load_auth_from_file(filepath):
                activation_bytes = self.api_client.get_activation_bytes()
                self.auth_bytes.set(activation_bytes)
                self.write_log(f"Activation Bytes Received: {activation_bytes}")
                self.api_client.save_auth_to_file(self.auth_save_path)
                
                messagebox.showinfo("Success", "Auth file loaded! You can now fetch your library.")
                self.fetch_cloud_library()
        except Exception as e:
            self.write_log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Error", "Could not load auth file. Check the log.")

    def start_browser_login_thread(self):
        if hasattr(self, 'browser_login_btn') and self.browser_login_btn.winfo_exists():
            self.browser_login_btn.config(text="Connecting...", state=tk.DISABLED)
        threading.Thread(target=self.browser_login_worker, args=(self.locale.get(),), daemon=True).start()

    def browser_login_worker(self, locale):
        self.write_log(f"Starting external browser login for region: {locale}")
        
        def custom_login_callback(login_url):
            self.write_log("Opening default web browser...")
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
            self.write_log("Waiting for user to complete browser login and paste URL...")
            if self.api_client.login_with_browser(locale, custom_login_callback):
                activation_bytes = self.api_client.get_activation_bytes()
                
                self.root.after(0, self.auth_bytes.set, activation_bytes)
                self.write_log(f"Activation Bytes Received: {activation_bytes}")
                
                self.api_client.save_auth_to_file(self.auth_save_path)
                self.write_log(f"Session saved locally to {self.auth_save_path}")

                self.root.after(0, lambda: messagebox.showinfo("Success", "Connected to Audible!"))
                self.root.after(0, self.fetch_cloud_library)
                
        except Exception as e:
            error_trace = traceback.format_exc()
            self.write_log("ERROR DURING LOGIN:")
            self.write_log(error_trace)
            self.root.after(0, lambda: messagebox.showerror("Login Failed", str(e)))
            
        finally:
            self.write_log("Login thread terminated.")
            def restore_btn():
                if hasattr(self, 'browser_login_btn') and self.browser_login_btn.winfo_exists():
                    self.browser_login_btn.config(text="Login via Browser", state=tk.NORMAL)
            self.root.after(0, restore_btn)

    def fetch_cloud_library(self):
        self.write_log("DEBUG: fetch_cloud_library method started executing.")
        
        if not self.api_client.auth:
            self.write_log("DEBUG: fetch_cloud_library aborted - self.api_client.auth is missing or None.")
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        self.write_log("DEBUG: self.api_client.auth verified. Launching fetch_library_worker thread...")
        
        self.dl_status_var.set("Fetching data from Amazon... Please wait.")
        
        threading.Thread(target=self.fetch_library_worker, daemon=True).start()

    def fetch_library_worker(self):
        try:
            self.write_log("Querying Audible Library API...")
            client = audible.Client(auth=self.api_client.auth)

            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors,media", num_results=1000)
            
            self.cloud_items = response.get("items", [])
            self.write_log(f"Successfully retrieved {len(self.cloud_items)} library items.")
            
            self.save_cloud_cache()

            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

            threading.Thread(target=self.background_cover_downloader, daemon=True).start()
            
        except Exception as e:
            import traceback
            self.write_log(f"ERROR FETCHING LIBRARY:\n{traceback.format_exc()}")
            self.root.after(0, lambda: messagebox.showerror("Library Error", "Failed to fetch cloud library."))
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

    def background_cover_downloader(self):
        self.write_log("Starting background cover sync...")
        covers_downloaded = 0
        
        for item in getattr(self, 'cloud_items', []):
            asin = item.get("asin")
            if not asin: continue
                
            cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
            if os.path.exists(cover_path):
                continue 
                
            images = item.get("product_images", {})
            img_url = images.get("500") or images.get("252")
            
            if img_url:
                try:
                    img_data = requests.get(img_url, timeout=10).content
                    with open(cover_path, "wb") as f:
                        f.write(img_data)
                    covers_downloaded += 1
                except Exception as e:
                    pass
                    
        if covers_downloaded > 0:
            self.write_log(f"Downloaded {covers_downloaded} new covers.")

            if getattr(self, 'current_view_mode', 'list') == 'grid':
                self.root.after(0, self.refresh_library_ui)

    def update_cloud_ui(self, items):
        for row in self.cloud_tree.get_children():
            self.cloud_tree.delete(row)

        for item in items:
            try:
                asin = item.get("asin", "Unknown")
                title = item.get("title") or "Unknown"
                
                raw_authors = item.get("authors") or []
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                
                duration_min = item.get("runtime_length_min") or 0
                hours, mins = divmod(duration_min, 60)
                duration_str = f"{hours}h {mins}m"
                
                self.cloud_tree.insert("", "end", values=(title, authors, duration_str, asin))
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"DEBUG - Failed to parse UI for item: {e}")

    def download_title_prompt(self):
        selected = self.cloud_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title from the Cloud Library first.")
            return

        item = self.cloud_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

        if not asin or asin == "Unknown":
            messagebox.showerror("Data Error", "This item does not have a valid ASIN.")
            return

        save_dir = getattr(self, 'default_download_dir', '')
        if not save_dir:
            save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
            if not save_dir:
                return

        self.write_log(f"Starting download process for ASIN: {asin}")
        threading.Thread(target=self.download_worker, args=(asin, title, save_dir), daemon=True).start()

    def download_worker(self, asin, title, save_dir, is_queue=False, post_action=None):
        try:
            self.root.after(0, lambda: self.dl_status_var.set(f"Downloading: {title}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

            # Define UI Callbacks for the downloader
            def on_progress(percent_float):
                self.root.after(0, self.dl_progress_var.set, percent_float)
                if is_queue and asin in getattr(self, 'active_downloads', {}):
                    self.root.after(0, self.active_downloads[asin]["prog_var"].set, percent_float)
                    self.root.after(0, self.active_downloads[asin]["status_var"].set, f"{int(percent_float)}%")

            def check_cancel():
                return is_queue and asin in getattr(self, 'active_downloads', {}) and self.active_downloads[asin].get("cancel_flag")

            # Let core/downloader handle the network stream
            filepath, a_key, a_iv, ext = self.downloader.download_item(
                asin=asin, 
                title=title, 
                save_dir=save_dir, 
                progress_callback=on_progress, 
                check_cancel_callback=check_cancel
            )

            # Update UI on success
            if is_queue and asin in getattr(self, 'active_downloads', {}):
                self.root.after(0, self.active_downloads[asin]["status_var"].set, "Complete")
            
            self.add_stat("books_downloaded", 1)
            
            # Save to Database
            self.local_library[filepath] = {
                "title": title, 
                "format": ext.replace(".", "").upper(), 
                "path": filepath,
                "audible_key": a_key,
                "audible_iv": a_iv,
                "asin": asin  
            }
            self.db.save_local_db(self.local_library)
            self.root.after(0, self.refresh_library_ui)

            # Handle post-actions (Play or Convert automatically)
            if post_action == "play" or post_action == "convert":
                self.root.after(0, lambda: self.load_specific_file(filepath))
                if post_action == "play":
                    self.root.after(500, self.play_chapter)
                elif post_action == "convert":
                    self.root.after(500, self.start_convert_thread)
            elif not is_queue:
                self.root.after(0, lambda: messagebox.showinfo("Success", f"Finished downloading:\n{title}"))
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = str(e)
            
            self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")

            if is_queue and asin in getattr(self, 'active_downloads', {}):
                self.root.after(0, lambda: self.active_downloads[asin]["status_var"].set("Failed"))

            if not is_queue:
                self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))
                
        finally:
            if not is_queue:
                self.root.after(0, lambda: self.dl_status_var.set("Idle"))
                self.root.after(0, lambda: self.dl_progress_var.set(0))
        
    def seek_audio(self, offset):
        if not self.file_path or not self.chapters:
            return

        if not self.is_playing and not self.is_paused:
            return

        new_time = self.current_play_time + offset
        
        if new_time < 0:
            new_time = 0
        elif new_time >= self.chapter_duration:
            self.next_chapter()
            return
            
        self.current_play_time = new_time
        
        if self.is_playing:
            self.is_playing = False
            if self.player_process:
                self.player_process.terminate()
                self.player_process = None
            self.resume_playback()
            
        elif self.is_paused:
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
            self.progress_var.set(percent)
        
    def get_drm_flags(self, filepath):
        data = self.local_library.get(filepath, {})
        a_key = data.get("audible_key")
        a_iv = data.get("audible_iv")

        if a_key and a_iv:
            return ["-audible_key", a_key, "-audible_iv", a_iv]

        owner = data.get("owner", self.active_profile)
        
        if owner == self.active_profile and self.auth_bytes.get().strip():
            return ["-activation_bytes", self.auth_bytes.get().strip()]
            
        owner_auth_path = os.path.join(self.data_dir, f"auth_{owner}.json")
        if os.path.exists(owner_auth_path):
            try:
                temp_auth = audible.Authenticator.from_file(owner_auth_path)
                dynamic_bytes = temp_auth.get_activation_bytes()
                if dynamic_bytes:
                    return ["-activation_bytes", dynamic_bytes]
            except Exception as e:
                self.write_log(f"Failed to dynamically load auth for {owner}: {e}")
        
        return ["-activation_bytes", self.auth_bytes.get().strip()]
            
    def add_local_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b *.mp3")])
        if not filepath: return
        
        filename = os.path.basename(filepath)
        ext = filename.split(".")[-1].upper()
        
        title = filename
        authors = "Unknown Author"
        
        if ext in ["M4B", "MP3"]:
            try:
                data = self.converter.get_metadata_and_chapters(filepath)
                tags = data.get("format", {}).get("tags", {})

                if "title" in tags: 
                    title = tags["title"]
                if "artist" in tags: 
                    authors = tags["artist"]
                elif "album_artist" in tags: 
                    authors = tags["album_artist"]
                    
            except Exception as e:
                self.write_log(f"Failed to read tags for {filename}: {e}")

        self.local_library[filepath] = {
            "title": title, 
            "format": ext, 
            "path": filepath, 
            "authors": authors,
            "owner": self.active_profile
        }
        self.db.save_local_db(self.local_library)
        self.refresh_library_ui()

    

    def remove_local_file(self):
        selected = self.library_tree.focus()
        if not selected: 
            return
        
        item = self.library_tree.item(selected)
        title = item['values'][0]
        
        local_path = None
        for path, data in self.local_library.items():
            if data["title"] == title:
                local_path = path
                break
        
        if local_path and local_path in self.local_library:
            if messagebox.askyesno("Remove File", f"Remove '{title}' from your local library list?\n\n(This only removes it from the list, it does not delete the actual file from your hard drive.)"):
                del self.local_library[local_path]
                self.db.save_local_db(self.local_library)
                self.refresh_library_ui()
        else:
            messagebox.showinfo("Cloud Only", "This title is not currently in your downloaded local library.")

    def set_sleep_timer(self, mode, value=0):

        if hasattr(self, '_sleep_timer_id'):
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
        if getattr(self, 'sleep_mode', None) != "time":
            return
            
        if self.sleep_timer_seconds <= 0:
            self.sleep_mode = None
            self.timer_btn.config(text="Sleep: Off")
            
            if getattr(self, 'is_playing', False):
                self.write_log("Sleep timer (minutes) finished. Pausing playback.")
                self.pause_audio()
            return
            
        self.sleep_timer_seconds -= 1
        self.timer_btn.config(text=f"Sleep: {self.format_time(self.sleep_timer_seconds)}")
        
        self._sleep_timer_id = self.root.after(1000, self.sleep_timer_tick)

    
                
    def on_speed_change(self, selected_speed):
        if self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()
    
    def on_sleep_timer_set(self, event=None):
        val = self.sleep_time_var.get()

        if hasattr(self, '_sleep_timer_id'):
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
        if getattr(self, 'current_view_mode', 'list') != "grid":
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
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected:
                if self.file_path:
                    self.play_chapter()
                else:
                    messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item') or not self._selected_grid_item:
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
        for path, data in self.local_library.items():
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

        threading.Thread(target=self.fetch_metadata_worker, args=(local_path,), daemon=True).start()
        
        self.handle_action_on_selected("play")

    def load_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b")])
        if filepath:
            self.load_specific_file(filepath)

    def load_specific_file(self, filepath):
        self.file_path = filepath
        is_encrypted = filepath.endswith(".aax") or filepath.endswith(".aaxc")
        
        self.dl_status_var.set("Analyzing...")
        self.root.update()
        
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.file_path)
            if not success:
                self.dl_status_var.set("Verification Failed")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.file_path = ""
                return

        self.dl_status_var.set(f"Ready: {os.path.basename(self.file_path)}")
        self.chapters = self.extract_chapters(self.file_path)
        
        if self.chapters:
            local_data = self.local_library.get(filepath, {})
            
            # The Web Player tracks absolute time (last_position). 
            # The PC Player tracks chapter index + relative time.
            abs_pos = local_data.get("last_position")
            
            abs_pos = None
            if "progress" in local_data and self.active_profile in local_data["progress"]:
                abs_pos = local_data["progress"][self.active_profile]
            elif "last_position" in local_data:
                abs_pos = local_data["last_position"]
                # Translate Web's absolute time to PC's chapter format
                found_chap = 0
                for i, chap in enumerate(self.chapters):
                    start = float(chap.get("start_time", 0))
                    end = float(chap.get("end_time", 0))
                    if start <= abs_pos < end:
                        found_chap = i
                        break
                    # Catch-all if position somehow overshoots the last chapter
                    if i == len(self.chapters) - 1 and abs_pos >= end:
                        found_chap = i
                        
                self.current_chapter_idx = found_chap
                self.current_play_time = max(0.0, abs_pos - float(self.chapters[found_chap].get("start_time", 0)))
            else:
                # Fallback to standard PC tracking if no web data exists
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

        threading.Thread(target=self.fetch_metadata_worker, args=(filepath,), daemon=True).start()
        self.refresh_bookmarks_ui()

    def add_bookmark(self):
        if not getattr(self, 'file_path', None):
            messagebox.showwarning("No File", "Please load an audiobook first.")
            return

        was_playing = self.is_playing
        if was_playing:
            self.pause_audio()

        current_time = getattr(self, 'current_play_time', 0.0)
        chapter_idx = getattr(self, 'current_chapter_idx', 0)

        abs_time = current_time
        if self.chapters:
            abs_time += float(self.chapters[chapter_idx].get("start_time", 0))

        note = simpledialog.askstring("Add Bookmark", f"Add a note for {self.format_time(current_time)}:")

        if was_playing:
            self.is_paused = False
            self.resume_playback()
            
        if not note: return 

        local_data = self.local_library.get(self.file_path, {})
        if "bookmarks" not in local_data:
            local_data["bookmarks"] = []
            
        local_data["bookmarks"].append({
            "chapter_idx": chapter_idx,
            "time": current_time,
            "abs_time": abs_time,
            "note": note
        })
        
        self.db.save_local_db(self.local_library)
        self.refresh_bookmarks_ui()

    def refresh_bookmarks_ui(self):
        if not hasattr(self, 'bm_tree'): return
        
        for row in self.bm_tree.get_children():
            self.bm_tree.delete(row)
            
        if not getattr(self, 'file_path', None): return
        
        local_data = self.local_library.get(self.file_path, {})
        bookmarks = local_data.get("bookmarks", [])

        bookmarks.sort(key=lambda x: x.get("abs_time", 0))
        
        for idx, bm in enumerate(bookmarks):
            chap_idx = bm.get("chapter_idx", 0)

            chap_title = f"Chapter {chap_idx + 1}"
            if hasattr(self, 'chapters') and self.chapters and chap_idx < len(self.chapters):
                chap_title = self.chapters[chap_idx].get("tags", {}).get("title", chap_title)
                
            t_str = self.format_time(bm.get("time", 0))
            display_time = f"{chap_title} - {t_str}"

            self.bm_tree.insert("", "end", iid=str(idx), values=(display_time, bm.get("note", "")))

    def jump_to_bookmark(self, event=None):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        bookmarks = self.local_library.get(self.file_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            bm = bookmarks[idx]
            
            self.stop_audio()
            self.current_chapter_idx = bm.get("chapter_idx", 0)
            self.current_play_time = bm.get("time", 0.0)
            
            self.play_chapter()

    def delete_bookmark(self):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        bookmarks = self.local_library.get(self.file_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            del bookmarks[idx]
            self.db.save_local_db(self.local_library)
            self.refresh_bookmarks_ui()

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        cmd.extend(self.get_drm_flags(filepath))
        cmd.extend(["-i", filepath, "-t", "0.1", "-f", "null", "-"])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
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
            threading.Thread(target=self.split_worker, args=(self.file_path, output_dir), daemon=True).start()
        else:
            output_file = filedialog.asksaveasfilename(
                defaultextension=".m4b", 
                filetypes=[("M4B Audiobook", "*.m4b")], 
                initialfile=os.path.basename(self.file_path).replace(".aaxc", ".m4b").replace(".aax", ".m4b")
            )
            if not output_file: 
                return
            self.dl_status_var.set("Converting to .m4b... Please wait.")
            threading.Thread(target=self.convert_single_worker, args=(self.file_path, output_file), daemon=True).start()

    def convert_single_worker(self, input_path, output_path):
        try:
            total_duration = 0
            if hasattr(self, 'chapters') and self.chapters:
                total_duration = float(self.chapters[-1].get("end_time", 0))
            if total_duration == 0:
                total_duration = self.converter.get_duration(input_path)

            original_data = self.local_library.get(input_path, {})
            title = original_data.get("title", os.path.basename(output_path))
            asin = original_data.get("asin", "")

            authors = ""
            for item in getattr(self, 'cloud_items', []):
                if item.get("asin") == asin:
                    raw_authors = item.get("authors", [])
                    authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    break

            cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
            drm_flags = self.get_drm_flags(input_path) if input_path.endswith((".aax", ".aaxc")) else None

            # Route FFmpeg's progress stream into your Tkinter progress bar
            def on_progress(percent):
                self.root.after(0, self.dl_progress_var.set, percent)

            self.converter.convert_to_m4b(
                input_path=input_path, output_path=output_path, title=title,
                authors=authors, cover_path=cover_path, drm_flags=drm_flags,
                total_duration=total_duration, progress_cb=on_progress
            )

            self.local_library[output_path] = {
                "title": title, "format": "M4B", "path": output_path, "asin": asin
            }
            self.db.save_local_db(self.local_library)
            
            self.root.after(0, lambda: messagebox.showinfo("Success", "File converted with embedded metadata."))
            self.root.after(0, self.refresh_library_ui)

        except Exception as e:
            self.write_log(f"Conversion Error: {e}")
            self.root.after(0, lambda err=str(e): messagebox.showerror("Conversion Failed", err))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def split_worker(self, input_path, output_dir):
        try:
            drm_flags = self.get_drm_flags(input_path) if input_path.endswith((".aax", ".aaxc")) else None
            
            original_data = self.local_library.get(input_path, {})
            book_title = original_data.get("title", os.path.splitext(os.path.basename(input_path))[0])
            safe_book_title = "".join([c for c in book_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
            
            target_dir = os.path.join(output_dir, safe_book_title)
            os.makedirs(target_dir, exist_ok=True)
            
            def on_progress(percent):
                self.root.after(0, self.dl_progress_var.set, percent)

            self.converter.split_into_chapters(
                input_path=input_path, target_dir=target_dir, chapters=self.chapters,
                drm_flags=drm_flags, progress_cb=on_progress
            )

            self.root.after(0, lambda: messagebox.showinfo("Success", f"Audiobook split into {len(self.chapters)} files.\n\nSaved to:\n{target_dir}"))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Split Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def convert_all_worker(self, file_list):
        total = len(file_list)
        try:
            with keep.running():
                for idx, filepath in enumerate(file_list, 1):
                    if not os.path.exists(filepath): continue
                        
                    data = self.local_library.get(filepath, {})
                    title = data.get("title", "Unknown")
                    asin = data.get("asin", "")
                    
                    self.root.after(0, lambda i=idx, t=title: self.dl_status_var.set(f"Converting {i}/{total}: {t}"))
                    
                    base_name, _ = os.path.splitext(filepath)
                    out_path = f"{base_name}.m4b"
                    
                    drm_flags = self.get_drm_flags(filepath) if filepath.endswith((".aax", ".aaxc")) else None
                    cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
                    
                    authors = ""
                    for item in getattr(self, 'cloud_items', []):
                        if item.get("asin") == asin:
                            raw_authors = item.get("authors", [])
                            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                            break

                    try:
                        total_duration = self.converter.get_duration(filepath)
                        def on_progress(percent):
                            self.root.after(0, self.dl_progress_var.set, percent)

                        self.converter.convert_to_m4b(
                            input_path=filepath, output_path=out_path, title=title,
                            authors=authors, cover_path=cover_path, drm_flags=drm_flags,
                            total_duration=total_duration, progress_cb=on_progress
                        )
                        
                        self.local_library[out_path] = data
                        self.local_library[out_path]["format"] = "M4B"
                        self.local_library[out_path]["path"] = out_path
                        
                        if os.path.exists(filepath): os.remove(filepath)
                        del self.local_library[filepath]
                        self.db.save_local_db(self.local_library)
                        self.root.after(0, self.refresh_library_ui)
                            
                    except Exception as e:
                        self.write_log(f"Batch Convert Exception on {title}: {e}")
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))
            self.root.after(0, lambda: messagebox.showinfo("Convert All", "Batch conversion complete!"))

    def extract_chapters(self, filepath):
        metadata = self.converter.get_metadata_and_chapters(filepath)
        return metadata.get("chapters", [])

    def play_chapter(self):
        if not self.file_path or not self.chapters: return
        
        if self.is_paused:
            self.is_paused = False
            self.resume_playback()
            return
            
        self.stop_audio()
        
        chapter = self.chapters[self.current_chapter_idx]
        start_time = float(chapter.get("start_time", 0))
        end_time = float(chapter.get("end_time", 0))
        
        self.chapter_duration = end_time - start_time
        self.update_info()
        self.resume_playback()

    def resume_playback(self):
        chapter = self.chapters[self.current_chapter_idx]
        base_start = float(chapter.get("start_time", 0))
        
        actual_start_time = base_start + self.current_play_time
        remaining_duration = self.chapter_duration - self.current_play_time
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        drm_flags = self.get_drm_flags(self.file_path) if self.file_path.endswith((".aax", ".aaxc")) else None
        
        self.player.play(
            filepath=self.file_path,
            start_time=actual_start_time,
            remaining_duration=remaining_duration,
            speed=speed_val,
            volume=int(self.volume_var.get()),
            voice_boost=self.voice_boost_var.get(),
            skip_silence=self.skip_silence_var.get(),
            drm_flags=drm_flags
        )
        
        if os.name == 'nt':
            self.root.after(500, self.on_volume_change)
            
        import time
        self._last_tick_time = time.time()
        self.is_playing = True
        
        self.update_playback_progress(self.player.process)

    def pause_audio(self):
        if self.is_playing:
            self.player.stop()
            self.is_playing = False
            self.is_paused = True
            
            self.current_play_time = max(0, self.current_play_time - 1.5)
            
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            
            self.save_playback_state()

    def stop_audio(self):
        self.is_playing = False
        self.is_paused = False
        self.player.stop()
        self.save_playback_state()

    def on_volume_change(self, event=None):
        if os.name == 'nt':
            self.player.set_volume(self.volume_var.get())
        else:
            if self.is_playing:
                self.pause_audio()
                self.is_paused = False
                self.resume_playback()

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def update_playback_progress(self, active_proc):
        if not self.is_playing or self.player.process != active_proc or active_proc.poll() is not None:
            return
        
        import time
        now = time.time()
        delta = now - getattr(self, '_last_tick_time', now)
        self._last_tick_time = now
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        self.current_play_time += (delta * speed_val)

        real_time_delta = delta * speed_val
        self.session_listen_buffer += real_time_delta
        if self.session_listen_buffer >= 60.0:
            self.add_stat("seconds_listened", self.session_listen_buffer)
            self.session_listen_buffer = 0.0
        
        if self.current_play_time > self.chapter_duration:
            self.current_play_time = self.chapter_duration
            
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")

        # Save to database every 10 seconds so the web server stays updated
        if not hasattr(self, '_last_disk_save_time'):
            self._last_disk_save_time = now
            
        if now - self._last_disk_save_time > 10:
            self.save_playback_state()
            self._last_disk_save_time = now

        self.root.after(500, self.update_playback_progress, active_proc)

    def next_chapter(self):
        self.save_playback_state()

        if self.current_chapter_idx < len(self.chapters) - 1:
            self.current_chapter_idx += 1
            self.current_play_time = 0

            if getattr(self, 'sleep_mode', None) == "chapters":
                self.sleep_chapters_remaining -= 1
                if self.sleep_chapters_remaining <= 0:
                    self.sleep_mode = None
                    self.timer_btn.config(text="Sleep: Off")
                    self.write_log("Sleep timer (chapters) finished. Pausing playback.")
                    self.is_paused = True 

                    chapter = self.chapters[self.current_chapter_idx]
                    start_time = float(chapter.get("start_time", 0))
                    end_time = float(chapter.get("end_time", 0))
                    self.chapter_duration = end_time - start_time
                    self.update_info()
                    
                    curr_str = self.format_time(self.current_play_time)
                    dur_str = self.format_time(self.chapter_duration)
                    self.time_label.config(text=f"{curr_str} / {dur_str}")
                    self.progress_var.set(0)
                    
                    if self.player_process:
                        self.player_process.terminate()
                        self.player_process = None
                        
                    return 
                else:
                    self.timer_btn.config(text=f"Sleep: {self.sleep_chapters_remaining} ch")

            self.is_paused = False
            self.play_chapter()
        else:
            self.stop_audio()
            self.add_stat("books_finished", 1)
            self.info_label.config(text="Finished Book")

    def prev_chapter(self):
        self.save_playback_state()
        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            self.current_play_time = 0
            self.is_paused = False
            self.play_chapter()
        else:
            self.current_play_time = 0
            self.is_paused = False
            self.play_chapter()

    

    def update_info(self):
        if self.chapters:
            title = self.chapters[self.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")

