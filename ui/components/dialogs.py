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
            app.sleep_menu_popup.withdraw()
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
    
    app.root.after(5000, lambda: (toast.withdraw(), toast.destroy()))

def open_pairing_window(app):
    import socket
    import time
    import secrets
    
    # Dynamically grab the host machine's local IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()

    # --- Generate 5-Minute OTP ---
    if not hasattr(app, '_active_otps'):
        app._active_otps = {}
        
    # Clear expired OTPs
    now = time.time()
    app._active_otps = {k: v for k, v in app._active_otps.items() if v > now}
    
    # Mint fresh OTP
    otp = secrets.token_urlsafe(16)
    app._active_otps[otp] = now + 300  # Expires in 300 seconds (5 mins)

    # Use a mutable state dictionary so button callbacks always see the newest URL
    current_state = {"url": f"http://{local_ip}:8000/auth?otp={otp}"}

    top = tk.Toplevel(app.root)
    top.title("Pair Mobile Device")
    top.configure(bg="#2b2b2b")
    top.transient(app.root)
    top.resizable(False, False)
    
    main_frame = tk.Frame(top, bg="#2b2b2b", padx=25, pady=20)
    main_frame.pack(fill="both", expand=True)
    
    tk.Label(main_frame, text="Scan to Connect", font=("Arial", 16, "bold"), 
             bg="#2b2b2b", fg="white").pack(pady=(0, 10))
    
    tk.Label(main_frame, text="Point your phone's camera at this code\nto securely load your library.",
             bg="#2b2b2b", fg="#cccccc", wraplength=350, justify="center").pack(pady=(0, 15))

    # Generate initial QR
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(current_state["url"])
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    tk_image = ImageTk.PhotoImage(img)

    qr_label = tk.Label(main_frame, image=tk_image, bg="#2b2b2b")
    qr_label.image = tk_image
    qr_label.pack(pady=(0, 15))

    tk.Label(main_frame, text="Or open this URL manually:",
             bg="#2b2b2b", fg="#cccccc", font=("Arial", 9)).pack(pady=(0, 5))

    url_text = tk.Text(main_frame, height=2, wrap="word", bg="#1e1e1e", fg="#bb86fc",
                      font=("Consolas", 9), relief="flat", padx=10, pady=8)
    url_text.insert("1.0", current_state["url"])
    url_text.config(state="disabled")
    url_text.pack(fill="x", pady=(0, 5))

    # --- Button Callbacks ---
    def copy_url():
        top.clipboard_clear()
        top.clipboard_append(current_state["url"])
        copy_btn.config(text="Copied!")
        top.after(1500, lambda: copy_btn.config(text="Copy URL"))

    def refresh_qr_code():
        # Mint a new 5-minute OTP
        now = time.time()
        app._active_otps = {k: v for k, v in app._active_otps.items() if v > now}
        new_otp = secrets.token_urlsafe(16)
        app._active_otps[new_otp] = now + 300
        
        current_state["url"] = f"http://{local_ip}:8000/auth?otp={new_otp}"
        
        # Visually refresh the QR Code
        new_qr = qrcode.QRCode(box_size=8, border=2)
        new_qr.add_data(current_state["url"])
        new_qr.make(fit=True)
        new_img = new_qr.make_image(fill_color="black", back_color="white")
        new_tk_image = ImageTk.PhotoImage(new_img)
        
        qr_label.config(image=new_tk_image)
        qr_label.image = new_tk_image  # Prevent garbage collection
        
        # Visually refresh the Text Box
        url_text.config(state="normal")
        url_text.delete("1.0", tk.END)
        url_text.insert("1.0", current_state["url"])
        url_text.config(state="disabled")

    # --- Action Buttons ---
    btn_frame = tk.Frame(main_frame, bg="#2b2b2b")
    btn_frame.pack(pady=(10, 0))

    copy_btn = tk.Button(btn_frame, text="Copy URL", command=copy_url,
                         bg="#bb86fc", fg="#1e1e1e", font=("Arial", 9, "bold"),
                         relief="flat", padx=15, pady=5)
    copy_btn.pack(side=tk.LEFT, padx=5)

    regen_btn = tk.Button(btn_frame, text="Refresh Code", command=refresh_qr_code,
                          bg="#ff4444", fg="white", font=("Arial", 9, "bold"),
                          relief="flat", padx=15, pady=5)
    regen_btn.pack(side=tk.LEFT, padx=5)

    # Size and center the window
    top.update_idletasks()
    
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
    import os
    
    # 1. Auto-Populate Logic
    local_data = app.library_manager.local_library.get(filepath, {})
    initial_title = local_data.get("title", os.path.basename(filepath))
    initial_author = local_data.get("authors", "")
    
    if initial_author in ["Unknown Author", "Local File", "Unknown"]:
        initial_author = ""

    # Clean up file extensions from the title so the search is cleaner
    if initial_title.lower().endswith(('.m4b', '.mp3', '.aax', '.aaxc')):
        initial_title = os.path.splitext(initial_title)[0]

    # 2. Resized Geometry
    win = tk.Toplevel(app.root)
    win.title("Scrape Metadata")
    win.geometry("750x650") 
    win.transient(app.root)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    fg_color = style.lookup("TLabel", "foreground") or "#000000"
    win.configure(bg=bg_color)

    main_frame = ttk.Frame(win, padding=15)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(main_frame, text="Search Catalogs", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(main_frame, text=f"File: {os.path.basename(filepath)}", font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 15))

    # --- Search Form ---
    search_frame = ttk.Frame(main_frame)
    search_frame.pack(fill="x", pady=(0, 10))

    ttk.Label(search_frame, text="Title:").grid(row=0, column=0, sticky="e", padx=5, pady=2)
    title_var = tk.StringVar(value=initial_title)
    ttk.Entry(search_frame, textvariable=title_var, width=40).grid(row=0, column=1, sticky="w", pady=2)

    ttk.Label(search_frame, text="Author:").grid(row=1, column=0, sticky="e", padx=5, pady=2)
    author_var = tk.StringVar(value=initial_author)
    ttk.Entry(search_frame, textvariable=author_var, width=40).grid(row=1, column=1, sticky="w", pady=2)

    options_frame = ttk.LabelFrame(main_frame, text="Fields to Overwrite", padding=5)
    options_frame.pack(fill="x", pady=(0, 10))

    apply_title_var = tk.BooleanVar(value=True)
    apply_author_var = tk.BooleanVar(value=True)
    apply_series_var = tk.BooleanVar(value=True)
    apply_cover_var = tk.BooleanVar(value=True)

    ttk.Checkbutton(options_frame, text="Title", variable=apply_title_var).pack(side=tk.LEFT, padx=10)
    ttk.Checkbutton(options_frame, text="Author", variable=apply_author_var).pack(side=tk.LEFT, padx=10)
    ttk.Checkbutton(options_frame, text="Series", variable=apply_series_var).pack(side=tk.LEFT, padx=10)
    ttk.Checkbutton(options_frame, text="Cover Art", variable=apply_cover_var).pack(side=tk.LEFT, padx=10)

    status_var = tk.StringVar(value="")

    # --- Results Canvas ---
    results_outer = ttk.Frame(main_frame)
    results_outer.pack(fill="both", expand=True, pady=(10, 10))

    canvas = tk.Canvas(results_outer, bg=bg_color, highlightthickness=0)
    scrollbar = ttk.Scrollbar(results_outer, orient="vertical", command=canvas.yview)
    inner_frame = tk.Frame(canvas, bg=bg_color)

    inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas_win = canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_win, width=e.width))

    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill="both", expand=True)
    scrollbar.pack(side=tk.RIGHT, fill="y")

    # --- Bottom Controls ---
    bottom_frame = ttk.Frame(main_frame)
    bottom_frame.pack(fill="x", side=tk.BOTTOM)

    status_label = ttk.Label(bottom_frame, textvariable=status_var, font=("Segoe UI", 9))
    status_label.pack(side=tk.LEFT)

    btn_frame = ttk.Frame(bottom_frame)
    btn_frame.pack(side=tk.RIGHT)

    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
    apply_btn = ttk.Button(btn_frame, text="Apply Match", state=tk.DISABLED)
    apply_btn.pack(side=tk.RIGHT)

    search_btn = ttk.Button(search_frame, text="Search")
    search_btn.grid(row=0, column=2, rowspan=2, padx=10, sticky="ns")

    selected_asin = tk.StringVar(value="")
    app.scraper_image_cache = {} # Prevent Python garbage collection from deleting the images

    def select_item(asin, row_frame):
        selected_asin.set(asin)
        for child in inner_frame.winfo_children():
            child.config(bg=bg_color)
        row_frame.config(bg="#4a90e2") # Highlight color
        apply_btn.config(state=tk.NORMAL)

    def populate_results(products):
        for widget in inner_frame.winfo_children():
            widget.destroy()

        if not products:
            status_var.set("No matches found.")
            return

        status_var.set(f"Found {len(products)} result(s). Select one to apply.")

        import threading
        import requests
        from PIL import Image, ImageTk
        import io

        for idx, product in enumerate(products):
            asin = product.get("asin")
            title = product.get("title", "Unknown")
            raw_authors = product.get("authors", [])
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            source = product.get("source", "Audible")

            # Create Row
            row_frame = tk.Frame(inner_frame, bg=bg_color, pady=5, padx=5, highlightthickness=1, highlightbackground="#cccccc")
            row_frame.pack(fill="x", pady=2, padx=2)
            row_frame.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            # Image Placeholder
            img_container = tk.Frame(row_frame, width=128, height=128, bg="#dddddd")
            img_container.pack_propagate(False)
            img_container.pack(side=tk.LEFT, padx=(0, 10))
            img_container.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            img_lbl = tk.Label(img_container, text="Loading...", bg="#dddddd")
            img_lbl.pack(expand=True, fill="both")
            img_lbl.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            # Metadata Info
            info_frame = tk.Frame(row_frame, bg=bg_color)
            info_frame.pack(side=tk.LEFT, fill="both", expand=True)
            info_frame.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            t_lbl = tk.Label(info_frame, text=title, font=("Segoe UI", 10, "bold"), bg=bg_color, fg=fg_color, anchor="w", justify="left", wraplength=450)
            t_lbl.pack(fill="x")
            t_lbl.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            a_lbl = tk.Label(info_frame, text=authors, font=("Segoe UI", 9), bg=bg_color, fg=fg_color, anchor="w")
            a_lbl.pack(fill="x")
            a_lbl.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            tk.Label(info_frame, text=f"Source: {source} | ASIN: {asin}", font=("Segoe UI", 8, "italic"), fg=fg_color, bg=bg_color, anchor="w").pack(fill="x")

            # 3. Dynamic Thumbnail Fetching
            img_url = product.get("cover_url")
            if not img_url and "product_images" in product:
                images = product.get("product_images", {})
                img_url = images.get("115") or images.get("252") or images.get("500")

            if img_url:
                def load_img(url, lbl, current_asin):
                    try:
                        if url.startswith("http:"): url = url.replace("http:", "https:")
                        res = requests.get(url, timeout=5)
                        if res.status_code == 200:
                            img = Image.open(io.BytesIO(res.content))
                            img.thumbnail((128, 128)) 
                            photo = ImageTk.PhotoImage(img)
                            app.scraper_image_cache[f"{current_asin}_{url}"] = photo
                            app.root.after(0, lambda: lbl.config(image=photo, text="", bg=bg_color))
                    except Exception as e:
                        app.root.after(0, lambda: lbl.config(text="No Cover"))

                threading.Thread(target=load_img, args=(img_url, img_lbl, asin), daemon=True).start()
            else:
                img_lbl.config(text="No Cover")
    
    def do_search():
        if str(search_btn['state']) == tk.DISABLED:
            return

        t = title_var.get().strip()
        a = author_var.get().strip()
        query = f"{t} {a}".strip()

        if not query:
            status_var.set("Enter a search term.")
            return

        status_var.set("Searching...")
        search_btn.config(state=tk.DISABLED)
        win.update_idletasks()

        def capture_results(filepath=None, products=None, **kwargs):
            app.root.after(0, lambda: search_btn.config(state=tk.NORMAL))
            app.root.after(0, lambda: populate_results(products))
            app.metadata_manager.event_bus.unsubscribe("metadata.search_complete", capture_results)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", capture_error)

        def capture_error(error_msg=None, **kwargs):
            app.root.after(0, lambda: search_btn.config(state=tk.NORMAL))
            app.root.after(0, lambda: status_var.set(f"Error: {error_msg}"))
            app.metadata_manager.event_bus.unsubscribe("metadata.search_complete", capture_results)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", capture_error)

        app.metadata_manager.event_bus.subscribe("metadata.search_complete", capture_results)
        app.metadata_manager.event_bus.subscribe("metadata.error", capture_error)
        
        app.metadata_manager.search_catalog(filepath, query)

    def do_apply():
        asin = selected_asin.get()
        if not asin:
            status_var.set("Select a result first.")
            return

        if not messagebox.askyesno("Confirm Match", "Link this file to the selected title?\n\nThis will overwrite any existing metadata."):
            return

        status_var.set("Applying metadata and embedding tags...")
        apply_btn.config(state=tk.DISABLED)
        win.update_idletasks()

        def on_done(filepath=None, title=None, **kwargs):
            app.root.after(0, lambda: app.refresh_library_ui())
            app.root.after(0, win.destroy)
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        def on_error(error_msg=None, **kwargs):
            app.root.after(0, lambda: status_var.set(f"Error: {error_msg}"))
            apply_btn.config(state=tk.NORMAL)
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        app.metadata_manager.event_bus.subscribe("metadata.apply_complete", on_done)
        app.metadata_manager.event_bus.subscribe("metadata.error", on_error)
        
        fields = {
            "title": apply_title_var.get(),
            "author": apply_author_var.get(),
            "series": apply_series_var.get(),
            "cover": apply_cover_var.get()
        }
        app.metadata_manager.apply_scraped_metadata(filepath, asin, fields_to_apply=fields)

    search_btn.config(command=do_search)
    apply_btn.config(command=do_apply)
    win.bind("<Return>", lambda e: do_search())

    if initial_title or initial_author:
        win.after(100, do_search)

    win.focus_set()

