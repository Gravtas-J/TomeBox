import os
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

class LibraryPresenter:
    def __init__(self, app):
        self.app = app
        
        # Local UI State
        self.current_filtered_data = []
        self.cover_cache = {}
        self.current_sort_col = "Title"
        self.current_sort_descending = False
        self.last_canvas_width = 0
        self.resize_timer = None
        self.last_selected_card_frame = None

    def toggle_library_view(self):
        if self.app.current_view_mode == "list":
            self.app.current_view_mode = "grid"
            self.app.view_btn.config(text="List View")
            
            if hasattr(self.app, 'cover_toggle'):
                self.app.cover_toggle.pack(side=tk.RIGHT, padx=5)
            if hasattr(self.app, 'sort_label'):
                self.app.sort_label.pack(side=tk.LEFT, padx=(10, 5))
                self.app.sort_combo.pack(side=tk.LEFT)
            
            self.app.library_tree.grid_remove()
            self.app.h_scroll.grid_remove()
            
            if self.app.library_manager.cloud_items or self.app.library_manager.local_library:
                self.app.grid_canvas.grid(row=0, column=0, sticky="nsew")
            
            self.app.v_scroll.config(command=self.app.grid_canvas.yview)
            self.app.grid_canvas.config(yscrollcommand=self.app.v_scroll.set)
        else:
            self.app.current_view_mode = "list"
            self.app.view_btn.config(text="Grid View")
            
            if hasattr(self.app, 'cover_toggle'):
                self.app.cover_toggle.pack_forget()
            if hasattr(self.app, 'sort_label'):
                self.app.sort_label.pack_forget()
                self.app.sort_combo.pack_forget()
            
            self.app.grid_canvas.grid_remove()
            
            if self.app.library_manager.cloud_items or self.app.library_manager.local_library:
                self.app.library_tree.grid(row=0, column=0, sticky="nsew")
                self.app.h_scroll.grid(row=1, column=0, sticky="ew")
            
            self.app.v_scroll.config(command=self.app.library_tree.yview)
            self.app.library_tree.config(yscrollcommand=self.app.v_scroll.set)
            
        self.refresh_library_ui()

    def on_canvas_resize(self, event):
        if hasattr(self.app, 'grid_window_id'):
            self.app.grid_canvas.itemconfig(self.app.grid_window_id, width=event.width)
        if getattr(self, 'last_canvas_width', None) == event.width:
            return
        self.last_canvas_width = event.width
        if self.resize_timer is not None:
            self.app.root.after_cancel(self.resize_timer)
        self.resize_timer = self.app.root.after(200, self.draw_grid_view)

    def draw_grid_view(self):
        if self.app.current_view_mode != "grid": return
        
        for widget in self.app.grid_inner.winfo_children():
            widget.destroy()

        style = ttk.Style()
        default_bg = style.lookup("TFrame", "background") or "#f0f0f0"
        default_fg = style.lookup("TLabel", "foreground") or "#000000"
        select_bg = "#4a90e2" 

        self.app.grid_canvas.config(bg=default_bg)
        self.app.grid_inner.config(bg=default_bg)
        
        canvas_width = self.app.grid_canvas.winfo_width()
        cols = max(1, canvas_width // 190)

        for i in range(20): 
            self.app.grid_inner.columnconfigure(i, weight=0)
        for i in range(cols):
            self.app.grid_inner.columnconfigure(i, weight=1)
        
        for idx, row_data in enumerate(self.current_filtered_data):
            title, authors, series_str, duration_str, asin, status, row_path, date_str = row_data

            outer_card = tk.Frame(self.app.grid_inner, bg=default_bg)
            outer_card.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)

            card = tk.Frame(outer_card, bg=default_bg, width=170, height=240, bd=0, highlightthickness=0)
            card.pack_propagate(False) 
            card.pack(padx=2, pady=2) 
            
            is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
            is_missing_duration = duration_str in ["0h 0m", "N/A", ""]
            
            if is_missing_file:
                warning_lbl = tk.Label(card, text="⚠️ File Missing", bg="#ff4444", fg="#ffffff", font=("Segoe UI", 8, "bold"))
                warning_lbl.pack(side=tk.TOP, fill="x")
            elif is_missing_duration:
                warning_lbl = tk.Label(card, text="⚠️ No Duration", bg="#ffaa00", fg="#000000", font=("Segoe UI", 8, "bold"))
                warning_lbl.pack(side=tk.TOP, fill="x")

            img_obj = None
            if asin in self.cover_cache:
                img_obj = self.cover_cache[asin]
            else:
                cover_path = os.path.join(self.app.covers_dir, f"{asin}.jpg")
                if os.path.exists(cover_path):
                    try:
                        img = Image.open(cover_path)
                        img.thumbnail((150, 150))
                        img_obj = ImageTk.PhotoImage(img)
                        self.cover_cache[asin] = img_obj 
                    except: pass
                
            img_label = tk.Label(card, image=img_obj, text="No Cover" if not img_obj else "", bg=default_bg, fg=default_fg, bd=0, highlightthickness=0, takefocus=0, cursor="hand2")
            img_label.pack(pady=(5, 0))
            display_title = title[:45] + "..." if len(title) > 45 else title
            
            text_color = "#ff4444" if is_missing_file else ("#ffaa00" if is_missing_duration else default_fg)
            text_label = tk.Label(card, text=display_title, bg=default_bg, fg=text_color, font=("Segoe UI", 9), wraplength=150, justify="center", bd=0, highlightthickness=0, takefocus=0)
            text_label.pack(pady=(5, 0))
            
            def on_card_click(e, oc=outer_card, t=title, a=asin, s=status):
                if self.last_selected_card_frame is not None and self.last_selected_card_frame.winfo_exists():
                    self.last_selected_card_frame.config(bg=default_bg)
                
                oc.config(bg=select_bg)
                self.last_selected_card_frame = oc 
                self.app._selected_grid_item = {'values': [t, "", "", "", a, s, ""]} 
                self.app.on_item_select()
                
            def on_card_double_click(e, oc=outer_card, t=title, a=asin, s=status):
                on_card_click(e, oc, t, a, s)
                self.app.playback_presenter.master_play()

            outer_card.bind("<Button-1>", on_card_click)
            outer_card.bind("<Double-1>", on_card_double_click)
            card.bind("<Button-1>", on_card_click)
            card.bind("<Double-1>", on_card_double_click)
            img_label.bind("<Button-1>", on_card_click)
            img_label.bind("<Double-1>", on_card_double_click)
            text_label.bind("<Button-1>", on_card_click)
            text_label.bind("<Double-1>", on_card_double_click)

        self.app.grid_inner.update_idletasks()
        self.app.grid_canvas.configure(scrollregion=self.app.grid_canvas.bbox("all"))

    def sort_treeview(self, tree, col, descending):
        data = [(tree.set(child, col), child) for child in tree.get_children('')]
        
        def sort_key(item):
            val = item[0]
            if "h " in val and "m" in val:
                try:
                    parts = val.split("h ")
                    h = int(parts[0])
                    m = int(parts[1].replace("m", ""))
                    return h * 60 + m
                except ValueError:
                    pass

            if val == "N/A":
                return "0000-00-00"
                
            return val.lower()

        data.sort(key=sort_key, reverse=descending)
        self.current_sort_col = col
        self.current_sort_descending = descending
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        tree.heading(col, command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def _on_global_scroll(self, event):
        widget = event.widget
        if isinstance(widget, str):
            try:
                widget = self.app.root.nametowidget(widget)
            except Exception:
                return

        target_canvas = None
        current = widget
        
        while current:
            if isinstance(current, (ttk.Treeview, tk.Text, tk.Listbox)):
                return
                
            if isinstance(current, tk.Canvas):
                if hasattr(self.app, 'grid_canvas') and current == self.app.grid_canvas:
                    if self.app.current_view_mode != "grid":
                        return 
                target_canvas = current
                break
            
            try:
                current = current.master
            except AttributeError:
                break

        if not target_canvas:
            return

        num = getattr(event, 'num', 0)
        delta = getattr(event, 'delta', 0)

        if num == 4 or delta > 0:
            target_canvas.yview_scroll(-1, "units")
        elif num == 5 or delta < 0:
            target_canvas.yview_scroll(1, "units")

    def refresh_library_ui(self, *args):
        for row in self.app.library_tree.get_children():
            self.app.library_tree.delete(row)

        search_query = self.app.ui_state.search.get()
        current_filter = self.app.ui_state.filter.get()
        current_shelf = self.app.ui_state.shelf_filter.get()

        filtered_rows, shelf_list = self.app.library_manager.get_view_data(
            search_query=search_query,
            filter_type=current_filter,
            shelf_filter=current_shelf
        )

        if hasattr(self.app, 'sort_combo'):
            sort_pref = self.app.ui_state.sort.get()
            
            def get_sort_key(row):
                title, authors, series_str, duration_str, asin, status, row_path, date_str = row
                
                if sort_pref == "Title (A-Z)":
                    return title.lower()
                elif sort_pref == "Author (A-Z)":
                    return authors.lower()
                else: 
                    if row_path and row_path in self.app.library_manager.local_library:
                        return self.app.library_manager.local_library[row_path].get("date_added", 0)
                    return 0 
                    
            is_reverse = sort_pref == "Date Added (Newest)"
            filtered_rows.sort(key=get_sort_key, reverse=is_reverse)

        self.current_filtered_data = filtered_rows

        if hasattr(self.app, 'shelf_combo'):
            self.app.shelf_combo['values'] = shelf_list

        is_completely_empty = (not self.app.library_manager.cloud_items) and (not self.app.library_manager.local_library)

        if is_completely_empty:
            self.app.library_tree.grid_remove()
            self.app.h_scroll.grid_remove()
            self.app.grid_canvas.grid_remove()
            self.app.v_scroll.grid_remove()
            self.app.empty_state_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        else:
            self.app.empty_state_frame.grid_remove()
            self.app.v_scroll.grid(row=0, column=1, sticky="ns")
            
            if self.app.current_view_mode == "list":
                self.app.grid_canvas.grid_remove()
                self.app.library_tree.grid(row=0, column=0, sticky="nsew")
                self.app.h_scroll.grid(row=1, column=0, sticky="ew")

                self.app.library_tree.tag_configure('warning', foreground='#ffaa00') 
                self.app.library_tree.tag_configure('error', foreground='#ff4444')   

                for row in filtered_rows:
                    title, authors, series_str, duration_str, asin, status, row_path, date_str = row
                    
                    tags = ()
                    is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
                    is_missing_duration = duration_str in ["0h 0m", "N/A", ""]

                    if is_missing_file:
                        tags = ('error',)
                    elif is_missing_duration:
                        tags = ('warning',)

                    self.app.library_tree.insert("", "end", values=row, tags=tags)

                if self.current_sort_col and self.current_sort_descending is not None:
                    self.sort_treeview(self.app.library_tree, self.current_sort_col, self.current_sort_descending)
            else:
                self.app.library_tree.grid_remove()
                self.app.h_scroll.grid_remove()
                self.app.grid_canvas.grid(row=0, column=0, sticky="nsew")
                self.draw_grid_view()
                
        total_books = len(self.app.library_manager.local_library)
        formats = {}
        
        for path, data in self.app.library_manager.local_library.items():
            fmt = data.get("format", "UNKNOWN").upper()
            formats[fmt] = formats.get(fmt, 0) + 1
            
        self.app.ui_state.lib_count.set(f"Books found: {total_books}")
        
        if formats:
            tooltip_text = "\n".join([f"{f}: {c}" for f, c in sorted(formats.items())])
        else:
            tooltip_text = "Library is empty."
            
        if hasattr(self.app, 'lib_count_tooltip'):
            self.app.lib_count_tooltip.text = tooltip_text