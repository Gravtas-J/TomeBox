import io
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import qrcode
import requests
from PIL import Image, ImageTk
import secrets
import socket
import time
from core.controllers.library_manager import bump_library_version

def open_error_log_window(app):
    """Opens the Error Log popup window."""
    if not app.failed_tasks:
        return

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

    tree = ttk.Treeview(
        tree_frame, columns=("File", "Action", "Error"), show="headings"
    )
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
        tree.insert(
            "", "end", iid=str(idx), values=(filename, task["action"], task["error"])
        )

    btn_frame = ttk.Frame(win)
    btn_frame.pack(fill="x", padx=10, pady=(0, 10))

    def retry_selected():
        selected = tree.selection()
        if not selected:
            return

        paths_to_retry = []

        # Remove from list in reverse order so indices don't shift
        for iid in sorted(selected, key=int, reverse=True):
            idx = int(iid)
            task = app.failed_tasks.pop(idx)
            paths_to_retry.append(task["path"])
            tree.delete(iid)

        # Update button count on the main window
        app.ui_state.error_btn.set(f"Errors ({len(app.failed_tasks)})")
        if not app.failed_tasks:
            app.error_btn.config(state=tk.DISABLED)
            win.destroy()

        # Seamlessly push them back into the conversion queue!
        if paths_to_retry:
            app.conversion_manager.convert_batch(paths_to_retry)

    ttk.Button(btn_frame, text="Retry Selected", command=retry_selected).pack(
        side=tk.LEFT, padx=5
    )

    def clear_all():
        app.failed_tasks.clear()
        app.ui_state.error_btn.set("Errors (0)")
        app.error_btn.config(state=tk.DISABLED)
        win.destroy()

    ttk.Button(btn_frame, text="Clear All", command=clear_all).pack(
        side=tk.RIGHT, padx=5
    )


def open_audio_device_settings(app):
    """Queries the OS for audio hardware and displays a selection menu."""
    try:
        import sounddevice as sd
        devices = []

        # Find the WASAPI host API index on Windows
        wasapi_idx = None
        if os.name == "nt":
            try:
                wasapi_idx = next(
                    i
                    for i, hostapi in enumerate(sd.query_hostapis())
                    if "WASAPI" in hostapi["name"]
                )
            except StopIteration:
                pass

        # Query all devices and filter manually
        for d in sd.query_devices():
            # If we are on Windows and found WASAPI, skip devices using older APIs
            if wasapi_idx is not None and d.get("hostapi") != wasapi_idx:
                continue

            if d["max_output_channels"] > 0 and d["name"] not in devices:
                devices.append(d["name"])

    except ImportError:
        messagebox.showerror(
            "Dependency Missing",
            "Please run 'pip install sounddevice' to enable hardware scanning.",
        )
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

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    popup.configure(bg=bg_color)

    ttk.Label(popup, text="Select Hardware Output:").pack(pady=(20, 5))

    device_var = tk.StringVar(value=current_device)
    combo = ttk.Combobox(
        popup, textvariable=device_var, values=devices, state="readonly", width=50
    )
    combo.pack(pady=5)

    def apply():
        selected = device_var.get()
        app.settings["audio_device"] = selected
        if hasattr(app, "db"):
            app.db.save_settings(app.settings)

        app.playback.set_audio_device(selected)

        # If audio is actively playing, bounce the stream so the change takes effect immediately
        if app.playback.is_playing:
            app.playback_presenter.pause_audio()
            app.playback.is_paused = False
            app.playback_presenter.resume_playback()

        popup.destroy()

    ttk.Button(popup, text="Apply", command=apply).pack(pady=(10, 0))


def open_auth_window(app):
    if getattr(app, "auth_window", None) and app.auth_window.winfo_exists():
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

    reg_combo = ttk.Combobox(
        reg_frame,
        textvariable=app.ui_state.locale,
        values=["us", "uk", "au", "ca", "de", "fr", "jp"],
        state="readonly",
        width=5,
    )
    reg_combo.pack(side=tk.LEFT)

    btn_frame = ttk.Frame(auth_frame)
    btn_frame.pack(fill="x", pady=5)
    app.browser_login_btn = ttk.Button(
        btn_frame,
        text="Browser Login",
        command=app.auth_controller.start_browser_login_thread,
    )
    app.browser_login_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)
    app.auth_file_btn = ttk.Button(
        btn_frame, text="Load .json", command=app.auth_controller.load_auth_file_prompt
    )
    app.auth_file_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)

    profile_frame = ttk.Frame(auth_frame)
    profile_frame.pack(fill="x", pady=5)

    ttk.Label(profile_frame, text="Profile:").pack(side=tk.LEFT, padx=5)

    app.profiles_list = getattr(
        app, "profiles_list", app.settings.get("profiles", ["Main"])
    )
    app.profile_combo = ttk.Combobox(
        profile_frame, values=app.profiles_list, state="readonly", width=15
    )
    app.profile_combo.set(app.active_profile)
    app.profile_combo.pack(side=tk.LEFT, padx=5)

    ttk.Button(
        profile_frame, text="New", width=5, command=app.auth_controller.add_new_profile
    ).pack(side=tk.LEFT)
    app.profile_combo.bind("<<ComboboxSelected>>", app.auth_controller.switch_profile)

    bytes_frame = ttk.LabelFrame(main_frame, text="Decryption Bytes", padding=10)
    bytes_frame.pack(fill="x", pady=10)
    ttk.Entry(bytes_frame, textvariable=app.ui_state.auth_bytes, justify="center").pack(
        fill="x", pady=5
    )

    ttk.Button(main_frame, text="Close", command=app.auth_window.destroy).pack(
        pady=(10, 0)
    )


def open_chapter_window(app):
    from tkinter import messagebox

    if not app.playback.chapters:
        messagebox.showinfo(
            "Chapters", "No chapter data available. Please load an audiobook first."
        )
        return

    if getattr(app, "chapter_win", None) and app.chapter_win.winfo_exists():
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

    ttk.Label(main_frame, text="Table of Contents", font=("Segoe UI", 14, "bold")).pack(
        pady=(0, 10)
    )

    columns = ("Index", "Title", "Start Time")
    tree = ttk.Treeview(
        main_frame, columns=columns, show="headings", selectmode="browse"
    )

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

    for i, chap in enumerate(app.playback.chapters):
        start_sec = float(chap.get("start_time", 0))
        h, m = divmod(start_sec, 3600)
        m, s = divmod(m, 60)
        time_str = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
        title = chap.get("tags", {}).get("title", f"Chapter {i + 1}")
        tree.insert("", "end", values=(i + 1, title, time_str))

    tree.bind("<Double-1>", lambda e: app.on_chapter_select(tree))


def open_sleep_menu(app):
    if getattr(app, "sleep_menu_popup", None) and app.sleep_menu_popup.winfo_exists():
        app.sleep_menu_popup.destroy()
        return

    app.sleep_menu_popup = tk.Toplevel(app.root)
    app.sleep_menu_popup.wm_overrideredirect(True)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    app.sleep_menu_popup.config(
        bg=bg_color, highlightbackground="#4a90e2", highlightthickness=1
    )

    # Safely locate the button inside the PlayerBarView component
    btn_ref = getattr(app.player_bar, "timer_btn", None) or getattr(
        app.player_bar, "sleep_btn", None
    )

    # Initial dummy placement
    if btn_ref:
        x = btn_ref.winfo_rootx()
        y = btn_ref.winfo_rooty() + btn_ref.winfo_height() + 2
        app.sleep_menu_popup.geometry(f"+{x}+{y}")
    else:
        # Fallback to center screen if the button reference is completely missing
        app.sleep_menu_popup.geometry(
            f"+{app.root.winfo_rootx() + 200}+{app.root.winfo_rooty() + 200}"
        )

    inner = tk.Frame(app.sleep_menu_popup, bg=bg_color, padx=5, pady=5)
    inner.pack(fill="both", expand=True)

    ttk.Button(
        inner,
        text="Turn Off Timer",
        command=lambda: app.playback_presenter.set_sleep_timer("off"),
    ).pack(fill="x", pady=(0, 5))
    ttk.Button(
        inner,
        text="15 Minutes",
        command=lambda: app.playback_presenter.set_sleep_timer("time", 15),
    ).pack(fill="x", pady=1)
    ttk.Button(
        inner,
        text="30 Minutes",
        command=lambda: app.playback_presenter.set_sleep_timer("time", 30),
    ).pack(fill="x", pady=1)
    ttk.Button(
        inner,
        text="End of Chapter",
        command=lambda: app.playback_presenter.set_sleep_timer("chapters", 1),
    ).pack(fill="x", pady=1)

    ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=5)

    custom_time_frame = ttk.Frame(inner)
    custom_time_frame.pack(fill="x", pady=2)
    ttk.Label(custom_time_frame, text="Mins:").pack(side=tk.LEFT)
    min_var = tk.StringVar(value="60")
    ttk.Entry(custom_time_frame, textvariable=min_var, width=5).pack(
        side=tk.LEFT, padx=(5, 2)
    )
    ttk.Button(
        custom_time_frame,
        text="Set",
        width=4,
        command=lambda: app.playback_presenter.set_sleep_timer("time", min_var.get()),
    ).pack(side=tk.LEFT)

    custom_chap_frame = ttk.Frame(inner)
    custom_chap_frame.pack(fill="x", pady=2)
    ttk.Label(custom_chap_frame, text="Chaps:").pack(side=tk.LEFT)
    chap_var = tk.StringVar(value="2")
    ttk.Entry(custom_chap_frame, textvariable=chap_var, width=5).pack(
        side=tk.LEFT, padx=(1, 2)
    )
    ttk.Button(
        custom_chap_frame,
        text="Set",
        width=4,
        command=lambda: app.playback_presenter.set_sleep_timer(
            "chapters", chap_var.get()
        ),
    ).pack(side=tk.LEFT)

    # Recalculate exact height after elements are packed
    app.sleep_menu_popup.update_idletasks()
    popup_height = app.sleep_menu_popup.winfo_reqheight()

    # Shift the popup so it anchors nicely above the player bar button
    if btn_ref:
        x = btn_ref.winfo_rootx()
        y = btn_ref.winfo_rooty()
        app.sleep_menu_popup.geometry(f"+{x}+{y - popup_height - 2}")

    def on_focus_out(event):
        if app.sleep_menu_popup.focus_get() is None or not str(
            app.sleep_menu_popup.focus_get()
        ).startswith(str(app.sleep_menu_popup)):
            app.sleep_menu_popup.withdraw()
            app.sleep_menu_popup.destroy()

    app.sleep_menu_popup.bind("<FocusOut>", on_focus_out)
    app.sleep_menu_popup.focus_set()

