import tkinter as tk
from tkinter import ttk, messagebox
import socket
import qrcode
from PIL import Image, ImageTk
import os

def open_error_log_window(app):
    """Opens the Error Log popup window."""
    if not app.failed_tasks: return
    
    win = tk.Toplevel(app.root)
    win.title("Error Log & Recovery")
    win.geometry("800x400")
    win.transient(app.root)
    
    # Apply theme background
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#1e1e1e"
    win.configure(bg=bg_color)
    
    tree_frame = ttk.Frame(win)
    tree_frame.pack(fill="both", expand=True, padx=10, pady=10)
    
    tree = ttk.Treeview(tree_frame, columns=("File", "Action", "Error"), show="headings")
    tree.heading("File", text="File")
    tree.heading("Action", text="Action")
    tree.heading("Error", text="Error Reason")
    tree.column("File", width=250, stretch=tk.NO)
    tree.column("Action", width=100, stretch=tk.NO)
    tree.column("Error", width=400, stretch=tk.YES)
    
    v_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=v_scroll.set)
    
    tree.pack(side=tk.LEFT, fill="both", expand=True)
    v_scroll.pack(side=tk.RIGHT, fill="y")
    
    # Populate the list using the app's failed_tasks array
    for idx, task in enumerate(app.failed_tasks):
        filename = os.path.basename(task["path"])
        tree.insert("", "end", iid=str(idx), values=(filename, task["action"], task["error"]))
        
    btn_frame = ttk.Frame(win)
    btn_frame.pack(fill="x", padx=10, pady=(0, 10))
    
    def retry_selected():
        selected = tree.selection()
        if not selected: return
        
        paths_to_retry = []
        
        # Remove from list in reverse order so indices don't shift
        for iid in sorted(selected, key=int, reverse=True):
            idx = int(iid)
            task = app.failed_tasks.pop(idx)
            paths_to_retry.append(task["path"])
            tree.delete(iid)
        
        # Update button count on the main window
        app.error_btn_var.set(f"Errors ({len(app.failed_tasks)})")
        if not app.failed_tasks:
            app.error_btn.config(state=tk.DISABLED)
            win.destroy()
            
        # Seamlessly push them back into the conversion queue!
        if paths_to_retry:
            app.conversion_manager.convert_batch(paths_to_retry)
            
    ttk.Button(btn_frame, text="Retry Selected", command=retry_selected).pack(side=tk.LEFT, padx=5)
    
    def clear_all():
        app.failed_tasks.clear()
        app.error_btn_var.set("Errors (0)")
        app.error_btn.config(state=tk.DISABLED)
        win.destroy()
        
    ttk.Button(btn_frame, text="Clear All", command=clear_all).pack(side=tk.RIGHT, padx=5)

def open_audio_device_settings(app):
        """Queries the OS for audio hardware and displays a selection menu."""
        try:
            import sounddevice as sd
            devices = []
            # Query the OS and filter out inputs (microphones)
            for d in sd.query_devices():
                if d['max_output_channels'] > 0 and d['name'] not in devices:
                    devices.append(d['name'])
        except ImportError:
            messagebox.showerror("Dependency Missing", "Please run 'pip install sounddevice' to enable hardware scanning.")
            return
        except Exception as e:
            messagebox.showerror("Device Error", f"Could not query audio devices:\n{e}")
            return

        devices.insert(0, "System Default")
        current_device = app.settings.get("audio_device", "System Default")

        popup = tk.Toplevel(app.root)
        popup.title("Playback Device Settings")
        popup.geometry("450x150")
        popup.transient(app.root)
        
        # Theme matching
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        popup.configure(bg=bg_color)
        
        ttk.Label(popup, text="Select Hardware Output:").pack(pady=(20, 5))
        
        device_var = tk.StringVar(value=current_device)
        combo = ttk.Combobox(popup, textvariable=device_var, values=devices, state="readonly", width=50)
        combo.pack(pady=5)
        
        def apply():
            selected = device_var.get()
            app.settings["audio_device"] = selected
            app.db.save_settings(app.settings)
            
            app.playback.set_audio_device(selected)
            
            # If audio is actively playing, bounce the stream so the change takes effect immediately
            if app.is_playing:
                app.pause_audio()
                app.is_paused = False
                app.resume_playback()
                
            popup.destroy()
            
        ttk.Button(popup, text="Apply", command=apply).pack(pady=(10, 0))

