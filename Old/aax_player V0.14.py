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

try:
    import audible
except ImportError:
    messagebox.showerror("Missing Dependency", "Please run: pip install audible requests pillow")
    exit()

class AAXManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAX Library Manager & Player")
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
                return json.load(f)
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
        lib_frame = tk.LabelFrame(parent, text="Unified Library", padx=10, pady=5)
        lib_frame.pack(fill="both", expand=True, padx=5, pady=5)

        tree_frame = tk.Frame(lib_frame)
        tree_frame.pack(fill="both", expand=True, pady=5)

        scroll = ttk.Scrollbar(tree_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        # NEW: Added "Series" to the columns tuple
        self.library_tree = ttk.Treeview(tree_frame, columns=("Title", "Author", "Series", "Duration", "ASIN", "Status"), show="headings", yscrollcommand=scroll.set)
        scroll.config(command=self.library_tree.yview)

        for col in self.library_tree["columns"]:
            self.library_tree.heading(col, text=col, command=lambda _col=col: self.sort_treeview(self.library_tree, _col, False))
            
        # Adjusted widths to accommodate the new column
        self.library_tree.column("Title", width=250)
        self.library_tree.column("Author", width=120)
        self.library_tree.column("Series", width=120)
        self.library_tree.column("Duration", width=70)
        self.library_tree.column("ASIN", width=90)
        self.library_tree.column("Status", width=110)
        self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
        
        self.library_tree.bind("<Double-1>", self.master_play)

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

    def refresh_library_ui(self):
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        local_titles = {data["title"]: data for path, data in self.local_library.items()}
        cloud_titles = []

        for item in self.cloud_items:
            asin = item.get("asin", "Unknown")
            title = item.get("title") or "Unknown"
            cloud_titles.append(title)
            
            # Author Parsing
            raw_authors = item.get("authors") or []
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            # NEW: Series Parsing
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
            
            # Duration Parsing
            duration_min = item.get("runtime_length_min") or 0
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            local_data = local_titles.get(title)
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            
            # Insert with the new series_str variable included
            self.library_tree.insert("", "end", values=(title, authors, series_str, duration_str, asin, status))

        for path, data in self.local_library.items():
            if data["title"] not in cloud_titles:
                # Include an extra "N/A" for the missing series data on orphaned local files
                self.library_tree.insert("", "end", values=(data["title"], "Local File", "N/A", "N/A", "Unknown", f"Downloaded ({data['format']})"))
    
    def handle_action_on_selected(self, action_type):
        selected = self.library_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title first.")
            return

        item = self.library_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

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

        self.write_log("DEBUG: self.auth_object verified. Prepping UI tree...")
        
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)
        
        # NEW: Added an extra empty string for the Series column
        self.library_tree.insert("", "end", values=("Fetching data from Amazon...", "", "", "", "", "Working..."))
        
        self.write_log("DEBUG: UI updated. Launching fetch_library_worker thread...")
        threading.Thread(target=self.fetch_library_worker, daemon=True).start()

    def fetch_library_worker(self):
        try:
            self.write_log("Querying Audible Library API...")
            client = audible.Client(auth=self.auth_object)
            
            # NEW: Added 'series' and 'contributors' to the response_groups
            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors", num_results=1000)
            
            self.cloud_items = response.get("items", [])
            self.write_log(f"Successfully retrieved {len(self.cloud_items)} library items.")
            
            # Save the fresh data to the hard drive
            self.save_cloud_cache()

            self.root.after(0, self.refresh_library_ui)
        except Exception as e:
            import traceback
            self.write_log(f"ERROR FETCHING LIBRARY:\n{traceback.format_exc()}")
            self.root.after(0, lambda: messagebox.showerror("Library Error", "Failed to fetch cloud library."))

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
                    chunk = response.read(32768)
                    if not chunk: break
                    out_file.write(chunk)
                    
                    if total_size > 0:
                        downloaded += len(chunk)
                        percent_float = (downloaded / total_size) * 100
                        self.root.after(0, self.dl_progress_var.set, percent_float)
                        
                        percent_int = int(percent_float)
                        if percent_int >= last_percent + 10:
                            self.write_log(f"Download Progress: {percent_int}%")
                            last_percent = percent_int
            
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
            self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")
            error_msg = str(e) 
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

        # NEW: Relocated status label
        self.status_label = tk.Label(play_frame, text="No file loaded", fg="gray")
        self.status_label.pack(side=tk.TOP, pady=(0, 5))

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
        
        self.status_label.config(text="Analyzing...", fg="orange")
        self.root.update()
        
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.file_path)
            if not success:
                self.status_label.config(text="Verification Failed", fg="red")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.file_path = ""
                return

        self.status_label.config(text=f"Ready: {os.path.basename(self.file_path)}", fg="green")
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
        output_file = filedialog.asksaveasfilename(
            defaultextension=".m4b", 
            filetypes=[("M4B Audiobook", "*.m4b")], 
            initialfile=os.path.basename(self.file_path).replace(".aaxc", ".m4b").replace(".aax", ".m4b")
        )
        if not output_file: 
            return

        # Removed the convert_btn reference here
        self.status_label.config(text="Converting to .m4b... Please wait.", fg="orange")
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
            self.root.after(0, lambda: self.status_label.config(text=f"Ready: {os.path.basename(input_path)}", fg="green"))

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
        
        if self.file_path.endswith(".aax") or self.file_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(self.file_path))
            
        cmd.append(self.file_path)

        if self.debug_mode.get():
            self.write_log(f"Starting player: {' '.join(cmd)}")

        self.player_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
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
        
        self.current_play_time += delta
        
        if self.current_play_time > self.chapter_duration:
            self.current_play_time = self.chapter_duration
            
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")
        
        # Updating every 500ms provides a smoother progress bar without taxing the CPU
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