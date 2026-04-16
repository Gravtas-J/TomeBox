import os
import json
import threading

class DatabaseManager:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.local_db_path = os.path.join(self.data_dir, "library.json")
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self.db_lock = threading.Lock()
        self.last_db_mtime = 0

    def load_settings(self):
        with self.db_lock:
            if os.path.exists(self.settings_path):
                try:
                    with open(self.settings_path, "r") as f:
                        return json.load(f)
                except Exception: pass
            return {}

    def save_settings(self, settings_dict):
        with self.db_lock:
            with open(self.settings_path, "w") as f:
                json.dump(settings_dict, f, indent=4)

    def load_local_db(self):
        with self.db_lock:
            if os.path.exists(self.local_db_path):
                try:
                    with open(self.local_db_path, "r") as f:
                        raw_db = json.load(f)
                    return {path: data for path, data in raw_db.items() if os.path.exists(path)}
                except Exception: pass
            return {}

    def save_local_db(self, library_dict):
        with self.db_lock:
            with open(self.local_db_path, "w") as f:
                json.dump(library_dict, f, indent=4)
            if os.path.exists(self.local_db_path):
                self.last_db_mtime = os.path.getmtime(self.local_db_path)

    # --- New Methods to Centralize Profile Data ---
    def get_auth_path(self, profile_name):
        return os.path.join(self.data_dir, f"auth_{profile_name}.json")

    def get_cloud_cache_path(self, profile_name):
        return os.path.join(self.data_dir, f"cloud_{profile_name}.json")