import tkinter as tk
from tkinter import ttk
from ui.components.dialogs import open_sleep_menu, open_chapter_window

def setup_player_bar(app):
    """Builds the bottom player bar and attaches it to the main app instance."""
    
    play_frame = ttk.LabelFrame(app.root, text="Playback", padding=10)
    play_frame.pack(side=tk.BOTTOM, fill="x", padx=5, pady=5)

    # --- Top Row: Info & Time ---
    top_row = ttk.Frame(play_frame)
    top_row.pack(fill="x", pady=2)
    
    app.info_label = ttk.Label(top_row, text="Select a book", justify="left")
    app.info_label.pack(side=tk.LEFT, padx=5)
    
    app.time_label = ttk.Label(top_row, text="00:00 / 00:00")
    app.time_label.pack(side=tk.RIGHT, padx=5)

    # --- Progress Bar ---
    app.progress_var = tk.DoubleVar()
    app.progress_bar = ttk.Progressbar(play_frame, variable=app.progress_var, maximum=100)
    app.progress_bar.pack(fill="x", padx=5, pady=5)
    
    # Safely bind the progress click if you added the method, otherwise ignore
    if hasattr(app, 'on_progress_click'):
        app.progress_bar.bind("<Button-1>", app.on_progress_click)

    # --- Controls Row ---
    controls_frame = ttk.Frame(play_frame)
    controls_frame.pack(pady=5)

    ttk.Button(controls_frame, text="<< Prev Chapter", width=14, command=app.prev_chapter).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="-30s", width=5, command=lambda: app.seek_audio(-30)).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="Play", width=8, command=app.master_play).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="Pause", width=8, command=app.pause_audio).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="+30s", width=5, command=lambda: app.seek_audio(30)).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="Next Chapter >>", width=14, command=app.next_chapter).pack(side=tk.LEFT, padx=2)
    ttk.Button(controls_frame, text="🔖 Bookmark", width=12, command=app.add_bookmark).pack(side=tk.LEFT, padx=(10, 2))
    ttk.Button(controls_frame, text="📑 Chapters", command=lambda: open_chapter_window(app)).pack(side=tk.LEFT, padx=(15, 2))

    # --- Speed Dropdown ---
    app.playback_speed = tk.StringVar(value="1.0x")
    speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
    
    speed_menu = ttk.Combobox(controls_frame, textvariable=app.playback_speed, values=speed_options, state="readonly", width=5)
    speed_menu.bind("<<ComboboxSelected>>", app.on_speed_change)
    speed_menu.pack(side=tk.LEFT, padx=10)

    # --- Volume ---
    app.volume_var = tk.DoubleVar(value=100.0)
    vol_frame = ttk.Frame(controls_frame)
    vol_frame.pack(side=tk.LEFT, padx=5)
    ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
    app.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=app.volume_var, command=app.on_volume_change, length=80)
    app.vol_slider.pack(side=tk.LEFT)

    # --- Sleep Timer ---
    timer_frame = ttk.Frame(controls_frame)
    timer_frame.pack(side=tk.LEFT, padx=15)
    
    app.timer_btn = ttk.Button(timer_frame, text="Sleep: Off", command=lambda: open_sleep_menu(app), width=16)
    app.timer_btn.pack(side=tk.LEFT)
    
    app.timer_countdown_var = tk.StringVar(value="")
    ttk.Label(timer_frame, textvariable=app.timer_countdown_var, width=5).pack(side=tk.LEFT)

    # --- Filters Row ---
    app.voice_boost_var = tk.BooleanVar(value=app.settings.get("voice_boost", False))
    app.skip_silence_var = tk.BooleanVar(value=app.settings.get("skip_silence", False))
    
    filters_frame = ttk.Frame(play_frame)
    filters_frame.pack(fill="x", pady=(5, 0))
    
    ttk.Label(filters_frame, text="Filters:").pack(side=tk.LEFT, padx=(5, 10))
    
    ttk.Checkbutton(
        filters_frame, text="Voice Boost (Compressor)", 
        variable=app.voice_boost_var, command=app.on_filter_change
    ).pack(side=tk.LEFT, padx=5)
    
    ttk.Checkbutton(
        filters_frame, text="Skip Silence", 
        variable=app.skip_silence_var, command=app.on_filter_change
    ).pack(side=tk.LEFT, padx=5)