import tkinter as tk
from tkinter import ttk
from ui.components.dialogs import open_sleep_menu, open_chapter_window

def setup_player_bar(app):
    """Builds the bottom player bar and attaches it to the main app instance."""
    
    # Save reference so we can hide the "Playback" frame title in compact mode
    app.play_frame = ttk.LabelFrame(app.root, text="Playback", padding=10)
    app.play_frame.pack(side=tk.BOTTOM, fill="x", padx=5, pady=5)

    # --- Top Row: Info, Time, & Toggle ---
    top_row = ttk.Frame(app.play_frame)
    top_row.pack(fill="x", pady=2)
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    
    # NEW: Cover Art Thumbnail
    app.player_cover_lbl = tk.Label(top_row, text="", bg=bg_color)
    app.player_cover_lbl.pack(side=tk.LEFT, padx=(0, 10))
    
    app.info_label = ttk.Label(top_row, text="Select a book", justify="left")
    app.info_label.pack(side=tk.LEFT, padx=5)
    
    app.btn_compact = ttk.Button(top_row, text="▼ Compact", width=10, command=lambda: toggle_player_mode(app), state=tk.DISABLED)
    app.btn_compact.pack(side=tk.RIGHT, padx=5)

    app.time_label = ttk.Label(top_row, text="00:00 / 00:00")
    app.time_label.pack(side=tk.RIGHT, padx=15)

    # --- Progress Bar ---
    app.progress_bar = ttk.Progressbar(app.play_frame, variable=app.ui_state.playback_progress, maximum=100)
    app.progress_bar.pack(fill="x", padx=5, pady=5)
    
    if hasattr(app, 'on_progress_click'):
        app.progress_bar.bind("<Button-1>", app.on_progress_click)

    # --- Controls Container ---
    app.controls_frame = ttk.Frame(app.play_frame)
    app.controls_frame.pack(pady=5, fill="x")
    
    center_wrapper = ttk.Frame(app.controls_frame)
    center_wrapper.pack(anchor="center")

    # 1. Core Playback Buttons (Always Visible)
    app.playback_btns_frame = ttk.Frame(center_wrapper)
    app.playback_btns_frame.pack(side=tk.LEFT)

    app.btn_prev = ttk.Button(app.playback_btns_frame, text="<< Prev Chapter", width=14, command=app.prev_chapter)
    app.btn_prev.pack(side=tk.LEFT, padx=2)
    
    app.btn_m30 = ttk.Button(app.playback_btns_frame, text="-30s", width=5, command=lambda: app.seek_audio(-30))
    app.btn_m30.pack(side=tk.LEFT, padx=2)
    
    app.btn_play = ttk.Button(app.playback_btns_frame, text="Play", width=8, command=app.master_play)
    app.btn_play.pack(side=tk.LEFT, padx=2)
    
    app.btn_pause = ttk.Button(app.playback_btns_frame, text="Pause", width=8, command=app.pause_audio)
    app.btn_pause.pack(side=tk.LEFT, padx=2)
    
    app.btn_p30 = ttk.Button(app.playback_btns_frame, text="+30s", width=5, command=lambda: app.seek_audio(30))
    app.btn_p30.pack(side=tk.LEFT, padx=2)
    
    app.btn_next = ttk.Button(app.playback_btns_frame, text="Next Chapter >>", width=14, command=app.next_chapter)
    app.btn_next.pack(side=tk.LEFT, padx=2)

    # 2. Extra Controls (Hidden in Compact Mode)
    app.extras_frame = ttk.Frame(center_wrapper)
    app.extras_frame.pack(side=tk.LEFT)

    ttk.Button(app.extras_frame, text="🔖 Bookmark", width=12, command=app.add_bookmark).pack(side=tk.LEFT, padx=(10, 2))
    ttk.Button(app.extras_frame, text="📑 Chapters", command=lambda: open_chapter_window(app)).pack(side=tk.LEFT, padx=(5, 2))

    speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
    speed_menu = ttk.Combobox(app.extras_frame, textvariable=app.ui_state.playback_speed, values=speed_options, state="readonly", width=5)
    speed_menu.bind("<<ComboboxSelected>>", app.on_speed_change)
    speed_menu.pack(side=tk.LEFT, padx=10)

    vol_frame = ttk.Frame(app.extras_frame)
    vol_frame.pack(side=tk.LEFT, padx=5)
    ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
    app.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=app.ui_state.volume, command=app.on_volume_change, length=80)
    app.vol_slider.pack(side=tk.LEFT)

    timer_frame = ttk.Frame(app.extras_frame)
    timer_frame.pack(side=tk.LEFT, padx=15)
    app.timer_btn = ttk.Button(timer_frame, text="Sleep: Off", command=lambda: open_sleep_menu(app), width=16)
    app.timer_btn.pack(side=tk.LEFT)
    
    ttk.Label(timer_frame, textvariable=app.ui_state.timer_countdown, width=5).pack(side=tk.LEFT)

    # --- Filters Row (Hidden in Compact Mode) ---
    
    app.filters_frame = ttk.Frame(app.play_frame)
    app.filters_frame.pack(fill="x", pady=(5, 0))
    
    ttk.Label(app.filters_frame, text="Filters:").pack(side=tk.LEFT, padx=(5, 10))
    ttk.Checkbutton(app.filters_frame, text="Voice Boost (Compressor)", variable=app.ui_state.voice_boost, command=app.on_filter_change).pack(side=tk.LEFT, padx=5)
    ttk.Checkbutton(app.filters_frame, text="Skip Silence", variable=app.ui_state.skip_silence, command=app.on_filter_change).pack(side=tk.LEFT, padx=5)

    def enforce_initial_state():
        is_compact = app.settings.get("compact_player", False)
        if is_compact:
            # 1. Reset state to expanded
            app.settings["compact_player"] = False
            if hasattr(app, 'db'):
                app.db.save_settings(app.settings)
            
            # 2. Rescue the window size if the OS saved the tiny compact dimensions
            app.root.update_idletasks()
            geom = app.root.geometry()
            if "450x" in geom or "1x1" in geom:
                app.root.geometry("950x650") # Your standard library size

    app.root.after(200, enforce_initial_state)

