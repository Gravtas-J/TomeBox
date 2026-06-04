import os
import tkinter as tk
from tkinter import ttk

class LibraryPresenter:
    def __init__(self, app):
        self.app = app
        
        # Local UI State
        self.current_filtered_data = []
        self.current_sort_col = "Title"
        self.current_sort_descending = False
        
        # Debounce timer for bulk imports
        self._refresh_timer = None

    def toggle_library_view(self):
        # 1. Capture the currently selected item BEFORE we switch views
        target_asin = None
        if self.app.current_view_mode == "list":
            selected = self.app.library_tree.selection()
            if selected:
                vals = self.app.library_tree.item(selected[0], 'values')
                if len(vals) > 5: target_asin = vals[5]
        else:
            if getattr(self.app, '_selected_grid_item', None):
                vals = self.app._selected_grid_item.get('values', [])
                if len(vals) > 5: target_asin = vals[5]

        # 2. Swap the UI Components
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
            
        # 3. Force an immediate refresh to populate the new view
        self._do_refresh_library_ui()
        
        # 4. Snap the new view's focus to the memorized target
        if target_asin:
            self._focus_asin(target_asin)
        else:
            # Fallback: Jump to the very top if nothing was selected
            if self.app.current_view_mode == "list":
                self.app.library_tree.yview_moveto(0)
            else:
                self.app.grid_canvas.yview_moveto(0)

    def _focus_asin(self, target_asin):
        """Scrolls to and highlights a specific ASIN in the active view."""
        if self.app.current_view_mode == "list":
            children = self.app.library_tree.get_children()
            for child in children:
                vals = self.app.library_tree.item(child, 'values')
                if len(vals) > 5 and vals[5] == target_asin:
                    self.app.library_tree.selection_set(child)
                    self.app.library_tree.focus(child)
                    self.app.library_tree.see(child)
                    self.app.on_item_select()
                    break
        else:
            for idx, item in enumerate(self.app.grid_canvas.data):
                if item.get("asin") == target_asin:
                    # Math to jump exactly to that row
                    row = idx // self.app.grid_canvas.cols
                    fraction = row / max(1, self.app.grid_canvas.rows)
                    self.app.grid_canvas.yview_moveto(fraction)
                    
                    self.app._selected_grid_item = {'values': [
                        item.get("title", ""), item.get("authors", ""), item.get("narrator", ""),
                        item.get("series", ""), item.get("duration_str", ""), item.get("asin", ""),
                        item.get("status", ""), item.get("path", "")
                    ]}
                    self.app.on_item_select()
                    break

    def refresh_library_ui(self, *args):
        """Debounces the refresh so bulk imports don't freeze the UI."""
        if self._refresh_timer:
            self.app.root.after_cancel(self._refresh_timer)
        self._refresh_timer = self.app.root.after(150, self._do_refresh_library_ui)

    def _do_refresh_library_ui(self):
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
                title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row
                if sort_pref == "Title (A-Z)": return title.lower()
                elif sort_pref == "Author (A-Z)": return authors.lower()
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
                self.app.library_tree.tag_configure('finished', foreground='#777777')
                self.app.library_tree.tag_configure('started', foreground='#4a90e2')

                for row in filtered_rows:
                    title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row
                    
                    tags = ()
                    is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
                    is_missing_duration = duration_str in ["0h 0m", "N/A", ""]

                    read_state = "Unread"
                    if row_path and row_path in self.app.library_manager.local_library:
                        local_data = self.app.library_manager.local_library[row_path]
                        prog_sec = local_data.get("progress", {}).get(self.app.active_profile, 0)
                        dur_sec = (local_data.get("duration_min", 0) or 0) * 60
                        if dur_sec > 0:
                            if prog_sec / dur_sec >= 0.95: read_state = "Finished"
                            elif prog_sec > 0: read_state = "Started"

                    display_status = status
                    if read_state == "Finished":
                        display_status = f"✔ {status}"
                        tags = ('finished',)
                    elif read_state == "Started":
                        display_status = f"◐ {status}"
                        tags = ('started',)

                    if is_missing_file:
                        tags = ('error',)
                    elif is_missing_duration:
                        tags = ('warning',)

                    display_row = (title, authors, narrator, series_str, duration_str, asin, display_status, row_path, date_str)
                    self.app.library_tree.insert("", "end", values=display_row, tags=tags)

                if self.current_sort_col and self.current_sort_descending is not None:
                    self.sort_treeview(self.app.library_tree, self.current_sort_col, self.current_sort_descending)
            else:
                self.app.library_tree.grid_remove()
                self.app.h_scroll.grid_remove()
                self.app.grid_canvas.grid(row=0, column=0, sticky="nsew")
                
                # Format Data for Virtual Grid
                grid_data = []
                for row in filtered_rows:
                    title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row
                    grid_data.append({
                        "title": title,
                        "authors": authors,
                        "narrator": narrator,
                        "series": series_str,
                        "duration_str": duration_str,
                        "asin": asin,
                        "status": status,
                        "path": row_path,
                        "cover_path": os.path.join(self.app.covers_dir, f"{asin}.jpg"),
                        "date_str": date_str
                    })
                
                # Push data to engine (handles render instantly)
                self.app.grid_canvas.set_data(grid_data)
                
                # Force highlight styling onto the active cell
                self.app.on_item_select()
                
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

    def sort_treeview(self, tree, col, descending):
        data = [(tree.set(child, col), child) for child in tree.get_children('')]
        def sort_key(item):
            val = str(item[0])
            if col == "Duration":
                if "h " in val and "m" in val:
                    try:
                        parts = val.split("h ")
                        return int(parts[0]) * 60 + int(parts[1].replace("m", ""))
                    except ValueError: pass
                return -1
            if val in ["", "N/A", "Unknown"]: return "\x00"
            return val.lower()

        data.sort(key=sort_key, reverse=descending)
        
        self.current_sort_col = col
        self.current_sort_descending = descending
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        for c in tree["columns"]:
            tree.heading(c, text=c)
            
        arrow = " ▼" if descending else " ▲"
        tree.heading(col, text=f"{col}{arrow}", command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def handle_tree_double_click(self, event):
        import tkinter.font as tkfont
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        
        if region == "separator":
            column_id = tree.identify_column(event.x)
            if not column_id: return "break"
            col_name = tree.column(column_id, "id")
            
            font = tkfont.nametofont("TkTextFont")
            max_width = font.measure(tree.heading(column_id, "text")) + 30 
            
            for item in tree.get_children():
                try:
                    width = font.measure(str(tree.set(item, col_name))) + 30
                    if width > max_width: max_width = width
                except Exception: pass
            
            tree.column(column_id, width=min(max_width, 800))
            return "break"
        elif region in ("tree", "cell"):
            if hasattr(self.app, 'playback_presenter'):
                self.app.playback_presenter.master_play(event)

    def _on_global_scroll(self, event):
        widget = event.widget
        if isinstance(widget, str):
            try: widget = self.app.root.nametowidget(widget)
            except Exception: return

        target_canvas = None
        current = widget
        
        while current:
            if isinstance(current, (ttk.Treeview, tk.Text, tk.Listbox)): return
            if isinstance(current, tk.Canvas):
                if hasattr(self.app, 'grid_canvas') and current == self.app.grid_canvas:
                    if self.app.current_view_mode != "grid": return 
                    target_canvas = current
                    break
            try: current = current.master
            except AttributeError: break

        if not target_canvas: return

        num = getattr(event, 'num', 0)
        delta = getattr(event, 'delta', 0)

        if num == 4 or delta > 0: target_canvas.yview_scroll(-1, "units")
        elif num == 5 or delta < 0: target_canvas.yview_scroll(1, "units")

    def handle_keyboard_scroll(self, event):
        import tkinter as tk
        from tkinter import ttk
        focused = self.app.root.focus_get()
        
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)): return
        target = getattr(self.app, 'grid_canvas', None) if self.app.current_view_mode == "grid" else getattr(self.app, 'library_tree', None)
        if not target: return
            
        if event.keysym in ("Prior", "Next", "Home", "End") and self.app.current_view_mode == "list":
            children = target.get_children()
            if children:
                if event.keysym == "Home": new_idx = 0
                elif event.keysym == "End": new_idx = len(children) - 1
                else:
                    selected = target.selection()
                    current_idx = 0
                    if selected:
                        try: current_idx = children.index(selected[0])
                        except ValueError: pass
                    try: page_size = int(target.cget('height'))
                    except Exception: page_size = 15 
                        
                    if event.keysym == 'Next': new_idx = min(current_idx + page_size, len(children) - 1)
                    else: new_idx = max(current_idx - page_size, 0)
                        
                new_item = children[new_idx]
                target.selection_set(new_item)
                target.focus(new_item)
                target.see(new_item)
                target.event_generate("<<TreeviewSelect>>")
                return "break"
                
        if event.keysym == "Home": 
            target.yview_moveto(0.0)
            return "break"
        elif event.keysym == "End": 
            target.yview_moveto(1.0)
            return "break"
            
        if self.app.current_view_mode == "list" and focused == getattr(self.app, 'library_tree', None):
            self.app.root.after(10, self.app.on_item_select)
            return
            
        if event.keysym == "Up": target.yview_scroll(-1, "units")
        elif event.keysym == "Down": target.yview_scroll(1, "units")
        elif event.keysym == "Prior": target.yview_scroll(-1, "pages")
        elif event.keysym == "Next": target.yview_scroll(1, "pages")

    def handle_alpha_jump(self, event):
        import tkinter as tk
        from tkinter import ttk
        
        if not getattr(event, 'char', None): return
        char = event.char.lower()
        if not char or not char.isalnum(): return
            
        focused = self.app.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)): return
        if self.app.current_view_mode != "list": return
            
        tree = getattr(self.app, 'library_tree', None)
        if not tree: return
            
        sort_col = getattr(self, 'current_sort_col', 'Title')
        if not sort_col: sort_col = 'Title'
            
        children = tree.get_children()
        if not children: return
            
        selected = tree.selection()
        start_idx = 0
        if selected:
            try: start_idx = children.index(selected[0]) + 1
            except ValueError: pass
                
        search_sequence = list(children[start_idx:]) + list(children[:start_idx])
        
        for item in search_sequence:
            try: val = str(tree.set(item, sort_col)).lower()
            except tk.TclError:
                values = tree.item(item).get('values', [])
                val = str(values[0]).lower() if values else ""
                
            if val.startswith("the "): val = val[4:]
            elif val.startswith("a "): val = val[2:]
            elif val.startswith("an "): val = val[3:]
            
            if val.startswith(char):
                tree.selection_set(item)
                tree.focus(item)
                tree.see(item)
                self.app.root.after(10, self.app.on_item_select)
                return "break"