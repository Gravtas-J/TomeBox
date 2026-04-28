import os
import json
import threading
import requests
import traceback
try:
    import audible
except ImportError:
    pass

import re

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
from core.utils.process_runner import ProcessRunner

def _normalize_title(title):
    """Strips common boilerplate and normalises punctuation for comparison."""
    if not title:
        return ""
    
    t = title.lower()
    
    # Normalise fancy quotes to ASCII
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    
    # Strip Audible-style suffixes
    suffixes = [
        " (unabridged)",
        " (abridged)",
        " (audible audio edition)",
        " (audiobook)",
        ": a novel",
    ]
    for suffix in suffixes:
        if t.endswith(suffix):
            t = t[:-len(suffix)]
    
    # Strip "Book N" / "Volume N" / "Vol. N" series markers
    t = re.sub(r",?\s*(book|volume|vol\.?|part)\s+\d+\s*$", "", t)
    
    # Remove all punctuation
    t = re.sub(r"[^\w\s]", " ", t)
    
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    
    return t


def _find_matching_cloud_item(title, cloud_items, threshold=85):
    """Returns the best-matching cloud item for a given local title, or None."""
    if not title or not cloud_items:
        return None
    
    target = _normalize_title(title)
    if not target:
        return None
    
    if RAPIDFUZZ_AVAILABLE:
        best_match = None
        best_score = 0
        
        for item in cloud_items:
            cloud_title = _normalize_title(item.get("title", ""))
            if not cloud_title:
                continue
            
            # Standard fuzzy match on the full title
            score = fuzz.token_set_ratio(target, cloud_title)
            
            # Series-aware second pass: if the file's title appears to contain 
            # the series name as a prefix, strip it and try again
            raw_series = item.get("series", []) or []
            for series_entry in raw_series:
                if not isinstance(series_entry, dict):
                    continue
                series_name = _normalize_title(series_entry.get("title", ""))
                if not series_name or len(series_name) < 4:
                    continue
                
                # If the target starts with the series name, strip it and rematch
                if target.startswith(series_name):
                    stripped_target = target[len(series_name):].strip()
                    if stripped_target:
                        series_score = fuzz.token_set_ratio(stripped_target, cloud_title)
                        score = max(score, series_score)
            
            if score > best_score:
                best_score = score
                best_match = item
        
        return best_match if best_score >= threshold else None
    
    # Fallback: exact match only
    for item in cloud_items:
        if _normalize_title(item.get("title", "")) == target:
            return item
    return None
