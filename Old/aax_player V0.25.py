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

try:
    import audible
except ImportError:
    messagebox.showerror("Missing Dependency", "Please run: pip install audible requests pillow")
    exit()

class AAXManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TomeBox")
        self.root.geometry("1150x780")

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.local_db_path = os.path.join(self.base_dir, "library.json")
        self.auth_save_path = os.path.join(self.base_dir, "my_audible_auth.json")
        self.log_file_path = os.path.join(self.base_dir, "aax_manager.log")
        self.settings_path = os.path.join(self.base_dir, "settings.json")
        self.auth_object = None
        self.local_library = self.load_local_db()
        self.cloud_items = [] 
        
        self.settings = self.load_settings()
        self.default_download_dir = self.settings.get("download_dir", "")
        self.cloud_cache_path = os.path.join(self.base_dir, "cloud_cache.json")
        
        self.auth_object = None
        self.local_library = self.load_local_db()
        self.cloud_library = []
        self.cloud_items = self.load_cloud_cache()

        self.file_path = ""
        self.auth_bytes = tk.StringVar(value="")
        self.chapters = []
        self.current_chapter_idx = 0
        self.player_process = None
        
        self.debug_mode = tk.BooleanVar(value=False)
        self.dl_progress_var = tk.DoubleVar()
        self.dl_status_var = tk.StringVar(value="Idle")

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.after(500, self.auto_load_auth)
        self.root.after(900000, self.run_background_sync)
    
    def run_background_sync(self):
        threading.Thread(target=self.silent_sync_worker, daemon=True).start()
        # Schedule the next check in 15 minutes (900000 milliseconds)
        self.root.after(900000, self.run_background_sync)
    
    def build_menu_bar(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        
        export_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Export Library", menu=export_menu)
        export_menu.add_command(label="Export to CSV", command=self.export_csv_worker)
        export_menu.add_command(label="Export to HTML Page", command=self.export_html_worker)

        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing)

    def silent_sync_worker(self):
        if not getattr(self, 'auth_object', None):
            return

        try:
            self.write_log("Background sync: Polling Audible API...")
            client = audible.Client(auth=self.auth_object)
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
        if self.file_path and self.file_path in self.local_library:
            self.local_library[self.file_path]["last_chapter"] = getattr(self, 'current_chapter_idx', 0)
            self.local_library[self.file_path]["last_time"] = getattr(self, 'current_play_time', 0.0)
            self.save_local_db()


    def fetch_metadata_worker(self, filepath):
        local_data = self.local_library.get(filepath, {})
        title = local_data.get("title", "")
        asin = local_data.get("asin")
        
        # If ASIN isn't saved locally, try matching the title to the cloud data
        if not asin:
            for item in getattr(self, 'cloud_items', []):
                if item.get("title") == title:
                    asin = item.get("asin")
                    break
        
        if not asin or not self.auth_object:
            self.root.after(0, lambda: self.cover_label.config(image="", text="Metadata Unavailable"))
            self.root.after(0, lambda: self.author_label.config(text=""))
            return
            
        try:
            client = audible.Client(auth=self.auth_object)
            resp = client.get(f"1.0/catalog/products/{asin}", response_groups="media,product_attrs")
            product = resp.get("product", {})
            
            raw_authors = product.get("authors", [])
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            images = product.get("product_images", {})
            image_url = images.get("500") or images.get("252") # Grab 500px resolution if available
            
            if image_url:
                img_data = requests.get(image_url).content
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((250, 250)) # Resize to fit the UI panel
                photo = ImageTk.PhotoImage(img)
                
                def update_ui():
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                    self.author_label.config(text=authors)
                
                self.root.after(0, update_ui)
            else:
                self.root.after(0, lambda: self.cover_label.config(image="", text="No Cover Art Found"))
                self.root.after(0, lambda: self.author_label.config(text=authors))
                
        except Exception as e:
            self.write_log(f"Metadata Fetch Error: {e}")
            self.root.after(0, lambda: self.cover_label.config(image="", text="Failed to load metadata"))
            
    def load_settings(self):
        if os.path.exists(self.settings_path):
            with open(self.settings_path, "r") as f:
                return json.load(f)
        return {}
    
    def save_settings(self):
        with open(self.settings_path, "w") as f:
            json.dump(self.settings, f, indent=4)

    def load_local_db(self):
        if os.path.exists(self.local_db_path):
            with open(self.local_db_path, "r") as f:
                raw_db = json.load(f)
                
            # Dictionary comprehension to keep only files that actually exist
            cleaned_db = {path: data for path, data in raw_db.items() if os.path.exists(path)}
            return cleaned_db
        return {}

    def save_local_db(self):
        with open(self.local_db_path, "w") as f:
            json.dump(self.local_library, f, indent=4)
    
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
            self.save_settings()
            self.lbl_download_dir.config(text=directory)

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
        try:
            self.execute_download(asin, title, save_dir)
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Finished downloading:\n{title}"))
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")
            error_msg = str(e) 
            self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def download_queue_worker(self, items, save_dir):
        for item in items:
            title = item[0]
            asin = item[3]
            
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            if os.path.exists(os.path.join(save_dir, f"{safe_title}.aaxc")) or os.path.exists(os.path.join(save_dir, f"{safe_title}.aax")):
                self.write_log(f"Skipping {title}, file already exists.")
                continue

            self.download_worker(asin, title, save_dir, is_queue=True)
                
        self.root.after(0, lambda: self.dl_status_var.set("All downloads completed."))
        self.root.after(0, lambda: self.dl_progress_var.set(0))
        self.root.after(0, lambda: messagebox.showinfo("Download Queue Finished", "Finished processing all titles."))
    
    def execute_download(self, asin, title, save_dir):
        self.root.after(0, lambda: self.dl_status_var.set(f"Downloading: {title}"))
        self.root.after(0, lambda: self.dl_progress_var.set(0))
        
        client = audible.Client(auth=self.auth_object)
        self.write_log(f"Requesting DRM license and download URL from Audible for: {title}...")
        
        resp = client.post(
            f"1.0/content/{asin}/licenserequest",
            body={"drm_type": "Adrm", "consumption_type": "Download"}
        )

        content_license = resp.get("content_license", {})
        content_metadata = content_license.get("content_metadata", {})
        content_url = content_metadata.get("content_url", {}).get("offline_url")
        
        if not content_url:
            raise Exception("Could not find 'offline_url' in the payload.")
        
        offline_key = content_metadata.get("content_key", {}).get("offline_key")
        audible_key, audible_iv = None, None
        
        if offline_key:
            import rsa
            import base64
            priv_pem = getattr(self.auth_object, "rsa_private_key", None) or getattr(self.auth_object, "_rsa_private_key", None)
            if priv_pem:
                priv_key = rsa.PrivateKey.load_pkcs1(priv_pem.encode('utf-8'))
                decrypted = rsa.decrypt(base64.b64decode(offline_key), priv_key)
                audible_key = decrypted[:16].hex()
                audible_iv = decrypted[16:].hex()

        safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
        ext = ".aaxc" if audible_key else ".aax"
        filepath = os.path.join(save_dir, f"{safe_title}{ext}")
        
        self.write_log(f"Downloading file to: {filepath}")
        
        headers = {"User-Agent": "Audible/6.6.1 (iPhone; iOS 15.5; Scale/3.00)"}
        import urllib.request
        req = urllib.request.Request(content_url, headers=headers)
        
        with urllib.request.urlopen(req) as response, open(filepath, 'wb') as out_file:
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            while True:
                chunk = response.read(32768)
                if not chunk: break
                out_file.write(chunk)
                if total_size > 0:
                    downloaded += len(chunk)
                    percent = (downloaded / total_size) * 100
                    self.root.after(0, self.dl_progress_var.set, percent)
        
        self.local_library[filepath] = {
            "title": title, 
            "format": "AAXC" if audible_key else "AAX", 
            "path": filepath,
            "audible_key": audible_key,
            "audible_iv": audible_iv
        }
        self.save_local_db()
        self.root.after(0, self.refresh_local_ui)

    def build_auth_components(self, parent):
        self.locale = tk.StringVar(value="us")

        auth_frame = tk.LabelFrame(parent, text="Audible Authentication", padx=10, pady=10)
        auth_frame.pack(fill="x", padx=5, pady=5)

        reg_frame = tk.Frame(auth_frame)
        reg_frame.pack(fill="x", pady=5)
        tk.Label(reg_frame, text="Region:").pack(side=tk.LEFT, padx=5)
        tk.OptionMenu(reg_frame, self.locale, *["us", "uk", "au", "ca", "de", "fr", "jp"]).pack(side=tk.LEFT)

        btn_frame = tk.Frame(auth_frame)
        btn_frame.pack(fill="x", pady=5)
        self.browser_login_btn = tk.Button(btn_frame, text="Browser Login", command=self.start_browser_login_thread)
        self.browser_login_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)
        self.auth_file_btn = tk.Button(btn_frame, text="Load .json", command=self.load_auth_file_prompt)
        self.auth_file_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)

        tk.Checkbutton(auth_frame, text="Enable API Debug Output", variable=self.debug_mode).pack(anchor="w", pady=5)

        bytes_frame = tk.LabelFrame(parent, text="Decryption Bytes", padx=10, pady=5)
        bytes_frame.pack(fill="x", padx=5, pady=5)
        tk.Entry(bytes_frame, textvariable=self.auth_bytes, justify="center").pack(fill="x", pady=5)

        # NEW: Cover Art and Metadata Frame
        self.cover_frame = tk.Frame(parent)
        self.cover_frame.pack(fill="x", padx=5, pady=10)
        
        self.cover_label = tk.Label(self.cover_frame, text="No Cover Art")
        self.cover_label.pack(pady=5)
        
        self.author_label = tk.Label(self.cover_frame, text="", fg="gray", font=("Arial", 10, "italic"))
        self.author_label.pack(pady=2)
        
        self.current_cover_photo = None # Prevents Python from garbage-collecting the image



    def download_all_prompt(self):
        save_dir = getattr(self, 'default_download_dir', '')
        if not save_dir:
            save_dir = filedialog.askdirectory(title="Select Download Folder for All Titles")
            if not save_dir: return
            self.default_download_dir = save_dir
            self.settings["download_dir"] = save_dir
            self.save_settings()
            self.lbl_download_dir.config(text=save_dir)

        items_to_download = []
        for child in self.cloud_tree.get_children():
            values = self.cloud_tree.item(child)['values']
            if values[3] and values[3] != "Unknown":
                items_to_download.append(values)

        if not items_to_download:
            return

        threading.Thread(target=self.download_queue_worker, args=(items_to_download, save_dir), daemon=True).start()

    def build_library_components(self, parent):
        # 1. The Main Splitter
        self.main_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # 2. The Top Pane: Library List
        lib_frame = tk.LabelFrame(self.main_paned, text="Unified Library", padx=10, pady=5)
        self.main_paned.add(lib_frame, weight=1)

        # 3. The Bottom Pane: Download Queue (Created, but NOT added to the screen yet)
        self.queue_frame = tk.LabelFrame(self.main_paned, text="Active Downloads", padx=10, pady=5)
        queue_controls = tk.Frame(self.queue_frame)
        queue_controls.pack(fill="x", pady=(0, 5))
        tk.Button(queue_controls, text="Cancel All Downloads", fg="red", command=self.cancel_all_downloads).pack(side=tk.RIGHT)

        self.queue_canvas = tk.Canvas(self.queue_frame, height=120)
        self.queue_canvas = tk.Canvas(self.queue_frame, height=120)
        queue_scroll = ttk.Scrollbar(self.queue_frame, orient="vertical", command=self.queue_canvas.yview)
        self.queue_inner = tk.Frame(self.queue_canvas)

        self.queue_inner.bind("<Configure>", lambda e: self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox("all")))
        self.queue_canvas.create_window((0, 0), window=self.queue_inner, anchor="nw")
        self.queue_canvas.configure(yscrollcommand=queue_scroll.set)

        self.queue_canvas.pack(side="left", fill="both", expand=True)
        queue_scroll.pack(side="right", fill="y")

        # 4. Dictionary to track active download states
        self.active_downloads = {}

        # 5. Search and Filter Bar
        filter_frame = tk.Frame(lib_frame)
        filter_frame.pack(fill="x", pady=(0, 5))

        tk.Label(filter_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_library_ui()) 
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=35)
        search_entry.pack(side=tk.LEFT, padx=(0, 20))

        tk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.filter_var = tk.StringVar(value="All")
        filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=["All", "Downloaded", "Cloud Only"], state="readonly", width=15)
        filter_combo.pack(side=tk.LEFT)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_library_ui())

        # Download All Button
        self.dl_all_btn = ttk.Button(filter_frame, text="Download Missing", command=self.start_download_all)
        self.dl_all_btn.pack(side=tk.RIGHT, padx=(20, 5))
        # Toggle Queue Button
        self.toggle_queue_btn = ttk.Button(filter_frame, text="Show/Hide Queue", command=self.toggle_queue_visibility)
        self.toggle_queue_btn.pack(side=tk.RIGHT, padx=5)
        # 6. Existing Treeview Setup
        tree_frame = tk.Frame(lib_frame)
        tree_frame.pack(fill="both", expand=True, pady=5)

        scroll = ttk.Scrollbar(tree_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        self.library_tree = ttk.Treeview(tree_frame, columns=("Title", "Author", "Series", "Duration", "ASIN", "Status"), show="headings", yscrollcommand=scroll.set)
        scroll.config(command=self.library_tree.yview)

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

        # 7. Action Buttons
        btn_frame = tk.Frame(lib_frame)
        btn_frame.pack(fill="x", pady=2)
        tk.Button(btn_frame, text="Refresh Cloud", command=self.fetch_cloud_library).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Download Selected", command=lambda: self.handle_action_on_selected("download")).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Convert Selected", command=lambda: self.handle_action_on_selected("convert")).pack(side=tk.LEFT, padx=5)
        
        local_btn_frame = tk.Frame(lib_frame)
        local_btn_frame.pack(fill="x", pady=2)
        tk.Button(local_btn_frame, text="Add Local File", command=self.add_local_file).pack(side=tk.LEFT, padx=5)
        tk.Button(local_btn_frame, text="Remove from List", command=self.remove_local_file).pack(side=tk.LEFT, padx=5)

        dir_frame = tk.Frame(lib_frame)
        dir_frame.pack(fill="x", pady=5)
        tk.Button(dir_frame, text="Set Download Folder", command=self.set_download_folder).pack(side=tk.LEFT, padx=5)
        self.lbl_download_dir = tk.Label(dir_frame, text=getattr(self, 'default_download_dir', '') or "No default folder set", fg="gray")
        self.lbl_download_dir.pack(side=tk.LEFT, fill="x", expand=True, padx=5)

        dl_prog_frame = tk.Frame(lib_frame)
        dl_prog_frame.pack(fill="x", padx=5)
        
        self.dl_status_var = tk.StringVar(value="Idle")
        self.dl_progress_var = tk.DoubleVar()
        tk.Label(dl_prog_frame, textvariable=self.dl_status_var).pack(side=tk.TOP, anchor="w")
        ttk.Progressbar(dl_prog_frame, variable=self.dl_progress_var, maximum=100).pack(side=tk.TOP, fill="x")

        self.refresh_library_ui()

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
            
            # NEW: Clear the global download UI when canceling
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
        row_frame = tk.Frame(self.queue_inner)
        row_frame.pack(fill="x", pady=2, padx=5)

        title_lbl = tk.Label(row_frame, text=title[:40] + ("..." if len(title) > 40 else ""), width=35, anchor="w")
        title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        prog_var = tk.DoubleVar()
        prog_bar = ttk.Progressbar(row_frame, variable=prog_var, maximum=100, length=200)
        prog_bar.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 10))

        status_var = tk.StringVar(value="Waiting...")
        status_lbl = tk.Label(row_frame, textvariable=status_var, width=15, anchor="w", fg="gray")
        status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        # The Cancel Button sets a specific flag in our dictionary that the download thread will look for
        cancel_btn = tk.Button(row_frame, text=" ✕ ", fg="red", command=lambda a=asin: self.cancel_download(a))
        cancel_btn.pack(side=tk.RIGHT)

        # Store these references so the download thread can update them
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

        if messagebox.askyesno("Download All", f"Found {len(missing_items)} missing audiobooks.\n\nDo you want to batch download them all now? This may take a while depending on your internet connection."):
            self.dl_all_btn.config(state=tk.DISABLED)
            threading.Thread(target=self.download_all_worker, args=(missing_items,), daemon=True).start()

    def download_all_worker(self, missing_items):
        total = len(missing_items)
        
        save_dir = getattr(self, 'default_download_dir', "")
        if not save_dir:
            save_dir = getattr(self, 'base_dir', os.getcwd())

        # 1. Open the UI Drawer
        self.root.after(0, lambda: self.toggle_queue_drawer(True))
        
        # 2. Build all the UI rows immediately so you can see the queue
        for item in missing_items:
            asin = item.get("asin")
            title = item.get("title", "Unknown")
            self.root.after(0, self.add_queue_ui_row, asin, title)
        
        # 3. Process the downloads
        for idx, item in enumerate(missing_items):
            title = item.get("title", "Unknown")
            asin = item.get("asin")
            
            # If the user clicked the "X" cancel button before we got to this book, skip it
            if asin in self.active_downloads and self.active_downloads[asin]["cancel_flag"]:
                self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Canceled"))
                continue
            
            self.root.after(0, lambda i=idx+1, t=total, name=title: self.dl_status_var.set(f"Batch Downloading ({i}/{t}): {name}..."))
            
            if asin in self.active_downloads:
                self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Starting..."))
            
            try:
                self.download_worker(asin, title, save_dir, is_queue=True)
            except Exception as e:
                self.write_log(f"Failed to batch download {title}: {e}")
                if asin in self.active_downloads:
                    self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Failed"))
                
        self.root.after(0, lambda: self.dl_status_var.set("Batch Download Complete"))
        self.root.after(0, lambda: self.dl_progress_var.set(0))
        self.root.after(0, self.refresh_library_ui)
        self.root.after(0, lambda: self.dl_all_btn.config(state=tk.NORMAL))
        
        # Close the drawer automatically 5 seconds after everything finishes
        self.root.after(5000, lambda: self.toggle_queue_drawer(False))
        self.root.after(5000, lambda: self.dl_status_var.set("Idle"))

    def refresh_library_ui(self, *args):
        # Clear the current treeview
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        search_query = self.search_var.get().lower()
        current_filter = self.filter_var.get()

        local_titles = {data["title"]: data for path, data in self.local_library.items()}
        cloud_titles = []
        rows_to_insert = []

        # 1. Compile Cloud Data
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

        # 2. Compile Orphaned Local Data (Files not found in the cloud cache)
        for path, data in self.local_library.items():
            if data["title"] not in cloud_titles:
                rows_to_insert.append((data["title"], "Local File", "N/A", "N/A", data.get("asin", "Unknown"), f"Downloaded ({data['format']})"))

        # 3. Apply Filters and Insert
        for row in rows_to_insert:
            title, authors, series_str, duration_str, asin, status = row

            # Status Filter Check
            if current_filter == "Downloaded" and "Downloaded" not in status:
                continue
            if current_filter == "Cloud Only" and status != "Cloud Only":
                continue

            # Search Query Check (Searches Title, Author, and Series simultaneously)
            if search_query:
                search_target = f"{title} {authors} {series_str}".lower()
                if search_query not in search_target:
                    continue

            # If it passes both checks, draw it on the screen
            self.library_tree.insert("", "end", values=row)
    
    def handle_action_on_selected(self, action_type):
        selected = self.library_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title first.")
            return

        item = self.library_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][4]

        # Check if we have it locally
        local_path = None
        for path, data in self.local_library.items():
            if data["title"] == title:
                local_path = path
                break

        if local_path:
            if not os.path.exists(local_path):
                messagebox.showerror("File Missing", "The file was deleted or moved. Please remove it from the list and re-download.")
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
                # Pass the intended action to the worker
                threading.Thread(target=self.download_worker, args=(asin, title, save_dir, False, action_type), daemon=True).start()

    
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
        
        # Configure root grid for dynamic resizing
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Main vertical container
        main_vbox = tk.Frame(self.root)
        main_vbox.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_vbox.rowconfigure(0, weight=1)
        main_vbox.columnconfigure(0, weight=1)

        # Top section: Split pane for Library (Left) and Settings/File (Right)
        top_split = ttk.PanedWindow(main_vbox, orient=tk.HORIZONTAL)
        top_split.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        left_panel = tk.Frame(top_split)
        right_panel = tk.Frame(top_split)

        top_split.add(left_panel, weight=3)
        top_split.add(right_panel, weight=1)

        # Bottom section: Full width player
        bottom_panel = tk.Frame(main_vbox)
        bottom_panel.grid(row=1, column=0, sticky="ew")

        self.build_library_components(left_panel)
        self.build_auth_components(right_panel)
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
            with open(output_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Title", "Author(s)", "Series", "Duration (mins)", "ASIN", "Status", "Local Path"])

                local_titles = {data["title"]: data for path, data in self.local_library.items()}
                cloud_titles = []

                for item in self.cloud_items:
                    title = item.get("title", "Unknown")
                    cloud_titles.append(title)
                    
                    raw_authors = item.get("authors") or []
                    authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    
                    raw_series = item.get("series") or []
                    series_list = []
                    for s in raw_series:
                        if isinstance(s, dict):
                            s_title = s.get("title", "")
                            s_seq = s.get("sequence", "")
                            if s_title and s_seq:
                                series_list.append(f"{s_title} (Bk {s_seq})")
                            elif s_title:
                                series_list.append(s_title)
                    series_str = ", ".join(series_list)

                    duration = item.get("runtime_length_min", 0)
                    asin = item.get("asin", "Unknown")

                    local_data = local_titles.get(title)
                    status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
                    local_path = local_data['path'] if local_data else ""

                    writer.writerow([title, authors, series_str, duration, asin, status, local_path])

                for path, data in self.local_library.items():
                    if data["title"] not in cloud_titles:
                        writer.writerow([data["title"], "Local File", "N/A", "N/A", data.get("asin", "Unknown"), f"Downloaded ({data['format']})", path])

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
            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>My TomeBox Library</title>
                <style>
                    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e1e; color: #f0f0f0; margin: 0; padding: 20px; }
                    h1 { text-align: center; color: #ffffff; }
                    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; padding: 20px 0; }
                    .card { background: #2d2d2d; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); overflow: hidden; display: flex; flex-direction: column; }
                    .cover-art { width: 100%; height: 250px; object-fit: cover; background-color: #3d3d3d; display: flex; align-items: center; justify-content: center; color: #aaaaaa; }
                    .card-content { padding: 15px; flex-grow: 1; display: flex; flex-direction: column; }
                    .title { font-size: 1.1em; font-weight: bold; margin: 0 0 5px 0; color: #ffffff; }
                    .author { color: #cccccc; font-size: 0.9em; margin: 0 0 10px 0; font-style: italic; }
                    .series { font-size: 0.85em; color: #f39c12; margin-bottom: 10px; }
                    .status { margin-top: auto; font-size: 0.85em; padding: 5px; border-radius: 4px; text-align: center; font-weight: bold; }
                    .status.downloaded { background-color: #2e5a36; color: #a3e4b3; }
                    .status.cloud { background-color: #4a4a4a; color: #cccccc; }
                </style>
            </head>
            <body>
                <h1>My TomeBox Library</h1>
                <div class="grid">
            """

            local_titles = {data["title"]: data for path, data in self.local_library.items()}
            cloud_titles = []

            for item in self.cloud_items:
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

                images = item.get("product_images", {})
                img_url = images.get("500") or images.get("252") or ""
                
                local_data = local_titles.get(title)
                is_downloaded = bool(local_data)
                status_class = "downloaded" if is_downloaded else "cloud"
                status_text = f"Downloaded ({local_data['format']})" if is_downloaded else "Cloud Only"

                img_tag = f'<img src="{img_url}" class="cover-art" alt="Cover">' if img_url else '<div class="cover-art">No Cover Art</div>'

                html_content += f"""
                    <div class="card">
                        {img_tag}
                        <div class="card-content">
                            <h3 class="title">{title}</h3>
                            <p class="author">{authors}</p>
                            <p class="series">{series_str}</p>
                            <div class="status {status_class}">{status_text}</div>
                        </div>
                    </div>
                """

            for path, data in self.local_library.items():
                if data["title"] not in cloud_titles:
                    html_content += f"""
                        <div class="card">
                            <div class="cover-art">Local File</div>
                            <div class="card-content">
                                <h3 class="title">{data["title"]}</h3>
                                <p class="author">Local File</p>
                                <div class="status downloaded">Downloaded ({data['format']})</div>
                            </div>
                        </div>
                    """

            html_content += """
                </div>
            </body>
            </html>
            """

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(html_content)

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
        self.write_log(f"DEBUG: Looking for auth file at: {self.auth_save_path}")
        
        if os.path.exists(self.auth_save_path):
            self.write_log("DEBUG: Auth file found! Attempting to load...")
            try:
                self.auth_object = audible.Authenticator.from_file(self.auth_save_path)
                activation_bytes = self.auth_object.get_activation_bytes()
                self.auth_bytes.set(activation_bytes)
                self.write_log(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
                
                self.write_log("DEBUG: Sending trigger to fetch_cloud_library now...")
                self.fetch_cloud_library()
                self.write_log("DEBUG: Returned from fetch_cloud_library trigger.")
                
            except Exception as e:
                self.write_log(f"DEBUG EXCEPTION in auto_load_auth: {e}")
                self.write_log(f"Failed to load saved session. You may need to log in again. Error: {e}")
        else:
            self.write_log("DEBUG: Auth file does not exist. Halting auto-load sequence.")
            self.write_log("No saved session found. Please log in.")

    def load_auth_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Auth File", "*.json")], title="Select Audible Auth File")
        if not filepath: return

        self.write_log(f"Loading auth from external file: {filepath}")
        try:
            self.auth_object = audible.Authenticator.from_file(filepath)
            activation_bytes = self.auth_object.get_activation_bytes()
            
            self.auth_bytes.set(activation_bytes)
            self.write_log(f"Activation Bytes Received: {activation_bytes}")
            self.write_log("Auth file loaded successfully.")
            
            self.auth_object.to_file(self.auth_save_path)
            
            messagebox.showinfo("Success", "Auth file loaded! You can now fetch your library.")
            self.fetch_cloud_library()
        except Exception as e:
            self.write_log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Error", "Could not load auth file. Check the log.")

    def start_browser_login_thread(self):
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
            self.auth_object = audible.Authenticator.from_login_external(
                locale=locale, 
                login_url_callback=custom_login_callback
            )
            
            self.write_log("Authentication successful! Retrieving activation bytes...")
            activation_bytes = self.auth_object.get_activation_bytes()
            
            self.root.after(0, self.auth_bytes.set, activation_bytes)
            self.write_log(f"Activation Bytes Received: {activation_bytes}")
            
            self.auth_object.to_file(self.auth_save_path)
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
            self.root.after(0, lambda: self.browser_login_btn.config(text="Login via Browser", state=tk.NORMAL))

    def fetch_cloud_library(self):
        self.write_log("DEBUG: fetch_cloud_library method started executing.")
        
        if not self.auth_object:
            self.write_log("DEBUG: fetch_cloud_library aborted - self.auth_object is missing or None.")
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        self.write_log("DEBUG: self.auth_object verified. Launching fetch_library_worker thread...")
        
        self.dl_status_var.set("Fetching data from Amazon... Please wait.")
        
        threading.Thread(target=self.fetch_library_worker, daemon=True).start()

    def fetch_library_worker(self):
        try:
            self.write_log("Querying Audible Library API...")
            client = audible.Client(auth=self.auth_object)
            
            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors", num_results=1000)
            
            self.cloud_items = response.get("items", [])
            self.write_log(f"Successfully retrieved {len(self.cloud_items)} library items.")
            
            self.save_cloud_cache()

            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))
        except Exception as e:
            import traceback
            self.write_log(f"ERROR FETCHING LIBRARY:\n{traceback.format_exc()}")
            self.root.after(0, lambda: messagebox.showerror("Library Error", "Failed to fetch cloud library."))
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

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
        filepath = None 
        try:
            self.root.after(0, lambda: self.dl_status_var.set(f"Downloading: {title}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

            from audible.aescipher import decrypt_voucher_from_licenserequest
            client = audible.Client(auth=self.auth_object)
            
            self.write_log(f"Requesting AAXC license and download link for ASIN: {asin}")
            
            # 1. Request the license and download URL
            body = {
                "drm_type": "Adrm", 
                "consumption_type": "Download"
            }
            lic_resp = client.post(
                f"1.0/content/{asin}/licenserequest",
                body=body
            )
            
            # 2. Extract Download URL using a recursive search
            def find_url(d):
                if isinstance(d, dict):
                    if "offline_url" in d: return d["offline_url"]
                    for k, v in d.items():
                        res = find_url(v)
                        if res: return res
                elif isinstance(d, list):
                    for item in d:
                        res = find_url(item)
                        if res: return res
                return None
            
            download_link = find_url(lic_resp)
            
            if not download_link:
                raise Exception("Could not find the offline download URL in the API response.")

            # 3. Decrypt the Voucher using the audible library's built-in tool
            self.write_log("Decrypting AAXC voucher...")
            decrypted_voucher = decrypt_voucher_from_licenserequest(self.auth_object, lic_resp)
            
            # Extract the raw hex strings
            def find_key_iv(d):
                k, i = None, None
                if isinstance(d, dict):
                    if "key" in d and "iv" in d: return d["key"], d["iv"]
                    for val in d.values():
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                elif isinstance(d, list):
                    for val in d:
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                return k, i
            
            a_key, a_iv = find_key_iv(decrypted_voucher)
            
            if not a_key or not a_iv:
                raise Exception("Decrypted voucher did not contain 'key' and 'iv'.")

            self.write_log(f"Extracted AAXC Key: {a_key}")
            
            # 4. Download the AAXC file
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            filepath = os.path.join(save_dir, f"{safe_title}.aaxc")
            
            self.write_log(f"Downloading AAXC file to: {filepath}")
            
            headers = {"User-Agent": "Audible/6.6.1 (iPhone; iOS 15.5; Scale/3.00)"}
            import urllib.request
            req = urllib.request.Request(download_link, headers=headers)
            
            with urllib.request.urlopen(req) as response, open(filepath, 'wb') as out_file:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                last_percent = 0
                
                while True:
                    # NEW: Check if the user clicked the cancel button
                    if is_queue and asin in self.active_downloads:
                        if self.active_downloads[asin]["cancel_flag"]:
                            raise Exception("Download canceled by user.")

                    chunk = response.read(32768)
                    if not chunk: break
                    out_file.write(chunk)
                    
                    if total_size > 0:
                        downloaded += len(chunk)
                        percent_float = (downloaded / total_size) * 100
                        
                        # Update the main global bar
                        self.root.after(0, self.dl_progress_var.set, percent_float)
                        
                        # NEW: Update the specific bar in the Queue Drawer
                        if is_queue and asin in self.active_downloads:
                            self.root.after(0, self.active_downloads[asin]["prog_var"].set, percent_float)
                            self.root.after(0, self.active_downloads[asin]["status_var"].set, f"{int(percent_float)}%")
                        
                        percent_int = int(percent_float)
                        if percent_int >= last_percent + 10:
                            self.write_log(f"Download Progress: {percent_int}%")
                            last_percent = percent_int
            
            # Mark the queue item as complete when finished
            if is_queue and asin in self.active_downloads:
                self.root.after(0, self.active_downloads[asin]["status_var"].set, "Complete")
            
            self.write_log(f"Download complete: {safe_title}.aaxc")
            
            self.local_library[filepath] = {
                "title": title, 
                "format": "AAXC", 
                "path": filepath,
                "audible_key": a_key,
                "audible_iv": a_iv
            }
            self.save_local_db()
            self.root.after(0, self.refresh_library_ui)

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
            
            # NEW: Clean up partial files if the user canceled
            if "canceled by user" in error_msg.lower() and filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    self.write_log(f"Cleaned up partial file: {filepath}")
                except OSError as cleanup_error:
                    self.write_log(f"Failed to clean up partial file: {cleanup_error}")
            else:
                # Only log the full error trace if it was a genuine crash, not a normal cancel
                self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")

            if not is_queue:
                self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))
                
        finally:
            if not is_queue:
                self.root.after(0, lambda: self.dl_status_var.set("Idle"))
                self.root.after(0, lambda: self.dl_progress_var.set(0))
        self.local_library[filepath] = {
                "title": title, 
                "format": "AAXC", 
                "path": filepath,
                "audible_key": a_key,
                "audible_iv": a_iv,
                "asin": asin  # NEW: Save ASIN for metadata fetching
            }
        self.save_local_db()
        self.root.after(0, self.refresh_library_ui)
        
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
            else:
                return ["-activation_bytes", self.auth_bytes.get().strip()]
    def add_local_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b *.mp3")])
        if not filepath: return
        
        filename = os.path.basename(filepath)
        ext = filename.split(".")[-1].upper()
        
        self.local_library[filepath] = {"title": filename, "format": ext, "path": filepath}
        self.save_local_db()
        self.refresh_local_ui()

    def remove_local_file(self):
        selected = self.library_tree.focus()
        if not selected: 
            return
        
        item = self.library_tree.item(selected)
        title = item['values'][0]
        
        # Look up the local path by title
        local_path = None
        for path, data in self.local_library.items():
            if data["title"] == title:
                local_path = path
                break
        
        if local_path and local_path in self.local_library:
            if messagebox.askyesno("Remove File", f"Remove '{title}' from your local library list?\n\n(This only removes it from the list, it does not delete the actual file from your hard drive.)"):
                del self.local_library[local_path]
                self.save_local_db()
                self.refresh_library_ui()
        else:
            messagebox.showinfo("Cloud Only", "This title is not currently in your downloaded local library.")

    def refresh_local_ui(self):
        for row in self.local_tree.get_children():
            self.local_tree.delete(row)
            
        for path, data in self.local_library.items():
            self.local_tree.insert("", "end", values=(data['title'], data['format'], data['path']))

    def send_to_player(self):
            selected = self.local_tree.focus()
            if not selected: return
            
            item = self.local_tree.item(selected)
            filepath = item['values'][2]
            
            if not os.path.exists(filepath):
                messagebox.showerror("Error", "File no longer exists at that path.")
                return

            self.load_specific_file(filepath)

    def build_player_components(self, parent):
        play_frame = tk.LabelFrame(parent, text="Playback", padx=10, pady=5)
        play_frame.pack(fill="x", expand=True, padx=5, pady=5)

        self.is_playing = False
        self.is_paused = False
        self.chapter_duration = 0
        self.current_play_time = 0

        # self.status_label = tk.Label(play_frame, text="No file loaded", fg="gray")
        # self.status_label.pack(side=tk.TOP, pady=(0, 5))

        top_row = tk.Frame(play_frame)
        top_row.pack(fill="x", pady=2)
        
        self.info_label = tk.Label(top_row, text="", fg="blue", justify="left")
        self.info_label.pack(side=tk.LEFT, padx=5)
        
        self.time_label = tk.Label(top_row, text="00:00 / 00:00", fg="gray")
        self.time_label.pack(side=tk.RIGHT, padx=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(play_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=5, pady=5)

        controls_frame = tk.Frame(play_frame)
        controls_frame.pack(pady=5)

        tk.Button(controls_frame, text="<< Prev Chapter", width=12, command=self.prev_chapter).pack(side=tk.LEFT, padx=5)
        tk.Button(controls_frame, text="-30s", width=5, command=lambda: self.seek_audio(-30)).pack(side=tk.LEFT, padx=2)
        tk.Button(controls_frame, text="Play", width=8, command=self.master_play).pack(side=tk.LEFT, padx=2)
        tk.Button(controls_frame, text="Pause", width=8, command=self.pause_audio).pack(side=tk.LEFT, padx=2)
        tk.Button(controls_frame, text="Stop", width=8, command=self.stop_audio).pack(side=tk.LEFT, padx=2)
        tk.Button(controls_frame, text="+30s", width=5, command=lambda: self.seek_audio(30)).pack(side=tk.LEFT, padx=2)
        tk.Button(controls_frame, text="Next Chapter >>", width=12, command=self.next_chapter).pack(side=tk.LEFT, padx=5)

        # Speed Control Dropdown
        self.playback_speed = tk.StringVar(value="1.0x")
        speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
        speed_menu = tk.OptionMenu(controls_frame, self.playback_speed, *speed_options, command=self.on_speed_change)
        speed_menu.config(width=4)
        speed_menu.pack(side=tk.LEFT, padx=10)

        # NEW: Volume Control Slider
        self.volume_var = tk.DoubleVar(value=100.0)
        vol_frame = tk.Frame(controls_frame)
        vol_frame.pack(side=tk.LEFT, padx=5)
        tk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
        self.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume_var, command=self.on_volume_change, length=80)
        self.vol_slider.pack(side=tk.LEFT)

    def on_volume_change(self, event=None):
        if os.name == 'nt':
            try:
                from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
                
                vol_float = float(self.volume_var.get()) / 100.0
                sessions = AudioUtilities.GetAllSessions()
                for session in sessions:
                    if session.Process and session.Process.name() == "ffplay.exe":
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume.SetMasterVolume(vol_float, None)
                        
            except ImportError:
                self.write_log("Volume control failed: pycaw or comtypes not installed.")
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"Volume change error: {e}")
        else:
            # Mac and Linux Fallback: Restart the process with the new volume
            if self.is_playing:
                self.pause_audio()
                self.is_paused = False
                self.resume_playback()
                
    def on_speed_change(self, selected_speed):
        if self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()
    
    def master_play(self, event=None):
        selected = self.library_tree.focus()
        
        if selected:
            item = self.library_tree.item(selected)
            title = item['values'][0]
            
            local_path = None
            for path, data in self.local_library.items():
                if data["title"] == title:
                    local_path = path
                    break
                    
            if local_path and self.file_path == local_path:
                self.play_chapter()
                return
                
            self.stop_audio()
            self.handle_action_on_selected("play")
            return

        if self.file_path:
            self.play_chapter()

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

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        cmd.extend(self.get_drm_flags(filepath))
        cmd.extend(["-i", filepath, "-t", "0.1", "-f", "null", "-"])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
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
            # CHANGED
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
            # CHANGED
            self.dl_status_var.set("Converting to .m4b... Please wait.")
            threading.Thread(target=self.convert_worker, args=(self.file_path, output_file), daemon=True).start()

    def convert_worker(self, input_path, output_path):
        cmd = ["ffmpeg", "-y"]
        if input_path.endswith(".aax") or input_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(input_path))
        cmd.extend(["-i", input_path, "-c", "copy", output_path])
        
        try:
            subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            
            original_data = self.local_library.get(input_path, {})
            title = original_data.get("title", os.path.basename(output_path))
            asin = original_data.get("asin", "")
            
            self.local_library[output_path] = {
                "title": title, 
                "format": "M4B", 
                "path": output_path,
                "asin": asin
            }
            self.save_local_db()
            
            self.root.after(0, lambda: messagebox.showinfo("Success", "File converted."))
            self.root.after(0, self.refresh_library_ui)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Conversion Failed", str(e)))
        finally:
            # Removed the convert_btn reference here as well
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))

    def split_worker(self, input_path, output_dir):
        try:
            base_flags = []
            if input_path.endswith(".aax") or input_path.endswith(".aaxc"):
                base_flags = self.get_drm_flags(input_path)

            total_chaps = len(self.chapters)
            
            original_data = self.local_library.get(input_path, {})
            book_title = original_data.get("title", os.path.splitext(os.path.basename(input_path))[0])
            safe_book_title = "".join([c for c in book_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
            
            target_dir = os.path.join(output_dir, safe_book_title)
            os.makedirs(target_dir, exist_ok=True)
            
            for idx, chapter in enumerate(self.chapters):
                # CHANGED: Now pushes progress to the global download progress bar
                self.root.after(0, lambda p=((idx + 1) / total_chaps) * 100: self.dl_progress_var.set(p))
                
                chap_title = chapter.get("tags", {}).get("title", f"Chapter {idx + 1}")
                safe_chap_title = "".join([c for c in chap_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
                
                out_name = f"{idx + 1:03d} - {safe_chap_title}.m4b"
                out_path = os.path.join(target_dir, out_name)

                start = chapter.get("start_time", 0)
                end = chapter.get("end_time", 0)

                cmd = ["ffmpeg", "-y"]
                cmd.extend(base_flags)
                cmd.extend(["-i", input_path, "-ss", str(start), "-to", str(end), "-c", "copy", out_path])
                
                subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Audiobook successfully split into {total_chaps} files.\n\nFiles were saved to:\n{target_dir}"))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Split Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def extract_chapters(self, filepath):
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", filepath]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            data = json.loads(result.stdout)
            return data.get("chapters", [])
        except Exception:
            return []

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

    def pause_audio(self):
        if self.is_playing and self.player_process:
            self.is_playing = False
            self.is_paused = True
            self.player_process.terminate()
            self.player_process = None
            
            self.current_play_time = max(0, self.current_play_time - 1.5)
            
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            
            self.save_playback_state()

    def resume_playback(self):
        chapter = self.chapters[self.current_chapter_idx]
        base_start_time = float(chapter.get("start_time", 0))
        
        actual_start_time = base_start_time + self.current_play_time
        remaining_duration = self.chapter_duration - self.current_play_time
        
        cmd = [
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", 
            "-ss", str(actual_start_time), "-t", str(remaining_duration)
        ]
        
        # Apply the static volume flag ONLY for Mac and Linux
        if os.name != 'nt':
            vol_int = int(self.volume_var.get())
            cmd.extend(["-volume", str(vol_int)])
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        if speed_val != 1.0:
            cmd.extend(["-af", f"atempo={speed_val}"])
        
        if self.file_path.endswith(".aax") or self.file_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(self.file_path))
            
        cmd.append(self.file_path)

        if self.debug_mode.get():
            self.write_log(f"Starting player: {' '.join(cmd)}")

        self.player_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        # Only trigger the pycaw volume injection on Windows
        if os.name == 'nt':
            self.root.after(500, self.on_volume_change)
            
        import time
        import time
        self._last_tick_time = time.time()
        self.is_playing = True
        
        active_proc = self.player_process
        threading.Thread(target=self.monitor_player_output, args=(active_proc,), daemon=True).start()
        self.update_playback_progress(active_proc)

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def update_playback_progress(self, active_proc):
        if not self.is_playing or self.player_process != active_proc or active_proc.poll() is not None:
            return
        
        import time
        now = time.time()
        delta = now - getattr(self, '_last_tick_time', now)
        self._last_tick_time = now
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        self.current_play_time += (delta * speed_val)
        
        if self.current_play_time > self.chapter_duration:
            self.current_play_time = self.chapter_duration
            
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")
        
        self.root.after(500, self.update_playback_progress, active_proc)

    def monitor_player_output(self, proc):
        if not proc: return
        
        for line in proc.stderr:
            if line.strip():
                self.write_log(f"[PLAYER ERROR]: {line.strip()}")
        
        proc.wait()
        
        # Only take action if this thread's process is still the active one
        if self.player_process == proc and self.is_playing:
            if proc.returncode == 0:
                # Chapter finished cleanly, trigger the next chapter
                self.root.after(0, self.next_chapter)
            else:
                self.write_log(f"[CRITICAL]: Player crashed with code {proc.returncode}.")
                self.root.after(0, self.stop_audio)

    def next_chapter(self):
        self.save_playback_state()
        if self.current_chapter_idx < len(self.chapters) - 1:
            self.current_chapter_idx += 1
            self.current_play_time = 0
            self.is_paused = False
            self.play_chapter()
        else:
            self.stop_audio()
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

    def stop_audio(self):
        self.is_playing = False
        self.is_paused = False
        if self.player_process:
            self.player_process.terminate()
            self.player_process = None
            
        self.save_playback_state()

    def update_info(self):
        if self.chapters:
            title = self.chapters[self.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AAXManagerApp(root)
    root.mainloop()