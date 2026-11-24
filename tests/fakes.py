from unittest.mock import MagicMock

from core.controllers.stats_manager import StatsManager
from core.events import EventBus


class FakeDBManager:
    def __init__(self):
        self.settings = {
            "active_profile": "Main",
            "paired_devices": {},
            "device_salt": "fake_salt_123",
            "last_played_Main": None,
        }
        self.stats = {}
        self.achievements = []

    def hash_device_token(self, raw_token):
        return f"hashed_{raw_token}"

    def load_settings(self):
        return self.settings

    def save_settings(self, new_settings):
        self.settings.update(new_settings)

    def save_setting(self, key, val):
        self.settings[key] = val

    # In-memory mirror of DatabaseManager's stats tables
    def increment_stat(self, name, amount):
        self.stats[name] = self.stats.get(name, 0.0) + float(amount)
        return self.stats[name]

    def get_stats(self):
        return dict(self.stats)

    def get_unlocked_achievements(self):
        return list(self.achievements)

    def unlock_achievement(self, ach_id):
        if ach_id in self.achievements:
            return False
        self.achievements.append(ach_id)
        return True

    def save_local_db(self, library_dict):
        pass


class FakeLibraryManager:
    def __init__(self):
        self.local_library = {}
        self.cloud_items = []
        self.master_metadata = {}
        self.current_status = "Idle"

    def remove_local_file(self, path):
        if path in self.local_library:
            del self.local_library[path]

    def cancel_import(self):
        pass


class FakePlaybackController:
    def __init__(self):
        self.is_playing = False
        self.is_paused = False
        self.file_path = None
        self.current_chapter_idx = 0
        self.current_play_time = 0.0
        self.chapter_duration = 0.0

    def get_current_state(self):
        return None


class FakeTomebox:
    def __init__(self):
        # FIX: Added missing path attributes for route resolution
        self.base_dir = "/fake/base"
        self.covers_dir = "/fake/covers"

        # Pre-set this to skip the disk-scanning block in /api/library
        self._web_master_metadata = {}

        self.db = FakeDBManager()
        self.settings = self.db.settings
        self.library_manager = FakeLibraryManager()
        # Real StatsManager over the in-memory stats, on its own bus so tests
        # never leak achievement events into the process-wide default bus.
        self.stats_manager = StatsManager(self.db, event_bus=EventBus())

        # Stub the api_client expected by refresh and auth routes
        self.api_client = MagicMock()
        self.api_client.is_authenticated.return_value = True

        # Required for playback sync and progress updates
        self.playback_controller = MagicMock()

        # Stub managers for download, conversion, and metadata routes
        class FakeDownloadManager:
            def __init__(self):
                self.queue = []
                self.is_processing = False
                self.web_state = {"active_asin": None, "progress": 0, "status": "Idle"}

        self.download_manager = FakeDownloadManager()
        self.conversion_manager = MagicMock()
        self.metadata_manager = MagicMock()
        self.metadata_manager.search_google_books.return_value = []
        self.converter = MagicMock()
