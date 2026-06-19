import math
import tkinter as tk
from tkinter import font as tkfont


class VirtualGridView(tk.Canvas):
    """
    Phase 5 (Ultimate): Native Vector Rendering.
    Bypasses Tkinter's widget engine entirely for 144hz-adjacent scrolling performance.
    """

    def __init__(
        self,
        parent,
        image_cache,
        cell_width=200,
        cell_height=300,
        on_click_cb=None,
        on_double_click_cb=None,
        **kwargs,
    ):
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bg", "#1e1e1e")
        super().__init__(parent, **kwargs)

        self.image_cache = image_cache
        self.cell_width = cell_width
        self.cell_height = cell_height
        self._title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self._title_line_h = self._title_font.metrics("linespace")
        self._title_wrap = self.cell_width - 20

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
        self.unused_pool = []  # list of cell_dicts
        self._init_pool(50)

        # Bindings
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-1>", self._on_double_click)
        self.bind("<MouseWheel>", self._on_mousewheel_win)
        self.bind("<Button-4>", self._on_mousewheel_mac)
        self.bind("<Button-5>", self._on_mousewheel_mac)

    def _title_line_count(self, text):
        """How many lines `text` wraps to at the title width."""
        if not text:
            return 1
        lines, cur = 1, ""
        for word in text.split():
            trial = word if not cur else f"{cur} {word}"
            if self._title_font.measure(trial) <= self._title_wrap:
                cur = trial
            else:
                lines += 1
                cur = word
        return lines

    def _init_pool(self, size):
        """Creates raw canvas vector shapes instead of expensive Tkinter frames."""
        for _ in range(size):
            cell = {
                # Outline is used for the active selection border
                "bg_id": self.create_rectangle(
                    0, 0, 0, 0, fill="#1e1e1e", outline="", width=2, state="hidden"
                ),
                "cover_id": self.create_image(0, 0, anchor="n", state="hidden"),
                "title_id": self.create_text(
                    0,
                    0,
                    anchor="nw",
                    fill="white",
                    font=("Segoe UI", 10, "bold"),
                    width=self.cell_width - 20,
                    state="hidden",
                ),
                "badge_bg_id": self.create_oval(
                    0, 0, 0, 0, fill="", outline="#1e1e1e", width=2, state="hidden"
                ),
                "badge_text_id": self.create_text(
                    0, 0, text="", fill="white",
                    font=("Segoe UI", 10, "bold"), anchor="center", state="hidden",
                ),
                "author_id": self.create_text(
                    0,
                    0,
                    anchor="nw",
                    fill="#95a5a6",
                    font=("Segoe UI", 9),
                    width=self.cell_width - 20,
                    state="hidden",
                ),
                "photo_ref": None,  # Prevents Tkinter garbage collection
                "current_index": None,
                "current_asin": None,
                "current_read_state": None,
                "badge_visible": False,
                "last_x": -9999,
                "last_y": -9999,
                "is_hidden": True,
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
        if new_cols != self.cols or getattr(self, "last_width", 0) != event.width:
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
        if self.cols == 0 or not self.data:
            return None

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
                item = self.data[idx]
                raw_asin = item.get("asin", "")
                path = item.get("path", "")

                # Rebuild the fingerprint
                fp = raw_asin if raw_asin and raw_asin != "Unknown" else path
                if not fp:
                    fp = f"fallback_{idx}"

                if fp in self.active_asins:
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
                    self.itemconfig(cell["badge_bg_id"], state="hidden")
                    self.itemconfig(cell["badge_text_id"], state="hidden")
                    cell["is_hidden"] = True
                self.unused_pool.append(cell)
            return

        top_frac, bottom_frac = super().yview()
        total_height = self.rows * self.cell_height

        start_row = max(0, int((top_frac * total_height) // self.cell_height) - 1)
        end_row = min(
            self.rows - 1, int((bottom_frac * total_height) // self.cell_height) + 1
        )

        start_idx = start_row * self.cols
        end_idx = min(len(self.data) - 1, ((end_row + 1) * self.cols) - 1)

        visible_indices = set(range(start_idx, end_idx + 1))

        canvas_width = getattr(self, "last_width", self.winfo_width())
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
            raw_asin = item.get("asin", "")
            path = item.get("path", "")

            fingerprint = raw_asin if raw_asin and raw_asin != "Unknown" else path
            if not fingerprint:
                fingerprint = f"fallback_{idx}"

            # Inject new data only if recycled or swapped based on the fingerprint
            content_changed = False
            read_state = item.get("read_state", "Unread")
            if (
                is_new
                or cell.get("current_fingerprint") != fingerprint
                or cell.get("current_read_state") != read_state
            ):
                cover_path = item.get("cover_path")
                content_changed = True
                title = item.get("title", "Unknown")
                display_title = title[:60] + "..." if len(title) > 60 else title

                authors = item.get("authors", "Unknown")
                if isinstance(authors, list):
                    authors = ", ".join(
                        [a.get("name", "") for a in authors if isinstance(a, dict)]
                    )
                display_author = authors[:40] + "..." if len(authors) > 40 else authors

                cover_size = (self.cell_width - 20, self.cell_height - 90)

                # Pass the unique fingerprint to the image cache, NOT the raw ASIN
                photo = self.image_cache.get_thumbnail(
                    fingerprint, cover_path, title, authors, size=cover_size
                )

                self.itemconfig(cell["cover_id"], image=photo)
                self.itemconfig(cell["title_id"], text=display_title)
                cell["title_lines"] = self._title_line_count(display_title)
                self.itemconfig(cell["author_id"], text=display_author)

                cell["photo_ref"] = photo
                cell["current_index"] = idx
                cell["current_asin"] = raw_asin
                cell["current_fingerprint"] = fingerprint

                cell["current_read_state"] = read_state
                if read_state == "Finished":
                    self.itemconfig(cell["badge_bg_id"], fill="#2ecc71")
                    self.itemconfig(cell["badge_text_id"], text="✔")
                    cell["badge_visible"] = True
                elif read_state == "Started":
                    self.itemconfig(cell["badge_bg_id"], fill="#4a90e2")
                    self.itemconfig(cell["badge_text_id"], text="◐")
                    cell["badge_visible"] = True
                else:
                    cell["badge_visible"] = False
                badge_state = "normal" if (cell["badge_visible"] and not cell["is_hidden"]) else "hidden"
                self.itemconfig(cell["badge_bg_id"], state=badge_state)
                self.itemconfig(cell["badge_text_id"], state=badge_state)

                # Selection highlight relies on raw_asin
                if fingerprint in self.active_asins:
                    self.itemconfig(cell["bg_id"], outline="#4a90e2")
                else:
                    self.itemconfig(cell["bg_id"], outline="")

            # Vector Translation Math
            row = idx // self.cols
            col = idx % self.cols
            x = self.x_offset + (col * self.cell_width)
            y = row * self.cell_height

            # THE PERFORMANCE FIX: Diff Check vectors before commanding Tkinter
            if cell["last_x"] != x or cell["last_y"] != y or content_changed:
                self.coords(cell["bg_id"], x + 2, y + 2, x + self.cell_width - 2, y + self.cell_height - 2)
                self.coords(cell["cover_id"], x + (self.cell_width // 2), y + 10)

                self.coords(cell["title_id"], x + 10, y + self.cell_height - 75)
                # author hugs the title's real bottom instead of a fixed offset
                title_top = y + self.cell_height - 75
                self.coords(cell["title_id"], x + 10, title_top)
                # bbox under-reports while hidden, so place by measured line count
                author_y = title_top + cell.get("title_lines", 1) * self._title_line_h + 4
                self.coords(cell["author_id"], x + 10, author_y)

                r = 11
                cx = x + self.cell_width - 25
                cy = y + 25
                self.coords(cell["badge_bg_id"], cx - r, cy - r, cx + r, cy + r)
                self.coords(cell["badge_text_id"], cx, cy)
                cell["last_x"] = x
                cell["last_y"] = y

            if cell["is_hidden"]:
                self.itemconfig(cell["bg_id"], state="normal")
                self.itemconfig(cell["cover_id"], state="normal")
                self.itemconfig(cell["title_id"], state="normal")
                self.itemconfig(cell["author_id"], state="normal")
                if cell["badge_visible"]:
                    self.itemconfig(cell["badge_bg_id"], state="normal")
                    self.itemconfig(cell["badge_text_id"], state="normal")
                cell["is_hidden"] = False