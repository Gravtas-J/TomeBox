import subprocess
import json
import threading
import os
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import traceback
import requests

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

            self.auth_object = None
            self.local_library = self.load_local_db()
            self.cloud_library = []
            
            self.file_path = ""
            self.auth_bytes = tk.StringVar(value="")
            self.chapters = []
            self.current_chapter_idx = 0
            self.player_process = None
            
            self.debug_mode = tk.BooleanVar(value=False)

            self.setup_ui()
            self.auto_load_auth()

    def load_local_db(self):
        if os.path.exists(self.local_db_path):
            with open(self.local_db_path, "r") as f:
                return json.load(f)
        return {}

    def save_local_db(self):
        with open(self.local_db_path, "w") as f:
            json.dump(self.local_library, f, indent=4)

    def setup_ui(self):
            notebook = ttk.Notebook(self.root)
            notebook.pack(fill="both", expand=True, padx=10, pady=10)

            self.tab_library = ttk.Frame(notebook)
            self.tab_settings = ttk.Frame(notebook)

            notebook.add(self.tab_library, text="Library & Player")
            notebook.add(self.tab_settings, text="Settings & Login")

            self.build_settings_tab()
            self.build_library_tab()

    # --- TAB 1: SETTINGS & LOGIN ---
    def build_settings_tab(self):
        self.locale = tk.StringVar(value="us")

        frame = tk.LabelFrame(self.tab_settings, text="Audible Authentication", padx=10, pady=10)
        frame.pack(fill="x", padx=10, pady=10)

        tk.Label(frame, text="Region:").grid(row=0, column=0, sticky="e", padx=5)
        tk.OptionMenu(frame, self.locale, *["us", "uk", "au", "ca", "de", "fr", "jp"]).grid(row=0, column=1, sticky="w", pady=5)

        btn_frame = tk.Frame(frame)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=10)

        self.browser_login_btn = tk.Button(btn_frame, text="Login via Browser", command=self.start_browser_login_thread)
        self.browser_login_btn.grid(row=0, column=0, padx=5)

        self.auth_file_btn = tk.Button(btn_frame, text="Load External Auth File (.json)", command=self.load_auth_file_prompt)
        self.auth_file_btn.grid(row=0, column=1, padx=5)

        log_frame = tk.LabelFrame(self.tab_settings, text="Connection Log", padx=10, pady=5)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        tk.Checkbutton(log_frame, text="Enable API Debug Output", variable=self.debug_mode).pack(anchor="w")

        self.log_text = tk.Text(log_frame, height=10, wrap="word", state=tk.DISABLED)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        self.log_text.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")

        bytes_frame = tk.LabelFrame(self.tab_settings, text="Decryption Bytes", padx=10, pady=10)
        bytes_frame.pack(fill="x", padx=10, pady=5)
        tk.Entry(bytes_frame, textvariable=self.auth_bytes, justify="center", width=20).pack(pady=5)

    def write_log(self, message):
        def append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, append)

    def auto_load_auth(self):
        if os.path.exists(self.auth_save_path):
            self.write_log("Found saved authentication session. Loading...")
            try:
                self.auth_object = audible.Authenticator.from_file(self.auth_save_path)
                activation_bytes = self.auth_object.get_activation_bytes()
                self.auth_bytes.set(activation_bytes)
                self.write_log(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
            except Exception as e:
                self.write_log("Failed to load saved session. You may need to log in again.")
        else:
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
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
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


    # --- TAB 2: LIBRARY MANAGER ---
    def build_library_tab(self):
            main_split = tk.Frame(self.tab_library)
            main_split.pack(fill="both", expand=True, padx=5, pady=5)

            left_panel = tk.Frame(main_split)
            left_panel.pack(side=tk.LEFT, fill="both", expand=True)

            right_panel = tk.Frame(main_split, width=350)
            right_panel.pack(side=tk.RIGHT, fill="y", padx=5)
            right_panel.pack_propagate(False)

            cloud_frame = tk.LabelFrame(left_panel, text="Cloud Library (Available on Audible)", padx=10, pady=5)
            cloud_frame.pack(fill="both", expand=True, padx=5, pady=5)

            self.cloud_tree = ttk.Treeview(cloud_frame, columns=("Title", "Author", "Duration", "ASIN"), show="headings", height=8)
            self.cloud_tree.heading("Title", text="Title")
            self.cloud_tree.heading("Author", text="Author")
            self.cloud_tree.heading("Duration", text="Duration")
            self.cloud_tree.heading("ASIN", text="ASIN")
            self.cloud_tree.column("Title", width=250)
            self.cloud_tree.column("Author", width=150)
            self.cloud_tree.column("Duration", width=70)
            self.cloud_tree.column("ASIN", width=100)
            self.cloud_tree.pack(fill="both", expand=True, pady=5)

            cloud_btn_frame = tk.Frame(cloud_frame)
            cloud_btn_frame.pack(pady=2)
            tk.Button(cloud_btn_frame, text="Refresh Cloud Library", command=self.fetch_cloud_library).grid(row=0, column=0, padx=5)
            tk.Button(cloud_btn_frame, text="Download Selected to Local", command=self.download_title_prompt).grid(row=0, column=1, padx=5)

            local_frame = tk.LabelFrame(left_panel, text="Local Library (Downloaded & Converted)", padx=10, pady=5)
            local_frame.pack(fill="both", expand=True, padx=5, pady=5)

            self.local_tree = ttk.Treeview(local_frame, columns=("Title", "Format", "Path"), show="headings", height=6)
            self.local_tree.heading("Title", text="Title")
            self.local_tree.heading("Format", text="Format")
            self.local_tree.heading("Path", text="File Path")
            self.local_tree.column("Title", width=250)
            self.local_tree.column("Format", width=70)
            self.local_tree.column("Path", width=250)
            self.local_tree.pack(fill="both", expand=True, pady=5)

            btn_frame = tk.Frame(local_frame)
            btn_frame.pack(pady=5)
            tk.Button(btn_frame, text="Add File to Local Library", command=self.add_local_file).grid(row=0, column=0, padx=5)
            tk.Button(btn_frame, text="Remove from List", command=self.remove_local_file).grid(row=0, column=1, padx=5)
            tk.Button(btn_frame, text="Send to Player", command=self.send_to_player).grid(row=0, column=2, padx=5)

            self.refresh_local_ui()
            self.build_player_components(right_panel)

    def fetch_cloud_library(self):
        if not self.auth_object:
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        for row in self.cloud_tree.get_children():
            self.cloud_tree.delete(row)
        
        self.cloud_tree.insert("", "end", values=("Fetching data from Amazon...", "", "", ""))
        threading.Thread(target=self.fetch_library_worker, daemon=True).start()

    def fetch_library_worker(self):
            try:
                self.write_log("Querying Audible Library API...")
                client = audible.Client(auth=self.auth_object)
                
                # Increased num_results to 1000 to cover larger libraries
                response = client.get("1.0/library", response_groups="product_desc,product_attrs", num_results=1000)
                
                if self.debug_mode.get():
                    self.write_log(f"DEBUG - API Keys Returned: {list(response.keys())}")
                    raw_dump = json.dumps(response, indent=2)[:1000]
                    self.write_log(f"DEBUG - Payload Snippet:\n{raw_dump}\n...[truncated]")

                items = response.get("items", [])
                if not items:
                    self.write_log("WARNING: API call succeeded, but 'items' array was empty or missing.")
                else:
                    self.write_log(f"Successfully retrieved {len(items)} library items.")

                self.root.after(0, lambda: self.update_cloud_ui(items))
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                self.write_log(f"ERROR FETCHING LIBRARY:\n{error_trace}")
                self.root.after(0, lambda: messagebox.showerror("Library Error", "Failed to fetch cloud library. Check the Settings log."))

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
            messagebox.showwarning("Selection Required", "Please select a title from the Cloud Library first.")
            return

        item = self.cloud_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

        if not asin or asin == "Unknown":
            messagebox.showerror("Data Error", "This item does not have a valid ASIN.")
            return

        save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
        if not save_dir:
            return

        self.write_log(f"Starting download process for ASIN: {asin}")
        threading.Thread(target=self.download_worker, args=(asin, title, save_dir), daemon=True).start()

    def download_worker(self, asin, title, save_dir):
        try:
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
                        percent = int((downloaded / total_size) * 100)
                        if percent >= last_percent + 10:
                            self.write_log(f"Download Progress: {percent}%")
                            last_percent = percent
            
            self.write_log(f"Download complete: {safe_title}.aaxc")
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Finished downloading:\n{title}"))
            
            self.local_library[filepath] = {
                "title": title, 
                "format": "AAXC", 
                "path": filepath,
                "audible_key": a_key,
                "audible_iv": a_iv
            }
            self.save_local_db()
            self.root.after(0, self.refresh_local_ui)
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")
            error_msg = str(e) 
            self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))

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
        selected = self.local_tree.focus()
        if not selected: return
        
        item = self.local_tree.item(selected)
        filepath = item['values'][2]
        
        if filepath in self.local_library:
            del self.local_library[filepath]
            self.save_local_db()
            self.refresh_local_ui()

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

    # --- TAB 3: PLAYER & CONVERTER ---
    def build_player_components(self, parent):
            file_frame = tk.LabelFrame(parent, text="Active File", padx=10, pady=10)
            file_frame.pack(fill="x", padx=5, pady=5)

            tk.Button(file_frame, text="Load File Manually", command=self.load_file_prompt).pack(pady=5)
            
            self.status_label = tk.Label(file_frame, text="No file loaded", fg="gray")
            self.status_label.pack(pady=2)

            self.convert_btn = tk.Button(file_frame, text="Convert to DRM-Free .m4b", command=self.start_convert_thread, state=tk.DISABLED)
            self.convert_btn.pack(pady=5)

            play_frame = tk.LabelFrame(parent, text="Playback", padx=10, pady=10)
            play_frame.pack(fill="both", expand=True, padx=5, pady=5)

            self.info_label = tk.Label(play_frame, text="", fg="blue", wraplength=300)
            self.info_label.pack(pady=5)

            self.is_playing = False
            self.chapter_duration = 0
            self.current_play_time = 0
            
            self.progress_var = tk.DoubleVar()
            self.progress_bar = ttk.Progressbar(play_frame, variable=self.progress_var, maximum=100)
            self.progress_bar.pack(fill="x", padx=10, pady=5)

            self.time_label = tk.Label(play_frame, text="00:00 / 00:00", fg="gray")
            self.time_label.pack(pady=2)

            controls_frame = tk.Frame(play_frame)
            controls_frame.pack(pady=5)

            tk.Button(controls_frame, text="<< Prev", command=self.prev_chapter).grid(row=0, column=0, padx=5)
            tk.Button(controls_frame, text="Play", command=self.play_chapter).grid(row=0, column=1, padx=5)
            tk.Button(controls_frame, text="Stop", command=self.stop_audio).grid(row=0, column=2, padx=5)
            tk.Button(controls_frame, text="Next >>", command=self.next_chapter).grid(row=0, column=3, padx=5)

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
                self.convert_btn.config(state=tk.DISABLED)
                return
            self.convert_btn.config(state=tk.NORMAL)
        else:
            self.convert_btn.config(state=tk.DISABLED) 

        self.status_label.config(text=f"Ready: {os.path.basename(self.file_path)}", fg="green")
        self.chapters = self.extract_chapters(self.file_path)
        
        if self.chapters:
            self.current_chapter_idx = 0
            self.update_info()

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
            output_file = filedialog.asksaveasfilename(defaultextension=".m4b", filetypes=[("M4B Audiobook", "*.m4b")], initialfile=os.path.basename(self.file_path).replace(".aaxc", ".m4b").replace(".aax", ".m4b"))
            if not output_file: return

            self.convert_btn.config(text="Converting...", state=tk.DISABLED)
            self.status_label.config(text="Converting to .m4b... Please wait.", fg="orange")
            threading.Thread(target=self.convert_worker, args=(self.file_path, output_file), daemon=True).start()

    def convert_worker(self, input_path, output_path):
        cmd = ["ffmpeg", "-y"]
        if input_path.endswith(".aax") or input_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(input_path))
        cmd.extend(["-i", input_path, "-c", "copy", output_path])
        
        try:
            subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.root.after(0, lambda: messagebox.showinfo("Success", "File converted."))
            self.local_library[output_path] = {"title": os.path.basename(output_path), "format": "M4B", "path": output_path}
            self.save_local_db()
            self.root.after(0, self.refresh_local_ui)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Conversion Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.convert_btn.config(text="Convert to DRM-Free .m4b", state=tk.NORMAL))
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
            self.stop_audio()
            
            chapter = self.chapters[self.current_chapter_idx]
            start_time = float(chapter.get("start_time", 0))
            end_time = float(chapter.get("end_time", 0))
            
            self.chapter_duration = end_time - start_time
            self.current_play_time = 0
            
            cmd = [
                "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", 
                "-ss", str(start_time), "-t", str(self.chapter_duration)
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
            
            self.is_playing = True
            self.update_info()
            threading.Thread(target=self.monitor_player_output, daemon=True).start()
            self.update_playback_progress()

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def update_playback_progress(self):
        # If process died or was stopped, halt the tracker
        if not self.is_playing or not self.player_process or self.player_process.poll() is not None:
            self.is_playing = False
            return
        
        self.current_play_time += 1
        if self.current_play_time > self.chapter_duration:
            self.current_play_time = self.chapter_duration
            
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")
        
        # Loop this function every 1000 milliseconds (1 second)
        self.root.after(1000, self.update_playback_progress)

    def monitor_player_output(self):
            proc = self.player_process
            if not proc: return
            
            for line in proc.stderr:
                if line.strip():
                    self.write_log(f"[PLAYER ERROR]: {line.strip()}")
            
            proc.wait()
            if proc.returncode != 0 and self.is_playing:
                self.write_log(f"[CRITICAL]: Player crashed with code {proc.returncode}.")
                self.root.after(0, self.stop_audio)

    def next_chapter(self):
        if self.current_chapter_idx < len(self.chapters) - 1:
            self.current_chapter_idx += 1
            self.play_chapter()

    def prev_chapter(self):
        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            self.play_chapter()

    def stop_audio(self):
        self.is_playing = False
        if self.player_process:
            self.player_process.terminate()
            self.player_process = None
        self.progress_var.set(0)
        self.time_label.config(text="00:00 / 00:00")

    def update_info(self):
        if self.chapters:
            title = self.chapters[self.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")

if __name__ == "__main__":
    root = tk.Tk()
    app = AAXManagerApp(root)
    root.mainloop()