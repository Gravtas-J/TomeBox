import tkinter as tk
from tkinter import ttk, messagebox
import socket
import qrcode
from PIL import Image, ImageTk

def open_auth_window(app):
    if hasattr(app, 'auth_window') and app.auth_window.winfo_exists():
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

    if hasattr(app, 'chapter_win') and app.chapter_win.winfo_exists():
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
    if hasattr(app, 'sleep_menu_popup') and app.sleep_menu_popup.winfo_exists():
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
    if hasattr(app, 'ach_window') and app.ach_window.winfo_exists():
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
    # Dynamically grab the host machine's local IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()

    # Construct the secure URL
    token = app.db.load_settings().get("auth_token")
    pairing_url = f"http://{local_ip}:8000/?token={token}"

    top = tk.Toplevel(app.root)
    top.title("Pair Mobile Device")
    top.geometry("400x500")
    top.configure(bg="#2b2b2b")

    tk.Label(top, text="Scan to Connect", font=("Arial", 16, "bold"), bg="#2b2b2b", fg="white").pack(pady=15)
    tk.Label(top, text="Point your phone's camera at this code to securely load your library.", 
             bg="#2b2b2b", fg="#cccccc", wraplength=350, justify="center").pack(pady=5)

    # Generate the QR Code
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(pairing_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to a Tkinter-compatible image
    tk_image = ImageTk.PhotoImage(img)

    qr_label = tk.Label(top, image=tk_image, bg="#2b2b2b")
    qr_label.image = tk_image  # Keep a reference to prevent garbage collection
    qr_label.pack(pady=20)

    # Provide the raw URL for manual entry if needed
    url_entry = tk.Entry(top, width=45, justify="center")
    url_entry.insert(0, pairing_url)
    url_entry.configure(state="readonly")
    url_entry.pack(pady=10)