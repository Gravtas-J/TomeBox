class StatsManager:
    def __init__(self, db_manager, callbacks):
        self.db = db_manager
        self.on_achievement = callbacks.get("on_achievement")
        
        self.achievements = {
            "first_dl": {"title": "System Integration Complete", "desc": "Download your first audiobook.", "type": "books_downloaded", "threshold": 1},
            "hoarder_1": {"title": "Spatial Expansion", "desc": "Download 10 audiobooks.", "type": "books_downloaded", "threshold": 10},
            "first_finish": {"title": "Core Consumed", "desc": "Finish an audiobook.", "type": "books_finished", "threshold": 1},
            "finish_5": {"title": "Path Advancement", "desc": "Finish 5 audiobooks.", "type": "books_finished", "threshold": 5},
            "listen_10h": {"title": "Mana Cultivator", "desc": "Listen for 10 total hours.", "type": "seconds_listened", "threshold": 36000},
            "listen_50h": {"title": "Dao of the Tome", "desc": "Listen for 50 total hours.", "type": "seconds_listened", "threshold": 180000}
        }
        
    def add_stat(self, stat_name, amount=1):
        settings = self.db.load_settings()
        stats = settings.get("stats", {})
        stats[stat_name] = stats.get(stat_name, 0) + amount
        settings["stats"] = stats
        self.db.save_settings(settings)
        self.check_achievements(settings)

    def check_achievements(self, settings):
        stats = settings.get("stats", {})
        unlocked = stats.get("unlocked_achievements", [])
        
        for ach_id, data in self.achievements.items():
            if ach_id not in unlocked:
                current_val = stats.get(data["type"], 0)
                if current_val >= data["threshold"]:
                    unlocked.append(ach_id)
                    settings["stats"]["unlocked_achievements"] = unlocked
                    self.db.save_settings(settings)
                    if self.on_achievement:
                        self.on_achievement(data["title"], data["desc"])