def toggle_player_mode(app):
    if not getattr(app, 'file_path', None):
        return
    is_compact = app.settings.get("compact_player", False)
    new_state = not is_compact
    app.settings["compact_player"] = new_state
    if hasattr(app, 'db'):
        app.db.save_settings(app.settings)
    _apply_compact_state(app, new_state)

def _apply_compact_state(app, is_compact):
    """Executes the UI repacking to shift between the full Library layout and the Mini-Player layout."""
    import os
    from PIL import Image, ImageTk, ImageOps

    if is_compact:
        # 1. Hide the top menu bar
        app._saved_menu = app.root.cget("menu")
        app.root.config(menu="")

        app._was_zoomed = (app.root.state() == 'zoomed')
        if app._was_zoomed:
            app.root.state('normal')
        app.root.resizable(False, False)
        # 2. Save original window size (unless handled by the bootstrapper)
        if not getattr(app, '_booting_compact', False):
            app._pre_compact_geom = app.root.geometry()

        # 3. Take a snapshot of the current UI and hide everything EXCEPT the play_frame
        app._hidden_pack_slaves = []
        app._hidden_grid_slaves = []

        for widget in app.root.pack_slaves():
            if widget != app.play_frame and widget != getattr(app, 'compact_cover_lbl', None):
                info = widget.pack_info()
                app._hidden_pack_slaves.append((widget, info))
                widget.pack_forget()

        for widget in app.root.grid_slaves():
            if widget != app.play_frame and widget != getattr(app, 'compact_cover_lbl', None):
                info = widget.grid_info()
                app._hidden_grid_slaves.append((widget, info))
                widget.grid_forget()

        # 4. Create or reveal the giant cover art canvas
        from tkinter import ttk
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#2b2b2b"
        if bg_color == "": bg_color = "#2b2b2b" # Failsafe against the white background bug
        
        if not hasattr(app, 'compact_cover_lbl'):
            app.compact_cover_lbl = tk.Label(app.root, bg=bg_color)
        else:
            app.compact_cover_lbl.config(bg=bg_color)

        app.compact_cover_lbl.pack(side=tk.TOP, fill="both", expand=True)

        # 5. Hide extras and format the media buttons
        app.play_frame.config(text="")
        app.extras_frame.pack_forget()
        app.filters_frame.pack_forget()

        app.btn_prev.config(text="⏮", width=4)
        app.btn_m30.config(text="↺", width=4)
        app.btn_play.config(text="▶", width=5)
        app.btn_pause.config(text="⏸", width=5)
        app.btn_p30.config(text="↻", width=4)
        app.btn_next.config(text="⏭", width=4)

        app.btn_compact.config(text="▲ Expand")

        # 6. Render the high-res cover art
        cover_path = None
        if hasattr(app, 'file_path') and app.file_path:
            local_data = app.library_manager.local_library.get(app.file_path, {})
            asin = local_data.get("asin")
            if asin:
                cp = os.path.join(app.covers_dir, f"{asin}.jpg")
                if os.path.exists(cp):
                    cover_path = cp

        if cover_path:
            try:
                img = Image.open(cover_path).convert("RGB")
                img = ImageOps.fit(img, (450, 450), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                photo = ImageTk.PhotoImage(img)
                app.compact_cover_lbl.config(image=photo)
                app.compact_cover_lbl.image = photo 
            except Exception:
                app.compact_cover_lbl.config(image="")
        else:
            app.compact_cover_lbl.config(image="")

        # 7. Shrink the main OS window
        app.root.geometry("450x610")

    else:
        # 1. Restore the top menu bar
        if hasattr(app, '_saved_menu') and app._saved_menu:
            app.root.config(menu=app._saved_menu)

        app.root.resizable(True, True)

        # 2. Hide the giant cover canvas
        if hasattr(app, 'compact_cover_lbl'):
            app.compact_cover_lbl.pack_forget()

        # 3. Restore all main UI elements in their exact original order
        if hasattr(app, '_hidden_pack_slaves'):
            for widget, info in app._hidden_pack_slaves:
                try: widget.pack(**info)
                except Exception: pass
        if hasattr(app, '_hidden_grid_slaves'):
            for widget, info in app._hidden_grid_slaves:
                try: widget.grid(**info)
                except Exception: pass

        # 4. Restore the full player bar UI
        app.play_frame.config(text="Playback")
        app.extras_frame.pack(side=tk.LEFT)
        app.filters_frame.pack(fill="x", pady=(5, 0))

        app.btn_prev.config(text="<< Prev Chapter", width=14)
        app.btn_m30.config(text="-30s", width=5)
        app.btn_play.config(text="Play", width=8)
        app.btn_pause.config(text="Pause", width=8)
        app.btn_p30.config(text="+30s", width=5)
        app.btn_next.config(text="Next Chapter >>", width=14)

        app.btn_compact.config(text="▼ Compact")

        # 5. Snap the OS window back to its original dimensions
        if hasattr(app, '_pre_compact_geom'):
            app.root.geometry(app._pre_compact_geom)

        if getattr(app, '_was_zoomed', False):
            app.root.state('zoomed')