def _normalise_series(name):
    return " ".join((name or "").split()).lower()

def open_library_folders_window(app):
    """Opens a UI dialog to manage the background scanner's watched folders."""
    import os
    import tkinter as tk
    from tkinter import filedialog, ttk

    win = tk.Toplevel(app.root)
    win.title("Manage Library Folders")
    win.geometry("500x350")
    win.transient(app.root)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    win.configure(bg=bg_color)

    main_frame = ttk.Frame(win, padding=15)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(
        main_frame, text="Watched Library Folders", font=("Segoe UI", 12, "bold")
    ).pack(anchor="w", pady=(0, 5))
    ttk.Label(
        main_frame,
        text="TomeBox will automatically scan these folders for new audiobooks.",
        font=("Segoe UI", 9, "italic"),
    ).pack(anchor="w", pady=(0, 10))

    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill="both", expand=True, pady=(0, 10))

    folder_listbox = tk.Listbox(
        list_frame, bg="#2b2b2b", fg="white", selectbackground="#4a90e2"
    )
    folder_listbox.pack(side=tk.LEFT, fill="both", expand=True)

    scrollbar = ttk.Scrollbar(
        list_frame, orient="vertical", command=folder_listbox.yview
    )
    scrollbar.pack(side=tk.RIGHT, fill="y")
    folder_listbox.config(yscrollcommand=scrollbar.set)

    current_folders = app.settings.get("library_folders", [])
    for f in current_folders:
        folder_listbox.insert(tk.END, f)

    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x")

    def add_folder():
        folder = filedialog.askdirectory(parent=win, title="Select Library Folder")
        if folder and folder not in folder_listbox.get(0, tk.END):
            folder_listbox.insert(tk.END, os.path.normpath(folder))

    def remove_folder():
        selected = folder_listbox.curselection()
        if selected:
            folder_listbox.delete(selected[0])

    def save_folders():
        folders = list(folder_listbox.get(0, tk.END))
        app.settings["library_folders"] = folders
        if hasattr(app, "db"):
            app.db.save_settings(app.settings)

        app.library_manager.run_background_library_scan(
            app.converter,
            app.active_profile,
            app.logger,
            app.thread_pool,
            on_refresh_cb=lambda: app.root.after(
                0, app.library_presenter.refresh_library_ui
            ),
        )
        win.destroy()

    ttk.Button(btn_frame, text="Add Folder", command=add_folder).pack(
        side=tk.LEFT, padx=(0, 5)
    )
    ttk.Button(btn_frame, text="Remove Selected", command=remove_folder).pack(
        side=tk.LEFT
    )
    ttk.Button(btn_frame, text="Save & Scan", command=save_folders).pack(side=tk.RIGHT)
    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(
        side=tk.RIGHT, padx=(0, 5)
    )


