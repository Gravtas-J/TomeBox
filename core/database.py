import os
import json
import sqlite3
import threading
import uuid

class DatabaseManager:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.db_path = os.path.join(self.data_dir, "tomebox.db")
        self.db_lock = threading.Lock()
        self.last_db_mtime = 0
        
        # Establish a single, persistent, thread-safe connection
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL") # Faster, safer concurrent writes
        
        self._initialize_tables()

    def _initialize_tables(self):
        with self.db_lock:
            cursor = self.conn.cursor()
            # Document-store approach: perfectly preserves existing structures
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS library (
                    path TEXT PRIMARY KEY,
                    data TEXT
                )
            """)
            self.conn.commit()

    def load_settings(self):
        settings_dict = {}
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            for key, value in cursor.fetchall():
                try:
                    settings_dict[key] = json.loads(value)
                except:
                    settings_dict[key] = value
            if "auth_token" not in settings_dict:
                new_token = str(uuid.uuid4())
                settings_dict["auth_token"] = new_token
                # Save immediately so it persists in the database
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", 
                    ("auth_token", json.dumps(new_token))
                )
                self.conn.commit()
        return settings_dict

    def save_settings(self, settings_dict):
        with self.db_lock:
            cursor = self.conn.cursor()
            for key, val in settings_dict.items():
                cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(val)))
            self.conn.commit()

    def load_local_db(self):
        library = {}
        with self.db_lock:
            if not os.path.exists(self.db_path):
                return {}
                
            cursor = self.conn.cursor()
            cursor.execute("SELECT path, data FROM library")
            for path, data_str in cursor.fetchall():
                if os.path.exists(path):  # Clean up missing files on load
                    try:
                        library[path] = json.loads(data_str)
                    except Exception:
                        pass
        return library

    def save_local_db(self, library_dict):
        with self.db_lock:
            cursor = self.conn.cursor()
            
            # 1. Get current paths in DB to handle deletions
            cursor.execute("SELECT path FROM library")
            existing_paths = {row[0] for row in cursor.fetchall()}
            
            # 2. Delete items that are no longer in the dictionary
            current_paths = set(library_dict.keys())
            paths_to_delete = existing_paths - current_paths
            for path in paths_to_delete:
                cursor.execute("DELETE FROM library WHERE path = ?", (path,))

            # 3. Insert or update current items
            for path, data in library_dict.items():
                cursor.execute("INSERT OR REPLACE INTO library (path, data) VALUES (?, ?)", (path, json.dumps(data)))
                
            self.conn.commit()
            
            if os.path.exists(self.db_path):
                self.last_db_mtime = os.path.getmtime(self.db_path)

    # Leave auth and cloud files as JSON so the Audible API and Web Server don't break
    def get_auth_path(self, profile_name):
        return os.path.join(self.data_dir, f"auth_{profile_name}.json")

    def get_cloud_cache_path(self, profile_name):
        return os.path.join(self.data_dir, f"cloud_{profile_name}.json")