def open_manual_metadata_window(app, filepath):
    import os
    from tkinter import filedialog
    from PIL import Image, ImageTk
    
    local_data = app.library_manager.local_library.get(filepath, {})
    if not local_data: return

    win = tk.Toplevel(app.root)
    win.title("Edit Metadata")
    win.geometry("580x420")
    win.transient(app.root)
    win.resizable(False, False)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    win.configure(bg=bg_color)

    main_frame = ttk.Frame(win, padding=20)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(main_frame, text="Manual Edit", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 15))

    # Split layout: Image on left, Form on right
    content_frame = ttk.Frame(main_frame)
    content_frame.pack(fill="x", pady=(0, 15))

    # --- Left Column: Cover Art ---
    cover_frame = ttk.Frame(content_frame)
    cover_frame.pack(side=tk.LEFT, padx=(0, 15), fill="y")

    img_lbl = tk.Label(cover_frame, text="No Cover", width=20, height=8, bg="#dddddd")
    img_lbl.pack(pady=(0, 10))

    selected_cover_path = [None] 

    def update_preview(img_path):
        try:
            img = Image.open(img_path)
            img.thumbnail((125, 125))
            photo = ImageTk.PhotoImage(img)
            img_lbl.config(image=photo, text="", width=125, height=125)
            img_lbl.image = photo 
        except Exception:
            pass

    # Try to load existing cover on startup
    existing_asin = local_data.get("asin")
    if existing_asin:
        existing_cover = os.path.join(app.covers_dir, f"{existing_asin}.jpg")
        if os.path.exists(existing_cover):
            update_preview(existing_cover)

    def pick_cover():
        path = filedialog.askopenfilename(
            parent=win,
            title="Select Cover Art",
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp")]
        )
        if path:
            selected_cover_path[0] = path
            update_preview(path)

    ttk.Button(cover_frame, text="Change Cover...", command=pick_cover).pack()

    # --- Right Column: Text Form ---
    form_frame = ttk.Frame(content_frame)
    form_frame.pack(side=tk.LEFT, fill="both", expand=True)

    ttk.Label(form_frame, text="Title:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    title_var = tk.StringVar(value=local_data.get("title", ""))
    ttk.Entry(form_frame, textvariable=title_var, width=38).grid(row=0, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Author:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    author_var = tk.StringVar(value=local_data.get("authors", ""))
    ttk.Entry(form_frame, textvariable=author_var, width=38).grid(row=1, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Series:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    series_var = tk.StringVar(value=local_data.get("series", ""))
    ttk.Entry(form_frame, textvariable=series_var, width=38).grid(row=2, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="ASIN:").grid(row=3, column=0, sticky="e", padx=5, pady=5)
    asin_var = tk.StringVar(value=local_data.get("asin", ""))
    ttk.Entry(form_frame, textvariable=asin_var, width=38).grid(row=3, column=1, sticky="w", pady=5)

    # --- Options ---
    options_frame = ttk.Frame(main_frame)
    options_frame.pack(fill="x", pady=5)
    embed_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(options_frame, text="Embed tags into audio file (FFmpeg)", variable=embed_var).pack(side=tk.LEFT)

    status_var = tk.StringVar(value="")
    ttk.Label(main_frame, textvariable=status_var, font=("Segoe UI", 9)).pack(anchor="w", pady=5)

    # --- Controls ---
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", side=tk.BOTTOM)

    def do_save():
        status_var.set("Saving...")
        save_btn.config(state=tk.DISABLED)
        win.update_idletasks()

        new_data = {
            "title": title_var.get().strip(),
            "authors": author_var.get().strip(),
            "series": series_var.get().strip(),
            "asin": asin_var.get().strip()
        }

        def on_done(filepath=None, title=None, **kwargs):
            app.root.after(0, lambda: app.refresh_library_ui())
            # Force the sidebar to fetch the newly updated display metadata
            app.root.after(0, lambda: app.metadata_manager.fetch_display_metadata(filepath))
            app.root.after(0, win.destroy)
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        def on_error(error_msg=None, **kwargs):
            app.root.after(0, lambda: status_var.set(f"Error: {error_msg}"))
            app.root.after(0, lambda: save_btn.config(state=tk.NORMAL))
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        app.metadata_manager.event_bus.subscribe("metadata.apply_complete", on_done)
        app.metadata_manager.event_bus.subscribe("metadata.error", on_error)

        # Pass the selected cover path to the backend
        app.metadata_manager.apply_manual_metadata(
            filepath, 
            new_data, 
            embed_to_file=embed_var.get(),
            new_cover_path=selected_cover_path[0]
        )

    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
    save_btn = ttk.Button(btn_frame, text="Save", command=do_save)
    save_btn.pack(side=tk.RIGHT)

    win.focus_set()


def open_cover_modal(app, asin, title, explicit_path=None):
    """Opens a high-resolution, clickable cover art modal."""
    import os
    from PIL import Image, ImageTk

    existing = getattr(app, "_active_cover_modal", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        app._active_cover_modal = None

    # Resolve cover path
    cover_path = explicit_path
    if not cover_path:
        padded_asin = str(asin).zfill(10)
        test_path_padded = os.path.join(app.covers_dir, f"{padded_asin}.jpg")
        test_path_raw = os.path.join(app.covers_dir, f"{asin}.jpg")
        if os.path.exists(test_path_padded):
            cover_path = test_path_padded
        elif os.path.exists(test_path_raw):
            cover_path = test_path_raw

    if not cover_path or not os.path.exists(cover_path):
        return

    try:
        img = Image.open(cover_path)

        max_size = 800
        w, h = img.size
        if w > max_size or h > max_size:
            ratio = min(max_size / w, max_size / h)
            new_w, new_h = int(w * ratio), int(h * ratio)
            resample_filter = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((new_w, new_h), resample_filter)

        # Build modal hidden so we don't see a flicker
        modal = tk.Toplevel(app.root)
        modal.title(title)
        modal.withdraw()

        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#1e1e1e"
        modal.configure(bg=bg_color, highlightthickness=2, highlightbackground="#4a90e2")

        photo = ImageTk.PhotoImage(img)
        modal.image = photo  # prevent GC

        # takefocus=0 keeps focus on the toplevel so clicking the image
        # doesn't trigger FocusOut on the modal
        lbl = tk.Label(modal, image=photo, bg=bg_color, bd=0,
                       cursor="hand2", takefocus=0)
        lbl.pack(fill="both", expand=True)

        # Center before stripping decorations
        modal.update_idletasks()
        mw, mh = img.width, img.height
        x = app.root.winfo_x() + (app.root.winfo_width() // 2) - (mw // 2)
        y = app.root.winfo_y() + (app.root.winfo_height() // 2) - (mh // 2)
        modal.geometry(f"{mw}x{mh}+{x}+{y}")

        modal.overrideredirect(True)
        modal.deiconify()
        modal.lift()
        modal.attributes("-topmost", True)
        modal.focus_force()

        # Register as the active modal
        app._active_cover_modal = modal

        def dismiss(event=None):
            if getattr(app, "_active_cover_modal", None) is modal:
                app._active_cover_modal = None
            try:
                modal.withdraw()
                modal.destroy()
            except tk.TclError:
                pass

        # Dismiss bindings: click image, Escape, click outside (FocusOut), or WM close
        lbl.bind("<Button-1>", dismiss)
        modal.bind("<Escape>", dismiss)
        modal.bind("<FocusOut>", dismiss)
        modal.protocol("WM_DELETE_WINDOW", dismiss)

    except Exception as e:
        # Make sure we don't leave a stale singleton reference
        app._active_cover_modal = None
        import traceback
        traceback.print_exc()
        if hasattr(app, "logger"):
            app.logger.error(f"Failed to open cover modal: {e}")

def open_device_management_window(app):
    import time
    from datetime import datetime

    if getattr(app, 'device_win', None) and app.device_win.winfo_exists():
        app.device_win.lift()
        app.device_win.focus_set()
        return

    app.device_win = tk.Toplevel(app.root)
    app.device_win.title("Manage Paired Devices")
    app.device_win.geometry("500x400")
    app.device_win.transient(app.root)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    
    app.device_win.configure(bg=bg_color)

    main_frame = ttk.Frame(app.device_win, padding=10)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(main_frame, text="Connected Devices", font=("Segoe UI", 12, "bold")).pack(pady=(0, 10), anchor="w")
    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill="both", expand=True, pady=(0, 10))

    # --- Treeview & Left-Aligned Scrollbar Setup ---
    columns = ("TokenHash", "Device Name", "Last Seen") 
    
    # Pack the scrollbar FIRST, on the left
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
    scrollbar.pack(side=tk.RIGHT, fill="y", padx=(0, 5))

    tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")

    tree.heading("Device Name", text="Device Name")
    tree.column("Device Name", width=200, anchor="w")

    tree.heading("Last Seen", text="Last Seen")
    tree.column("Last Seen", width=150, anchor="w")

    # Hide the TokenHash column (used for data tracking)
    tree.column("TokenHash", width=0, stretch=tk.NO)
    tree.heading("TokenHash", text="")

    # Tie them together and pack the tree next to the scrollbar
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.configure(command=tree.yview)
    tree.pack(side=tk.LEFT, fill="both", expand=True)

    def refresh_list():
        for row in tree.get_children():
            tree.delete(row)

        devices = app.settings.get("paired_devices", {})
        for token_hash, data in devices.items():
            name = data.get("name", "Unknown Device")
            last_seen_ts = data.get("last_seen", 0)

            if last_seen_ts:
                last_seen_str = datetime.fromtimestamp(last_seen_ts).strftime("%Y-%m-%d %H:%M")
            else:
                last_seen_str = "Never"

            tree.insert("", "end", values=(token_hash, name, last_seen_str))

    refresh_list()

    # --- Bottom Controls ---
    btn_frame = ttk.Frame(app.device_win, padding=(10, 0, 10, 10))
    btn_frame.pack(fill="x", side=tk.BOTTOM)

    def revoke_device():
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Select Device", "Please select a device to revoke.", parent=app.device_win)
            return

        item = tree.item(selected[0])
        token_hash = item['values'][0]
        device_name = item['values'][1]

        if messagebox.askyesno("Revoke Access", f"Are you sure you want to disconnect '{device_name}'?\n\nThis device will immediately lose access to your library.", parent=app.device_win):
            devices = app.settings.get("paired_devices", {})
            if token_hash in devices:
                del devices[token_hash]
                app.settings["paired_devices"] = devices
                app.db.save_settings(app.settings)
                refresh_list()

    ttk.Button(btn_frame, text="Close", command=app.device_win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
    ttk.Button(btn_frame, text="Revoke Selected", command=revoke_device).pack(side=tk.RIGHT)