import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
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
        self.conn.execute("PRAGMA journal_mode=WAL")  # Faster, safer concurrent writes
        self.conn.execute("PRAGMA synchronous=FULL")
        
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
            # Stats live in their own tables, NOT in the settings blob: the
            # settings table is rewritten wholesale from long-lived in-memory
            # copies, which silently rolled stats back to their startup values.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    name TEXT PRIMARY KEY,
                    value REAL NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    id TEXT PRIMARY KEY,
                    unlocked_at REAL NOT NULL
                )
            """)
            self._migrate_stats_out_of_settings(cursor)
            self.conn.commit()

    def _migrate_stats_out_of_settings(self, cursor):
        """One-time move of the legacy settings['stats'] blob into the stats tables.

        Rules (explicit, no merging):
          - If the stats and achievements tables are both empty, copy the
            legacy numeric stats and unlocked achievement ids across.
          - If either table already has rows, they are the source of truth and
            the legacy blob is discarded.
          - In both cases the legacy 'stats' settings row is deleted.
        Caller holds db_lock and commits.
        """
        cursor.execute("SELECT value FROM settings WHERE key='stats'")
        row = cursor.fetchone()
        if row is None:
            return

        cursor.execute("SELECT COUNT(*) FROM stats")
        stats_rows = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM achievements")
        ach_rows = cursor.fetchone()[0]

        if stats_rows == 0 and ach_rows == 0:
            try:
                legacy = json.loads(row[0])
            except (TypeError, ValueError):
                legacy = {}
            if not isinstance(legacy, dict):
                legacy = {}

            for name, value in legacy.items():
                if name == "unlocked_achievements":
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                cursor.execute(
                    "INSERT INTO stats (name, value) VALUES (?, ?)", (name, float(value))
                )

            unlocked = legacy.get("unlocked_achievements", [])
            if isinstance(unlocked, list):
                migrated_at = time.time()
                for ach_id in unlocked:
                    cursor.execute(
                        "INSERT OR IGNORE INTO achievements (id, unlocked_at) VALUES (?, ?)",
                        (str(ach_id), migrated_at),
                    )

        cursor.execute("DELETE FROM settings WHERE key='stats'")

    # --- Stats -----------------------------------------------------------------

    def increment_stat(self, name, amount):
        """Atomically adds amount to a stat and returns the new total."""
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO stats (name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value = value + excluded.value
                """,
                (name, float(amount)),
            )
            cursor.execute("SELECT value FROM stats WHERE name = ?", (name,))
            total = cursor.fetchone()[0]
            self.conn.commit()
            return total

    def get_stats(self):
        """Returns {stat_name: float_value} for every recorded stat."""
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name, value FROM stats")
            return {name: value for name, value in cursor.fetchall()}

    def get_unlocked_achievements(self):
        """Returns unlocked achievement ids, oldest unlock first."""
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM achievements ORDER BY unlocked_at, id")
            return [row[0] for row in cursor.fetchall()]

    def unlock_achievement(self, ach_id):
        """Records an unlock. Returns True only if it was not already unlocked."""
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO achievements (id, unlocked_at) VALUES (?, ?)",
                (ach_id, time.time()),
            )
            inserted = cursor.rowcount == 1
            self.conn.commit()
            return inserted

    def load_settings(self):
        settings_dict = {}
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            for key, value in cursor.fetchall():
                try:
                    settings_dict[key] = json.loads(value)
                except Exception:
                    settings_dict[key] = value

            db_changed = False

            # Legacy token (kept temporarily for backwards compatibility)
            if "auth_token" not in settings_dict:
                new_token = str(uuid.uuid4())
                settings_dict["auth_token"] = new_token
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("auth_token", json.dumps(new_token)),
                )
                db_changed = True

            # NEW: Master salt for hashing device tokens
            if "device_salt" not in settings_dict:
                new_salt = secrets.token_hex(32)
                settings_dict["device_salt"] = new_salt
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("device_salt", json.dumps(new_salt)),
                )
                db_changed = True

            # NEW: Schema for tracking individual devices
            if "paired_devices" not in settings_dict:
                settings_dict["paired_devices"] = {}
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("paired_devices", json.dumps({})),
                )
                db_changed = True

            if db_changed:
                self.conn.commit()

        return settings_dict

    def save_settings(self, settings_dict):
        with self.db_lock:
            cursor = self.conn.cursor()
            for key, val in settings_dict.items():
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(val)),
                )
            self.conn.commit()

    def save_setting(self, key, val):
        """Writes exactly one settings key. Use this instead of save_settings()
        when only one value changed, so stale keys in a long-lived settings
        dict are not written back over newer values."""
        with self.db_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(val)),
            )
            self.conn.commit()

    def hash_device_token(self, raw_token):
        """Securely hashes a device token using the master salt."""
        # Grab the salt from settings (avoiding a recursive lock if possible)
        salt = ""
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key='device_salt'")
            row = cursor.fetchone()
            if row:
                try:
                    salt = json.loads(row[0])
                except Exception:
                    salt = row[0]

        return hashlib.sha256(f"{salt}{raw_token}".encode()).hexdigest()

    def load_local_db(self):
        library = {}
        with self.db_lock:
            if not os.path.exists(self.db_path):
                return {}

            cursor = self.conn.cursor()
            cursor.execute("SELECT path, data FROM library")
            for path, data_str in cursor.fetchall():
                try:
                    data = json.loads(data_str)

                    if data.get("is_playlist", False):
                        # For playlists, check if the first actual audio file exists
                        chapters = data.get("chapters", [])
                        if chapters and os.path.exists(
                            chapters[0].get("file_path", "")
                        ):
                            library[path] = data
                    elif os.path.exists(path):
                        # Standard single-file check
                        library[path] = data

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
                cursor.execute(
                    "INSERT OR REPLACE INTO library (path, data) VALUES (?, ?)",
                    (path, json.dumps(data)),
                )

            self.conn.commit()

            if os.path.exists(self.db_path):
                self.last_db_mtime = os.path.getmtime(self.db_path)

    # Leave auth and cloud files as JSON so the Audible API and Web Server don't break
    def get_auth_path(self, profile_name):
        return os.path.join(self.data_dir, f"auth_{profile_name}.json")

    def get_cloud_cache_path(self, profile_name):
        return os.path.join(self.data_dir, f"cloud_{profile_name}.json")
