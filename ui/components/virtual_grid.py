import tkinter as tk
from tkinter import ttk
import math

class GridCell(tk.Frame):
    """The physical Tkinter widget that gets recycled for different books."""
    def __init__(self, parent, width, height, **kwargs):
        # Use your theme's background color
        super().__init__(parent, width=width, height=height, bg="#1e1e1e", **kwargs)
        self.pack_propagate(False)
        
        # Cover Image Canvas (Leaves ~50px at the bottom for text)
        self.cover_height = height - 55
        self.cover_canvas = tk.Canvas(self, width=width, height=self.cover_height, bg="#1e1e1e", highlightthickness=0)
        self.cover_canvas.pack(side="top", fill="x", pady=(5, 0))
        self.cover_id = self.cover_canvas.create_image(width//2, self.cover_height//2, anchor="center")
        
        # Title Label
        self.lbl_title = tk.Label(self, text="", bg="#1e1e1e", fg="white", font=("Segoe UI", 10, "bold"), anchor="w")
        self.lbl_title.pack(side="top", fill="x", padx=10)
        
        # Author Label
        self.lbl_author = tk.Label(self, text="", bg="#1e1e1e", fg="#95a5a6", font=("Segoe UI", 9), anchor="w")
        self.lbl_author.pack(side="top", fill="x", padx=10)
        
        self.current_index = None

        self.last_x = None
        self.last_y = None
        self.is_hidden = True
        for w in (self, self.cover_canvas, self.lbl_title, self.lbl_author):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-1>", self._on_double_click)
            # Explicitly force scroll events to bubble up to the parent grid
            w.bind("<MouseWheel>", parent._on_mousewheel_win)
            w.bind("<Button-4>", parent._on_mousewheel_mac)
            w.bind("<Button-5>", parent._on_mousewheel_mac)

    def _on_click(self, event):
        if self.current_index is not None and getattr(self.master, 'on_click_cb', None):
            self.master.on_click_cb(self.current_index)

    def _on_double_click(self, event):
        if self.current_index is not None and getattr(self.master, 'on_double_click_cb', None):
            self.master.on_double_click_cb(self.current_index)

    def update_data(self, index, data, photo):
        """Injects new data into the cell without destroying/recreating widgets."""
        self.current_index = index
        self.lbl_title.config(text=data.get("title", "Unknown Title"))
        
        # Safely handle author lists or strings
        authors = data.get("authors", "Unknown Author")
        if isinstance(authors, list):
            authors = ", ".join([a.get("name", "") for a in authors if isinstance(a, dict)])
        self.lbl_author.config(text=authors)
        
        self.cover_canvas.itemconfig(self.cover_id, image=photo)
        self.photo = photo  # Keep a local reference so Tkinter's garbage collector doesn't eat it


class VirtualGridView(tk.Canvas):
    """
    Phase 3: The Virtualized Grid Engine.
    Recycles a small pool of GridCell widgets based on scroll math.
    """
    def __init__(self, parent, image_cache, cell_width=200, cell_height=300, on_click_cb=None, on_double_click_cb=None, **kwargs):
        kwargs.setdefault('highlightthickness', 0)
        kwargs.setdefault('bg', '#1e1e1e')
        super().__init__(parent, **kwargs)
        
        self.image_cache = image_cache
        self.on_click_cb = on_click_cb
        self.on_double_click_cb = on_double_click_cb
        
        self.image_cache = image_cache
        self.cell_width = cell_width
        self.cell_height = cell_height
        
        self.data = []
        self.cols = 1
        self.rows = 0
        
        # --- THE RECYCLING POOL ---
        self.active_cells = {}  # Dict mapping: logical_index -> (canvas_window_id, GridCell)
        self.unused_pool = []   # List of unused (canvas_window_id, GridCell)
        self._init_pool(40)     # Pre-allocate enough for a 1080p screen
        
        # Bindings
        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_mousewheel_win)
        self.bind("<Button-4>", self._on_mousewheel_mac)
        self.bind("<Button-5>", self._on_mousewheel_mac)
        
    def _init_pool(self, size):
        """Creates physical widgets and stores them off-screen."""
        for _ in range(size):
            cell = GridCell(self, self.cell_width, self.cell_height)
            cell.is_hidden = True
            win_id = self.create_window(-1000, -1000, window=cell, anchor="nw", state="hidden")
            self.unused_pool.append((win_id, cell))

    def set_data(self, data):
        """Ingest new library data, reset scroll, and re-render."""
        self.data = data
        self.yview_moveto(0) # Reset scroll to top on filter/sort
        self._recalculate_layout()
        self._update_viewport()
        
    def _on_configure(self, event):
        new_cols = max(1, event.width // self.cell_width)
        
        if new_cols != self.cols or getattr(self, 'last_width', 0) != event.width:
            self.cols = new_cols
            self.last_width = event.width
            self._recalculate_layout()
            self._update_viewport()
            
    def _recalculate_layout(self):
        if not self.data:
            self.rows = 0
            self.configure(scrollregion=(0, 0, self.winfo_width(), 0))
            return
            
        self.rows = math.ceil(len(self.data) / self.cols)
        total_height = self.rows * self.cell_height
        self.configure(scrollregion=(0, 0, self.winfo_width(), total_height))

    # --- Scroll Passthroughs ---
    def yview(self, *args):
        super().yview(*args)
        self._update_viewport()
    def yview_scroll(self, *args):
        super().yview_scroll(*args)
        self._update_viewport()
    def yview_moveto(self, *args):
        super().yview_moveto(*args)
        self._update_viewport()
    def _on_mousewheel_win(self, event):
        self.yview_scroll(int(-1 * (event.delta / 120)), "units")
    def _on_mousewheel_mac(self, event):
        direction = -1 if event.num == 4 else 1
        self.yview_scroll(direction, "units")

    # --- THE RENDER LOOP ---
    def _update_viewport(self):
        """The heart of the virtual grid. Recycles cells based on visibility."""
        if not self.data or self.cols == 0:
            for idx in list(self.active_cells.keys()):
                win_id, cell = self.active_cells.pop(idx)
                if not cell.is_hidden:
                    self.itemconfig(win_id, state="hidden")
                    cell.is_hidden = True
                self.unused_pool.append((win_id, cell))
            return
            
        top_frac, bottom_frac = super().yview()
        total_height = self.rows * self.cell_height
        
        start_row = max(0, int((top_frac * total_height) // self.cell_height) - 1)
        end_row = min(self.rows - 1, int((bottom_frac * total_height) // self.cell_height) + 1)
        
        start_idx = start_row * self.cols
        end_idx = min(len(self.data) - 1, ((end_row + 1) * self.cols) - 1)
        
        visible_indices = set(range(start_idx, end_idx + 1))
        
        canvas_width = getattr(self, 'last_width', self.winfo_width())
        grid_width = self.cols * self.cell_width
        x_offset = max(0, (canvas_width - grid_width) // 2)
        
        # 1. PURGE: Remove cells that scrolled off-screen
        for idx in list(self.active_cells.keys()):
            if idx not in visible_indices:
                win_id, cell = self.active_cells.pop(idx)
                
                # PERFORMANCE FIX: Only hide if not already hidden
                if not cell.is_hidden:
                    self.itemconfig(win_id, state="hidden")
                    cell.is_hidden = True
                    
                self.unused_pool.append((win_id, cell))
                
        # 2. DRAW & REPOSITION: Assign cells or update existing ones
        for idx in visible_indices:
            is_new = False
            
            if idx not in self.active_cells:
                if not self.unused_pool:
                    self._init_pool(10)
                win_id, cell = self.unused_pool.pop()
                self.active_cells[idx] = (win_id, cell)
                is_new = True
                
            win_id, cell = self.active_cells[idx]
            
            if is_new:
                item = self.data[idx]
                asin = item.get("asin", f"local_{idx}")
                cover_path = item.get("cover_path")
                title = item.get("title", "Unknown")
                authors = item.get("authors", "Unknown")
                
                cover_size = (self.cell_width - 20, cell.cover_height - 10) 
                photo = self.image_cache.get_thumbnail(asin, cover_path, title, authors, size=cover_size)
                cell.update_data(idx, item, photo)
                
            row = idx // self.cols
            col = idx % self.cols
            x = x_offset + (col * self.cell_width)
            y = row * self.cell_height
            
            # --- THE PERFORMANCE FIX: Diff Checking ---
            # Only talk to Tkinter if coordinates actually changed
            if cell.last_x != x or cell.last_y != y:
                self.coords(win_id, x, y)
                cell.last_x = x
                cell.last_y = y
                
            # Only talk to Tkinter if visibility actually changed
            if cell.is_hidden:
                self.itemconfig(win_id, state="normal")
                cell.is_hidden = False