import threading

from core.events import default_bus
# Every stat TomeBox records, with the type it is reported as. add_stat()
# rejects names not listed here, so a typo can't silently create a new stat.
STAT_TYPES = {
    "seconds_listened": float,
    "books_finished": int,
    "books_downloaded": int,
}

# -- The Achievements are intentionally flavoured for style and personality of the application --
ACHIEVEMENTS = {
    "first_dl": {
        "title": "System Integration Complete",
        "desc": "Download your first audiobook.",
        "type": "books_downloaded",
        "threshold": 1,
    },
    "hoarder_1": {
        "title": "Spatial Expansion",
        "desc": "Download 10 audiobooks.",
        "type": "books_downloaded",
        "threshold": 10,
    },
    "first_finish": {
        "title": "Core Consumed",
        "desc": "Finish an audiobook.",
        "type": "books_finished",
        "threshold": 1,
    },
    "finish_5": {
        "title": "Path Advancement",
        "desc": "Finish 5 audiobooks.",
        "type": "books_finished",
        "threshold": 5,
    },
    "listen_10h": {
        "title": "Mana Cultivator",
        "desc": "Listen for 10 total hours.",
        "type": "seconds_listened",
        "threshold": 36000,
    },
    "listen_50h": {
        "title": "Dao of the Tome",
        "desc": "Listen for 50 total hours.",
        "type": "seconds_listened",
        "threshold": 180000,
    },
}


class StatsManager:
    def __init__(self, db_manager, callbacks=None, event_bus=None):
        self.db = db_manager
        self.stats_lock = threading.Lock()
        self.event_bus = event_bus or default_bus

        # Backwards compatibility: Map legacy callback to the event bus
        callbacks = callbacks or {}
        self.on_achievement = callbacks.get("on_achievement")
        if self.on_achievement:
            self.event_bus.subscribe(
                "stats.achievement_unlocked",
                lambda **kw: self.on_achievement(
                    kw.get("achievement", {}).get("title", "Achievement"),
                    kw.get("achievement", {}).get("desc", "Unlocked!"),
                ),
            )

        self.achievements = ACHIEVEMENTS

    def add_stat(self, stat_name, amount=1):
        """Adds amount to a known stat, then checks achievements.

        Returns the list of achievements newly unlocked by this call, each as
        {"id", "title", "desc"} (empty if none). Raises ValueError for an
        unknown stat name or a negative amount.
        """
        if stat_name not in STAT_TYPES:
            raise ValueError(f"Unknown stat: {stat_name!r}")
        if amount < 0:
            raise ValueError(f"Stat increments must be >= 0, got {amount!r}")

        with self.stats_lock:
            total = self.db.increment_stat(stat_name, amount)

            # Announce stat change for any interested UI elements (like a profile dashboard)
            self.event_bus.publish(
                "stats.updated", stat_name=stat_name, total=STAT_TYPES[stat_name](total)
            )

            return self._check_achievements_locked()

    def check_achievements(self):
        """Unlocks any achievement whose threshold is met. Returns the newly unlocked list."""
        with self.stats_lock:
            return self._check_achievements_locked()

    def _check_achievements_locked(self):
        stats = self.db.get_stats()
        already = set(self.db.get_unlocked_achievements())
        newly_unlocked = []

        for ach_id, data in self.achievements.items():
            if ach_id in already:
                continue
            if stats.get(data["type"], 0) < data["threshold"]:
                continue
            # unlock_achievement is INSERT OR IGNORE: only the caller that
            # actually inserts the row announces it, so no duplicate toasts.
            if self.db.unlock_achievement(ach_id):
                newly_unlocked.append(
                    {"id": ach_id, "title": data["title"], "desc": data["desc"]}
                )
                self.event_bus.publish("stats.achievement_unlocked", achievement=data)

        return newly_unlocked

    def get_snapshot(self):
        """Current stats and unlocks, typed per STAT_TYPES. Every known stat is
        present (0 if never recorded)."""
        raw = self.db.get_stats()
        return {
            "stats": {name: cast(raw.get(name, 0)) for name, cast in STAT_TYPES.items()},
            "unlocked_achievements": self.db.get_unlocked_achievements(),
        }