def open_auth_window(app):
    if getattr(app, 'auth_window', None) and app.auth_window.winfo_exists():
        app.auth_window.lift()
        app.auth_window.focus_set()
        return

    app.auth_window = tk.Toplevel(app.root)
    app.auth_window.title("Authentication & Profiles")
    app.auth_window.geometry("380x320")
    app.auth_window.resizable(False, False)
    app.auth_window.transient(app.root) 
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    app.auth_window.configure(bg=bg_color)
    
    main_frame = ttk.Frame(app.auth_window, padding=10)
    main_frame.pack(fill="both", expand=True)

    auth_frame = ttk.LabelFrame(main_frame, text="Audible Authentication", padding=10)
    auth_frame.pack(fill="x", pady=5)

    reg_frame = ttk.Frame(auth_frame)
    reg_frame.pack(fill="x", pady=5)
    ttk.Label(reg_frame, text="Region:").pack(side=tk.LEFT, padx=5)
    
    reg_combo = ttk.Combobox(reg_frame, textvariable=app.locale, values=["us", "uk", "au", "ca", "de", "fr", "jp"], state="readonly", width=5)
    reg_combo.pack(side=tk.LEFT)

    btn_frame = ttk.Frame(auth_frame)
    btn_frame.pack(fill="x", pady=5)
    app.browser_login_btn = ttk.Button(btn_frame, text="Browser Login", command=app.start_browser_login_thread)
    app.browser_login_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)
    app.auth_file_btn = ttk.Button(btn_frame, text="Load .json", command=app.load_auth_file_prompt)
    app.auth_file_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)

    profile_frame = ttk.Frame(auth_frame)
    profile_frame.pack(fill="x", pady=5)
    
    ttk.Label(profile_frame, text="Profile:").pack(side=tk.LEFT, padx=5)
    
    app.profiles_list = getattr(app, 'profiles_list', app.settings.get("profiles", ["Main"]))
    app.profile_combo = ttk.Combobox(profile_frame, values=app.profiles_list, state="readonly", width=15)
    app.profile_combo.set(app.active_profile)
    app.profile_combo.pack(side=tk.LEFT, padx=5)
    
    ttk.Button(profile_frame, text="New", width=5, command=app.add_new_profile).pack(side=tk.LEFT)
    app.profile_combo.bind("<<ComboboxSelected>>", app.switch_profile)

    bytes_frame = ttk.LabelFrame(main_frame, text="Decryption Bytes", padding=10)
    bytes_frame.pack(fill="x", pady=10)
    ttk.Entry(bytes_frame, textvariable=app.auth_bytes, justify="center").pack(fill="x", pady=5)
    
    ttk.Button(main_frame, text="Close", command=app.auth_window.destroy).pack(pady=(10, 0))

def open_chapter_window(app):
    from tkinter import messagebox
    if not hasattr(app, 'chapters') or not app.chapters:
        messagebox.showinfo("Chapters", "No chapter data available. Please load an audiobook first.")
        return

    if getattr(app, 'chapter_win', None) and app.chapter_win.winfo_exists():
        app.chapter_win.lift()
        app.chapter_win.focus_set()
        return

    app.chapter_win = tk.Toplevel(app.root)
    app.chapter_win.title("Select Chapter")
    app.chapter_win.geometry("450x500")
    app.chapter_win.transient(app.root)
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    app.chapter_win.configure(bg=bg_color)
    
    main_frame = ttk.Frame(app.chapter_win, padding=10)
    main_frame.pack(fill="both", expand=True)
    
    ttk.Label(main_frame, text="Table of Contents", font=("Segoe UI", 14, "bold")).pack(pady=(0, 10))

    columns = ("Index", "Title", "Start Time")
    tree = ttk.Treeview(main_frame, columns=columns, show="headings", selectmode="browse")
    
    tree.heading("Index", text="#")
    tree.column("Index", width=40, anchor="center")
    
    tree.heading("Title", text="Chapter Title")
    tree.column("Title", width=250, anchor="w")
    
    tree.heading("Start Time", text="Start Time")
    tree.column("Start Time", width=100, anchor="center")

    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    
    tree.pack(side=tk.LEFT, fill="both", expand=True)
    scrollbar.pack(side=tk.RIGHT, fill="y")

    for i, chap in enumerate(app.chapters):
        start_sec = float(chap.get('start_time', 0))
        h, m = divmod(start_sec, 3600)
        m, s = divmod(m, 60)
        time_str = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
        title = chap.get('tags', {}).get('title', f"Chapter {i+1}")
        tree.insert("", "end", values=(i+1, title, time_str))

    tree.bind("<Double-1>", lambda e: app.on_chapter_select(tree))

