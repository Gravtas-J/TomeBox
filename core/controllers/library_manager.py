import os
import json
import threading
import requests
import traceback
import datetime
try:
    import audible
except ImportError:
    pass
import queue
import re
import time
from tkinter import messagebox
from core.utils.process_runner import ProcessRunner
from core.utils.text import format_series_list, normalize_title, find_matching_cloud_item
from core.events import default_bus
from core.utils.fs import safe_unlink

class LibraryManager:
    def __init__(self, db_manager, api_client, base_dir, start_workers=True, event_bus=None):
        self.db = db_manager
        self.event_bus = event_bus or default_bus
        self.api = api_client
        self.base_dir = base_dir
        self.covers_dir = os.path.join(base_dir, "covers")
        self.cancel_requested = False
        self.current_status = ""

        self.import_queue = queue.Queue()
        self._is_importing = False
        self.on_queue_empty_cb = None

        self.active_task_id = None
        self.canceled_tasks = set()
        self.unimportable_files = set()

        if start_workers:
            threading.Thread(target=self._import_worker_loop, daemon=True).start()

        self.local_library = {}
        self.cloud_items = []
        self.master_metadata = {}
        
        self.active_profile = self.db.load_settings().get("active_profile", "Main")
        self.cloud_cache_path = self.db.get_cloud_cache_path(self.active_profile)
        self.load_state()

        self.is_rate_limited = False
        self.rate_limit_reset_time = 0.0

    def run_background_library_scan(self, converter, active_profile, logger, thread_pool, on_refresh_cb=None):
        def worker():
            import time
            import os
            settings = self.db.load_settings()
            folders = settings.get("library_folders", [])
            
            # Normalize the ignored list exactly how import_folder does it
            ignored_files = {os.path.normpath(p) for p in settings.get("ignored_files", [])} 
            
            if not folders: 
                return
                
            # Unified extensions
            valid_exts = (".aax", ".aaxc", ".m4b", ".mp3", ".m4a")
            untracked_dirs = set()
            
            logger.info(f"Background scanner checking {len(folders)} library folders...")
            
            # Gather all tracked file paths dynamically
            tracked_files = set()
            for path, data in self.local_library.items():
                if data.get("is_playlist"):
                    for ch in data.get("chapters", []):
                        ch_path = ch.get("file_path")
                        if ch_path:
                            tracked_files.add(os.path.normpath(ch_path))
                else:
                    if path:
                        tracked_files.add(os.path.normpath(path))
            
            for folder in folders:
                if not os.path.exists(folder): continue
                for root_dir, _, files in os.walk(folder):
                    
                    for f in files:
                        if ".temp." in f.lower() or f.lower().endswith(".tmp"):
                            continue
                        if f.lower().endswith(valid_exts):
                            # Strict match to how import_folder and the UI save paths
                            full_path = os.path.normpath(os.path.join(root_dir, f))
                            
                            # Gatekeeper 1: User explicitly ignored it
                            if full_path in ignored_files:
                                continue 
                            
                            # Gatekeeper 2: It's not in the library yet
                            if full_path not in tracked_files:
                                untracked_dirs.add(root_dir)
                                break # We only need one untracked file to trigger a folder scan
                            
                            # Gatekeeper 3: It's not in the library yet
                            if full_path not in tracked_files:
                                untracked_dirs.add(root_dir)
                                break
            
            if untracked_dirs:
                logger.info(f"Background scan found new files in {len(untracked_dirs)} directories. Queuing smart import...")
                
                # Default to merge so it behaves identically to Drag & Drop
                default_mode = settings.get("default_import_mode", "playlist")
                
                for directory in untracked_dirs:
                    self.import_folder(
                        folder_path=directory, 
                        converter=converter, 
                        active_profile=active_profile,
                        on_status_cb=None, 
                        on_complete_cb=lambda c, t: on_refresh_cb() if on_refresh_cb else None,
                        logger=logger,
                        task_id=f"auto_scan_{time.time()}_{os.path.basename(directory)}",
                        import_mode=default_mode  
                    )
        
        thread_pool.submit(worker)

    def trigger_rate_limit(self, cooldown_seconds=60):
        self.is_rate_limited = True
        self.rate_limit_reset_time = time.time() + cooldown_seconds
        self.current_status = f"Rate limited. Pausing tasks for {cooldown_seconds}s."
        self.event_bus.publish("library.rate_limited", cooldown=cooldown_seconds)

    def check_rate_limit(self):
        if self.is_rate_limited:
            if time.time() > self.rate_limit_reset_time:
                self.is_rate_limited = False
                self.current_status = ""
                return False
            return True
        return False
    
    def cancel_import(self, task_id=None):
        if task_id:
            self.canceled_tasks.add(task_id)
            if self.active_task_id == task_id:
                self.cancel_requested = True
        else:
            self.cancel_requested = True
            with self.import_queue.mutex:
                self.import_queue.queue.clear()
            self.canceled_tasks.clear()

    def _import_worker_loop(self):
        while True:
            task_func = self.import_queue.get()
            self._is_importing = True
            try:
                task_func()
            except Exception as e:
                print(f"Import Queue Error: {e}")
            finally:
                self._is_importing = False
                self.import_queue.task_done()

                if self.import_queue.empty():
                    self.current_status = ""
                    self.event_bus.publish("library.queue.empty")
                    if self.on_queue_empty_cb:
                        self.on_queue_empty_cb()
                
    def load_state(self):
        import time
        self.local_library = self.db.load_local_db()
        
        modified = False
        for filepath, data in self.local_library.items():
            if "date_added" not in data:
                try:
                    data["date_added"] = os.path.getctime(filepath)
                except OSError:
                    data["date_added"] = time.time()
                modified = True

        if modified:
            self.db.save_local_db(self.local_library)
            
        if os.path.exists(self.cloud_cache_path):
            try:
                with open(self.cloud_cache_path, "r") as f:
                    self.cloud_items = json.load(f)
            except Exception:
                self.cloud_items = []
                
        self._build_master_metadata()

    def _build_master_metadata(self):
        self.master_metadata = {}
        
        data_dir = os.path.join(self.base_dir, "data")
        if os.path.exists(data_dir):
            for f in os.listdir(data_dir):
                if f.startswith("cloud_") and f.endswith(".json") or f == "cloud_cache.json":
                    try:
                        with open(os.path.join(data_dir, f), "r") as file:
                            for item in json.load(file):
                                if item.get("title"): self.master_metadata[item["title"]] = item
                                if item.get("asin"): self.master_metadata[item["asin"]] = item
                    except Exception: pass

        for item in self.cloud_items:
            if item.get("title"): self.master_metadata[item["title"]] = item
            if item.get("asin"): self.master_metadata[item["asin"]] = item

    def get_authors_for_asin(self, asin):
        for item in self.cloud_items:
            if item.get("asin") == asin:
                raw_authors = item.get("authors", [])
                return ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
        return ""

    def get_view_data(self, search_query="", filter_type="All", shelf_filter="All Shelves"):
        from datetime import datetime
        rows = []
        all_unique_shelves = set()
        settings = self.db.load_settings()
        shelves_db = settings.get("shelves_db", {})
        
        cloud_titles = set()
        search_query = search_query.lower()

        local_titles = {data["title"]: data for path, data in self.local_library.items()}

        for item in self.cloud_items:
            title = item.get("title", "Unknown")
            cloud_titles.add(title)
            
            raw_authors = item.get("authors") or []
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            raw_narrators = item.get("narrators") or []
            narrator = ", ".join([n.get("name", "") for n in raw_narrators if isinstance(n, dict)]) or item.get("narrator", "")
            
            series_str = format_series_list(item.get("series"))
            if series_str.upper() in ["N/A", "NONE", "UNKNOWN", "NULL", "[]"]:
                series_str = ""
            duration_min = item.get("runtime_length_min") or 0
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            asin = item.get("asin", "Unknown")
            
            local_data = local_titles.get(title) 
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            local_path = local_data['path'] if local_data else ""
            
            date_val = local_data.get("date_added", 0) if local_data else 0
            date_str = datetime.fromtimestamp(date_val).strftime('%Y-%m-%d') if date_val > 0 else "N/A"

            # Add narrator to the tuple here
            rows.append((title, authors, narrator, series_str, duration_str, asin, status, local_path, date_str))
            all_unique_shelves.update(shelves_db.get(asin, []))

        for path, data in self.local_library.items():
            title = data.get("title", "Unknown")
            if title not in cloud_titles:
                asin = data.get("asin", "Unknown")
                meta = self.master_metadata.get(title) or self.master_metadata.get(asin, {})

                loc_authors = data.get("authors", "Local File")
                if meta.get("authors") and loc_authors in ["Unknown", "Unknown Author", "Local File"]:
                    loc_authors = ", ".join([a.get("name", "") for a in meta.get("authors") if isinstance(a, dict)])

                loc_narrator = data.get("narrator", "")
                if meta.get("narrators") and not loc_narrator:
                    loc_narrator = ", ".join([n.get("name", "") for n in meta.get("narrators") if isinstance(n, dict)])

                loc_series = data.get("series", "")
                if meta.get("series") and (not loc_series or loc_series.upper() == "N/A"):
                    loc_series = format_series_list(meta.get("series"))

                # Normalize to blank universally
                if loc_series.upper() in ["N/A", "NONE", "UNKNOWN", "NULL", "[]"]:
                    loc_series = ""

                duration_min = meta.get("runtime_length_min") or data.get("duration_min") or 0
                loc_duration = f"{duration_min//60}h {duration_min%60}m" if duration_min > 0 else "N/A"

                if asin == "Unknown" and meta.get("asin"):
                    asin = meta.get("asin")
                    
                date_val = data.get("date_added", 0)
                date_str = datetime.fromtimestamp(date_val).strftime('%Y-%m-%d') if date_val > 0 else "N/A"

                # Add loc_narrator to the tuple here
                rows.append((title, loc_authors, loc_narrator, loc_series, loc_duration, asin, f"Downloaded ({data.get('format', 'UNKNOWN')})", path, date_str))
                all_unique_shelves.update(shelves_db.get(asin, []))

        filtered_rows = []
        for row in rows:
            title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row

            if filter_type == "Downloaded" and "Downloaded" not in status: continue
            if filter_type == "Cloud Only" and status != "Cloud Only": continue
            if shelf_filter != "All Shelves" and shelf_filter not in shelves_db.get(asin, []): continue
            
            if search_query and search_query not in f"{title} {authors} {narrator} {series_str}".lower():
                continue

            filtered_rows.append(row)

        shelf_list = ["All Shelves"] + sorted(list(all_unique_shelves))
        return filtered_rows, shelf_list

    def fetch_cloud_library(self):
        if not self.api.auth:
            raise Exception("Not authenticated")
        
        self.cloud_items = self.api.fetch_library()
        self._build_master_metadata()
        
        try:
            with open(self.cloud_cache_path, "w") as f:
                json.dump(self.cloud_items, f, indent=4)
        except Exception as e:
            print(f"Cache save error: {e}")
            
        return True

    def add_local_file(self, filepath, metadata):
        self.local_library[filepath] = metadata
        self.db.save_local_db(self.local_library)

    # library_manager.py
    def handle_remove_clicked(self, app):
        # 1. Grab the cached selection to beat the macOS focus bug
        selected_items = getattr(app, '_cached_selection', app.library_tree.selection())
        if not selected_items:
            return

        # 2. Extract local paths directly from the Treeview data
        items_to_remove = []
        for item_id in selected_items:
            values = app.library_tree.item(item_id).get('values', [])
            if len(values) > 7 and values[7]:
                local_path = values[7]
                items_to_remove.append((item_id, local_path))

        # 3. If nothing is local, just tell them and bail out early
        if not items_to_remove:
            messagebox.showinfo(
                "Cloud Only",
                "The selected title(s) are not currently in your downloaded local library.",
                parent=app.root
            )
            return

        # 4. Prompt ONLY for the items that can actually be removed
        msg = (f"Remove {len(items_to_remove)} selected item(s) from your local library list?\n\n"
               "(This only removes them from the list, it does not delete the actual files from your hard drive.)")
        if not messagebox.askyesno("Remove Files", msg, parent=app.root):
            return

        removed = 0
        
        # Load settings to access the ignored list
        settings = self.db.load_settings()
        ignored_files = set(settings.get("ignored_files", []))

        # Save index for auto-selection later
        last_idx = 0
        try:
            last_idx = app.library_tree.index(selected_items[-1])
        except Exception:
            pass

        # 4. Perform the actual removal using our pre-filtered list
        for item_id, local_path in items_to_remove:
            entry_data = self.local_library.get(local_path, {})
            if self.remove_local_file(local_path):
                removed += 1
                ignored_files.add(local_path) # Add to ignore list
                
                for ch in entry_data.get("chapters", []):
                    ch_path = ch.get("file_path")
                    if ch_path:
                        ignored_files.add(os.path.normpath(ch_path))

                # --- Phase 1 Fix: Clear Player Ghost State ---
                if app.file_path == local_path:
                    app.playback_presenter.stop_audio()
                    app.playback.chapters = []
                    app.file_path = ""
                    app.playback_presenter.update_info() 
                    if hasattr(app.playback_presenter.view, 'time_label'):
                        app.playback_presenter.view.time_label.config(text="00:00 / 00:00")
                    app.ui_state.playback_progress.set(0)

        if removed > 0:
            # Save the updated ignored list back to the DB
            settings["ignored_files"] = list(ignored_files)
            self.db.save_settings(settings)
            
            app.library_presenter.refresh_library_ui()
            app.clear_sidebar()
            app._selected_local_path = None
            
            # --- Phase 3 Fix: Auto-select next row ---
            children = app.library_tree.get_children()
            if children:
                next_idx = min(last_idx - len(selected_items) + 1, len(children) - 1)
                next_idx = max(0, next_idx)
                next_item = children[next_idx]
                app.library_tree.selection_set(next_item)
                app.library_tree.focus(next_item)
                app.library_tree.see(next_item)
                app.on_item_select()
            
    def remove_local_file(self, filepath):
        if filepath in self.local_library:
            del self.local_library[filepath]
            self.db.save_local_db(self.local_library)
            self.event_bus.publish("library.file_removed", filepath=filepath)
            return True 
        return False
            
    def set_shelves(self, asin, tags_list):
        settings = self.db.load_settings()
        if "shelves_db" not in settings:
            settings["shelves_db"] = {}
            
        settings["shelves_db"][asin] = tags_list
        self.db.save_settings(settings)
    
    def _process_single_file_for_import(self, filepath, active_profile, converter, logger=None):
        import hashlib
        from core.utils.process_runner import ProcessRunner

        ext = os.path.splitext(filepath)[1].lower()
        filename = os.path.basename(filepath)
        title = filename
        authors = "Unknown Author"
        format_clean = ext.replace(".", "").upper()
        embedded_meta = {}
        extracted_chapters = []
        
        # Safely grab the creation date
        try:
            creation_date = os.path.getctime(filepath)
        except OSError:
            creation_date = time.time()

        if format_clean in ["M4B", "MP3", "M4A"]:
            try:
                data = converter.get_metadata_and_chapters(filepath)
                tags = data.get("format", {}).get("tags", {})
                extracted_chapters = data.get("chapters", [])

                if "title" in tags: title = tags["title"]
                if "artist" in tags: authors = tags["artist"]
                elif "album_artist" in tags: authors = tags["album_artist"]
                
                embedded_meta = {
                    "album": tags.get("album", ""),
                    "year": tags.get("date", "") or tags.get("year", ""),
                    "comment": tags.get("comment", ""),
                    "narrator": tags.get("composer", ""),
                    "duration_min": int(float(data.get("format", {}).get("duration", 0)) / 60),
                    "chapters": extracted_chapters,
                    "date_added": creation_date
                }
                
                series_name = tags.get("series") or tags.get("show") or tags.get("album_sort")
                series_part = tags.get("series-part") or tags.get("episode_id") or tags.get("movement")
                
                if series_name:
                    if series_part:
                        embedded_meta["series"] = f"{series_name}, Book {series_part}"
                    else:
                        embedded_meta["series"] = series_name
                
                for stream in data.get("streams", []):
                    if stream.get("codec_type") == "video" or stream.get("disposition", {}).get("attached_pic") == 1:
                        embedded_meta["has_embedded_cover"] = True
                        break
            except Exception as e:
                if logger: logger(f"Failed to read tags for {filename}: {e}")
                raise ValueError(f"Corrupt or unreadable media file: {filename}") from e

        matched_cloud_item = find_matching_cloud_item(title, self.cloud_items)

        entry = {
            "title": title,
            "format": format_clean,
            "path": filepath,
            "authors": authors,
            "owner": active_profile,
            "duration_min": embedded_meta.get("duration_min", 0),
            "chapters": extracted_chapters,
            "date_added": embedded_meta.get("date_added", creation_date)
        }

        if embedded_meta.get("series"): entry["series"] = embedded_meta["series"]
        if embedded_meta.get("narrator"): entry["narrator"] = embedded_meta["narrator"]
        if embedded_meta.get("year"): entry["year"] = embedded_meta["year"]

        if matched_cloud_item:
            entry["title"] = matched_cloud_item.get("title", title)
            entry["asin"] = matched_cloud_item.get("asin", "")
            
            raw_authors = matched_cloud_item.get("authors", [])
            if raw_authors:
                entry["authors"] = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            if logger:
                logger(f"Matched '{title}' to cloud library: {entry['title']} ({entry['asin']})")
        else:
            fake_asin = "LOCAL_" + hashlib.md5(filepath.encode()).hexdigest()[:10]
            cover_output = os.path.join(self.covers_dir, f"{fake_asin}.jpg")
            extraction_succeeded = os.path.exists(cover_output) and os.path.getsize(cover_output) > 0
            
            # 1. Try to extract an embedded cover first
            if not extraction_succeeded:
                try:
                    extract_cmd = ["ffmpeg", "-y", "-i", filepath, "-an", "-vframes", "1", cover_output]
                    result = ProcessRunner.run_blocking(extract_cmd, capture_output=True)
                    
                    if result.returncode == 0 and os.path.exists(cover_output) and os.path.getsize(cover_output) > 0:
                        extraction_succeeded = True
                        if logger: logger(f"Extracted embedded cover for {title}")
                except Exception as e:
                    if logger: logger(f"Cover extraction failed for {title}: {e}")
                    
            if not extraction_succeeded:
                directory = os.path.dirname(filepath)
                valid_covers = ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "art.jpg", "art.png"]
                
                for c in valid_covers:
                    test_path = os.path.join(directory, c)
                    if os.path.exists(test_path):
                        try:
                            from PIL import Image
                            img = Image.open(test_path).convert("RGB")
                            img.save(cover_output, "JPEG")
                            extraction_succeeded = True
                            if logger: logger(f"Auto-assigned local cover for {title} from '{c}'")
                            break
                        except Exception:
                            pass
            
            if extraction_succeeded:
                entry["asin"] = fake_asin

        return entry
    
    def import_files(self, file_paths, converter, active_profile, on_status_cb, on_complete_cb, logger=None, task_id=None):
        if self._is_importing or not self.import_queue.empty():
            self.current_status = f"Queued {len(file_paths)} files for import..."
            if on_status_cb: on_status_cb(self.current_status)
        else:
            self.current_status = "Initializing import..."
            if on_status_cb: on_status_cb(self.current_status)
            
        def worker():
            self.cancel_requested = False
            self.active_task_id = task_id
            added_count = 0
            total_expected = len(file_paths)

            def update_status(msg):
                self.current_status = msg
                self.event_bus.publish("library.import.status", task_id=task_id, status=msg)
                if on_status_cb: on_status_cb(msg)

            try:
                if task_id and task_id in self.canceled_tasks:
                    update_status("Import cancelled.")
                    return

                valid_exts = [".aax", ".aaxc", ".m4b", ".mp3"]
                
                for filepath in file_paths:
                    if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                        update_status("Import cancelled.")
                        break
                    if not os.path.exists(filepath): continue
                    if filepath in self.local_library: continue
                    
                    ext = os.path.splitext(filepath)[1].lower()
                    if ext not in valid_exts: continue
                    
                    filename = os.path.basename(filepath)
                    update_status(f"Importing: {filename}")
                    
                    entry = self._process_single_file_for_import(filepath, active_profile, converter, logger)
                    
                    self.local_library[filepath] = entry
                    added_count += 1
                    
                if added_count > 0:
                    self.db.save_local_db(self.local_library)
                    
            except Exception as e:
                if logger: logger(f"Import Error: {e}")
            finally:
                if on_complete_cb:
                    on_complete_cb(added_count, total_expected)
                self.active_task_id = None
                self.current_status = ""

        self.import_queue.put(worker)

    def save_playback_state(self, state_dict, active_profile):
        if not state_dict: return
        
        file_path = state_dict["file_path"]
        if file_path in self.local_library:
            self.local_library[file_path]["last_chapter"] = state_dict["chapter_idx"]
            self.local_library[file_path]["last_time"] = state_dict["rel_time"]
            self.local_library[file_path]["last_position"] = state_dict["abs_time"]
            
            if "progress" not in self.local_library[file_path]:
                self.local_library[file_path]["progress"] = {}
            self.local_library[file_path]["progress"][active_profile] = state_dict["abs_time"]
            
            self.db.save_local_db(self.local_library)

            settings = self.db.load_settings()
            settings[f"last_played_{active_profile}"] = file_path
            self.db.save_settings(settings)

            self.event_bus.publish("library.state_saved", file_path=file_path, profile=active_profile)
    
    def silent_cloud_sync(self, logger, on_status_cb, on_refresh_cb):
        if not self.api.auth:
            return

        try:
            logger.info("Background sync: Polling Audible API...")
            new_items = self.api.fetch_library()
            
            if on_status_cb:
                on_status_cb("Library Synced (Online)")
            
            current_asins = {item.get("asin") for item in self.cloud_items if item.get("asin")}
            new_asins = {item.get("asin") for item in new_items if item.get("asin")}
            
            if current_asins != new_asins:
                logger.info(f"Background sync: Detected library change. Old: {len(self.cloud_items)}, New: {len(new_items)}")
                self.cloud_items = new_items
                self._build_master_metadata()
                
                try:
                    with open(self.cloud_cache_path, "w") as f:
                        json.dump(self.cloud_items, f, indent=4)
                except Exception as e:
                    logger.error(f"Cache save error during silent sync: {e}")
                    
                if on_refresh_cb:
                    on_refresh_cb()
            else:
                logger.info("Background sync: No changes detected.")
                
        except Exception as e:
            logger.info(f"Background sync failed silently: {e}")
            err_str = str(e).lower()
            if "429" in err_str:
                if on_status_cb: on_status_cb("Rate Limited by Audible")
            elif "50" in err_str:
                if on_status_cb: on_status_cb("Audible Servers Down")
            elif "connect" in err_str or "timeout" in err_str:
                if on_status_cb: on_status_cb("Offline - Check Connection")

    def monitor_local_files(self, logger, on_refresh_cb):
        import time
        import os
        
        while True:
            ui_needs_refresh = False
            
            missing_paths = []
            for path, data in list(self.local_library.items()):
                if data.get("is_playlist", False):
                    chapters = data.get("chapters", [])
                    if chapters and not os.path.exists(chapters[0].get("file_path", "")):
                        missing_paths.append(path)
                elif not os.path.exists(path):
                    missing_paths.append(path)
            
            if missing_paths:
                for path in missing_paths:
                    del self.local_library[path]
                    self.event_bus.publish("library.file_removed", filepath=path)

                logger.info(f"Detected {len(missing_paths)} deleted files. Updating library...")
                self.db.save_local_db(self.local_library)
                ui_needs_refresh = True

            if hasattr(self.db, 'db_path') and os.path.exists(self.db.db_path):
                try:
                    current_mtime = os.path.getmtime(self.db.db_path)
                    
                    if self.db.last_db_mtime == 0:
                        self.db.last_db_mtime = current_mtime
                    elif current_mtime > self.db.last_db_mtime:
                        logger.info("External DB change detected. Syncing local library...")
                        self.db.last_db_mtime = current_mtime
                        self.local_library = self.db.load_local_db()
                        ui_needs_refresh = True
                except Exception as e:
                    logger.error(f"DB Monitor Error: {e}")
            
            if ui_needs_refresh and on_refresh_cb:
                on_refresh_cb()
                
            time.sleep(30)

    def _build_playlist_entry(self, directory, files, album_name, active_profile, converter, logger):
        import hashlib
        import time

        first_file = files[0]
        meta_data = converter.get_metadata_and_chapters(first_file)
        tags = meta_data.get("format", {}).get("tags", {})

        title = album_name
        authors = tags.get("artist") or tags.get("album_artist", "Unknown Author")
        series_name = tags.get("series") or tags.get("show") or tags.get("album_sort")
        series_part = tags.get("series-part") or tags.get("episode_id") or tags.get("movement")
        series = f"{series_name}, Book {series_part}" if series_name and series_part else (series_name or "")

        virtual_path = os.path.join(directory, f"{''.join([c for c in album_name if c.isalnum()]).rstrip()}_playlist")
        fake_asin = "LOCAL_" + hashlib.md5(virtual_path.encode()).hexdigest()[:10]

        cover_output = os.path.join(self.covers_dir, f"{fake_asin}.jpg")
        if not os.path.exists(cover_output):
            extract_cmd = ["ffmpeg", "-y", "-i", first_file, "-an", "-vframes", "1", cover_output]
            ProcessRunner.run_blocking(extract_cmd, capture_output=True)

            if not os.path.exists(cover_output) or os.path.getsize(cover_output) == 0:
                valid_covers = ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "art.jpg", "art.png"]
                for c in valid_covers:
                    test_path = os.path.join(directory, c)
                    if os.path.exists(test_path):
                        try:
                            from PIL import Image
                            img = Image.open(test_path).convert("RGB")
                            img.save(cover_output, "JPEG")
                            break
                        except: pass

        chapters = []
        current_time = 0.0

        for idx, f in enumerate(files):
            try:
                dur = float(converter.get_metadata_and_chapters(f).get("format", {}).get("duration", 0))
            except:
                dur = 0

            chapters.append({
                "id": idx,
                "start_time": str(current_time),
                "end_time": str(current_time + dur),
                "tags": {"title": os.path.basename(f)},
                "file_path": f  
            })
            current_time += dur

        entry = {
            "title": title,
            "format": "PLAYLIST",
            "path": virtual_path,
            "authors": authors,
            "series": series,
            "owner": active_profile,
            "duration_min": int(current_time / 60),
            "chapters": chapters,
            "is_playlist": True,
            "date_added": time.time(),
            "asin": fake_asin
        }

        matched = find_matching_cloud_item(title, self.cloud_items)
        if matched:
            entry["title"] = matched.get("title", title)
            entry["asin"] = matched.get("asin", fake_asin)
            raw_authors = matched.get("authors", [])
            if raw_authors:
                entry["authors"] = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])

        return entry, virtual_path

    def import_folder(self, folder_path, converter, active_profile, on_status_cb, on_complete_cb, logger=None, on_progress_cb=None, task_id=None, import_mode='merge', on_book_start_cb=None, on_book_progress_cb=None, on_book_complete_cb=None):
        if self._is_importing or not self.import_queue.empty():
            self.current_status = f"Queued folder for import: {os.path.basename(folder_path)}"
            if on_status_cb: on_status_cb(self.current_status)
        else:
            self.current_status = "Initializing import..."
            if on_status_cb: on_status_cb(self.current_status)

        def worker():
            self.cancel_requested = False
            self.active_task_id = task_id
            added_count = 0
            total_expected = 0
            
            # --- TRANSPARENCY TRACKERS ---
            report = {
                "discovered_files": [],
                "missing_album_tags": [],
                "broken_metadata": [],
                "playlist_merges": [],
                "m4b_merges": [],
                "failed_imports": [],
                "skipped_existing": [],
                "skipped_invalid": []
            }
            
            def update_status(msg):
                self.current_status = msg
                if on_status_cb: on_status_cb(msg)

            try:
                if task_id and task_id in self.canceled_tasks:
                    update_status("Import cancelled.")
                    return
                
                import re
                if not os.path.isdir(folder_path):
                    if logger: logger(f"[Debug] Import Failed: {folder_path} is not a valid directory.")
                    return
                
                update_status("Scanning and grouping files...")
                
                def natural_sort_key(s):
                    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]
                
                valid_exts = (".aax", ".aaxc", ".m4b", ".mp3", ".m4a") 
                
                ignored_set = set()
                try:
                    settings = self.db.load_settings()
                    if settings and "ignored_files" in settings:
                        ignored_set = {os.path.normpath(p) for p in settings["ignored_files"]}
                except Exception:
                    pass
                
                # --- PHASE 1: Global File Pooling ---
                for root_dir, dirs, files in os.walk(folder_path):
                    for f in files:
                        if ".temp." in f.lower() or f.lower().endswith(".tmp"):
                            continue
                            
                        if f.lower().endswith(valid_exts):
                            full_path = os.path.normpath(os.path.join(root_dir, f))
                            if full_path not in ignored_set:
                                report["discovered_files"].append(full_path)
                                
                if not report["discovered_files"]:
                    if logger: logger(f"No valid audio files found in {folder_path} matching {valid_exts}")
                    return
                
                file_metadata_cache = {}
                global_album_groups = {}
                
                def advanced_sort_key(filepath):
                    tags = file_metadata_cache.get(filepath, {})
                    track_num = 999999
                    track_str = tags.get("track", "")
                    if track_str:
                        try:
                            track_num = int(str(track_str).split('/')[0])
                        except ValueError:
                            pass
                    return (track_num, natural_sort_key(os.path.basename(filepath)))
                
                # --- PHASE 2: Global Album Grouping ---
                update_status("Analyzing metadata for global grouping...")
                
                for full_path in report["discovered_files"]:
                    if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                        break
                        
                    ext = full_path.lower().split('.')[-1]
                    directory = os.path.dirname(full_path)
                    
                    if ext in ('aax', 'aaxc'):
                        global_album_groups.setdefault("AAX_NO_MERGE", []).append(full_path)
                        continue
                        
                    try:
                        data = converter.get_metadata_and_chapters(full_path)
                        tags = data.get("format", {}).get("tags", {})
                        file_metadata_cache[full_path] = tags
                        
                        album = tags.get("album")
                        if not album:
                            album = os.path.basename(directory)
                            report["missing_album_tags"].append(full_path)
                            
                    except Exception as e:
                        album = os.path.basename(directory)
                        file_metadata_cache[full_path] = {}
                        report["broken_metadata"].append(f"{full_path} | Error: {str(e)}")
                        
                    global_album_groups.setdefault(album, []).append(full_path)
                
                # --- PHASE 3: Book Queue Generation ---
                discovered = [] 
                book_counter = 0
                
                def make_sub_id(label):
                    nonlocal book_counter
                    book_counter += 1
                    safe = re.sub(r'[^A-Za-z0-9]+', '_', label)[:40]
                    return f"{task_id}__{book_counter}_{safe}"
                
                def record_book(title, kind, group_files):
                    sub_id = make_sub_id(title)
                    output_dir = os.path.dirname(group_files[0]) if group_files else folder_path
                    discovered.append({
                        'sub_task_id': sub_id, 'title': title, 'kind': kind,
                        'directory': output_dir, 'group_files': group_files,
                    })
                    if on_book_start_cb:
                        on_book_start_cb(sub_id, title)
                
                for album_name, group_files in global_album_groups.items():
                    if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                        break
                        
                    if album_name == "AAX_NO_MERGE":
                        for aax in group_files:
                            record_book(os.path.basename(aax), 'single', [aax])
                        continue
                        
                    if len(group_files) == 1:
                        kind = 'single'
                    elif import_mode == 'playlist':
                        kind = 'playlist'
                    else:
                        kind = 'merge'
                        
                    record_book(album_name, kind, group_files)
                
                if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                    update_status("Import cancelled.")
                    return
                
                total_expected = len(discovered)
                update_status(f"Importing {len(discovered)} book(s)...")
                
                for book in discovered:
                    if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                        break
                    
                    sub_task_id = book['sub_task_id']
                    album_name = book['title']
                    directory = book['directory']
                    group_files = book['group_files']
                    kind = book['kind']
                    
                    book_success = False
                    try:
                        if kind == 'playlist':
                            update_status(f"Building playlist for {album_name}...")
                            group_files.sort(key=advanced_sort_key)
                            entry, v_path = self._build_playlist_entry(
                                directory, group_files, album_name, active_profile, converter, logger
                            )
                            if v_path in self.local_library:
                                existing = self.local_library[v_path]
                                entry["progress"] = existing.get("progress", {})
                                entry["bookmarks"] = existing.get("bookmarks", [])
                                entry["last_position"] = existing.get("last_position", 0)
                                entry["last_chapter"] = existing.get("last_chapter", 0)
                                entry["date_added"] = existing.get("date_added", entry["date_added"])
                            self.local_library[v_path] = entry
                            added_count += 1
                            book_success = True
                            report["playlist_merges"].append(f"{album_name} ({len(group_files)} files)")
                        
                        elif kind == 'single':
                            for fp in group_files:
                                if self.cancel_requested or (task_id and task_id in self.canceled_tasks): break
                                if not os.path.exists(fp):
                                    report["skipped_invalid"].append(fp)
                                    continue
                                if fp in self.local_library:
                                    report["skipped_existing"].append(fp)
                                    continue
                                    
                                update_status(f"Importing: {os.path.basename(fp)}")
                                try:
                                    entry = self._process_single_file_for_import(fp, active_profile, converter, logger)
                                    self.local_library[fp] = entry
                                    added_count += 1
                                except Exception as err:
                                    if hasattr(self, 'unimportable_files'): 
                                        self.unimportable_files.add(os.path.normpath(fp))
                                    report["failed_imports"].append(f"{fp} | Error: {str(err)}")
                                    self.event_bus.publish("conversion.error", filepath=fp, action="import", error_msg=str(err))
                        
                        elif kind == 'merge':
                            safe_album = "".join(c for c in album_name if c.isalnum() or c in [' ', '-', '_']).rstrip()
                            out_m4b = os.path.join(directory, f"{safe_album}.m4b")
                            fallback_m4b = os.path.join(directory, f"{safe_album}.m4b")
                            
                            final_path = None
                            if out_m4b in group_files:
                                update_status(f"Using existing merge: {safe_album}")
                                final_path = out_m4b
                            elif fallback_m4b in group_files:
                                update_status(f"Using existing merge: {safe_album}")
                                final_path = fallback_m4b
                            else:
                                if os.path.exists(out_m4b):
                                    out_m4b = fallback_m4b
                                if not os.path.exists(out_m4b):
                                    update_status(f"Merging {len(group_files)} parts: {safe_album}...")
                                    group_files.sort(key=advanced_sort_key)
                                    per_book_prog = (lambda p, sid=sub_task_id: on_book_progress_cb(sid, p)) if on_book_progress_cb else on_progress_cb
                                    if converter.concat_to_m4b(group_files, out_m4b, title=album_name, logger=logger, progress_cb=per_book_prog):
                                        final_path = out_m4b
                                        report["m4b_merges"].append(f"{album_name} -> {out_m4b} ({len(group_files)} files)")
                                    else:
                                        suspected_orphan = os.path.join(directory, f"{safe_album}.m4b")
                                        if suspected_orphan in group_files:
                                            update_status(f"Corrupt part detected. Purging {os.path.basename(suspected_orphan)}...")
                                            try:
                                                safe_unlink(suspected_orphan, logger)
                                                group_files.remove(suspected_orphan)
                                                out_m4b = suspected_orphan
                                                if len(group_files) > 1:
                                                    update_status(f"Restarting merge: {safe_album}...")
                                                    if converter.concat_to_m4b(group_files, out_m4b, title=album_name, logger=logger, progress_cb=per_book_prog):
                                                        final_path = out_m4b
                                                        report["m4b_merges"].append(f"{album_name} -> {out_m4b} ({len(group_files)} files) [Recovered]")
                                            except Exception as e:
                                                report["failed_imports"].append(f"Merge purge failed for {suspected_orphan} | Error: {str(e)}")
                                else:
                                    final_path = out_m4b
                            
                            if final_path and os.path.exists(final_path):
                                if final_path not in self.local_library:
                                    update_status(f"Importing: {os.path.basename(final_path)}")
                                    try:
                                        entry = self._process_single_file_for_import(final_path, active_profile, converter, logger)
                                        self.local_library[final_path] = entry
                                        added_count += 1
                                        book_success = True
                                    except Exception as err:
                                        if hasattr(self, 'unimportable_files'): 
                                            self.unimportable_files.add(os.path.normpath(final_path))
                                        report["failed_imports"].append(f"{final_path} | Error: {str(err)}")
                                        self.event_bus.publish("conversion.error", filepath=final_path, action="import", error_msg=str(err))
                                else:
                                    report["skipped_existing"].append(final_path)
                                    book_success = True
                            elif final_path is None:
                                for fp in group_files:
                                    if self.cancel_requested or (task_id and task_id in self.canceled_tasks): break
                                    if not os.path.exists(fp):
                                        report["skipped_invalid"].append(fp)
                                        continue
                                    if fp in self.local_library:
                                        report["skipped_existing"].append(fp)
                                        continue
                                        
                                    try:
                                        entry = self._process_single_file_for_import(fp, active_profile, converter, logger)
                                        self.local_library[fp] = entry
                                        added_count += 1
                                    except Exception as err:
                                        if hasattr(self, 'unimportable_files'): 
                                            self.unimportable_files.add(os.path.normpath(fp))
                                        report["failed_imports"].append(f"{fp} | Fallback Error: {str(err)}")
                                        self.event_bus.publish("conversion.error", filepath=fp, action="import", error_msg=str(err))
                                book_success = added_count > 0
                    
                    except Exception as e:
                        if hasattr(self, 'unimportable_files'):
                            for bad_fp in group_files:
                                self.unimportable_files.add(os.path.normpath(bad_fp))
                        report["failed_imports"].append(f"Book grouping '{album_name}' failed entirely | Error: {str(e)}")
                        book_success = False
                        if group_files:
                            self.event_bus.publish("conversion.error", filepath=group_files[0], action="import", error_msg=f"Book processing failed: {e}")
                    finally:
                        if book_success:
                            self.db.save_local_db(self.local_library)
                        if on_book_complete_cb:
                            on_book_complete_cb(sub_task_id, book_success)
                
                if added_count > 0:
                    self.db.save_local_db(self.local_library)
                
                if self.cancel_requested or (task_id and task_id in self.canceled_tasks):
                    update_status("Import cancelled.")
                    return
                
                update_status(f"Successfully imported {added_count} book(s).")
                
            except Exception as e:
                report["failed_imports"].append(f"Fatal worker exception: {str(e)}")
                if logger: logger(f"Fatal error in import worker: {e}")
            finally:
                # --- WRITE DEDICATED IMPORT LOG ---
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                log_content = [
                    f"\n{'='*60}",
                    f"IMPORT SESSION: {timestamp}",
                    f"TARGET DIRECTORY: {folder_path}",
                    f"IMPORT MODE: {import_mode}",
                    f"{'='*60}",
                    f"TOTAL FILES DISCOVERED: {len(report['discovered_files'])}",
                    f"ENTRIES ADDED/UPDATED: {added_count}",
                    f"SKIPPED (EXISTING): {len(report['skipped_existing'])}",
                    f"SKIPPED (MISSING/INVALID): {len(report['skipped_invalid'])}",
                    f"HARD FAILURES: {len(report['failed_imports'])}",
                    f"{'-'*60}"
                ]
                
                if report['discovered_files']:
                    log_content.append("ALL DISCOVERED FILES:")
                    for f in sorted(report['discovered_files']):
                        log_content.append(f"  - {f}")
                    log_content.append(f"{'-'*60}")
                
                if report['missing_album_tags']:
                    log_content.append(f"MISSING ID3 'ALBUM' TAGS ({len(report['missing_album_tags'])} files):")
                    log_content.append("  *These files were grouped by their parent folder name.*")
                    for f in sorted(report['missing_album_tags']):
                        log_content.append(f"  - {f}")
                    log_content.append(f"{'-'*60}")

                if report['broken_metadata']:
                    log_content.append(f"BROKEN/UNREADABLE METADATA ({len(report['broken_metadata'])} files):")
                    for f in sorted(report['broken_metadata']):
                        log_content.append(f"  - {f}")
                    log_content.append(f"{'-'*60}")
                    
                if report['playlist_merges']:
                    log_content.append(f"PLAYLIST MERGES GENERATED ({len(report['playlist_merges'])} albums):")
                    for m in sorted(report['playlist_merges']):
                        log_content.append(f"  - {m}")
                    log_content.append(f"{'-'*60}")

                if report['m4b_merges']:
                    log_content.append(f"M4B MERGES COMPILED ({len(report['m4b_merges'])} albums):")
                    for m in sorted(report['m4b_merges']):
                        log_content.append(f"  - {m}")
                    log_content.append(f"{'-'*60}")

                if report['failed_imports']:
                    log_content.append(f"FAILED IMPORTS ({len(report['failed_imports'])} errors):")
                    for err in report['failed_imports']:
                        log_content.append(f"  - {err}")
                    log_content.append(f"{'-'*60}")

                try:
                    logs_dir = os.path.join(self.base_dir, "logs")
                    os.makedirs(logs_dir, exist_ok=True)
                    import_log_path = os.path.join(logs_dir, "import_history.log")
                    
                    with open(import_log_path, "a", encoding="utf-8") as log_file:
                        log_file.write("\n".join(log_content) + "\n")
                except Exception as e:
                    if logger: logger(f"Failed to write detailed import log: {e}")

                # Keep a brief summary for the main standard log
                if logger:
                    logger(f"Import finished for {os.path.basename(folder_path)} | "
                           f"Added: {added_count} | Skipped: {len(report['skipped_existing'])} | "
                           f"Errors: {len(report['failed_imports'])}. "
                           f"See logs/import_history.log for full transparent details.")
                
                if on_complete_cb:
                    on_complete_cb(added_count, total_expected)
                self.active_task_id = None
                self.current_status = ""
        
        self.import_queue.put(worker)