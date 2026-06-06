import tkinter as tk
from tkinter import ttk


def setup_sidebar(app, parent):
    """Builds the right-side information and bookmarks panel."""

    # --- INFO COMPONENTS ---
    app.cover_frame = ttk.Frame(parent)
    app.cover_frame.pack(fill="x", padx=5, pady=10)

    app.cover_label = ttk.Label(app.cover_frame, text="No Cover Art", cursor="hand2")
    app.cover_label.pack(pady=5)

    app.author_label = ttk.Label(
        app.cover_frame, text="", font=("Segoe UI", 10, "italic")
    )
    app.author_label.pack(pady=2)

    # --- NEW: Series Metadata Label ---
    app.series_label = ttk.Label(
        app.cover_frame, text="", font=("Segoe UI", 9, "bold"), foreground="#4a90e2"
    )
    app.series_label.pack(pady=(0, 5))

    app.current_cover_photo = None

    # --- BOOKMARKS COMPONENTS ---
    app.bm_frame = ttk.LabelFrame(parent, text="Bookmarks & Notes", padding=10)
    app.bm_frame.pack(fill="both", expand=True, padx=5, pady=5)

    scroll = ttk.Scrollbar(app.bm_frame)
    scroll.pack(side=tk.RIGHT, fill="y")

    app.bm_tree = ttk.Treeview(
        app.bm_frame,
        columns=("Time", "Note"),
        show="headings",
        yscrollcommand=scroll.set,
        height=5,
    )
    app.bm_tree.heading("Time", text="Time")
    app.bm_tree.heading("Note", text="Note")

    app.bm_tree.column("Time", width=140, anchor="w", stretch=False)
    app.bm_tree.column("Note", width=150, anchor="w")
    app.bm_tree.pack(fill="both", expand=True)

    scroll.config(command=app.bm_tree.yview)

    # Double click to jump to the bookmark
    app.bm_tree.bind("<Double-1>", app.bookmarks_presenter.jump_to_bookmark)

    btn_frame = ttk.Frame(app.bm_frame)
    btn_frame.pack(fill="x", pady=(5, 0))
    ttk.Button(
        btn_frame,
        text="Delete Selected",
        command=app.bookmarks_presenter.delete_bookmark,
    ).pack(side=tk.RIGHT)

    # --- EVENT BUBBLING SHIELD ---
    def block_context_menu(event):
        return "break"

    def recursively_block_clicks(widget):
        # Bind the block to everything inside the sidebar
        widget.bind("<Button-2>", block_context_menu)
        widget.bind("<Button-3>", block_context_menu)
        for child in widget.winfo_children():
            recursively_block_clicks(child)

    recursively_block_clicks(parent)