def open_sleep_menu(app):
    if getattr(app, 'sleep_menu_popup', None) and app.sleep_menu_popup.winfo_exists():
        app.sleep_menu_popup.destroy()
        return

    app.sleep_menu_popup = tk.Toplevel(app.root)
    app.sleep_menu_popup.wm_overrideredirect(True)
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    app.sleep_menu_popup.config(bg=bg_color, highlightbackground="#4a90e2", highlightthickness=1)

    x = app.timer_btn.winfo_rootx()
    y = app.timer_btn.winfo_rooty() + app.timer_btn.winfo_height() + 2
    app.sleep_menu_popup.geometry(f"+{x}+{y}")

    inner = tk.Frame(app.sleep_menu_popup, bg=bg_color, padx=5, pady=5)
    inner.pack(fill="both", expand=True)

    ttk.Button(inner, text="Turn Off Timer", command=lambda: app.set_sleep_timer("off")).pack(fill="x", pady=(0,5))
    ttk.Button(inner, text="15 Minutes", command=lambda: app.set_sleep_timer("time", 15)).pack(fill="x", pady=1)
    ttk.Button(inner, text="30 Minutes", command=lambda: app.set_sleep_timer("time", 30)).pack(fill="x", pady=1)
    ttk.Button(inner, text="End of Chapter", command=lambda: app.set_sleep_timer("chapters", 1)).pack(fill="x", pady=1)

    ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=5)

    custom_time_frame = ttk.Frame(inner)
    custom_time_frame.pack(fill="x", pady=2)
    ttk.Label(custom_time_frame, text="Mins:").pack(side=tk.LEFT)
    min_var = tk.StringVar(value="60")
    ttk.Entry(custom_time_frame, textvariable=min_var, width=5).pack(side=tk.LEFT, padx=(5, 2))
    ttk.Button(custom_time_frame, text="Set", width=4, command=lambda: app.set_sleep_timer("time", min_var.get())).pack(side=tk.LEFT)

    custom_chap_frame = ttk.Frame(inner)
    custom_chap_frame.pack(fill="x", pady=2)
    ttk.Label(custom_chap_frame, text="Chaps:").pack(side=tk.LEFT)
    chap_var = tk.StringVar(value="2")
    ttk.Entry(custom_chap_frame, textvariable=chap_var, width=5).pack(side=tk.LEFT, padx=(1, 2))
    ttk.Button(custom_chap_frame, text="Set", width=4, command=lambda: app.set_sleep_timer("chapters", chap_var.get())).pack(side=tk.LEFT)

    app.sleep_menu_popup.update_idletasks()
    popup_height = app.sleep_menu_popup.winfo_reqheight()

    x = app.timer_btn.winfo_rootx()
    y = app.timer_btn.winfo_rooty()
    app.sleep_menu_popup.geometry(f"+{x}+{y - popup_height - 2}")

    def on_focus_out(event):
        if app.sleep_menu_popup.focus_get() is None or not str(app.sleep_menu_popup.focus_get()).startswith(str(app.sleep_menu_popup)):
            app.sleep_menu_popup.destroy()
            
    app.sleep_menu_popup.bind("<FocusOut>", on_focus_out)
    app.sleep_menu_popup.focus_set()

