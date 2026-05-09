import tkinter as tk
from tkinter import ttk
from ui.components.dialogs import open_auth_window, open_achievements_window

import tkinter as tk
from tkinter import ttk
from ui.components.dialogs import open_auth_window, open_achievements_window, open_pairing_window, open_audio_device_settings

def setup_menu_bar(app):
    """Builds the top menu bar using native OS integration."""
    
    # Create the master native menu bar
    app.main_menu = tk.Menu(app.root)
    app.root.config(menu=app.main_menu)

    # --- FILE MENU ---
    app.file_menu = tk.Menu(app.main_menu, tearoff=0)
    app.main_menu.add_cascade(label="File", menu=app.file_menu)
    
    app.file_menu.add_command(label="Set Download Folder", command=app.set_download_folder)
    app.file_menu.add_command(label="Set Audio Output", command=lambda: open_audio_device_settings(app))
    app.file_menu.add_command(label="Authentication & Profiles", command=lambda: open_auth_window(app))
    app.file_menu.add_separator()
    app.file_menu.add_checkbutton(
        label="Minimize to Tray on Close", 
        variable=app.minimize_to_tray_var, 
        command=app.save_tray_setting
    )
    app.file_menu.add_separator()

    # --- APPEARANCE SUB-MENU ---
    app.appearance_menu = tk.Menu(app.file_menu, tearoff=0)
    app.file_menu.add_cascade(label="Appearance", menu=app.appearance_menu)
    
    app.palette_var = tk.StringVar(value=app.settings.get("classic_palette", "light"))
    
    app.appearance_menu.add_radiobutton(label="Light Default", variable=app.palette_var, value="light", command=lambda: app.apply_classic_palette("light"))
    app.appearance_menu.add_radiobutton(label="Dark Charcoal", variable=app.palette_var, value="dark", command=lambda: app.apply_classic_palette("dark"))
    app.appearance_menu.add_radiobutton(label="Terminal Green", variable=app.palette_var, value="terminal", command=lambda: app.apply_classic_palette("terminal"))
    app.appearance_menu.add_separator()
    app.appearance_menu.add_radiobutton(label="Solarized Dark", variable=app.palette_var, value="solarized_dark", command=lambda: app.apply_classic_palette("solarized_dark"))
    app.appearance_menu.add_radiobutton(label="Solarized Light", variable=app.palette_var, value="solarized_light", command=lambda: app.apply_classic_palette("solarized_light"))
    app.appearance_menu.add_separator()
    app.appearance_menu.add_radiobutton(label="Dracula", variable=app.palette_var, value="dracula", command=lambda: app.apply_classic_palette("dracula"))
    app.appearance_menu.add_radiobutton(label="Nordic Slate", variable=app.palette_var, value="nord", command=lambda: app.apply_classic_palette("nord"))
    app.appearance_menu.add_radiobutton(label="Cyberpunk", variable=app.palette_var, value="cyberpunk", command=lambda: app.apply_classic_palette("cyberpunk"))

    app.file_menu.add_separator()

    # --- EXPORT SUB-MENU ---
    app.export_menu = tk.Menu(app.file_menu, tearoff=0)
    app.file_menu.add_cascade(label="Export Library", menu=app.export_menu)
    app.export_menu.add_command(label="Export to CSV", command=app.export_csv_worker)
    app.export_menu.add_command(label="Export to HTML Page", command=app.export_html_worker)

    app.file_menu.add_separator()

    # --- ACHIEVEMENTS & SERVER ---
    app.file_menu.add_command(label="My Achievements", command=lambda: open_achievements_window(app))
    app.file_menu.add_separator()
    app.file_menu.add_command(label="Enable Web Server", command=app.toggle_web_server)

    # app.file_menu.add_command(label="Open Web UI (Beta)", command=app.open_web_ui)
    app.file_menu.add_command(label="Show Pairing Info", command=lambda: open_pairing_window(app), state=tk.DISABLED)
    app.file_menu.add_command(label="Remove Firewall Rule", command=app.remove_firewall_rule_prompt)
    app.file_menu.add_separator()
    app.file_menu.add_command(label="Exit", command=app.on_closing)

    # --- HELP / DONATE MENU ---
    app.help_menu = tk.Menu(app.main_menu, tearoff=0)
    app.main_menu.add_cascade(label="Donate / Help", menu=app.help_menu)
    
    app.help_menu.add_command(label="Support the Developer ☕", command=app.open_support_link)