import tkinter as tk
from tkinter import ttk

class PlayerBarView:
    def __init__(self, parent_root, ui_state, playback_presenter, bookmarks_presenter, settings, callbacks):
        self.root = parent_root
        self.ui_state = ui_state
        self.playback_presenter = playback_presenter
        self.bookmarks_presenter = bookmarks_presenter
        self.settings = settings
        self.callbacks = callbacks # Dictionary for global layout/dialog actions

        self.build_ui()
        
        # Link this view to the presenter so the presenter can update our widgets
        self.playback_presenter.set_view(self)

    def build_ui(self):
        self.play_frame = ttk.LabelFrame(self.root, text="Playback", padding=10)
        self.play_frame.pack(side=tk.BOTTOM, fill="x", padx=5, pady=5)

        top_row = ttk.Frame(self.play_frame)
        top_row.pack(fill="x", pady=2)
        
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        
        self.player_cover_lbl = tk.Label(top_row, text="", bg=bg_color)
        self.player_cover_lbl.pack(side=tk.LEFT, padx=(0, 10))
        
        self.info_label = ttk.Label(top_row, text="Select a book", justify="left")
        self.info_label.pack(side=tk.LEFT, padx=5)
        
        self.btn_compact = ttk.Button(top_row, text="▼ Compact", width=10, command=self.callbacks.get('toggle_compact'), state=tk.DISABLED)
        self.btn_compact.pack(side=tk.RIGHT, padx=5)

        self.time_label = ttk.Label(top_row, text="00:00 / 00:00")
        self.time_label.pack(side=tk.RIGHT, padx=15)

        self.progress_bar = ttk.Progressbar(self.play_frame, variable=self.ui_state.playback_progress, maximum=100)
        self.progress_bar.pack(fill="x", padx=5, pady=5)
        self.progress_bar.bind("<Button-1>", self.playback_presenter.on_progress_click)

        self.controls_frame = ttk.Frame(self.play_frame)
        self.controls_frame.pack(pady=5, fill="x")
        
        center_wrapper = ttk.Frame(self.controls_frame)
        center_wrapper.pack(anchor="center")

        self.playback_btns_frame = ttk.Frame(center_wrapper)
        self.playback_btns_frame.pack(side=tk.LEFT)

        self.btn_prev = ttk.Button(self.playback_btns_frame, text="<< Prev Chapter", width=14, command=self.playback_presenter.prev_chapter)
        self.btn_prev.pack(side=tk.LEFT, padx=2)
        
        self.btn_m30 = ttk.Button(self.playback_btns_frame, text="-30s", width=5, command=lambda: self.playback_presenter.seek_audio(-30))
        self.btn_m30.pack(side=tk.LEFT, padx=2)
        
        self.btn_play = ttk.Button(self.playback_btns_frame, text="Play", width=8, command=self.playback_presenter.master_play)
        self.btn_play.pack(side=tk.LEFT, padx=2)
        
        self.btn_pause = ttk.Button(self.playback_btns_frame, text="Pause", width=8, command=self.playback_presenter.pause_audio)
        self.btn_pause.pack(side=tk.LEFT, padx=2)
        
        self.btn_p30 = ttk.Button(self.playback_btns_frame, text="+30s", width=5, command=lambda: self.playback_presenter.seek_audio(30))
        self.btn_p30.pack(side=tk.LEFT, padx=2)
        
        self.btn_next = ttk.Button(self.playback_btns_frame, text="Next Chapter >>", width=14, command=self.playback_presenter.next_chapter)
        self.btn_next.pack(side=tk.LEFT, padx=2)

        self.extras_frame = ttk.Frame(center_wrapper)
        self.extras_frame.pack(side=tk.LEFT)

        ttk.Button(self.extras_frame, text="🔖 Bookmark", width=12, command=self.bookmarks_presenter.add_bookmark).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(self.extras_frame, text="📑 Chapters", command=self.callbacks.get('open_chapter')).pack(side=tk.LEFT, padx=(5, 2))

        speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
        speed_menu = ttk.Combobox(self.extras_frame, textvariable=self.ui_state.playback_speed, values=speed_options, state="readonly", width=5)
        speed_menu.bind("<<ComboboxSelected>>", self.playback_presenter.on_speed_change)
        speed_menu.pack(side=tk.LEFT, padx=10)

        vol_frame = ttk.Frame(self.extras_frame)
        vol_frame.pack(side=tk.LEFT, padx=5)
        ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
        self.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.ui_state.volume, command=self.playback_presenter.on_volume_change, length=80)
        self.vol_slider.pack(side=tk.LEFT)

        timer_frame = ttk.Frame(self.extras_frame)
        timer_frame.pack(side=tk.LEFT, padx=15)
        self.timer_btn = ttk.Button(timer_frame, text="Sleep: Off", command=self.callbacks.get('open_sleep'), width=16)
        self.timer_btn.pack(side=tk.LEFT)
        
        ttk.Label(timer_frame, textvariable=self.ui_state.timer_countdown, width=5).pack(side=tk.LEFT)

        self.filters_frame = ttk.Frame(self.play_frame)
        self.filters_frame.pack(fill="x", pady=(5, 0))
        
        ttk.Label(self.filters_frame, text="Filters:").pack(side=tk.LEFT, padx=(5, 10))
        ttk.Checkbutton(self.filters_frame, text="Voice Boost (Compressor)", variable=self.ui_state.voice_boost, command=self.callbacks.get('on_filter_change')).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(self.filters_frame, text="Skip Silence", variable=self.ui_state.skip_silence, command=self.callbacks.get('on_filter_change')).pack(side=tk.LEFT, padx=5)

    def apply_compact_layout(self):
        self.play_frame.config(text="")
        self.extras_frame.pack_forget()
        self.filters_frame.pack_forget()

        self.btn_prev.config(text="⏮", width=4)
        self.btn_m30.config(text="↺", width=4)
        self.btn_play.config(text="▶", width=5)
        self.btn_pause.config(text="⏸", width=5)
        self.btn_p30.config(text="↻", width=4)
        self.btn_next.config(text="⏭", width=4)
        self.btn_compact.config(text="▲ Expand")

    def apply_standard_layout(self):
        self.play_frame.config(text="Playback")
        self.extras_frame.pack(side=tk.LEFT)
        self.filters_frame.pack(fill="x", pady=(5, 0))

        self.btn_prev.config(text="<< Prev Chapter", width=14)
        self.btn_m30.config(text="-30s", width=5)
        self.btn_play.config(text="Play", width=8)
        self.btn_pause.config(text="Pause", width=8)
        self.btn_p30.config(text="+30s", width=5)
        self.btn_next.config(text="Next Chapter >>", width=14)
        self.btn_compact.config(text="▼ Compact")