def open_achievements_window(app):
    if getattr(app, 'ach_window', None) and app.ach_window.winfo_exists():
        app.ach_window.lift()
        app.ach_window.focus_set()
        return

    app.ach_window = tk.Toplevel(app.root)
    app.ach_window.title("My Achievements")
    app.ach_window.geometry("450x600")
    app.ach_window.transient(app.root)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    fg_color = style.lookup("TLabel", "foreground") or "#000000"
    app.ach_window.configure(bg=bg_color)
    
    main_frame = ttk.Frame(app.ach_window, padding=10)
    main_frame.pack(fill="both", expand=True)
    
    ttk.Label(main_frame, text="TomeBox Achievements", font=("Segoe UI", 16, "bold")).pack(pady=(0, 15))

    canvas = tk.Canvas(main_frame, bg=bg_color, highlightthickness=0)
    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg=bg_color)
    
    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
    
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    stats = app.settings.get("stats", {})
    unlocked = stats.get("unlocked_achievements", [])

    for ach_id, data in getattr(app, 'achievements', {}).items():
        is_unlocked = ach_id in unlocked
        border_color = "#4a90e2" if is_unlocked else "#555555"
        status_icon = "🏆" if is_unlocked else "🔒"
        text_color = fg_color if is_unlocked else "#888888"
        
        card = tk.Frame(scrollable_frame, bg=bg_color, highlightbackground=border_color, highlightthickness=1)
        card.pack(fill="x", pady=5, padx=5)
        
        header_frame = tk.Frame(card, bg=bg_color)
        header_frame.pack(fill="x", padx=10, pady=(10, 0))
        
        tk.Label(header_frame, text=status_icon, font=("Segoe UI", 16), bg=bg_color).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(header_frame, text=data["title"], font=("Segoe UI", 12, "bold"), fg=text_color, bg=bg_color).pack(side=tk.LEFT)
        tk.Label(card, text=data["desc"], font=("Segoe UI", 9), fg=text_color, bg=bg_color).pack(anchor="w", padx=45, pady=(0, 5))

        current_val = stats.get(data["type"], 0)
        threshold = data["threshold"]
        
        if data["type"] == "seconds_listened":
            curr_h = int(current_val // 3600)
            thresh_h = int(threshold // 3600)
            prog_text = f"Progress: {curr_h}h / {thresh_h}h"
            percent = min(100, (current_val / threshold) * 100) if threshold > 0 else 0
        else:
            prog_text = f"Progress: {int(current_val)} / {threshold}"
            percent = min(100, (current_val / threshold) * 100) if threshold > 0 else 0
            
        if is_unlocked:
            prog_text = "Completed!"
            percent = 100

        bottom_frame = tk.Frame(card, bg=bg_color)
        bottom_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        tk.Label(bottom_frame, text=prog_text, font=("Segoe UI", 8, "italic"), fg=text_color, bg=bg_color).pack(side=tk.RIGHT)

        bar_bg = "#333333" if is_unlocked else "#d3d3d3"
        bar_canvas = tk.Canvas(bottom_frame, height=6, bg=bar_bg, highlightthickness=0)
        bar_canvas.pack(side=tk.LEFT, fill="x", expand=True, padx=(35, 10))
        
        if percent > 0:
            bar_canvas.update_idletasks()
            bar_canvas.bind("<Configure>", lambda e, p=percent, c=bar_canvas, b=border_color: c.create_rectangle(0, 0, e.width * (p/100), e.height, fill=b, outline=""))

def show_achievement_toast(app, title, desc):
    toast = tk.Toplevel(app.root)
    toast.wm_overrideredirect(True)
    toast.attributes('-topmost', True)
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#2b2b2b"
    fg_color = style.lookup("TLabel", "foreground") or "#f0f0f0"
    accent_color = "#f39c12" 
    
    toast.configure(bg=accent_color)
    
    inner = tk.Frame(toast, bg=bg_color, highlightthickness=0)
    inner.pack(fill="both", expand=True, padx=2, pady=2) 
    
    tk.Label(inner, text="🏆 Achievement Unlocked!", font=("Segoe UI", 9, "bold"), bg=bg_color, fg=accent_color).pack(anchor="w", padx=15, pady=(10, 0))
    tk.Label(inner, text=title, font=("Segoe UI", 11, "bold"), bg=bg_color, fg=fg_color).pack(anchor="w", padx=15)
    tk.Label(inner, text=desc, font=("Segoe UI", 9), bg=bg_color, fg=fg_color).pack(anchor="w", padx=15, pady=(0, 10))
    
    toast.update_idletasks()
    w = toast.winfo_width()
    h = toast.winfo_height()
    
    x = app.root.winfo_screenwidth() - w - 20
    y = app.root.winfo_screenheight() - h - 60
    toast.geometry(f"+{x}+{y}")
    
    app.root.after(5000, toast.destroy)

def open_pairing_window(app):
    import socket
    
    # Dynamically grab the host machine's local IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()

    token = app.db.load_settings().get("auth_token")
    pairing_url = f"http://{local_ip}:8000/auth?token={token}"

    top = tk.Toplevel(app.root)
    top.title("Pair Mobile Device")
    top.configure(bg="#2b2b2b")
    top.transient(app.root)
    top.resizable(False, False)
    
    # Build all widgets, then size the window to fit
    main_frame = tk.Frame(top, bg="#2b2b2b", padx=25, pady=20)
    main_frame.pack(fill="both", expand=True)
    
    tk.Label(main_frame, text="Scan to Connect", font=("Arial", 16, "bold"), 
             bg="#2b2b2b", fg="white").pack(pady=(0, 10))
    
    tk.Label(main_frame, text="Point your phone's camera at this code\nto securely load your library.",
             bg="#2b2b2b", fg="#cccccc", wraplength=350, justify="center").pack(pady=(0, 15))

    # Generate QR
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(pairing_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    tk_image = ImageTk.PhotoImage(img)

    qr_label = tk.Label(main_frame, image=tk_image, bg="#2b2b2b")
    qr_label.image = tk_image
    qr_label.pack(pady=(0, 15))

    tk.Label(main_frame, text="Or open this URL manually:",
             bg="#2b2b2b", fg="#cccccc", font=("Arial", 9)).pack(pady=(0, 5))

    # URL display - use a Text widget for proper wrapping with monospace
    url_text = tk.Text(main_frame, height=2, wrap="word", bg="#1e1e1e", fg="#bb86fc",
                      font=("Consolas", 9), relief="flat", padx=10, pady=8)
    url_text.insert("1.0", pairing_url)
    url_text.config(state="disabled")
    url_text.pack(fill="x", pady=(0, 5))

    # Copy button for convenience
    def copy_url():
        top.clipboard_clear()
        top.clipboard_append(pairing_url)
        copy_btn.config(text="Copied!")
        top.after(1500, lambda: copy_btn.config(text="Copy URL"))
    
    copy_btn = tk.Button(main_frame, text="Copy URL", command=copy_url,
                         bg="#bb86fc", fg="#1e1e1e", font=("Arial", 9, "bold"),
                         relief="flat", padx=15, pady=5)
    copy_btn.pack(pady=(5, 0))

    # Now size the window to fit its contents
    top.update_idletasks()
    
    # Centre on parent window
    parent_x = app.root.winfo_x()
    parent_y = app.root.winfo_y()
    parent_w = app.root.winfo_width()
    parent_h = app.root.winfo_height()
    win_w = top.winfo_reqwidth()
    win_h = top.winfo_reqheight()
    
    x = parent_x + (parent_w // 2) - (win_w // 2)
    y = parent_y + (parent_h // 2) - (win_h // 2)
    top.geometry(f"+{x}+{y}")

def open_match_to_audible_window(app, filepath):
    """Lets the user manually search Audible's catalog and link a file to a cloud item."""
    if not app.api_client.auth:
        messagebox.showwarning(
            "Not Logged In",
            "You need to be logged into Audible to search the catalog.\n\n"
            "Go to File > Authentication & Profiles to sign in."
        )
        return
    
    # Get the file's current title for the initial search
    local_data = app.library_manager.local_library.get(filepath, {})
    initial_query = local_data.get("title", "")
    
    win = tk.Toplevel(app.root)
    win.title("Match File to Audible")
    win.geometry("600x500")
    win.transient(app.root)
    
    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    win.configure(bg=bg_color)
    
    main_frame = ttk.Frame(win, padding=15)
    main_frame.pack(fill="both", expand=True)
    
    ttk.Label(
        main_frame,
        text="Search Audible Catalog",
        font=("Segoe UI", 14, "bold")
    ).pack(anchor="w", pady=(0, 5))
    
    ttk.Label(
        main_frame,
        text=f"Linking: {os.path.basename(filepath)}",
        font=("Segoe UI", 9, "italic"),
        wraplength=550
    ).pack(anchor="w", pady=(0, 15))
    
    # Search row
    search_frame = ttk.Frame(main_frame)
    search_frame.pack(fill="x", pady=(0, 10))
    
    search_var = tk.StringVar(value=initial_query)
    search_entry = ttk.Entry(search_frame, textvariable=search_var)
    search_entry.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 5))
    
    status_var = tk.StringVar(value="")
    
    # Results tree
    tree_frame = ttk.Frame(main_frame)
    tree_frame.pack(fill="both", expand=True, pady=(0, 10))
    
    columns = ("Title", "Author", "ASIN")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
    tree.heading("Title", text="Title")
    tree.heading("Author", text="Author")
    tree.heading("ASIN", text="ASIN")
    tree.column("Title", width=300)
    tree.column("Author", width=150)
    tree.column("ASIN", width=100)
    
    scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side=tk.LEFT, fill="both", expand=True)
    scroll.pack(side=tk.RIGHT, fill="y")
    
    # Status row
    status_label = ttk.Label(main_frame, textvariable=status_var, font=("Segoe UI", 9))
    status_label.pack(anchor="w", pady=(0, 5))
    
    # Result storage so we can grab the ASIN of the selected row
    results_by_iid = {}
    
    def populate_results(products):
        # Clear existing rows
        for row in tree.get_children():
            tree.delete(row)
        results_by_iid.clear()
        
        if not products:
            status_var.set("No matches found. Try a different search term.")
            return
        
        for product in products:
            title = product.get("title", "Unknown")
            asin = product.get("asin", "")
            
            raw_authors = product.get("authors", [])
            authors = ", ".join([
                a.get("name", "") for a in raw_authors if isinstance(a, dict)
            ])
            
            iid = tree.insert("", "end", values=(title, authors, asin))
            results_by_iid[iid] = asin
        
        status_var.set(f"Found {len(products)} result{'s' if len(products) != 1 else ''}. Select one and click Apply.")
    
    def do_search():
        query = search_var.get().strip()
        if not query:
            status_var.set("Enter a search term.")
            return
        
        status_var.set("Searching Audible...")
        win.update_idletasks()
        
        # Use the metadata manager's existing search but capture results synchronously
        # via a temporary callback override
        original_search_complete = app.metadata_manager.on_search_complete
        original_error = app.metadata_manager.on_error
        
        def capture_results(fp, products):
            app.root.after(0, lambda: populate_results(products))
            app.metadata_manager.on_search_complete = original_search_complete
            app.metadata_manager.on_error = original_error
        
        def capture_error(msg):
            app.root.after(0, lambda: status_var.set(f"Error: {msg}"))
            app.metadata_manager.on_search_complete = original_search_complete
            app.metadata_manager.on_error = original_error
        
        app.metadata_manager.on_search_complete = capture_results
        app.metadata_manager.on_error = capture_error
        app.metadata_manager.search_catalog(filepath, query)
    
    def do_apply():
        selection = tree.focus()
        if not selection:
            status_var.set("Select a result first.")
            return
        
        asin = results_by_iid.get(selection)
        if not asin:
            status_var.set("Invalid selection.")
            return
        
        # Confirm
        title_picked = tree.item(selection)["values"][0]
        if not messagebox.askyesno(
            "Confirm Match",
            f"Link this file to:\n\n{title_picked}\n\nThis will overwrite any existing metadata."
        ):
            return
        
        status_var.set("Applying metadata and embedding tags...")
        win.update_idletasks()
        
        # Use the existing apply pipeline
        original_apply = app.metadata_manager.on_apply_complete
        original_error = app.metadata_manager.on_error
        
        def on_done(fp, new_title):
            app.root.after(0, lambda: app.refresh_library_ui())
            app.root.after(0, win.destroy)
            app.metadata_manager.on_apply_complete = original_apply
            app.metadata_manager.on_error = original_error
        
        def on_error(msg):
            app.root.after(0, lambda: status_var.set(f"Error: {msg}"))
            app.metadata_manager.on_apply_complete = original_apply
            app.metadata_manager.on_error = original_error
        
        app.metadata_manager.on_apply_complete = on_done
        app.metadata_manager.on_error = on_error
        app.metadata_manager.apply_scraped_metadata(filepath, asin)
    
    # Action buttons
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x")
    
    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
    ttk.Button(btn_frame, text="Apply Match", command=do_apply).pack(side=tk.RIGHT)
    ttk.Button(search_frame, text="Search", command=do_search).pack(side=tk.RIGHT)
    
    # Allow Enter to trigger search from the entry box
    search_entry.bind("<Return>", lambda e: do_search())
    
    # Auto-search on open if we have a starting query
    if initial_query:
        win.after(100, do_search)
    
    search_entry.focus_set()