def open_achievements_window(app):
    if getattr(app, "ach_window", None) and app.ach_window.winfo_exists():
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

    ttk.Label(
        main_frame, text="TomeBox Achievements", font=("Segoe UI", 16, "bold")
    ).pack(pady=(0, 15))

    canvas = tk.Canvas(main_frame, bg=bg_color, highlightthickness=0)
    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg=bg_color)

    scrollable_frame.bind(
        "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.bind(
        "<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width)
    )

    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    stats = app.settings.get("stats", {})
    unlocked = stats.get("unlocked_achievements", [])

    for ach_id, data in getattr(app, "achievements", {}).items():
        is_unlocked = ach_id in unlocked
        border_color = "#4a90e2" if is_unlocked else "#555555"
        status_icon = "🏆" if is_unlocked else "🔒"
        text_color = fg_color if is_unlocked else "#888888"

        card = tk.Frame(
            scrollable_frame,
            bg=bg_color,
            highlightbackground=border_color,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=5, padx=5)

        header_frame = tk.Frame(card, bg=bg_color)
        header_frame.pack(fill="x", padx=10, pady=(10, 0))

        tk.Label(
            header_frame, text=status_icon, font=("Segoe UI", 16), bg=bg_color
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(
            header_frame,
            text=data["title"],
            font=("Segoe UI", 12, "bold"),
            fg=text_color,
            bg=bg_color,
        ).pack(side=tk.LEFT)
        tk.Label(
            card, text=data["desc"], font=("Segoe UI", 9), fg=text_color, bg=bg_color
        ).pack(anchor="w", padx=45, pady=(0, 5))

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

        tk.Label(
            bottom_frame,
            text=prog_text,
            font=("Segoe UI", 8, "italic"),
            fg=text_color,
            bg=bg_color,
        ).pack(side=tk.RIGHT)

        bar_bg = "#333333" if is_unlocked else "#d3d3d3"
        bar_canvas = tk.Canvas(bottom_frame, height=6, bg=bar_bg, highlightthickness=0)
        bar_canvas.pack(side=tk.LEFT, fill="x", expand=True, padx=(35, 10))

        if percent > 0:
            bar_canvas.update_idletasks()
            bar_canvas.bind(
                "<Configure>",
                lambda e, p=percent, c=bar_canvas, b=border_color: c.create_rectangle(
                    0, 0, e.width * (p / 100), e.height, fill=b, outline=""
                ),
            )


def show_achievement_toast(app, title, desc):
    toast = tk.Toplevel(app.root)
    toast.wm_overrideredirect(True)
    toast.attributes("-topmost", True)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#2b2b2b"
    fg_color = style.lookup("TLabel", "foreground") or "#f0f0f0"
    accent_color = "#f39c12"

    toast.configure(bg=accent_color)

    inner = tk.Frame(toast, bg=bg_color, highlightthickness=0)
    inner.pack(fill="both", expand=True, padx=2, pady=2)

    tk.Label(
        inner,
        text="🏆 Achievement Unlocked!",
        font=("Segoe UI", 9, "bold"),
        bg=bg_color,
        fg=accent_color,
    ).pack(anchor="w", padx=15, pady=(10, 0))
    tk.Label(
        inner, text=title, font=("Segoe UI", 11, "bold"), bg=bg_color, fg=fg_color
    ).pack(anchor="w", padx=15)
    tk.Label(inner, text=desc, font=("Segoe UI", 9), bg=bg_color, fg=fg_color).pack(
        anchor="w", padx=15, pady=(0, 10)
    )

    toast.update_idletasks()
    w = toast.winfo_width()
    h = toast.winfo_height()

    x = app.root.winfo_screenwidth() - w - 20
    y = app.root.winfo_screenheight() - h - 60
    toast.geometry(f"+{x}+{y}")

    app.root.after(5000, lambda: (toast.withdraw(), toast.destroy()))


def open_pairing_window(app):
    import json
    import secrets
    import time

    from core import wireguard

    app_payload, wg_conf, otp = wireguard.build_pairing_payload(app, port=8000)
    payload = json.loads(app_payload)
    print(f"[PAIR] app_payload={app_payload!r}")
    # QR carries the JSON packet; the text box shows a human-usable URL.
    current_state = {
        "payload": payload,                                          # mutated on refresh
        "qr_text": app_payload,                                      # what the QR encodes
        "manual_url": f"{payload['lan']}/auth?otp={payload['otp']}", # what a human types
    }

    top = tk.Toplevel(app.root)
    top.title("Pair Mobile Device")
    top.configure(bg="#2b2b2b")
    top.transient(app.root)
    top.resizable(False, False)

    main_frame = tk.Frame(top, bg="#2b2b2b", padx=25, pady=20)
    main_frame.pack(fill="both", expand=True)

    tk.Label(
        main_frame, text="Scan to Connect", font=("Arial", 16, "bold"),
        bg="#2b2b2b", fg="white",
    ).pack(pady=(0, 10))

    tk.Label(
        main_frame,
        text="Point your phone's camera at this code\nto securely load your library.",
        bg="#2b2b2b", fg="#cccccc", wraplength=350, justify="center",
    ).pack(pady=(0, 15))

# --- Side-by-side QR codes ---
    qr_row = tk.Frame(main_frame, bg="#2b2b2b")
    qr_row.pack(pady=(0, 15))

    # LEFT: WireGuard tunnel config (only if remote access is configured)
    if wg_conf:
        wg_col = tk.Frame(qr_row, bg="#2b2b2b")
        wg_col.pack(side=tk.LEFT, padx=10)

        tk.Label(
            wg_col,
            text="1. Scan this with the WireGuard app (remote access)",
            bg="#2b2b2b", fg="#cccccc", font=("Arial", 9, "bold"),
        ).pack(pady=(10, 5))

        wg_qr = qrcode.QRCode(box_size=6, border=2)
        wg_qr.add_data(wg_conf)
        wg_qr.make(fit=True)
        wg_img = wg_qr.make_image(fill_color="black", back_color="white")
        wg_tk_image = ImageTk.PhotoImage(wg_img)

        wg_label = tk.Label(wg_col, image=wg_tk_image, bg="#2b2b2b")
        wg_label.image = wg_tk_image   # prevent garbage collection
        wg_label.pack(pady=(0, 15))
    else:
        setup_col = tk.Frame(qr_row, bg="#2b2b2b")
        setup_col.pack(side=tk.LEFT, padx=10)

        status = wireguard.get_status(app)
        if status["tools_available"]:
            msg = "Remote access isn't set up.\nOne-time admin permission needed."
        else:
            msg = ("Remote access isn't set up.\nWireGuard will be installed "
                   "automatically.\nOne-time admin permission needed.")
        btn_state = "normal"

        tk.Label(
            setup_col, text=msg, bg="#2b2b2b", fg="#cccccc",
            font=("Arial", 9), justify="center", wraplength=200,
        ).pack(pady=(0, 10))

        def do_setup():
            top.destroy()               # close the pairing window
            open_remote_setup(app)      # diagnose → prompt → setup

        setup_btn = tk.Button(
            setup_col, text="Set up remote access", command=do_setup,
            state=btn_state, bg="#bb86fc", fg="#1e1e1e",
            font=("Arial", 9, "bold"), relief="flat", padx=15, pady=5,
        )
        setup_btn.pack()
    # RIGHT: TomeBox pairing payload
    app_col = tk.Frame(qr_row, bg="#2b2b2b")
    app_col.pack(side=tk.LEFT, padx=10)

    tk.Label(
        app_col,
        text="2. TomeBox app\n(pair library)" if wg_conf else "Scan with the TomeBox app",
        bg="#2b2b2b", fg="#cccccc",
        font=("Arial", 9, "bold"), justify="center",
    ).pack(pady=(0, 6))

    qr = qrcode.QRCode(box_size=5, border=2)
    qr.add_data(current_state["qr_text"])
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    tk_image = ImageTk.PhotoImage(img)

    qr_label = tk.Label(app_col, image=tk_image, bg="#2b2b2b")
    qr_label.image = tk_image
    qr_label.pack()
    # Second QR: the raw WireGuard config, imported by the WireGuard app.
    # if wg_conf:
    #     tk.Label(
    #         main_frame,
    #         text="1. Scan this with the WireGuard app (remote access)",
    #         bg="#2b2b2b", fg="#cccccc", font=("Arial", 9, "bold"),
    #     ).pack(pady=(10, 5))

    #     wg_qr = qrcode.QRCode(box_size=6, border=2)
    #     wg_qr.add_data(wg_conf)
    #     wg_qr.make(fit=True)
    #     wg_img = wg_qr.make_image(fill_color="black", back_color="white")
    #     wg_tk_image = ImageTk.PhotoImage(wg_img)

    #     wg_label = tk.Label(main_frame, image=wg_tk_image, bg="#2b2b2b")
    #     wg_label.image = wg_tk_image   # prevent garbage collection
    #     wg_label.pack(pady=(0, 15))
    tk.Label(
        main_frame, text="Or open this URL manually:",
        bg="#2b2b2b", fg="#cccccc", font=("Arial", 9),
    ).pack(pady=(0, 5))

    url_text = tk.Text(
        main_frame, height=2, wrap="word", bg="#1e1e1e", fg="#bb86fc",
        font=("Consolas", 9), relief="flat", padx=10, pady=8,
    )
    url_text.insert("1.0", current_state["manual_url"])
    url_text.config(state="disabled")
    url_text.pack(fill="x", pady=(0, 5))

    # --- Button Callbacks (NOTE: nested inside open_pairing_window) ---
    def copy_url():
        top.clipboard_clear()
        top.clipboard_append(current_state["manual_url"])
        copy_btn.config(text="Copied!")
        top.after(1500, lambda: copy_btn.config(text="Copy URL"))

    def refresh_qr_code():
        # Re-mint ONLY the OTP. Deliberately does NOT call build_pairing_payload
        # again — that would provision a brand-new WireGuard peer on every click.
        now = time.time()
        app._active_otps = {k: v for k, v in app._active_otps.items() if v > now}
        new_otp = secrets.token_hex(4)
        app._active_otps[new_otp] = now + 600

        p = current_state["payload"]
        p["otp"] = new_otp
        current_state["qr_text"] = json.dumps(p)
        current_state["manual_url"] = f"{p['lan']}/auth?otp={new_otp}"

        new_qr = qrcode.QRCode(box_size=8, border=2)
        new_qr.add_data(current_state["qr_text"])
        new_qr.make(fit=True)
        new_img = new_qr.make_image(fill_color="black", back_color="white")
        new_tk_image = ImageTk.PhotoImage(new_img)

        qr_label.config(image=new_tk_image)
        qr_label.image = new_tk_image  # prevent garbage collection

        url_text.config(state="normal")
        url_text.delete("1.0", tk.END)
        url_text.insert("1.0", current_state["manual_url"])
        url_text.config(state="disabled")

    # --- Action Buttons ---
    btn_frame = tk.Frame(main_frame, bg="#2b2b2b")
    btn_frame.pack(pady=(10, 0))

    copy_btn = tk.Button(
        btn_frame,
        text="Copy URL",
        command=copy_url,
        bg="#bb86fc",
        fg="#1e1e1e",
        font=("Arial", 9, "bold"),
        relief="flat",
        padx=15,
        pady=5,
    )
    copy_btn.pack(side=tk.LEFT, padx=5)

    regen_btn = tk.Button(
        btn_frame,
        text="Refresh Code",
        command=refresh_qr_code,
        bg="#ff4444",
        fg="white",
        font=("Arial", 9, "bold"),
        relief="flat",
        padx=15,
        pady=5,
    )
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

def reset_remote_access(app):
    """Full teardown: clear settings, delete config, uninstall tunnel service.
    The tunnel uninstall needs elevation, so we shell out elevated for that one step."""
    from tkinter import messagebox
    from core import wireguard

    confirm = messagebox.askyesno(
        "Remove Remote Access",
        "This will:\n\n"
        "• Remove the WireGuard tunnel from this computer\n"
        "• Delete the tunnel configuration\n"
        "• Clear all paired device slots\n\n"
        "Paired phones will need to re-pair after you set up again.\n\n"
        "Continue?",
        parent=app.root,
    )
    if not confirm:
        return

    # 1. Uninstall the tunnel service (needs elevation).
    try:
        be = wireguard._backend()
        be.uninstall_tunnel()
    except Exception as e:
        app.logger(f"Tunnel uninstall failed (may need admin): {e}")

    # 2. Delete the config file.
    conf_path = app.settings.get("wg_conf_path", "")
    if conf_path:
        import os, shutil
        wg_dir = os.path.dirname(conf_path)
        if os.path.isdir(wg_dir):
            shutil.rmtree(wg_dir, ignore_errors=True)

    # 3. Clear all WG keys from settings.
    for key in ("wg_server_public", "wg_endpoint", "wg_pool",
                "wg_conf_path", "wg_listen_port"):
        app.settings.pop(key, None)
    app.db.save_settings(app.settings)

    messagebox.showinfo(
        "Remote Access Removed",
        "Remote access has been removed. You can set it up again from the "
        "pairing window at any time.",
        parent=app.root,
    )

def open_remote_setup(app):
    """Diagnose the connection, then either dead-end (CGNAT) or offer setup with an
    optional DDNS hostname (the normal case)."""
    from core import wireguard

    win = tk.Toplevel(app.root)
    win.title("Set Up Remote Access")
    win.configure(bg="#2b2b2b")
    win.transient(app.root)
    win.resizable(False, False)

    frame = tk.Frame(win, bg="#2b2b2b", padx=25, pady=20)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Set Up Remote Access", font=("Arial", 15, "bold"),
             bg="#2b2b2b", fg="white").pack(pady=(0, 12))

    status_lbl = tk.Label(frame, text="Checking your connection…", bg="#2b2b2b",
                          fg="#cccccc", wraplength=380, justify="left")
    status_lbl.pack(pady=(0, 12))

    body = tk.Frame(frame, bg="#2b2b2b")
    body.pack(fill="x")

    def ask_connection_type():
        """Put the CGNAT question to the user directly. Auto-detection lied too
        often (ISPs that don't lay out their networks conventionally), so the user
        tells us what they have."""
        for w in body.winfo_children():
            w.destroy()

        status_lbl.config(
            text="What kind of internet connection does this computer have?",
            fg="#cccccc",
        )

        tk.Label(body,
                 text="If you're not sure: most home connections have a public IP. "
                      "If your ISP uses CGNAT (common on mobile broadband and some "
                      "fibre plans), incoming connections can't reach you directly.",
                 bg="#2b2b2b", fg="#888", wraplength=380, justify="left"
                 ).pack(pady=(0, 14))

        tk.Button(body, text="I have a public IP address",
                  command=lambda: _render_endpoint_form(prefill_detected()),
                  bg="#bb86fc", fg="#1e1e1e", relief="flat",
                  font=("Arial", 10, "bold"), padx=15, pady=6).pack(fill="x", pady=(0, 6))

        tk.Button(body, text="I'm behind CGNAT / not directly reachable",
                  command=cgnat_path,
                  bg="#555", fg="white", relief="flat",
                  font=("Arial", 10), padx=15, pady=6).pack(fill="x", pady=(0, 6))

        tk.Label(body,
                 text="Not sure? Try “public IP” first — if remote access doesn't "
                      "work afterwards, come back and choose CGNAT.",
                 bg="#2b2b2b", fg="#888", font=("Arial", 8),
                 wraplength=380, justify="left").pack(pady=(6, 0))

    def prefill_detected() -> str:
        """Best-effort IP to pre-fill — a convenience, NOT a gate. The user can
        overwrite it with anything (a DDNS hostname, a corrected IP)."""
        try:
            ip = wireguard.detect_public_endpoint()
            return ip or ""
        except Exception:
            return ""

    def cgnat_path():
        for w in body.winfo_children():
            w.destroy()
        status_lbl.config(text="Remote access needs a reachable address.", fg="#ffcc66")
        tk.Label(body,
                 text="Because your connection is behind CGNAT, TomeBox can't be "
                      "reached directly from the internet. Your options:\n\n"
                      "• Ask your ISP for a public IP (often free on request)\n"
                      "• Use a forwarded port from a VPN or relay (advanced)\n\n"
                      "A dynamic DNS hostname will NOT fix CGNAT.",
                 bg="#2b2b2b", fg="#cccccc", wraplength=380, justify="left"
                 ).pack(pady=(0, 12))

        tk.Button(body, text="I have a forwarded port →",
                  command=_render_forwarded_form,
                  bg="#bb86fc", fg="#1e1e1e", relief="flat",
                  font=("Arial", 9, "bold"), padx=15, pady=5).pack(pady=(0, 6))
        tk.Button(body, text="Close", command=win.destroy,
                  bg="#555", fg="white", relief="flat", padx=15, pady=5).pack()
        
    def _render_forwarded_form():
        for w in body.winfo_children():
            w.destroy()
        status_lbl.config(
            text="Enter the forwarded connection details from your VPN or relay.",
            fg="#cccccc",
        )

        tk.Label(body, text="Public address & port (what your VPN/relay exposes):",
                 bg="#2b2b2b", fg="#cccccc", justify="left").pack(anchor="w", pady=(4, 2))
        ep = tk.Entry(body, bg="#1e1e1e", fg="#bb86fc", relief="flat",
                      font=("Consolas", 10), width=38)
        ep.insert(0, "")
        ep.pack(fill="x", pady=(0, 2))
        tk.Label(body, text="e.g.  proton-server-ip:41234",
                 bg="#2b2b2b", fg="#888", font=("Arial", 8)).pack(anchor="w", pady=(0, 10))

        tk.Label(body,
                 text="WireGuard will listen on the forwarded port so the two line "
                      "up. Note: remote access only works while your VPN is "
                      "connected on this computer.",
                 bg="#2b2b2b", fg="#ffcc66", wraplength=380,
                 justify="left").pack(anchor="w", pady=(0, 10))
        def done(ok, msg):
            messagebox.showinfo("Remote Access", msg, parent=app.root)
            win.destroy()
            if ok:
                open_pairing_window(app)
        def proceed():
            raw = ep.get().strip()
            if ":" not in raw:
                messagebox.showwarning("Address needed",
                                       "Enter the address and port as ip:port.",
                                       parent=win)
                return
            host, _, port = raw.rpartition(":")
            try:
                port = int(port)
            except ValueError:
                messagebox.showwarning("Port", "Port must be a number.", parent=win)
                return

            app.settings["wg_endpoint"] = f"{host.strip()}:{port}"
            app.settings["wg_listen_port"] = port   # NEW: match the forwarded port
            app.db.save_settings(app.settings)

            btn.config(text="Setting up…", state="disabled")
            win.update_idletasks()

            def worker():
                ok, msg = wireguard.launch_setup(app, app_port=8000)
                app.root.after(0, lambda: done(ok, msg))
            app.thread_pool.submit(worker, task_type="standard")

        btn = tk.Button(body, text="Set up with forwarded port", command=proceed,
                        bg="#bb86fc", fg="#1e1e1e", font=("Arial", 10, "bold"),
                        relief="flat", padx=15, pady=6)
        btn.pack()

    def _render_endpoint_form(prefill=""):
        tk.Label(body,
                 text="Remote address (leave as-is, or enter a dynamic DNS hostname\n"
                      "if your home IP address changes):",
                 bg="#2b2b2b", fg="#cccccc", justify="left").pack(anchor="w", pady=(4, 4))

        entry = tk.Entry(body, bg="#1e1e1e", fg="#bb86fc", relief="flat",
                         font=("Consolas", 10), width=38)
        entry.insert(0, prefill)
        entry.pack(fill="x", pady=(0, 4))

        tk.Label(body,
                 text="Examples:  203.0.113.45   or   myhome.duckdns.org",
                 bg="#2b2b2b", fg="#888", font=("Arial", 8)).pack(anchor="w", pady=(0, 12))
        tk.Label(body, text="WireGuard port (change only if 51820 is already "
                            "forwarded to another machine):",
                 bg="#2b2b2b", fg="#cccccc", justify="left").pack(anchor="w", pady=(8, 2))

        port_entry = tk.Entry(body, bg="#1e1e1e", fg="#bb86fc", relief="flat",
                              font=("Consolas", 10), width=10)
        port_entry.insert(0, str(app.settings.get("wg_listen_port",
                                                  wireguard.LISTEN_PORT)))
        port_entry.pack(anchor="w", pady=(0, 4))

        tk.Label(body, text="Your router must forward this UDP port to this computer.",
                 bg="#2b2b2b", fg="#888", font=("Arial", 8)).pack(anchor="w", pady=(0, 12))
        def proceed():
            host = entry.get().strip()
            if not host:
                messagebox.showwarning("Address needed",
                                       "Enter a public IP or a dynamic DNS hostname.",
                                       parent=win)
                return
            try:
                port = int(port_entry.get().strip())
                if not (1 <= port <= 65535):
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Port", "Port must be a number between 1 and 65535.",
                                       parent=win)
                return

            host = host.split(":")[0]
            app.settings["wg_listen_port"] = port
            app.settings["wg_endpoint"] = f"{host}:{port}"
            app.db.save_settings(app.settings)

            btn.config(text="Setting up…", state="disabled")
            win.update_idletasks()

            def worker():
                ok, msg = wireguard.launch_setup(app, app_port=8000)
                app.root.after(0, lambda: done(ok, msg))

            app.thread_pool.submit(worker, task_type="standard")

        def done(ok, msg):
            messagebox.showinfo("Remote Access", msg, parent=app.root)
            win.destroy()
            if ok:
                open_pairing_window(app)   # reopen — both QRs will be there now

        btn = tk.Button(body, text="Set up remote access", command=proceed,
                        bg="#bb86fc", fg="#1e1e1e", font=("Arial", 10, "bold"),
                        relief="flat", padx=15, pady=6)
        btn.pack()

    # Run the network probe off the UI thread — it does an HTTP call.
    # app.thread_pool.submit(ask_connection_type(), task_type="standard")
    ask_connection_type()

