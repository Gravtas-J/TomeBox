import tkinter as tk
import math

class VirtualGridView(tk.Canvas):
    """
    Phase 5 (Ultimate): Native Vector Rendering.
    Bypasses Tkinter's widget engine entirely for 144hz-adjacent scrolling performance.
    """
    def __init__(self, parent, image_cache, cell_width=200, cell_height=300, on_click_cb=None, on_double_click_cb=None, **kwargs):
        kwargs.setdefault('highlightthickness', 0)
        kwargs.setdefault('bg', '#1e1e1e')
        super().__init__(parent, **kwargs)
        
        self.image_cache = image_cache
        self.cell_width = cell_width
        self.cell_height = cell_height
        
        self.on_click_cb = on_click_cb
        self.on_double_click_cb = on_double_click_cb
        
        self.data = []
        self.cols = 1
        self.rows = 0
        self.x_offset = 0
        self.active_asins = set()
        
        # --- Range Selection Trackers ---
        self.last_clicked_index = None
        self.batch_selection = None

        # --- THE NATIVE CANVAS POOL ---
        self.active_cells = {}  # logical_index -> cell_dict
        self.unused_pool = []   # list of cell_dicts
        self._init_pool(50)     
        
        # Bindings
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-1>", self._on_double_click)
        self.bind("<MouseWheel>", self._on_mousewheel_win)
        self.bind("<Button-4>", self._on_mousewheel_mac)
        self.bind("<Button-5>", self._on_mousewheel_mac)
        
    def _init_pool(self, size):
        """Creates raw canvas vector shapes instead of expensive Tkinter frames."""
        for _ in range(size):
            cell = {
                # Outline is used for the active selection border
                "bg_id": self.create_rectangle(0, 0, 0, 0, fill="#1e1e1e", outline="", width=2, state="hidden"),
                "cover_id": self.create_image(0, 0, anchor="n", state="hidden"),
                "title_id": self.create_text(0, 0, anchor="nw", fill="white", font=("Segoe UI", 10, "bold"), width=self.cell_width - 20, state="hidden"),
                "author_id": self.create_text(0, 0, anchor="nw", fill="#95a5a6", font=("Segoe UI", 9), width=self.cell_width - 20, state="hidden"),
                
                "photo_ref": None, # Prevents Tkinter garbage collection
                "current_index": None,
                "current_asin": None,
                "last_x": -9999,
                "last_y": -9999,
                "is_hidden": True
            }
            self.unused_pool.append(cell)

    def set_data(self, data):
        self.data = data
        for cell in self.active_cells.values():
            cell["current_asin"] = None
        self.yview_moveto(0) 
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
        res = super().yview(*args)
        self._update_viewport()
        return res
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

    # --- Interaction Helpers ---
    def get_index_at(self, event_x, event_y):
        """Translates raw mouse clicks into logical grid indices."""
        if self.cols == 0 or not self.data: return None
        
        x = self.canvasx(event_x) - self.x_offset
        y = self.canvasy(event_y)
        
        col = int(x // self.cell_width)
        row = int(y // self.cell_height)
        
        if 0 <= col < self.cols:
            idx = (row * self.cols) + col
            if 0 <= idx < len(self.data):
                return idx
        return None

    def _on_click(self, event):
        idx = self.get_index_at(event.x, event.y)
        if idx is not None:
            # 0x0001 is the OS-level hex code for the Shift key being held down
            is_shift = bool(event.state & 0x0001)
            
            if is_shift and self.last_clicked_index is not None:
                # Calculate the range between the anchor click and this click
                start = min(self.last_clicked_index, idx)
                end = max(self.last_clicked_index, idx)
                # Store the batch so the main app can read it
                self.batch_selection = [self.data[i] for i in range(start, end + 1)]
            else:
                # Normal click: Set the new anchor and clear any old batches
                self.last_clicked_index = idx
                self.batch_selection = None
                
            if self.on_click_cb:
                self.on_click_cb(idx)

    def _on_double_click(self, event):
        idx = self.get_index_at(event.x, event.y)
        if idx is not None and self.on_double_click_cb:
            self.on_double_click_cb(idx)
            
    def set_active_asin(self, asin):
        """Legacy helper to keep single-clicks working smoothly."""
        self.set_active_asins({asin} if asin else set())

    def set_active_asins(self, asins):
        """Applies the blue selection border to multiple cells natively."""
        self.active_asins = set(asins)
        for cell in self.active_cells.values():
            idx = cell["current_index"]
            if idx is not None and idx < len(self.data):
                cell_asin = self.data[idx].get("asin")
                if cell_asin in self.active_asins:
                    self.itemconfig(cell["bg_id"], outline="#4a90e2")
                else:
                    self.itemconfig(cell["bg_id"], outline="")

    # --- THE VECTOR RENDER LOOP ---
    def _update_viewport(self):
        if not self.data or self.cols == 0:
            for idx in list(self.active_cells.keys()):
                cell = self.active_cells.pop(idx)
                if not cell["is_hidden"]:
                    self.itemconfig(cell["bg_id"], state="hidden")
                    self.itemconfig(cell["cover_id"], state="hidden")
                    self.itemconfig(cell["title_id"], state="hidden")
                    self.itemconfig(cell["author_id"], state="hidden")
                    cell["is_hidden"] = True
                self.unused_pool.append(cell)
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
        self.x_offset = max(0, (canvas_width - grid_width) // 2)
        
        # 1. PURGE
        for idx in list(self.active_cells.keys()):
            if idx not in visible_indices:
                cell = self.active_cells.pop(idx)
                if not cell["is_hidden"]:
                    self.itemconfig(cell["bg_id"], state="hidden")
                    self.itemconfig(cell["cover_id"], state="hidden")
                    self.itemconfig(cell["title_id"], state="hidden")
                    self.itemconfig(cell["author_id"], state="hidden")
                    cell["is_hidden"] = True
                self.unused_pool.append(cell)
                
        # 2. DRAW & VECTOR MATH
        for idx in visible_indices:
            is_new = False
            
            if idx not in self.active_cells:
                if not self.unused_pool:
                    self._init_pool(10)
                cell = self.unused_pool.pop()
                self.active_cells[idx] = cell
                is_new = True
                
            cell = self.active_cells[idx]
            
            item = self.data[idx]
            asin = item.get("asin", f"local_{idx}")
            
            # Inject new data only if recycled or swapped
            if is_new or cell.get("current_asin") != asin:
                cover_path = item.get("cover_path")
                
                title = item.get("title", "Unknown")
                # Expand truncation so it fills out two wrapped lines
                display_title = title[:60] + "..." if len(title) > 60 else title
                
                authors = item.get("authors", "Unknown")
                if isinstance(authors, list):
                    authors = ", ".join([a.get("name", "") for a in authors if isinstance(a, dict)])
                display_author = authors[:40] + "..." if len(authors) > 40 else authors
                
                # Shrink cover height by 5px to give text more breathing room
                cover_size = (self.cell_width - 20, self.cell_height - 70) 
                photo = self.image_cache.get_thumbnail(asin, cover_path, title, authors, size=cover_size)
                
                self.itemconfig(cell["cover_id"], image=photo)
                self.itemconfig(cell["title_id"], text=display_title)
                self.itemconfig(cell["author_id"], text=display_author)
                
                cell["photo_ref"] = photo
                cell["current_index"] = idx
                cell["current_asin"] = asin 
                
                if asin in self.active_asins:
                    self.itemconfig(cell["bg_id"], outline="#4a90e2")
                else:
                    self.itemconfig(cell["bg_id"], outline="")
                
            # Vector Translation Math
            row = idx // self.cols
            col = idx % self.cols
            x = self.x_offset + (col * self.cell_width)
            y = row * self.cell_height
            
            # THE PERFORMANCE FIX: Diff Check vectors before commanding Tkinter
            if cell["last_x"] != x or cell["last_y"] != y:
                self.coords(cell["bg_id"], x + 2, y + 2, x + self.cell_width - 2, y + self.cell_height - 2)
                self.coords(cell["cover_id"], x + (self.cell_width // 2), y + 10)
                
                # Adjust text anchors: Title gets more room, Author shifts to the bottom edge
                self.coords(cell["title_id"], x + 10, y + self.cell_height - 55)
                self.coords(cell["author_id"], x + 10, y + self.cell_height - 20)
                
                cell["last_x"] = x
                cell["last_y"] = y
                
            if cell["is_hidden"]:
                self.itemconfig(cell["bg_id"], state="normal")
                self.itemconfig(cell["cover_id"], state="normal")
                self.itemconfig(cell["title_id"], state="normal")
                self.itemconfig(cell["author_id"], state="normal")
                cell["is_hidden"] = False