import os
import json
import threading
import requests
import traceback
try:
    import audible
except ImportError:
    pass

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
            
            duration_min = item.get("runtime_length_min", 0)
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            asin = item.get("asin", "Unknown")
            
            # --- THE FIX: Look up against the new title dictionary ---
            local_data = local_titles.get(title) 
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            
            rows.append((title, authors, series_str, duration_str, asin, status))
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

                duration_min = meta.get("runtime_length_min") or data.get("duration_min", 0)
                loc_duration = f"{duration_min//60}h {duration_min%60}m" if duration_min > 0 else "N/A"

                if asin == "Unknown" and meta.get("asin"):
                    asin = meta.get("asin")

                rows.append((title, loc_authors, loc_series, loc_duration, asin, f"Downloaded ({data.get('format', 'UNKNOWN')})"))
                all_unique_shelves.update(shelves_db.get(asin, []))

        # 3. Apply Filters
        filtered_rows = []
        for row in rows:
            title, authors, series_str, duration_str, asin, status = row

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