def open_match_to_audible_window(app, filepath):
    import os

    # 1. Auto-Populate Logic
    local_data = app.library_manager.local_library.get(filepath, {})
    initial_title = local_data.get("title", os.path.basename(filepath))
    initial_author = local_data.get("authors", "")

    if initial_author in ["Unknown Author", "Local File", "Unknown"]:
        initial_author = ""

    # Clean up file extensions from the title so the search is cleaner
    if initial_title.lower().endswith((".m4b", ".mp3", ".aax", ".aaxc")):
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

    ttk.Label(main_frame, text="Search Catalogs", font=("Segoe UI", 14, "bold")).pack(
        anchor="w"
    )
    ttk.Label(
        main_frame,
        text=f"File: {os.path.basename(filepath)}",
        font=("Segoe UI", 9, "italic"),
    ).pack(anchor="w", pady=(0, 15))

    # --- Search Form ---
    search_frame = ttk.Frame(main_frame)
    search_frame.pack(fill="x", pady=(0, 10))

    ttk.Label(search_frame, text="Title:").grid(
        row=0, column=0, sticky="e", padx=5, pady=2
    )
    title_var = tk.StringVar(value=initial_title)
    ttk.Entry(search_frame, textvariable=title_var, width=40).grid(
        row=0, column=1, sticky="w", pady=2
    )

    ttk.Label(search_frame, text="Author:").grid(
        row=1, column=0, sticky="e", padx=5, pady=2
    )
    author_var = tk.StringVar(value=initial_author)
    ttk.Entry(search_frame, textvariable=author_var, width=40).grid(
        row=1, column=1, sticky="w", pady=2
    )

    options_frame = ttk.LabelFrame(main_frame, text="Fields to Overwrite", padding=5)
    options_frame.pack(fill="x", pady=(0, 10))

    apply_title_var = tk.BooleanVar(value=True)
    apply_author_var = tk.BooleanVar(value=True)
    apply_series_var = tk.BooleanVar(value=True)
    apply_cover_var = tk.BooleanVar(value=True)

    ttk.Checkbutton(options_frame, text="Title", variable=apply_title_var).pack(
        side=tk.LEFT, padx=10
    )
    ttk.Checkbutton(options_frame, text="Author", variable=apply_author_var).pack(
        side=tk.LEFT, padx=10
    )
    ttk.Checkbutton(options_frame, text="Series", variable=apply_series_var).pack(
        side=tk.LEFT, padx=10
    )
    ttk.Checkbutton(options_frame, text="Cover Art", variable=apply_cover_var).pack(
        side=tk.LEFT, padx=10
    )

    status_var = tk.StringVar(value="")

    # --- Results Canvas ---
    results_outer = ttk.Frame(main_frame)
    results_outer.pack(fill="both", expand=True, pady=(10, 10))

    canvas = tk.Canvas(results_outer, bg=bg_color, highlightthickness=0)
    scrollbar = ttk.Scrollbar(results_outer, orient="vertical", command=canvas.yview)
    inner_frame = tk.Frame(canvas, bg=bg_color)

    inner_frame.bind(
        "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas_win = canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_win, width=e.width))

    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill="both", expand=True)
    scrollbar.pack(side=tk.RIGHT, fill="y")

    # --- Bottom Controls ---
    bottom_frame = ttk.Frame(main_frame)
    bottom_frame.pack(fill="x", side=tk.BOTTOM)

    status_label = ttk.Label(
        bottom_frame, textvariable=status_var, font=("Segoe UI", 9)
    )
    status_label.pack(side=tk.LEFT)

    btn_frame = ttk.Frame(bottom_frame)
    btn_frame.pack(side=tk.RIGHT)

    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(
        side=tk.RIGHT, padx=(5, 0)
    )
    apply_btn = ttk.Button(btn_frame, text="Apply Match", state=tk.DISABLED)
    apply_btn.pack(side=tk.RIGHT)

    search_btn = ttk.Button(search_frame, text="Search")
    search_btn.grid(row=0, column=2, rowspan=2, padx=10, sticky="ns")

    selected_asin = tk.StringVar(value="")
    app.scraper_image_cache = {}  # Prevent Python garbage collection from deleting the images

    def select_item(asin, row_frame):
        selected_asin.set(asin)
        for child in inner_frame.winfo_children():
            child.config(bg=bg_color)
        row_frame.config(bg="#4a90e2")  # Highlight color
        apply_btn.config(state=tk.NORMAL)

    def populate_results(products):
        for widget in inner_frame.winfo_children():
            widget.destroy()

        if not products:
            status_var.set("No matches found.")
            return

        status_var.set(f"Found {len(products)} result(s). Select one to apply.")

        for idx, product in enumerate(products):
            asin = product.get("asin")
            title = product.get("title", "Unknown")
            raw_authors = product.get("authors", [])
            authors = ", ".join(
                [a.get("name", "") for a in raw_authors if isinstance(a, dict)]
            )
            source = product.get("source", "Audible")

            # Create Row
            row_frame = tk.Frame(
                inner_frame,
                bg=bg_color,
                pady=5,
                padx=5,
                highlightthickness=1,
                highlightbackground="#cccccc",
            )
            row_frame.pack(fill="x", pady=2, padx=2)
            row_frame.bind(
                "<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf)
            )

            # Image Placeholder
            img_container = tk.Frame(row_frame, width=128, height=128, bg="#dddddd")
            img_container.pack_propagate(False)
            img_container.pack(side=tk.LEFT, padx=(0, 10))
            img_container.bind(
                "<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf)
            )

            img_lbl = tk.Label(img_container, text="Loading...", bg="#dddddd")
            img_lbl.pack(expand=True, fill="both")
            img_lbl.bind(
                "<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf)
            )

            # Metadata Info
            info_frame = tk.Frame(row_frame, bg=bg_color)
            info_frame.pack(side=tk.LEFT, fill="both", expand=True)
            info_frame.bind(
                "<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf)
            )

            t_lbl = tk.Label(
                info_frame,
                text=title,
                font=("Segoe UI", 10, "bold"),
                bg=bg_color,
                fg=fg_color,
                anchor="w",
                justify="left",
                wraplength=450,
            )
            t_lbl.pack(fill="x")
            t_lbl.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            a_lbl = tk.Label(
                info_frame,
                text=authors,
                font=("Segoe UI", 9),
                bg=bg_color,
                fg=fg_color,
                anchor="w",
            )
            a_lbl.pack(fill="x")
            a_lbl.bind("<Button-1>", lambda e, a=asin, rf=row_frame: select_item(a, rf))

            tk.Label(
                info_frame,
                text=f"Source: {source} | ASIN: {asin}",
                font=("Segoe UI", 8, "italic"),
                fg=fg_color,
                bg=bg_color,
                anchor="w",
            ).pack(fill="x")

            # 3. Dynamic Thumbnail Fetching
            img_url = product.get("cover_url")
            if not img_url and "product_images" in product:
                images = product.get("product_images", {})
                img_url = images.get("115") or images.get("252") or images.get("500")

            if img_url:

                def load_img(url, lbl, current_asin):
                    try:
                        if url.startswith("http:"):
                            url = url.replace("http:", "https:")
                        res = requests.get(url, timeout=5)
                        if res.status_code == 200:
                            img = Image.open(io.BytesIO(res.content))
                            img.thumbnail((128, 128))
                            photo = ImageTk.PhotoImage(img)
                            app.scraper_image_cache[f"{current_asin}_{url}"] = photo
                            app.root.after(
                                0, lambda: lbl.config(image=photo, text="", bg=bg_color)
                            )
                    except Exception:
                        app.root.after(0, lambda: lbl.config(text="No Cover"))

                threading.Thread(
                    target=load_img, args=(img_url, img_lbl, asin), daemon=True
                ).start()
            else:
                img_lbl.config(text="No Cover")

    def do_search():
        if str(search_btn["state"]) == tk.DISABLED:
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
            print(
                f"[Scraper] API returned {len(products) if products else 0} results for query: {query}"
            )

            def safe_update():
                if win.winfo_exists():
                    if search_btn.winfo_exists():
                        search_btn.config(state=tk.NORMAL)
                    populate_results(products)

            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe(
                "metadata.search_complete", capture_results
            )
            app.metadata_manager.event_bus.unsubscribe("metadata.error", capture_error)

        def capture_error(error_msg=None, **kwargs):
            def safe_update():
                if win.winfo_exists():
                    status_var.set(f"Error: {error_msg}")
                    if search_btn.winfo_exists():
                        search_btn.config(state=tk.NORMAL)

            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe(
                "metadata.search_complete", capture_results
            )
            app.metadata_manager.event_bus.unsubscribe("metadata.error", capture_error)

        app.metadata_manager.event_bus.subscribe(
            "metadata.search_complete", capture_results
        )
        app.metadata_manager.event_bus.subscribe("metadata.error", capture_error)

        app.metadata_manager.search_catalog(filepath, query)

    def do_apply():
        asin = selected_asin.get()
        if not asin:
            status_var.set("Select a result first.")
            return

        if not messagebox.askyesno(
            "Confirm Match",
            "Link this file to the selected title?\n\nThis will overwrite any existing metadata.",
        ):
            return

        status_var.set("Applying metadata and embedding tags...")
        apply_btn.config(state=tk.DISABLED)
        win.update_idletasks()

        def on_done(filepath=None, title=None, **kwargs):
            def safe_update():
                if win.winfo_exists():
                    win.destroy()
                    app.root.lift()
                    app.root.focus_force()
            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        def on_error(error_msg=None, **kwargs):
            def safe_update():
                if win.winfo_exists():
                    status_var.set(f"Error: {error_msg}")
                    if apply_btn.winfo_exists():
                        apply_btn.config(state=tk.NORMAL)

            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe(
                "metadata.apply_complete", on_done
            )
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        app.metadata_manager.event_bus.subscribe("metadata.apply_complete", on_done)
        app.metadata_manager.event_bus.subscribe("metadata.error", on_error)
        
        fields = {
            "title": apply_title_var.get(),
            "author": apply_author_var.get(),
            "series": apply_series_var.get(),
            "cover": apply_cover_var.get(),
        }
        app.metadata_manager.apply_scraped_metadata(
            filepath, asin, fields_to_apply=fields
        )

    search_btn.config(command=do_search)
    apply_btn.config(command=do_apply)
    win.bind("<Return>", lambda e: do_search())

    if initial_title or initial_author:
        win.after(100, do_search)

    win.focus_set()

