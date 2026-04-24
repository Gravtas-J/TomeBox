import tkinter as tk
from tkinter import ttk

def setup_library_view(app, parent):
    """Builds the main library grid, list, and queue views."""
    style = ttk.Style()
    default_bg = style.lookup("TFrame", "background") or "#f0f0f0"
    app.main_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
    app.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

    lib_frame = ttk.LabelFrame(app.main_paned, text="", padding=10)
    app.main_paned.add(lib_frame, weight=1)

    app.queue_frame = ttk.LabelFrame(app.main_paned, text="Active Downloads", padding=10)
    
    queue_controls = ttk.Frame(app.queue_frame)
    queue_controls.pack(fill="x", pady=(0, 5))
    ttk.Button(queue_controls, text="Cancel All Downloads", command=app.cancel_all_downloads).pack(side=tk.RIGHT)

    # sv_ttk background color applied to the canvas
    app.queue_canvas = tk.Canvas(app.queue_frame, height=120, bg=default_bg, highlightthickness=0)
    queue_scroll = ttk.Scrollbar(app.queue_frame, orient="vertical", command=app.queue_canvas.yview)
    app.queue_inner = tk.Frame(app.queue_canvas, bg=default_bg)
    
    
    app.queue_inner.bind("<Configure>", lambda e: app.queue_canvas.configure(scrollregion=app.queue_canvas.bbox("all")))
    app.queue_canvas.create_window((0, 0), window=app.queue_inner, anchor="nw")
    app.queue_canvas.configure(yscrollcommand=queue_scroll.set)

    app.queue_canvas.pack(side="left", fill="both", expand=True)
    queue_scroll.pack(side="right", fill="y")

    app.queue_ui_elements = {}

    filter_frame = ttk.Frame(lib_frame)
    filter_frame.pack(fill="x", pady=(0, 5))

    ttk.Label(filter_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
    
    app.search_var = tk.StringVar()
    app.search_var.trace_add("write", lambda *args: app.refresh_library_ui()) 
    search_entry = ttk.Entry(filter_frame, textvariable=app.search_var, width=35)
    search_entry.pack(side=tk.LEFT, padx=(0, 20))

    ttk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
    
    app.filter_var = tk.StringVar(value="All")
    filter_combo = ttk.Combobox(filter_frame, textvariable=app.filter_var, values=["All", "Downloaded", "Cloud Only"], state="readonly", width=15)
    filter_combo.pack(side=tk.LEFT)
    filter_combo.bind("<<ComboboxSelected>>", lambda e: app.refresh_library_ui())

    ttk.Label(filter_frame, text="Shelf:").pack(side=tk.LEFT, padx=(10, 5))
    app.shelf_filter_var = tk.StringVar(value="All Shelves")
    app.shelf_combo = ttk.Combobox(filter_frame, textvariable=app.shelf_filter_var, state="readonly", width=15)
    app.shelf_combo.pack(side=tk.LEFT)
    app.shelf_combo.bind("<<ComboboxSelected>>", lambda e: app.refresh_library_ui())

    app.view_btn = ttk.Button(filter_frame, text="Grid View", command=app.toggle_library_view)
    app.view_btn.pack(side=tk.RIGHT, padx=5)

    app.toggle_queue_btn = ttk.Button(filter_frame, text="Show/Hide Queue", command=app.toggle_queue_visibility)
    app.toggle_queue_btn.pack(side=tk.RIGHT, padx=5)

    app.dl_all_btn = ttk.Button(filter_frame, text="Download Missing", command=app.start_download_all)
    app.dl_all_btn.pack(side=tk.RIGHT, padx=(5, 5))

    tree_frame = ttk.Frame(lib_frame)
    tree_frame.pack(fill="both", expand=True, pady=5)

    scroll = ttk.Scrollbar(tree_frame)
    scroll.pack(side=tk.RIGHT, fill="y")

    app.library_tree = ttk.Treeview(tree_frame, columns=("Title", "Author", "Series", "Duration", "ASIN", "Status"), show="headings", yscrollcommand=scroll.set)
    scroll.config(command=app.library_tree.yview)
    app.library_tree.bind("<<TreeviewSelect>>", app.on_item_select)
    
    app.current_view_mode = "list"
    app.grid_images_ref = [] 
    
    app.grid_canvas = tk.Canvas(tree_frame, bg=default_bg, highlightthickness=0)
    app.grid_inner = tk.Frame(app.grid_canvas, bg=default_bg)
    app.grid_window_id = app.grid_canvas.create_window((0, 0), window=app.grid_inner, anchor="nw")
    
    app.grid_canvas.configure(yscrollcommand=scroll.set)
    app.grid_inner.bind("<Configure>", lambda e: app.grid_canvas.configure(scrollregion=app.grid_canvas.bbox("all")))
    
    app.grid_canvas.bind("<Configure>", app.on_canvas_resize)
    app.root.bind_all("<MouseWheel>", app._on_grid_scroll)  
    app.root.bind_all("<Button-4>", app._on_grid_scroll)    
    app.root.bind_all("<Button-5>", app._on_grid_scroll)    
    app.root.bind_all("<Button-3>", app.show_context_menu)

    app.empty_state_frame = tk.Frame(tree_frame)
    app.empty_state_img_label = ttk.Label(app.empty_state_frame)
    app.empty_state_img_label.pack(pady=(80, 20))
    
    empty_text = (
        "Your library is completely empty.\n\n"
        "To get started:\n"
        "1. Navigate to 'File -> Authentication & Profiles' to link your Audible account.\n"
        "2. Download your library or drag and drop .aax or .m4b files directly into this window to import local media."
    )
    ttk.Label(app.empty_state_frame, text=empty_text, justify="center", font=("Segoe UI", 12)).pack()

    for col in app.library_tree["columns"]:
        app.library_tree.heading(col, text=col, command=lambda _col=col: app.sort_treeview(app.library_tree, _col, False))
        
    app.library_tree.column("Title", width=250)
    app.library_tree.column("Author", width=120)
    app.library_tree.column("Series", width=120)
    app.library_tree.column("Duration", width=70)
    app.library_tree.column("ASIN", width=90)
    app.library_tree.column("Status", width=110)
    app.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
    
    app.library_tree.bind("<Double-1>", app.master_play)

    btn_frame = ttk.Frame(lib_frame)
    btn_frame.pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="Refresh Cloud", command=app.fetch_cloud_library).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Download Selected", command=lambda: app.handle_action_on_selected("download")).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Convert Selected", command=lambda: app.handle_action_on_selected("convert")).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Convert All", command=app.start_convert_all_thread).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="Manage Shelves", command=app.manage_shelves_prompt).pack(side=tk.LEFT, padx=5)

    local_btn_frame = ttk.Frame(lib_frame)
    local_btn_frame.pack(fill="x", pady=2)
    ttk.Button(local_btn_frame, text="Add Local File", command=app.add_local_file).pack(side=tk.LEFT, padx=5)
    ttk.Button(local_btn_frame, text="Remove from List", command=app.remove_local_file).pack(side=tk.LEFT, padx=5)
    ttk.Button(local_btn_frame, text="Scrape Metadata", command=lambda: app.handle_action_on_selected("scrape")).pack(side=tk.LEFT, padx=5)

    dl_prog_frame = ttk.Frame(lib_frame)
    dl_prog_frame.pack(fill="x", padx=5)
    
    app.dl_status_var = tk.StringVar(value="Idle")
    app.dl_progress_var = tk.DoubleVar()
    ttk.Label(dl_prog_frame, textvariable=app.dl_status_var).pack(side=tk.TOP, anchor="w")
    ttk.Progressbar(dl_prog_frame, variable=app.dl_progress_var, maximum=100).pack(side=tk.TOP, fill="x")

    app.refresh_library_ui()