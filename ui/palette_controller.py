from ui.components.theme import apply_theme

class PaletteController:
    def __init__(self, app):
        self.app = app

    def apply_palette(self, palette_name):
        # 1. Update the UI state variable so the radio buttons match
        self.app.ui_state.palette.set(palette_name)
        
        # 2. Persist to the database
        self.app.settings["classic_palette"] = palette_name
        if hasattr(self.app, 'db'):
            self.app.db.save_settings(self.app.settings)
            
        # 3. Apply the actual visual theme
        apply_theme(self.app, palette_name)