def open_bulk_metadata_window(app, filepaths):
    """Opens a specialized dialog for editing metadata across multiple files iteratively."""
    if not filepaths:
        return

    win = tk.Toplevel(app.root)
    win.title(f"Bulk Edit Metadata ({len(filepaths)} items)")
    win.geometry("440x600")
    win.transient(app.root)
    win.grab_set()

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#1e1e1e"
    win.configure(bg=bg_color)

    main_frame = ttk.Frame(win, padding=10)
    main_frame.pack(fill="both", expand=True)

    # --- Dirty Tracking: Find shared values ---
    authors_set = set()
    series_set = set()
    narrators_set = set()

    for path in filepaths:
        data = app.library_manager.local_library.get(path, {})
        authors_set.add(data.get("authors", "").strip())
        series_set.add(data.get("series", "").strip())
        narrators_set.add(data.get("narrator", "").strip())

    initial_author = list(authors_set)[0] if len(authors_set) == 1 else "<multiple values>"
    initial_series = list(series_set)[0] if len(series_set) == 1 else "<multiple values>"
    initial_narrator = list(narrators_set)[0] if len(narrators_set) == 1 else "<multiple values>"

    # --- Scaffold the Form Layout ---
    form_frame = ttk.Frame(main_frame)
    form_frame.pack(fill="x", pady=10)

    # ttk.Label(form_frame, text="Title:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    # title_var = tk.StringVar(value=f"{len(filepaths)} titles selected")
    # ttk.Entry(form_frame, textvariable=title_var, width=38, state="disabled").grid(row=0, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Author(s):").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    author_var = tk.StringVar(value=initial_author)
    ttk.Entry(form_frame, textvariable=author_var, width=38).grid(row=1, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Narrator:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    narrator_var = tk.StringVar(value=initial_narrator)
    ttk.Entry(form_frame, textvariable=narrator_var, width=38).grid(row=2, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Series:").grid(row=3, column=0, sticky="e", padx=5, pady=5)
    series_var = tk.StringVar(value=initial_series)
    ttk.Entry(form_frame, textvariable=series_var, width=38).grid(row=3, column=1, sticky="w", pady=5)

    # ttk.Label(form_frame, text="ASIN:").grid(row=4, column=0, sticky="e", padx=5, pady=5)
    # ttk.Entry(form_frame, textvariable=tk.StringVar(value="<multiple values>"), width=38, state="disabled").grid(row=4, column=1, sticky="w", pady=5)

    ttk.Label(form_frame, text="Read Status:").grid(row=5, column=0, sticky="e", padx=5, pady=5)
    status_var = tk.StringVar(value="— Keep current —")
    ttk.Combobox(form_frame, textvariable=status_var, values=["— Keep current —", "Unread", "Finished"], state="readonly", width=35).grid(row=5, column=1, sticky="w", pady=5)
    # --- Series ordering ---
    order_frame = ttk.LabelFrame(main_frame, text="Series Order", padding=8)
    order_frame.pack(fill="both", expand=True, pady=(8, 4))

    ttk.Label(
        order_frame,
        text="Set the reading order for the selected books. Position 1 is first.",
        font=("Arial", 8),
    ).pack(anchor="w", pady=(0, 4))

    order_list = tk.Listbox(
        order_frame, height=8, bg="#1e1e1e", fg="#e0e0e0",
        selectbackground="#bb86fc", activestyle="none",
    )
    order_list.pack(fill="both", expand=True)

    lib = app.library_manager.local_library
    # Existing sequence first, then unsequenced by title — so a partially ordered
    # series opens in a sensible state rather than jumbled.
    ordered = sorted(
        filepaths,
        key=lambda p: (
            lib.get(p, {}).get("series_sequence") is None,
            lib.get(p, {}).get("series_sequence") or 0,
            lib.get(p, {}).get("title", "").lower(),
        ),
    )
    state = {"paths": ordered}
    order_state = {"paths": ordered}

    def redraw(select=None):
        order_list.delete(0, tk.END)
        for i, p in enumerate(order_state["paths"], start=1):
            order_list.insert(tk.END, f"{i:>3}.  {lib.get(p, {}).get('title', p)}")
        if select is not None:
            order_list.selection_set(select)

    def move(delta):
        sel = order_list.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(order_state["paths"])):
            return
        order_state["paths"][i], order_state["paths"][j] = order_state["paths"][j], order_state["paths"][i]
        redraw(select=j)

    redraw()

    move_frame = ttk.Frame(order_frame)
    move_frame.pack(fill="x", pady=(4, 0))
    ttk.Button(move_frame, text="▲ Up", command=lambda: move(-1), width=8).pack(side=tk.LEFT)
    ttk.Button(move_frame, text="▼ Down", command=lambda: move(1), width=8).pack(side=tk.LEFT, padx=4)

    apply_order_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        move_frame, text="Apply this order", variable=apply_order_var
    ).pack(side=tk.RIGHT)
    # --- Options ---
    options_frame = ttk.Frame(main_frame)
    options_frame.pack(fill="x", pady=5)
    
    embed_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(options_frame, text="Embed tags into audio files (FFmpeg)", variable=embed_var).pack(side=tk.LEFT)

    feedback_var = tk.StringVar(value="")
    ttk.Label(main_frame, textvariable=feedback_var, font=("Segoe UI", 9)).pack(anchor="w", pady=5)

    # --- Controls ---
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", side=tk.BOTTOM)

    def do_save():
        if apply_order_var.get():
            for i, p in enumerate(order_state["paths"], start=1):
                entry = app.library_manager.local_library.setdefault(p, {})
                entry["series_sequence"] = float(i)
                entry["series_sequence_user_set"] = True
            app.db.save_local_db(app.library_manager.local_library)
            bump_library_version(app)
        # Lock the UI
        save_btn.config(state=tk.DISABLED)
        cancel_btn.config(state=tk.DISABLED)
        win.protocol("WM_DELETE_WINDOW", lambda: None) # Prevent X-ing out during save

        new_author = author_var.get().strip()
        new_series = series_var.get().strip()
        new_narrator = narrator_var.get().strip()
        new_status = status_var.get()
        embed = embed_var.get()

        total_files = len(filepaths)
        state = {"completed": 0, "errors": 0, "failed": []}

        def on_err(error_msg=None, filepath=None, title=None, **kwargs):
            state["errors"] += 1
            state["failed"].append(title or (os.path.basename(filepath) if filepath else "Unknown book"))
            app.root.after(0, check_finished)

        def update_feedback():
            feedback_var.set(f"Saving batch... ({state['completed'] + state['errors']} / {total_files} processed)")
            win.update_idletasks()

        update_feedback()

        # --- Event Bus Listeners ---
        def on_done(filepath=None, **kwargs):
            if filepath in filepaths:
                state["completed"] += 1
                app.root.after(0, check_finished)

        def check_finished():
            update_feedback()
            # Wait until all submitted background tasks have fired an event
            if state["completed"] + state["errors"] >= total_files:
                def safe_update():
                    if hasattr(app, "image_cache"):
                        app.image_cache.clear()
                    app.library_presenter.refresh_library_ui()
                    
                    if win.winfo_exists():
                        win.destroy()
                        app.root.lift()
                        app.root.focus_force()

                    # Release the popup suppression flag
                    app._is_batch_editing = False 
                    
                    from tkinter import messagebox
                    if state["errors"] == 0:
                        messagebox.showinfo("Success", f"Bulk metadata applied to {state['completed']} items.", parent=app.root)
                    else:
                        failed_list = "\n".join(f"  • {b}" for b in state["failed"])
                        messagebox.showwarning(
                            "Finished with Errors",
                            f"Processed {total_files} items.\nSuccess: {state['completed']}\n"
                            f"Errors: {state['errors']}\n\nFailed:\n{failed_list}",
                            parent=app.root,
                        )

                # Small delay to let the user see the 100% completion state
                app.root.after(250, safe_update)
                app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
                app.metadata_manager.event_bus.unsubscribe("metadata.error", on_err)

        app.metadata_manager.event_bus.subscribe("metadata.apply_complete", on_done)
        app.metadata_manager.event_bus.subscribe("metadata.error", on_err)

        # Tell ActionRouter to ignore events while we batch process
        app._is_batch_editing = True  

        # --- The Iterative Submission Loop ---
        for path in filepaths:
            local = app.library_manager.local_library.get(path, {})

            payload = {
                "title": local.get("title", ""),
                "asin": local.get("asin", ""),
                "active_profile": getattr(app, "active_profile", "Main"),
            }

            if new_author != initial_author and new_author != "<multiple values>":
                payload["authors"] = new_author
            else:
                payload["authors"] = local.get("authors", "")

            if new_narrator != initial_narrator and new_narrator != "<multiple values>":
                payload["narrator"] = new_narrator
            else:
                payload["narrator"] = local.get("narrator", "")

            if new_series != initial_series and new_series != "<multiple values>":
                payload["series"] = new_series
            else:
                payload["series"] = local.get("series", "")

            if new_status != "— Keep current —":
                payload["status_override"] = new_status
                dur_min = local.get("duration_min", 0)
                payload["duration_sec"] = dur_min * 60 if dur_min else 0

            if getattr(app, "file_path", None) == path:
                if hasattr(app, "playback_presenter"):
                    app.playback_presenter.unload_current_file()
                app.file_path = None

            app.metadata_manager.apply_manual_metadata(
                path,
                payload,
                embed_to_file=embed,
                new_cover_path=None
            )

    cancel_btn = ttk.Button(btn_frame, text="Cancel", command=win.destroy)
    cancel_btn.pack(side=tk.RIGHT, padx=5)
    
    save_btn = ttk.Button(btn_frame, text="Save Batch", command=do_save)
    save_btn.pack(side=tk.RIGHT, padx=5)


