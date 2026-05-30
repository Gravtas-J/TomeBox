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
            title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row_data

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

            read_state = "Unread"
            if row_path and row_path in self.app.library_manager.local_library:
                local_data = self.app.library_manager.local_library[row_path]
                prog_sec = local_data.get("progress", {}).get(self.app.active_profile, 0)
                dur_sec = (local_data.get("duration_min", 0) or 0) * 60
                if dur_sec > 0:
                    if prog_sec / dur_sec >= 0.95: read_state = "Finished"
                    elif prog_sec > 0: read_state = "Started"

            display_title = title[:45] + "..." if len(title) > 45 else title
            if read_state == "Finished":
                display_title = "✔ " + display_title
            elif read_state == "Started":
                display_title = "◐ " + display_title

            text_color = "#ff4444" if is_missing_file else ("#ffaa00" if is_missing_duration else default_fg)
            if not is_missing_file and not is_missing_duration:
                if read_state == "Finished": text_color = "#777777"
                elif read_state == "Started": text_color = "#4a90e2"

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
            
            
            text_color = "#ff4444" if is_missing_file else ("#ffaa00" if is_missing_duration else default_fg)
            text_label = tk.Label(card, text=display_title, bg=default_bg, fg=text_color, font=("Segoe UI", 9), wraplength=150, justify="center", bd=0, highlightthickness=0, takefocus=0)
            text_label.pack(pady=(5, 0))
            
            def on_card_click(e, oc=outer_card, t=title, a=asin, s=status, p=row_path):
                if self.last_selected_card_frame is not None and self.last_selected_card_frame.winfo_exists():
                    self.last_selected_card_frame.config(bg=default_bg)
                
                oc.config(bg=select_bg)
                self.last_selected_card_frame = oc 
                self.app._selected_grid_item = {'values': [t, "", "", "", "", a, s, p, ""]}
                self.app.on_item_select()
                
            def on_card_double_click(e, oc=outer_card, t=title, a=asin, s=status, p=row_path):
                on_card_click(e, oc, t, a, s, p)
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
            val = str(item[0])
            
            # Duration must strictly return integers to prevent sort crashing
            if col == "Duration":
                if "h " in val and "m" in val:
                    try:
                        parts = val.split("h ")
                        h = int(parts[0])
                        m = int(parts[1].replace("m", ""))
                        return h * 60 + m
                    except ValueError:
                        pass
                # Missing/errored durations evaluate to -1
                return -1

            # All other columns must strictly return strings
            if val in ["", "N/A", "Unknown"]:
                # The null character forces empty/errored paths to the absolute top
                return "\x00"
                
            return val.lower()

        # Safely sort without type-mixing crashes
        data.sort(key=sort_key, reverse=descending)
        
        self.current_sort_col = col
        self.current_sort_descending = descending
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        # 1. Reset all column headers to their clean base names
        for c in tree["columns"]:
            tree.heading(c, text=c)
            
        # 2. Append the directional arrow to the active column
        arrow = " ▼" if descending else " ▲"
        
        # 3. Apply the new text and flip the command for the next click
        tree.heading(col, text=f"{col}{arrow}", command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def handle_tree_double_click(self, event):
        import tkinter.font as tkfont
        
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        
        if region == "separator":
            # Identify which column separator was clicked
            column_id = tree.identify_column(event.x)
            if not column_id:
                return "break"
                
            # Get the logical column name (protects against displaycolumns reordering)
            col_name = tree.column(column_id, "id")
            
            # Initialize font measurement
            font = tkfont.nametofont("TkTextFont")
            
            # Start by measuring the header text
            header_text = tree.heading(column_id, "text")
            max_width = font.measure(header_text) + 30 # +30px for padding and sort arrows
            
            # Scan all visible rows for the longest string
            for item in tree.get_children():
                try:
                    val = str(tree.set(item, col_name))
                    width = font.measure(val) + 30
                    if width > max_width:
                        max_width = width
                except Exception:
                    pass
            
            # Cap the max width to prevent runaway sizing (e.g., long file paths)
            max_width = min(max_width, 800)
            
            # Apply the calculated width
            tree.column(column_id, width=max_width)
            
            # Stop the event from propagating to prevent standard Tkinter drag glitches
            return "break"
            
        elif region in ("tree", "cell"):
            # Route to normal audio playback
            if hasattr(self.app, 'playback_presenter'):
                self.app.playback_presenter.master_play(event)

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
                title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row
                
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
                self.app.library_tree.tag_configure('finished', foreground='#777777')
                self.app.library_tree.tag_configure('started', foreground='#4a90e2')

                for row in filtered_rows:
                    title, authors, narrator, series_str, duration_str, asin, status, row_path, date_str = row
                    
                    tags = ()
                    is_missing_file = "Downloaded" in status and row_path and "PLAYLIST" not in status and not os.path.exists(row_path)
                    is_missing_duration = duration_str in ["0h 0m", "N/A", ""]

                    # Calculate progress
                    read_state = "Unread"
                    if row_path and row_path in self.app.library_manager.local_library:
                        local_data = self.app.library_manager.local_library[row_path]
                        prog_sec = local_data.get("progress", {}).get(self.app.active_profile, 0)
                        dur_sec = (local_data.get("duration_min", 0) or 0) * 60
                        if dur_sec > 0:
                            if prog_sec / dur_sec >= 0.95: read_state = "Finished"
                            elif prog_sec > 0: read_state = "Started"

                    # Format the status string visually (e.g. "✔ Finished (M4B)")
                    # Format the status string visually 
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
    
    def handle_keyboard_scroll(self, event):
        import tkinter as tk
        from tkinter import ttk
        focused = self.app.root.focus_get()
        
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return
            
        target = getattr(self.app, 'grid_canvas', None) if self.app.current_view_mode == "grid" else getattr(self.app, 'library_tree', None)
        if not target: 
            return
            
        # 1. Intercept Navigation Keys for List View to snap the selection focus
        if event.keysym in ("Prior", "Next", "Home", "End") and self.app.current_view_mode == "list":
            children = target.get_children()
            if children:
                if event.keysym == "Home":
                    new_idx = 0
                elif event.keysym == "End":
                    new_idx = len(children) - 1
                else:
                    selected = target.selection()
                    current_idx = 0
                    if selected:
                        try:
                            current_idx = children.index(selected[0])
                        except ValueError:
                            pass
                    
                    try:
                        page_size = int(target.cget('height'))
                    except Exception:
                        page_size = 15 
                        
                    if event.keysym == 'Next':
                        new_idx = min(current_idx + page_size, len(children) - 1)
                    else:  # Prior
                        new_idx = max(current_idx - page_size, 0)
                        
                new_item = children[new_idx]
                target.selection_set(new_item)
                target.focus(new_item)
                target.see(new_item)
                target.event_generate("<<TreeviewSelect>>")
                return "break"
                
        # 2. Intercept Home and End for Grid View to FORCE a visual scroll
        if event.keysym == "Home": 
            target.yview_moveto(0.0)
            return "break"
        elif event.keysym == "End": 
            target.yview_moveto(1.0)
            return "break"
            
        # 3. If the user is navigating the List View natively with arrows, let Treeview 
        # handle it so the selection changes, but trigger our sidebar update.
        if self.app.current_view_mode == "list" and focused == getattr(self.app, 'library_tree', None):
            self.app.root.after(10, self.app.on_item_select)
            return
            
        # 4. Otherwise, manually scroll the Grid Canvas or unfocused List
        if event.keysym == "Up": target.yview_scroll(-1, "units")
        elif event.keysym == "Down": target.yview_scroll(1, "units")
        elif event.keysym == "Prior": target.yview_scroll(-1, "pages")
        elif event.keysym == "Next": target.yview_scroll(1, "pages")

    def handle_alpha_jump(self, event):
        import tkinter as tk
        from tkinter import ttk
        
        # Ensure it's an actual printable character
        if not getattr(event, 'char', None):
            return
            
        char = event.char.lower()
        if not char or not char.isalnum():
            return
            
        # Ignore if typing in a text field
        focused = self.app.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return
            
        if self.app.current_view_mode != "list":
            return
            
        tree = getattr(self.app, 'library_tree', None)
        if not tree:
            return
            
        # Default to Title if current_sort_col isn't set yet
        sort_col = getattr(self, 'current_sort_col', 'Title')
        if not sort_col:
            sort_col = 'Title'
            
        children = tree.get_children()
        if not children:
            return
            
        # Start searching from the item *after* the currently selected one to allow cycling
        selected = tree.selection()
        start_idx = 0
        if selected:
            try:
                start_idx = children.index(selected[0]) + 1
            except ValueError:
                pass
                
        # Create a search sequence that wraps around to the beginning
        search_sequence = list(children[start_idx:]) + list(children[:start_idx])
        
        for item in search_sequence:
            try:
                val = str(tree.set(item, sort_col)).lower()
            except tk.TclError:
                # Fallback if sort_col isn't a valid column ID
                values = tree.item(item).get('values', [])
                val = str(values[0]).lower() if values else ""
                
            # Ignore articles for intuitive searching
            if val.startswith("the "): val = val[4:]
            elif val.startswith("a "): val = val[2:]
            elif val.startswith("an "): val = val[3:]
            
            if val.startswith(char):
                tree.selection_set(item)
                tree.focus(item)
                tree.see(item)
                self.app.root.after(10, self.app.on_item_select)
                return "break"