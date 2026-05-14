import threading
from core.events import default_bus

# -- The Achievements are intentionally falvoured for style and personality of the application --
ACHIEVEMENTS = {
    "first_dl": {"title": "System Integration Complete", "desc": "Download your first audiobook.", "type": "books_downloaded", "threshold": 1},
    "hoarder_1": {"title": "Spatial Expansion", "desc": "Download 10 audiobooks.", "type": "books_downloaded", "threshold": 10},
    "first_finish": {"title": "Core Consumed", "desc": "Finish an audiobook.", "type": "books_finished", "threshold": 1},
    "finish_5": {"title": "Path Advancement", "desc": "Finish 5 audiobooks.", "type": "books_finished", "threshold": 5},
    "listen_10h": {"title": "Mana Cultivator", "desc": "Listen for 10 total hours.", "type": "seconds_listened", "threshold": 36000},
    "listen_50h": {"title": "Dao of the Tome", "desc": "Listen for 50 total hours.", "type": "seconds_listened", "threshold": 180000}
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
                    kw.get("achievement", {}).get("desc", "Unlocked!")
                )
            )
            
        self.achievements = ACHIEVEMENTS
        
    def add_stat(self, stat_name, amount=1):
        with self.stats_lock:
            settings = self.db.load_settings()
            stats = settings.get("stats", {})
            stats[stat_name] = stats.get(stat_name, 0) + amount
            settings["stats"] = stats
            self.db.save_settings(settings)
            
            # Announce stat change for any interested UI elements (like a profile dashboard)
            self.event_bus.publish("stats.updated", stat_name=stat_name, total=stats[stat_name])
            
            self.check_achievements(settings)

    def check_achievements(self, settings):
        stats = settings.get("stats", {})
        unlocked = stats.get("unlocked_achievements", [])
        
        for ach_id, data in self.achievements.items():
            if ach_id not in unlocked:
                current_val = stats.get(data["type"], 0)
                if current_val >= data["threshold"]:
                    unlocked.append(ach_id)
                    
                    self.event_bus.publish("stats.achievement_unlocked", achievement=data)
                    
        stats["unlocked_achievements"] = unlocked
        settings["stats"] = stats
        self.db.save_settings(settings)