def open_manual_metadata_window(app, filepath):
    import os
    from tkinter import filedialog

    from PIL import Image, ImageTk

    local_data = app.library_manager.local_library.get(filepath, {})
    if not local_data:
        return

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

    ttk.Label(main_frame, text="Manual Edit", font=("Segoe UI", 14, "bold")).pack(
        anchor="w", pady=(0, 15)
    )

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
        # Default to THIS book's folder, resolving playlists and missing files.
        start_dir = None
        if local_data.get("is_playlist"):
            for ch in local_data.get("chapters", []):
                cp = ch.get("file_path")
                if cp and os.path.isfile(cp):
                    start_dir = os.path.dirname(cp)
                    break
        if not start_dir and filepath:
            book_dir = os.path.dirname(filepath)
            if book_dir and os.path.isdir(book_dir):
                start_dir = book_dir

        # Never open a generic system dir — fall back to a known app location.
        if not start_dir:
            candidates = [getattr(app, "_last_cover_dir", None)]
            if getattr(app, "settings", None):
                candidates += app.settings.get("library_folders", [])
            candidates.append(getattr(app, "covers_dir", None))
            start_dir = next((c for c in candidates if c and os.path.isdir(c)), None)

        path = filedialog.askopenfilename(
            parent=win,
            title="Select Cover Art",
            initialdir=start_dir,
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp")],
        )
        if path:
            selected_cover_path[0] = path
            update_preview(path)
            app._last_cover_dir = os.path.dirname(path)

    ttk.Button(cover_frame, text="Change Cover...", command=pick_cover).pack()

    # --- Right Column: Text Form ---
    form_frame = ttk.Frame(content_frame)
    form_frame.pack(side=tk.LEFT, fill="both", expand=True)

    ttk.Label(form_frame, text="Title:").grid(
        row=0, column=0, sticky="e", padx=5, pady=5
    )
    title_var = tk.StringVar(value=local_data.get("title", ""))
    ttk.Entry(form_frame, textvariable=title_var, width=38).grid(
        row=0, column=1, sticky="w", pady=5
    )

    ttk.Label(form_frame, text="Author:").grid(
        row=1, column=0, sticky="e", padx=5, pady=5
    )
    author_var = tk.StringVar(value=local_data.get("authors", ""))
    ttk.Entry(form_frame, textvariable=author_var, width=38).grid(
        row=1, column=1, sticky="w", pady=5
    )

    ttk.Label(form_frame, text="Narrator:").grid(
        row=2, column=0, sticky="e", padx=5, pady=5
    )
    narrator_var = tk.StringVar(value=local_data.get("narrator", ""))
    ttk.Entry(form_frame, textvariable=narrator_var, width=38).grid(
        row=2, column=1, sticky="w", pady=5
    )

    ttk.Label(form_frame, text="Series:").grid(
        row=3, column=0, sticky="e", padx=5, pady=5
    )
    series_var = tk.StringVar(value=local_data.get("series", ""))
    ttk.Entry(form_frame, textvariable=series_var, width=38).grid(
        row=3, column=1, sticky="w", pady=5
    )
    ttk.Label(form_frame, text="Sequence:").grid(
            row=4, column=0, sticky="e", padx=5, pady=5
        )
    seq_var  = tk.StringVar(value=local_data.get("sequence", ""))
    ttk.Entry(form_frame, textvariable=seq_var, width=38).grid(
        row=4, column=1, sticky="w", pady=5
    )

    ttk.Label(form_frame, text="ASIN:").grid(
        row=5, column=0, sticky="e", padx=5, pady=5
    )
    asin_var = tk.StringVar(value=local_data.get("asin", ""))
    ttk.Entry(form_frame, textvariable=asin_var, width=38).grid(
        row=5, column=1, sticky="w", pady=5
    )
    # --- STATUS DROPDOWN ---
    active_prof = getattr(app, "active_profile", "Main")
    
    # Check both the dictionary and the flat fallback field 
    prog_dict = local_data.get("progress", {}).get(active_prof, 0)
    prog_flat = local_data.get("last_position", 0)
    prog = max(prog_dict, prog_flat)
    
    dur_min = local_data.get("duration_min", 0)
    dur_sec = dur_min * 60 if dur_min else 0

    if dur_sec > 0 and prog >= (dur_sec * 0.95):
        current_status = "Finished"
    elif prog > 0:
        current_status = "Started"
    else:
        current_status = "Unread"

    ttk.Label(form_frame, text="Read Status:").grid(row=5, column=0, sticky="e", padx=5, pady=5)
    status_var = tk.StringVar(value=current_status)
    
    combo_values = ["Unread", "Finished"]
    if current_status == "Started":
        combo_values.insert(1, "Started")

    ttk.Combobox(form_frame, textvariable=status_var, values=combo_values, state="readonly", width=35).grid(row=5, column=1, sticky="w", pady=5)
    def _clear_entry_highlight(event):
        try:
            event.widget.selection_clear()
        except tk.TclError:
            pass

    for child in form_frame.winfo_children():
        if isinstance(child, ttk.Entry) and not isinstance(child, ttk.Combobox):
            child.bind("<FocusOut>", _clear_entry_highlight, add="+")
    # --- Options ---
    options_frame = ttk.Frame(main_frame)
    options_frame.pack(fill="x", pady=5)
    embed_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        options_frame, text="Embed tags into audio file (FFmpeg)", variable=embed_var
    ).pack(side=tk.LEFT)

    # Rename the feedback variable to avoid shadowing the combobox
    feedback_var = tk.StringVar(value="")
    ttk.Label(main_frame, textvariable=feedback_var, font=("Segoe UI", 9)).pack(
        anchor="w", pady=5
    )

    # --- Controls ---
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", side=tk.BOTTOM)

    def _fmt_seq(v):
        if v is None:
            return ""
        return str(int(v)) if float(v).is_integer() else str(v)


    def _parse_seq(s):
        s = (s or "").strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def do_save():
        feedback_var.set("Saving...")
        save_btn.config(state=tk.DISABLED)
        win.update_idletasks()


        # status_var.get() is now safely reading from the combobox
        new_data = {
            "title": title_var.get().strip(),
            "authors": author_var.get().strip(),
            "narrator": narrator_var.get().strip(),
            "series": series_var.get().strip(),
            "series_sequence": _parse_seq(seq_var.get()),
            "series_sequence_user_set": bool(seq_var.get().strip()),
            "asin": asin_var.get().strip(),
            "status_override": status_var.get(), 
            "duration_sec": dur_sec,
            "active_profile": active_prof
        }
        if status_var.get() != current_status:
            new_data["status_override"] = status_var.get()
        def on_done(filepath=None, title=None, **kwargs):
            def safe_update():
                if win.winfo_exists():
                    win.destroy()
                    app.root.lift()
                    app.root.focus_force()
            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe("metadata.apply_complete", on_done)
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        def on_error(error_msg=None, **kwargs):
            def safe_update():
                if win.winfo_exists():
                    feedback_var.set(f"Error: {error_msg}")
                    if save_btn.winfo_exists():
                        save_btn.config(state=tk.NORMAL)

            app.root.after(0, safe_update)
            app.metadata_manager.event_bus.unsubscribe(
                "metadata.apply_complete", on_done
            )
            app.metadata_manager.event_bus.unsubscribe("metadata.error", on_error)

        app.metadata_manager.event_bus.subscribe("metadata.apply_complete", on_done)
        app.metadata_manager.event_bus.subscribe("metadata.error", on_error)

        is_loaded = getattr(app, "file_path", None) == filepath
        
        if is_loaded:
            if hasattr(app, "playback_presenter"):
                app.playback_presenter.unload_current_file()
            app.file_path = None 

        app.metadata_manager.apply_manual_metadata(
            filepath,
            new_data,
            embed_to_file=embed_var.get(),
            new_cover_path=selected_cover_path[0],
        )

    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(
        side=tk.RIGHT, padx=(5, 0)
    )
    save_btn = ttk.Button(btn_frame, text="Save", command=do_save)
    save_btn.pack(side=tk.RIGHT)

    win.focus_set()