class LibraryManager:
    def __init__(self, db_manager, api_client, base_dir):
        self.db = db_manager
        self.api = api_client
        self.base_dir = base_dir
        self.covers_dir = os.path.join(base_dir, "covers")
        
        # Core State
        self.local_library = {}
        self.cloud_items = []
        self.master_metadata = {}
        
        # Load initial state
        self.active_profile = self.db.load_settings().get("active_profile", "Main")
        self.cloud_cache_path = self.db.get_cloud_cache_path(self.active_profile)
        self.load_state()

    def load_state(self):
        """Bootstraps the library from the database and disk caches."""
        self.local_library = self.db.load_local_db()
        
        if os.path.exists(self.cloud_cache_path):
            try:
                with open(self.cloud_cache_path, "r") as f:
                    self.cloud_items = json.load(f)
            except Exception:
                self.cloud_items = []
                
        self._build_master_metadata()

    def _build_master_metadata(self):
        """Creates a fast lookup dictionary for all known book metadata."""
        self.master_metadata = {}
        
        # Load from all cloud caches
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

        # Override with current active profile's items
        for item in self.cloud_items:
            if item.get("title"): self.master_metadata[item["title"]] = item
            if item.get("asin"): self.master_metadata[item["asin"]] = item

    def get_view_data(self, search_query="", filter_type="All", shelf_filter="All Shelves"):
        rows = []
        all_unique_shelves = set()
        settings = self.db.load_settings()
        shelves_db = settings.get("shelves_db", {})
        
        cloud_titles = set()
        search_query = search_query.lower()

        # --- THE FIX: Create a reverse lookup keyed by Title instead of Path ---
        local_titles = {data["title"]: data for path, data in self.local_library.items()}

        # 1. Process Cloud Items
        for item in self.cloud_items:
            title = item.get("title", "Unknown")
            cloud_titles.add(title)
            
            raw_authors = item.get("authors") or []
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            raw_series = item.get("series") or []
            series_list = [f"{s.get('title')} (Bk {s.get('sequence', '')})" for s in raw_series if isinstance(s, dict) and s.get("title")]
            series_str = ", ".join(series_list)
            
            duration_min = item.get("runtime_length_min") or 0
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            asin = item.get("asin", "Unknown")
            
            # --- THE FIX: Look up against the new title dictionary ---
            local_data = local_titles.get(title) 
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            local_path = local_data['path'] if local_data else ""

            rows.append((title, authors, series_str, duration_str, asin, status, local_path))
            all_unique_shelves.update(shelves_db.get(asin, []))

        # 2. Process Local-Only Items
        for path, data in self.local_library.items():
            title = data.get("title", "Unknown")
            if title not in cloud_titles:
                asin = data.get("asin", "Unknown")
                meta = self.master_metadata.get(title) or self.master_metadata.get(asin, {})

                loc_authors = data.get("authors", "Local File")
                if meta.get("authors") and loc_authors in ["Unknown", "Unknown Author", "Local File"]:
                    loc_authors = ", ".join([a.get("name", "") for a in meta.get("authors") if isinstance(a, dict)])

                loc_series = data.get("series", "N/A")
                if meta.get("series") and loc_series == "N/A":
                    loc_series = ", ".join([f"{s.get('title')} (Bk {s.get('sequence', '')})" for s in meta.get("series") if isinstance(s, dict) and s.get("title")])

                duration_min = meta.get("runtime_length_min") or data.get("duration_min") or 0
                loc_duration = f"{duration_min//60}h {duration_min%60}m" if duration_min > 0 else "N/A"

                if asin == "Unknown" and meta.get("asin"):
                    asin = meta.get("asin")

                rows.append((title, loc_authors, loc_series, loc_duration, asin, f"Downloaded ({data.get('format', 'UNKNOWN')})", path))
                all_unique_shelves.update(shelves_db.get(asin, []))

        # 3. Apply Filters
        filtered_rows = []
        for row in rows:
            title, authors, series_str, duration_str, asin, status, row_path = row

            if filter_type == "Downloaded" and "Downloaded" not in status: continue
            if filter_type == "Cloud Only" and status != "Cloud Only": continue
            if shelf_filter != "All Shelves" and shelf_filter not in shelves_db.get(asin, []): continue
            
            if search_query and search_query not in f"{title} {authors} {series_str}".lower():
                continue

            filtered_rows.append(row)

        shelf_list = ["All Shelves"] + sorted(list(all_unique_shelves))
        return filtered_rows, shelf_list

    def fetch_cloud_library(self):
        """Fetches the latest library from Audible. Returns True on success."""
        if not self.api.auth:
            raise Exception("Not authenticated")
            
        client = audible.Client(auth=self.api.auth)
        response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors,media", num_results=1000)
        
        self.cloud_items = response.get("items", [])
        self._build_master_metadata()
        
        # Save to disk
        try:
            with open(self.cloud_cache_path, "w") as f:
                json.dump(self.cloud_items, f, indent=4)
        except Exception as e:
            print(f"Cache save error: {e}")
            
        return True

    def add_local_file(self, filepath, metadata):
        self.local_library[filepath] = metadata
        self.db.save_local_db(self.local_library)

    def remove_local_file(self, filepath):
        if filepath in self.local_library:
            del self.local_library[filepath]
            self.db.save_local_db(self.local_library)
            
    def set_shelves(self, asin, tags_list):
        settings = self.db.load_settings()
        if "shelves_db" not in settings:
            settings["shelves_db"] = {}
            
        settings["shelves_db"][asin] = tags_list
        self.db.save_settings(settings)

    def import_files(self, file_paths, converter, active_profile, on_status_cb, on_complete_cb, logger=None):
        """Processes an array of files, extracts metadata, and adds them to the library database."""
        def worker():
            valid_exts = [".aax", ".aaxc", ".m4b", ".mp3"]
            added_count = 0
            
            for filepath in file_paths:
                if not os.path.exists(filepath): continue
                
                # Prevent re-adding files that are already in the database
                if filepath in self.local_library: continue
                
                ext = os.path.splitext(filepath)[1].lower()
                if ext not in valid_exts: continue
                
                filename = os.path.basename(filepath)
                title = filename
                authors = "Unknown Author"
                format_clean = ext.replace(".", "").upper()
                embedded_meta = {}
                
                if on_status_cb:
                    on_status_cb(f"Importing: {filename}")
                
                # Scrape tags if it's an M4B or MP3
                if format_clean in ["M4B", "MP3"]:
                    try:
                        data = converter.get_metadata_and_chapters(filepath)
                        tags = data.get("format", {}).get("tags", {})

                        if "title" in tags: title = tags["title"]
                        if "artist" in tags: authors = tags["artist"]
                        elif "album_artist" in tags: authors = tags["album_artist"]
                        
                        # Grab extended metadata just like the folder importer
                        embedded_meta = {
                            "album": tags.get("album", ""),
                            "year": tags.get("date", "") or tags.get("year", ""),
                            "comment": tags.get("comment", ""),
                            "narrator": tags.get("composer", ""),
                            "duration_min": int(float(data.get("format", {}).get("duration", 0)) / 60)
                        }
                        
                        if "series" in tags:
                            embedded_meta["series"] = tags["series"]
                        elif "show" in tags:
                            embedded_meta["series"] = tags["show"]
                        
                        for stream in data.get("streams", []):
                            if stream.get("codec_type") == "video" or stream.get("disposition", {}).get("attached_pic") == 1:
                                embedded_meta["has_embedded_cover"] = True
                                break
                    except Exception as e:
                        if logger: logger(f"Failed to read tags for {filename}: {e}")

                # Try to match against cloud library to avoid duplicates
                matched_cloud_item = _find_matching_cloud_item(title, self.cloud_items)

                entry = {
                    "title": title,
                    "format": format_clean,
                    "path": filepath,
                    "authors": authors,
                    "owner": active_profile,
                    "duration_min": embedded_meta.get("duration_min", 0),
                }

                if embedded_meta.get("series"):
                    entry["series"] = embedded_meta["series"]
                if embedded_meta.get("narrator"):
                    entry["narrator"] = embedded_meta["narrator"]
                if embedded_meta.get("year"):
                    entry["year"] = embedded_meta["year"]

                if matched_cloud_item:
                    # Use the cloud item's title and ASIN so the library view dedupes correctly
                    entry["title"] = matched_cloud_item.get("title", title)
                    entry["asin"] = matched_cloud_item.get("asin", "")
                    
                    # Pull richer authors from cloud if available
                    raw_authors = matched_cloud_item.get("authors", [])
                    if raw_authors:
                        entry["authors"] = ", ".join([
                            a.get("name", "") for a in raw_authors if isinstance(a, dict)
                        ])
                    
                    if logger:
                        logger(f"Matched '{title}' to cloud library: {entry['title']} ({entry['asin']})")
                        
                else:
                    # Always attempt to extract a cover for unmatched local files
                    import hashlib
                    from core.utils.process_runner import ProcessRunner
                    
                    fake_asin = "LOCAL_" + hashlib.md5(filepath.encode()).hexdigest()[:10]
                    cover_output = os.path.join(self.covers_dir, f"{fake_asin}.jpg")
                    
                    extraction_succeeded = os.path.exists(cover_output) and os.path.getsize(cover_output) > 0
                    
                    if not extraction_succeeded:
                        try:
                            # Use -vframes 1 instead of -vcodec copy so PNGs are safely converted to JPGs
                            extract_cmd = [
                                "ffmpeg", "-y", "-i", filepath,
                                "-an", "-vframes", "1", cover_output
                            ]
                            result = ProcessRunner.run_blocking(extract_cmd, capture_output=True)
                            
                            if result.returncode == 0 and os.path.exists(cover_output) and os.path.getsize(cover_output) > 0:
                                extraction_succeeded = True
                                if logger: logger(f"Extracted embedded cover for {title}")
                        except Exception as e:
                            if logger: logger(f"Cover extraction failed for {title}: {e}")
                    
                    if extraction_succeeded:
                        entry["asin"] = fake_asin

                self.local_library[filepath] = entry
                added_count += 1
                
            # Save and ping the UI
            if added_count > 0:
                self.db.save_local_db(self.local_library)
                
            if on_complete_cb:
                on_complete_cb(added_count)

        # Run it in the background so a 50-file drag-and-drop doesn't freeze the app
        threading.Thread(target=worker, daemon=True).start()

    def save_playback_state(self, state_dict, active_profile):
        """Writes playback progress to the local database."""
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
    
    def silent_cloud_sync(self, logger, on_status_cb, on_refresh_cb):
        """Background thread to poll Audible for new purchases silently."""
        if not self.api.auth:
            return

        try:
            logger.info("Background sync: Polling Audible API...")
            client = audible.Client(auth=self.api.auth)
            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors", num_results=1000)
            new_items = response.get("items", [])
            
            if on_status_cb:
                on_status_cb("Library Synced (Online)")
            
            if len(new_items) != len(self.cloud_items):
                logger.info(f"Background sync: Detected library change. Old: {len(self.cloud_items)}, New: {len(new_items)}")
                self.cloud_items = new_items
                self._build_master_metadata()
                
                # Save the new cache
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
            elif "50" in err_str: # 500, 502, 503, etc.
                if on_status_cb: on_status_cb("Audible Servers Down")
            elif "connect" in err_str or "timeout" in err_str:
                if on_status_cb: on_status_cb("Offline - Check Connection")

    def monitor_local_files(self, logger, on_refresh_cb):
        """Infinite loop that watches for deleted audio files and external DB writes."""
        import time
        import os
        
        while True:
            ui_needs_refresh = False
            
            # 1. Check if any actual audio files were deleted from the hard drive
            missing_paths = [path for path in list(self.local_library.keys()) if not os.path.exists(path)]
            
            if missing_paths:
                for path in missing_paths:
                    del self.local_library[path]
                    
                logger.info(f"Detected {len(missing_paths)} deleted files. Updating library...")
                self.db.save_local_db(self.local_library)
                ui_needs_refresh = True

            # 2. Check if the SQLite database file was edited externally (e.g. by Web App)
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
                
            time.sleep(2)

    def import_folder(self, folder_path, converter, active_profile, on_status_cb, on_complete_cb, logger=None):
        def worker():
            import re
            
            if not os.path.isdir(folder_path):
                if on_complete_cb: on_complete_cb(0)
                return
            
            if on_status_cb: on_status_cb("Scanning and grouping files...")
            
            # Helper for natural sorting (1, 2, 10 instead of 1, 10, 2)
            def natural_sort_key(s):
                return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

            dir_to_files = {}
            valid_exts = (".aax", ".aaxc", ".m4b", ".mp3")
            
            # 1. Group all valid audio files by their parent directory
            for root_dir, dirs, files in os.walk(folder_path):
                audio_files = [f for f in files if f.lower().endswith(valid_exts)]
                if audio_files:
                    dir_to_files[root_dir] = audio_files

            file_paths = []
            
            # 2. Process groups and catch MP3/M4B blobs using Album metadata
            for directory, files in dir_to_files.items():
                if len(files) == 1:
                    file_paths.extend([os.path.join(directory, files[0])])
                    continue
                
                if on_status_cb: on_status_cb(f"Analyzing parts in {os.path.basename(directory)}...")
                
                album_groups = {}
                for f in files:
                    full_path = os.path.join(directory, f)
                    ext = f.lower().split('.')[-1]
                    
                    # Never merge AAX files
                    if ext in ['aax', 'aaxc']:
                        album_groups.setdefault("AAX_NO_MERGE", []).append(full_path)
                        continue
                        
                    try:
                        # Probe the file for its album tag
                        data = converter.get_metadata_and_chapters(full_path)
                        tags = data.get("format", {}).get("tags", {})
                        album = tags.get("album", os.path.basename(directory))
                    except Exception:
                        album = os.path.basename(directory)
                        
                    album_groups.setdefault(album, []).append(full_path)
                    
                # Process each identified album group
                for album_name, group_files in album_groups.items():
                    if album_name == "AAX_NO_MERGE" or len(group_files) == 1:
                        file_paths.extend(group_files)
                    else:
                        safe_album_name = "".join([c for c in album_name if c.isalnum() or c in [' ', '-', '_']]).rstrip()
                        out_m4b = os.path.join(directory, f"{safe_album_name}.m4b")
                        
                        # Prevent naming collision if a file is already named exactly AlbumName.m4b
                        if os.path.exists(out_m4b) and out_m4b in group_files:
                            out_m4b = os.path.join(directory, f"{safe_album_name}_merged.m4b")
                            
                        if not os.path.exists(out_m4b):
                            if on_status_cb: on_status_cb(f"Merging {len(group_files)} parts: {safe_album_name}...")
                            
                            group_files.sort(key=natural_sort_key)
                            success = converter.concat_to_m4b(group_files, out_m4b, title=album_name, logger=logger)
                            
                            if success:
                                file_paths.append(out_m4b)
                            else:
                                file_paths.extend(group_files)
                        else:
                            file_paths.append(out_m4b)

            if not file_paths:
                if logger: logger(f"No audio files found in {folder_path}")
                if on_complete_cb: on_complete_cb(0)
                return
            final_count = len(file_paths)
            if on_status_cb: on_status_cb(f"Found {len(file_paths)} formatted books. Importing...")

            valid_exts = [".aax", ".aaxc", ".m4b", ".mp3"]
            added_count = 0
            
            for filepath in file_paths:
                if not os.path.exists(filepath):
                    continue
                
                # Skip files already in library
                if filepath in self.local_library:
                    continue
                
                ext = os.path.splitext(filepath)[1].lower()
                if ext not in valid_exts:
                    continue
                
                filename = os.path.basename(filepath)
                title = filename
                authors = "Unknown Author"
                format_clean = ext.replace(".", "").upper()
                embedded_meta = {}

                if on_status_cb:
                    on_status_cb(f"Importing: {filename}")

                # Scrape tags if it's an M4B or MP3
                if format_clean in ["M4B", "MP3"]:
                    try:
                        data = converter.get_metadata_and_chapters(filepath)
                        tags = data.get("format", {}).get("tags", {})
                        
                        if "title" in tags: title = tags["title"]
                        if "artist" in tags: authors = tags["artist"]
                        elif "album_artist" in tags: authors = tags["album_artist"]
                        
                        embedded_meta = {
                            "album": tags.get("album", ""),
                            "year": tags.get("date", "") or tags.get("year", ""),
                            "comment": tags.get("comment", ""),
                            "narrator": tags.get("composer", ""),
                            "duration_min": int(float(data.get("format", {}).get("duration", 0)) / 60)
                        }
                        
                        if "series" in tags:
                            embedded_meta["series"] = tags["series"]
                        elif "show" in tags:
                            embedded_meta["series"] = tags["show"]
                        
                        for stream in data.get("streams", []):
                            if stream.get("codec_type") == "video" or stream.get("disposition", {}).get("attached_pic") == 1:
                                embedded_meta["has_embedded_cover"] = True
                                break
                                
                    except Exception as e:
                        if logger: logger(f"Failed to read tags for {filename}: {e}")

                matched_cloud_item = _find_matching_cloud_item(title, self.cloud_items)

                entry = {
                    "title": title,
                    "format": format_clean,
                    "path": filepath,
                    "authors": authors,
                    "owner": active_profile,
                    "duration_min": embedded_meta.get("duration_min", 0),
                }

                if embedded_meta.get("series"):
                    entry["series"] = embedded_meta["series"]
                if embedded_meta.get("narrator"):
                    entry["narrator"] = embedded_meta["narrator"]
                if embedded_meta.get("year"):
                    entry["year"] = embedded_meta["year"]

                if matched_cloud_item:
                    # Use the cloud item's title and ASIN so the library view dedupes correctly
                    entry["title"] = matched_cloud_item.get("title", title)
                    entry["asin"] = matched_cloud_item.get("asin", "")
                    
                    # Pull richer authors from cloud if available
                    raw_authors = matched_cloud_item.get("authors", [])
                    if raw_authors:
                        entry["authors"] = ", ".join([
                            a.get("name", "") for a in raw_authors if isinstance(a, dict)
                        ])
                    
                    if logger:
                        logger(f"Matched '{title}' to cloud library: {entry['title']} ({entry['asin']})")
                        
                else:
                    # Always attempt to extract a cover for unmatched local files
                    import hashlib
                    from core.utils.process_runner import ProcessRunner
                    fake_asin = "LOCAL_" + hashlib.md5(filepath.encode()).hexdigest()[:10]
                    cover_output = os.path.join(self.covers_dir, f"{fake_asin}.jpg")
                    
                    extraction_succeeded = os.path.exists(cover_output) and os.path.getsize(cover_output) > 0
                    
                    if not extraction_succeeded:
                        try:
                            # Use -vframes 1 instead of -vcodec copy so PNGs are safely converted to JPGs
                            extract_cmd = [
                                "ffmpeg", "-y", "-i", filepath,
                                "-an", "-vframes", "1", cover_output
                            ]
                            result = ProcessRunner.run_blocking(extract_cmd, capture_output=True)
                            
                            if result.returncode == 0 and os.path.exists(cover_output) and os.path.getsize(cover_output) > 0:
                                extraction_succeeded = True
                                if logger: logger(f"Extracted embedded cover for {title}")
                        except Exception as e:
                            if logger: logger(f"Cover extraction failed for {title}: {e}")
                    
                    if extraction_succeeded:
                        entry["asin"] = fake_asin

                self.local_library[filepath] = entry
                added_count += 1
            
            if added_count > 0:
                self.db.save_local_db(self.local_library)
            
            # Fire the complete callback exactly once, at the very end
            if on_complete_cb:
                on_complete_cb(added_count)
        
        threading.Thread(target=worker, daemon=True).start()