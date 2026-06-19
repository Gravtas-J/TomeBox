import tkinter as tk
from tkinter import ttk

from ui.components.dialogs import open_library_folders_window


class ToolTip:
    """Creates a hover tooltip for any Tkinter widget."""

    def __init__(self, widget):
        self.widget = widget
        self.tip_window = None
        self.id = None
        self.text = ""
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def showtip(self, event=None):
        if not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#2a2a2a",
            foreground="white",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Helvetica", "9", "normal"),
            padx=5,
            pady=3,
        )
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


def setup_library_view(app, parent):
    """Builds the main library grid, list, and queue views."""
    style = ttk.Style()
    default_bg = style.lookup("TFrame", "background") or "#f0f0f0"
    app.main_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
    app.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

    lib_frame = ttk.LabelFrame(app.main_paned, text="", padding=10)
    app.main_paned.add(lib_frame, weight=1)

    app.queue_frame = ttk.LabelFrame(
        app.main_paned, text="Active Downloads", padding=10
    )

    queue_controls = ttk.Frame(app.queue_frame)
    queue_controls.pack(fill="x", pady=(0, 5))
    ttk.Button(
        queue_controls, text="Cancel All Downloads", command=app.cancel_all_downloads
    ).pack(side=tk.RIGHT)

    # sv_ttk background color applied to the canvas
    app.queue_canvas = tk.Canvas(
        app.queue_frame, height=120, bg=default_bg, highlightthickness=0
    )
    queue_scroll = ttk.Scrollbar(
        app.queue_frame, orient="vertical", command=app.queue_canvas.yview
    )
    app.queue_inner = tk.Frame(app.queue_canvas, bg=default_bg)

    app.queue_inner.bind(
        "<Configure>",
        lambda e: app.queue_canvas.configure(scrollregion=app.queue_canvas.bbox("all")),
    )
    app.queue_canvas.create_window((0, 0), window=app.queue_inner, anchor="nw")
    app.queue_canvas.configure(yscrollcommand=queue_scroll.set)

    app.queue_canvas.pack(side="left", fill="both", expand=True)
    queue_scroll.pack(side="right", fill="y")

    app.queue_ui_elements = {}

    count_frame = ttk.Frame(lib_frame)
    count_frame.pack(fill="x", pady=(0, 5))

    count_label = ttk.Label(
        count_frame,
        textvariable=app.ui_state.lib_count,
        cursor="question_arrow",
        font=("Segoe UI", 9, "bold"),
    )
    count_label.pack(side=tk.LEFT)
    app.lib_count_tooltip = ToolTip(count_label)

    filter_frame = ttk.Frame(lib_frame)
    filter_frame.pack(fill="x", pady=(0, 5))

    ttk.Label(filter_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))

    app.ui_state.search.trace_add(
        "write", lambda *args: app.library_presenter.refresh_library_ui()
    )
    app.search_entry = ttk.Entry(
        filter_frame, textvariable=app.ui_state.search, width=35
    )
    app.search_entry.pack(side=tk.LEFT, padx=(0, 20))

    ttk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))

    filter_combo = ttk.Combobox(
        filter_frame,
        textvariable=app.ui_state.filter,
        values=["All", "Downloaded", "Cloud Only"],
        state="readonly",
        width=15,
    )
    filter_combo.pack(side=tk.LEFT)
    filter_combo.bind(
        "<<ComboboxSelected>>", lambda e: app.library_presenter.refresh_library_ui()
    )

    ttk.Label(filter_frame, text="Shelf:").pack(side=tk.LEFT, padx=(10, 5))
    app.shelf_combo = ttk.Combobox(
        filter_frame, textvariable=app.ui_state.shelf_filter, state="readonly", width=15
    )
    app.shelf_combo.pack(side=tk.LEFT)
    app.shelf_combo.bind(
        "<<ComboboxSelected>>", lambda e: app.library_presenter.refresh_library_ui()
    )

    app.sort_label = ttk.Label(filter_frame, text="Sort:")
    sort_options = [
        "Title (A-Z)",
        "Author (A-Z)",
        "Date Added (Newest)",
        "Date Added (Oldest)",
    ]

    app.sort_combo = ttk.Combobox(
        filter_frame,
        textvariable=app.ui_state.sort,
        values=sort_options,
        state="readonly",
        width=16,
    )

    def on_sort_change(event):
        pref = app.ui_state.sort.get()
        app.settings["sort_pref"] = pref
        if hasattr(app, "db"):
            app.db.save_settings(app.settings)
        col, desc = {
            "Title (A-Z)": ("Title", False),
            "Author (A-Z)": ("Author", False),
            "Date Added (Newest)": ("Date Added", True),
        }.get(pref, ("Date Added", True))
        app.library_presenter.current_sort_col = col
        app.library_presenter.current_sort_descending = desc
        app.library_presenter.refresh_library_ui()

    app.sort_combo.bind("<<ComboboxSelected>>", on_sort_change)

    _c, _d = {
        "Title (A-Z)": ("Title", False),
        "Author (A-Z)": ("Author", False),
        "Date Added (Newest)": ("Date Added", True),
    }.get(app.ui_state.sort.get(), ("Date Added", True))
    app.library_presenter.current_sort_col = _c
    app.library_presenter.current_sort_descending = _d

    app.view_btn = ttk.Button(
        filter_frame,
        text="Grid View",
        command=app.library_presenter.toggle_library_view,
    )
    app.view_btn.pack(side=tk.RIGHT, padx=5)

    app.toggle_queue_btn = ttk.Button(
        filter_frame,
        text="Show/Hide Queue",
        command=app.import_session.toggle_queue_visibility,
    )
    app.toggle_queue_btn.pack(side=tk.RIGHT, padx=5)

    app.toggle_sidebar_btn = ttk.Button(
        filter_frame, text="Show/Hide Info", command=app.toggle_sidebar_visibility
    )
    app.toggle_sidebar_btn.pack(side=tk.RIGHT, padx=5)

    app.dl_all_btn = ttk.Button(
        filter_frame, text="Download Missing", command=app.start_download_all
    )
    app.dl_all_btn.pack(side=tk.RIGHT, padx=(5, 5))

    tree_frame = ttk.Frame(lib_frame)
    tree_frame.pack(fill="both", expand=True, pady=5)

    # 1. Use Grid to strictly bound the Treeview
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)

    app.v_scroll = ttk.Scrollbar(tree_frame, orient="vertical")
    app.v_scroll.grid(row=0, column=1, sticky="ns")

    app.h_scroll = ttk.Scrollbar(tree_frame, orient="horizontal")
    app.h_scroll.grid(row=1, column=0, sticky="ew")

    app.library_tree = ttk.Treeview(
        tree_frame,
        columns=(
            "Title",
            "Author",
            "Narrator",
            "Series",
            "Duration",
            "ASIN",
            "Status",
            "File Path",
            "Date Added",
        ),
        displaycolumns=(
            "Title",
            "ASIN",
            "Author",
            "Narrator",
            "Series",
            "Duration",
            "Date Added",
            "Status",
            "File Path",
        ),
        show="headings",
        yscrollcommand=app.v_scroll.set,
        xscrollcommand=app.h_scroll.set,
    )
    app.library_tree.grid(row=0, column=0, sticky="nsew")

    app.v_scroll.config(command=app.library_tree.yview)
    app.h_scroll.config(command=app.library_tree.xview)
    app.library_tree.bind("<<TreeviewSelect>>", app.on_item_select)

    app.library_tree.bind("<Double-1>", app.library_presenter.handle_tree_double_click)

    app.current_view_mode = "list"

    def on_grid_click(index):
        if 0 <= index < len(app.grid_canvas.data):
            item = app.grid_canvas.data[index]
            app._selected_grid_item = {
                "values": [
                    item.get("title", ""),
                    item.get("authors", ""),
                    item.get("narrator", ""),
                    item.get("series", ""),
                    item.get("duration_str", ""),
                    item.get("asin", ""),
                    item.get("status", ""),
                    item.get("path", ""),
                ]
            }
            app.on_item_select()

    def on_grid_double_click(index):
        on_grid_click(index)
        if hasattr(app, "playback_presenter"):
            app.playback_presenter.master_play(None)

    from ui.components.virtual_grid import VirtualGridView

    app.grid_canvas = VirtualGridView(
        tree_frame,
        image_cache=app.image_cache,
        cell_width=200,
        cell_height=300,
        on_click_cb=on_grid_click,
        on_double_click_cb=on_grid_double_click,
    )
    app.grid_canvas.configure(yscrollcommand=app.v_scroll.set)

    app.root.bind_all("<MouseWheel>", app.library_presenter._on_global_scroll)
    app.root.bind_all("<Button-4>", app.library_presenter._on_global_scroll)
    app.root.bind_all("<Button-5>", app.library_presenter._on_global_scroll)
    app.root.bind_all("<Button-3>", app.show_context_menu)

    app.root.bind_all("<Button-2>", app.show_context_menu)
    app.root.bind_all("<Control-Button-1>", app.show_context_menu)

    app.empty_state_frame = tk.Frame(tree_frame)
    app.empty_state_img_label = ttk.Label(app.empty_state_frame)
    app.empty_state_img_label.pack(pady=(80, 20))

    empty_text = (
        "Your library is completely empty.\n\n"
        "To get started:\n"
        "1. Navigate to 'File -> Authentication & Profiles' to link your Audible account.\n"
        "2. Download your library or drag and drop .aax or .m4b files directly into this window to import local media."
    )
    ttk.Label(
        app.empty_state_frame, text=empty_text, justify="center", font=("Segoe UI", 12)
    ).pack()

    for col in app.library_tree["columns"]:
        app.library_tree.heading(
            col,
            text=col,
            command=lambda _col=col: app.library_presenter.sort_treeview(
                app.library_tree, _col, False
            ),
        )

    # 2. Turn off stretch for ALL columns
    app.library_tree.column("Title", width=250, minwidth=200, stretch=tk.NO)
    app.library_tree.column("Author", width=120, minwidth=100, stretch=tk.NO)
    app.library_tree.column("Narrator", width=120, minwidth=100, stretch=tk.NO)
    app.library_tree.column("Series", width=120, minwidth=100, stretch=tk.NO)
    app.library_tree.column("Duration", width=70, minwidth=70, stretch=tk.NO)
    app.library_tree.column("ASIN", width=90, minwidth=90, stretch=tk.NO)
    app.library_tree.column("File Path", width=350, minwidth=250, stretch=tk.NO)
    app.library_tree.column("Status", width=110, minwidth=100, stretch=tk.NO)
    app.library_tree.column("Date Added", width=100, minwidth=90, stretch=tk.NO)

    btn_frame = ttk.Frame(lib_frame)
    btn_frame.pack(fill="x", pady=2)
    ttk.Button(
        btn_frame,
        text="Refresh Cloud",
        command=app.cloud_server_controller.fetch_cloud_library,
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        btn_frame,
        text="Download Selected",
        command=lambda: app.handle_action_on_selected("download"),
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        btn_frame,
        text="Split into Chapters",
        command=lambda: app.handle_action_on_selected("convert"),
    ).pack(side=tk.LEFT, padx=5)
    # ttk.Button(btn_frame, text="Convert All", command=app.start_convert_all_thread).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        btn_frame, text="Manage Shelves", command=app.manage_shelves_prompt
    ).pack(side=tk.LEFT, padx=5)

    local_btn_frame = ttk.Frame(lib_frame)
    local_btn_frame.pack(fill="x", pady=2)
    ttk.Button(
        local_btn_frame,
        text="Add Local File",
        command=app.import_session.add_local_file,
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        local_btn_frame, text="Import Folder", command=app.import_session.import_folder
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        local_btn_frame,
        text="Remove from List",
        command=lambda: app.library_manager.handle_remove_clicked(app),
    ).pack(side=tk.LEFT, padx=5)
    ttk.Button(
        local_btn_frame,
        text="Scrape Metadata",
        command=lambda: app.handle_action_on_selected("scrape"),
    ).pack(side=tk.LEFT, padx=5)
    # ttk.Button(local_btn_frame, text="Match to Audible", command=lambda: app.match_to_audible_prompt()).pack(side=tk.LEFT, padx=5)

    ttk.Button(
        local_btn_frame,
        text="Library Folders",
        command=lambda: open_library_folders_window(app),
    ).pack(side=tk.LEFT, padx=5)

    dl_prog_frame = ttk.Frame(lib_frame)
    dl_prog_frame.pack(fill="x", padx=5)

    status_frame = ttk.Frame(dl_prog_frame)
    status_frame.pack(side=tk.TOP, fill="x")

    ttk.Label(status_frame, textvariable=app.ui_state.dl_status).pack(side=tk.LEFT)
    # The new Cancel Task button
    ttk.Button(status_frame, text="Cancel Task", command=app.cancel_active_task).pack(
        side=tk.RIGHT
    )

    app.error_btn = ttk.Button(
        status_frame,
        textvariable=app.ui_state.error_btn,
        command=app.open_error_log,
        state=tk.DISABLED,
    )
    app.error_btn.pack(side=tk.RIGHT, padx=5)

    api_health_label = ttk.Label(
        status_frame, textvariable=app.ui_state.api_health, foreground="#888888"
    )
    api_health_label.pack(side=tk.RIGHT, padx=15)

    ttk.Progressbar(dl_prog_frame, variable=app.ui_state.dl_progress, maximum=100).pack(
        side=tk.TOP, fill="x"
    )

    app.library_presenter.refresh_library_ui()