def open_series_ordering_window(app):
    """Order every book in a series at once — far less tedious than editing each."""
    import tkinter as tk
    from tkinter import ttk, messagebox

    lib = app.library_manager.local_library

    # Group by the series STRING as stored. Imperfect if the user has typos, but
    # that's a library-hygiene problem they can see and fix here.
    groups = {}
    for path, data in lib.items():
        raw = (data.get("series") or "").strip()
        if not raw:
            continue
        key = _normalise_series(raw)
        g = groups.setdefault(key, {"paths": [], "names": {}})
        g["paths"].append(path)
        g["names"][raw] = g["names"].get(raw, 0) + 1

    if not groups:
        messagebox.showinfo("Series Ordering", "No books have a series set.",
                            parent=app.root)
        return

    for g in groups.values():
        g["display"] = max(g["names"].items(), key=lambda kv: kv[1])[0]

    # display name -> normalised key, for the picker
    display_to_key = {g["display"]: k for k, g in groups.items()}

    top = tk.Toplevel(app.root)
    top.title("Series Ordering")
    top.configure(bg="#2b2b2b")
    top.transient(app.root)

    frame = tk.Frame(top, bg="#2b2b2b", padx=20, pady=15)
    frame.pack(fill="both", expand=True)

    tk.Label(frame, text="Series:", bg="#2b2b2b", fg="white").pack(anchor="w")
    series_names = sorted(display_to_key.keys())
    picker = ttk.Combobox(frame, values=series_names, state="readonly", width=50)
    picker.current(0)
    picker.pack(fill="x", pady=(0, 10))

    tk.Label(
        frame,
        text="Drag to reorder, or select and use the buttons. Position 1 is first.",
        bg="#2b2b2b", fg="#aaaaaa", font=("Arial", 8),
    ).pack(anchor="w")

    listbox = tk.Listbox(frame, height=14, width=70, bg="#1e1e1e", fg="#e0e0e0",
                         selectbackground="#bb86fc", activestyle="none")
    listbox.pack(fill="both", expand=True, pady=(4, 8))

    state = {"paths": []}

    def load_series(*_):
        key = display_to_key.get(picker.get())
        paths = list(groups[key]["paths"])
        paths.sort(key=lambda p: (
            lib[p].get("series_sequence") is None,
            lib[p].get("series_sequence") or 0,
            lib[p].get("title", "").lower(),
        ))
        state["paths"] = paths
        redraw()

    def move(delta):
        sel = listbox.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(state["paths"])):
            return
        state["paths"][i], state["paths"][j] = state["paths"][j], state["paths"][i]
        redraw()
        listbox.selection_set(j)

    def redraw():
        listbox.delete(0, tk.END)
        for i, p in enumerate(state["paths"], start=1):
            listbox.insert(tk.END, f"{i:>3}.  {lib[p].get('title', p)}")

    def save():
        key = display_to_key.get(picker.get())
        canonical = groups[key]["display"]
        for i, p in enumerate(state["paths"], start=1):
            lib[p]["series_sequence"] = float(i)
            lib[p]["series_sequence_user_set"] = True
            lib[p]["series"] = canonical      # tidy up whitespace/case variants
        app.db.save_local_db(lib)

    picker.bind("<<ComboboxSelected>>", load_series)

    btns = tk.Frame(frame, bg="#2b2b2b")
    btns.pack(fill="x")
    for label, cmd in (("▲ Up", lambda: move(-1)), ("▼ Down", lambda: move(1))):
        tk.Button(btns, text=label, command=cmd, bg="#555", fg="white",
                  relief="flat", padx=12, pady=4).pack(side=tk.LEFT, padx=4)
    tk.Button(btns, text="Save Order", command=save, bg="#bb86fc", fg="#1e1e1e",
              font=("Arial", 9, "bold"), relief="flat", padx=15, pady=4
              ).pack(side=tk.RIGHT)

    load_series()

def open_cover_modal(app, asin, title, explicit_path=None):
    """Opens a standardized, high-resolution, clickable cover art modal."""
    import os
    import tkinter as tk
    from tkinter import ttk

    from PIL import Image, ImageOps, ImageTk

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
        # Convert to RGB to ensure PIL's ImageOps.pad doesn't crash on strange color profiles
        original_img = Image.open(cover_path).convert("RGB")

        # Standardized Modal Size
        max_size = 700

        modal = tk.Toplevel(app.root)
        modal.title(title)
        modal.withdraw()

        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#1e1e1e"
        modal.configure(
            bg=bg_color, highlightthickness=2, highlightbackground="#4a90e2"
        )

        lbl = tk.Label(modal, bg=bg_color, bd=0, cursor="hand2", takefocus=0)
        lbl.pack(fill="both", expand=True)

        # --- Rendering & Toggle Logic ---
        fill_var = tk.BooleanVar(value=app.settings.get("lightbox_fill", False))

        def render_image():
            img = original_img.copy()
            if fill_var.get():
                # Enlarge to Fill (Center Crop)
                img = ImageOps.fit(
                    img,
                    (max_size, max_size),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            else:
                # Fit Container (Letterbox with theme background color)
                img = ImageOps.pad(
                    img,
                    (max_size, max_size),
                    method=Image.Resampling.LANCZOS,
                    color=bg_color,
                )

            photo = ImageTk.PhotoImage(img)
            lbl.config(image=photo)
            lbl.image = photo

            # Position and reveal the window only on the first render
            if modal.state() == "withdrawn":
                modal.update_idletasks()
                x = app.root.winfo_x() + (app.root.winfo_width() // 2) - (max_size // 2)
                y = (
                    app.root.winfo_y()
                    + (app.root.winfo_height() // 2)
                    - (max_size // 2)
                )
                modal.geometry(f"{max_size}x{max_size}+{x}+{y}")
                modal.overrideredirect(True)
                modal.deiconify()
                modal.lift()
                modal.attributes("-topmost", True)
                modal.focus_force()

        render_image()
        app._active_cover_modal = modal

        # --- Context Menu ---
        menu = tk.Menu(modal, tearoff=0)

        def on_toggle():
            app.settings["lightbox_fill"] = fill_var.get()
            if hasattr(app, "db"):
                app.db.save_settings(app.settings)
            render_image()

        menu.add_checkbutton(
            label="Enlarge to Fill", variable=fill_var, command=on_toggle
        )

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        def dismiss(event=None):
            if getattr(app, "_active_cover_modal", None) is modal:
                app._active_cover_modal = None
            try:
                modal.withdraw()  # Retained your macOS Grey Box fix!
                modal.destroy()
            except tk.TclError:
                pass

        # Bindings: Left-click dismisses, Right-click opens menu
        lbl.bind("<Button-1>", dismiss)
        lbl.bind("<Button-3>", show_menu)
        modal.bind("<Escape>", dismiss)
        modal.bind("<FocusOut>", dismiss)
        modal.protocol("WM_DELETE_WINDOW", dismiss)

    except Exception as e:
        app._active_cover_modal = None
        import traceback

        traceback.print_exc()
        if hasattr(app, "logger"):
            app.logger.error(f"Failed to open cover modal: {e}")


def open_device_management_window(app):
    from datetime import datetime

    if getattr(app, "device_win", None) and app.device_win.winfo_exists():
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

    ttk.Label(main_frame, text="Connected Devices", font=("Segoe UI", 12, "bold")).pack(
        pady=(0, 10), anchor="w"
    )
    list_frame = ttk.Frame(main_frame)
    list_frame.pack(fill="both", expand=True, pady=(0, 10))

    # --- Treeview & Left-Aligned Scrollbar Setup ---
    columns = ("TokenHash", "Device Name", "Last Seen")

    # Pack the scrollbar FIRST, on the left
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
    scrollbar.pack(side=tk.RIGHT, fill="y", padx=(0, 5))

    tree = ttk.Treeview(
        list_frame, columns=columns, show="headings", selectmode="browse"
    )

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
                last_seen_str = datetime.fromtimestamp(last_seen_ts).strftime(
                    "%Y-%m-%d %H:%M"
                )
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
            messagebox.showwarning(
                "Select Device",
                "Please select a device to revoke.",
                parent=app.device_win,
            )
            return

        item = tree.item(selected[0])
        token_hash = item["values"][0]
        device_name = item["values"][1]

        if messagebox.askyesno(
            "Revoke Access",
            f"Are you sure you want to disconnect '{device_name}'?\n\nThis device will immediately lose access to your library.",
            parent=app.device_win,
        ):
            devices = app.settings.get("paired_devices", {})
            if token_hash in devices:
                del devices[token_hash]
                app.settings["paired_devices"] = devices
                app.db.save_settings(app.settings)
                refresh_list()

    ttk.Button(btn_frame, text="Close", command=app.device_win.destroy).pack(
        side=tk.RIGHT, padx=(5, 0)
    )
    ttk.Button(btn_frame, text="Revoke Selected", command=revoke_device).pack(
        side=tk.RIGHT
    )


def open_shelf_management_window(app, title, asin):
    import tkinter as tk
    from tkinter import ttk

    if "shelves_db" not in app.settings:
        app.settings["shelves_db"] = {}

    # 1. Gather all unique existing shelves across the entire library
    all_shelves = set()
    for shelves in app.settings["shelves_db"].values():
        all_shelves.update(shelves)
    all_shelves = sorted(list(all_shelves))

    # 2. Get current shelves for this specific book
    current_shelves = set(app.settings["shelves_db"].get(asin, []))

    win = tk.Toplevel(app.root)
    win.title("Manage Shelves")
    win.geometry("380x480")
    win.transient(app.root)
    win.resizable(False, False)

    style = ttk.Style()
    bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
    win.configure(bg=bg_color)

    main_frame = ttk.Frame(win, padding=15)
    main_frame.pack(fill="both", expand=True)

    ttk.Label(main_frame, text="Manage Shelves", font=("Segoe UI", 12, "bold")).pack(
        anchor="w"
    )
    ttk.Label(
        main_frame,
        text=title[:45] + ("..." if len(title) > 45 else ""),
        font=("Segoe UI", 9, "italic"),
    ).pack(anchor="w", pady=(0, 10))

    # --- New Shelf Entry ---
    add_frame = ttk.Frame(main_frame)
    add_frame.pack(fill="x", pady=(0, 10))

    new_shelf_var = tk.StringVar()
    entry = ttk.Entry(add_frame, textvariable=new_shelf_var)
    entry.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 5))

    checkbox_vars = {}

    def add_new_shelf(event=None):
        new_shelf = new_shelf_var.get().strip()
        if new_shelf and new_shelf not in checkbox_vars:
            # Remove empty state label if it exists
            for widget in inner_frame.winfo_children():
                if isinstance(widget, ttk.Label):
                    widget.destroy()

            var = tk.BooleanVar(value=True)
            checkbox_vars[new_shelf] = var
            ttk.Checkbutton(inner_frame, text=new_shelf, variable=var).pack(
                anchor="w", pady=2
            )
            new_shelf_var.set("")

            # Auto-scroll to the newly added item
            canvas.update_idletasks()
            canvas.yview_moveto(1.0)

    ttk.Button(add_frame, text="Add", width=6, command=add_new_shelf).pack(
        side=tk.RIGHT
    )
    entry.bind("<Return>", add_new_shelf)

    # --- Existing Shelves List (Scrollable) ---
    list_frame = ttk.LabelFrame(main_frame, text="Your Shelves", padding=5)
    list_frame.pack(fill="both", expand=True, pady=(0, 10))

    canvas = tk.Canvas(list_frame, bg=bg_color, highlightthickness=0)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    inner_frame = tk.Frame(canvas, bg=bg_color)

    inner_frame.bind(
        "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas_win = canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_win, width=e.width))

    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill="both", expand=True)
    scrollbar.pack(side=tk.RIGHT, fill="y")

    if not all_shelves:
        ttk.Label(
            inner_frame,
            text="No custom shelves created yet.\nType a name above to create one.",
            justify="center",
            font=("Segoe UI", 9, "italic"),
        ).pack(pady=20)
    else:
        for shelf in all_shelves:
            var = tk.BooleanVar(value=(shelf in current_shelves))
            checkbox_vars[shelf] = var
            ttk.Checkbutton(inner_frame, text=shelf, variable=var).pack(
                anchor="w", pady=2
            )

    # --- Bottom Controls ---
    btn_frame = ttk.Frame(main_frame)
    btn_frame.pack(fill="x", side=tk.BOTTOM)

    def save_shelves():
        selected_shelves = [shelf for shelf, var in checkbox_vars.items() if var.get()]
        app.settings["shelves_db"][asin] = selected_shelves
        app.db.save_settings(app.settings)

        app.root.after(0, app.library_presenter.refresh_library_ui)
        win.destroy()

    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(
        side=tk.RIGHT, padx=(5, 0)
    )
    ttk.Button(btn_frame, text="Save", command=save_shelves).pack(side=tk.RIGHT)

    win.focus_set()
