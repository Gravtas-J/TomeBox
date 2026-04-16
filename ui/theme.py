import tkinter as tk
from tkinter import ttk

def apply_theme(app, palette_name):
    style = ttk.Style()
    style.theme_use("clam")

    palettes = {
        "light": {"bg": "#f0f0f0", "fg": "#000000", "entry": "#ffffff", "select": "#0078D7", "btn": "#e1e1e1", "border": "#cccccc"},
        "dark": {"bg": "#2b2b2b", "fg": "#e0e0e0", "entry": "#1e1e1e", "select": "#4a90e2", "btn": "#3c3c3c", "border": "#555555"},
        "terminal": {"bg": "#0c0c0c", "fg": "#00ff00", "entry": "#000000", "select": "#005500", "btn": "#1a1a1a", "border": "#004400"},
        "solarized_dark": {"bg": "#002b36", "fg": "#839496", "entry": "#073642", "select": "#cb4b16", "btn": "#073642", "border": "#586e75"},
        "solarized_light": {"bg": "#fdf6e3", "fg": "#657b83", "entry": "#eee8d5", "select": "#268bd2", "btn": "#eee8d5", "border": "#93a1a1"},
        "dracula": {"bg": "#282a36", "fg": "#f8f8f2", "entry": "#44475a", "select": "#bd93f9", "btn": "#44475a", "border": "#6272a4"},
        "cyberpunk": {"bg": "#0a0a2a", "fg": "#00ffcc", "entry": "#161638", "select": "#ff00ff", "btn": "#20204a", "border": "#00ffff"},
        "nord": {"bg": "#2e3440", "fg": "#d8dee9", "entry": "#3b4252", "select": "#5e81ac", "btn": "#434c5e", "border": "#4c566a"}
    }
    
    colors = palettes.get(palette_name, palettes["light"])
    
    app.root.configure(bg=colors["bg"])
    
    def paint_structural_frames(widget):
        if type(widget) in (tk.Frame, tk.Tk, tk.Toplevel):
            try:
                widget.configure(bg=colors["bg"])
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            paint_structural_frames(child)
            
    paint_structural_frames(app.root)
    
    style.configure(".", background=colors["bg"], foreground=colors["fg"], bordercolor=colors["border"], lightcolor=colors["bg"], darkcolor=colors["bg"])
    style.configure("TFrame", background=colors["bg"])
    
    style.configure("TButton", background=colors["btn"], borderwidth=1, bordercolor=colors["border"])
    style.map("TButton", background=[("active", colors["select"])])
    
    style.configure("TMenubutton", background=colors["bg"], foreground=colors["fg"], borderwidth=0, arrowcolor=colors["bg"])
    style.map("TMenubutton", background=[("active", colors["select"])], foreground=[("active", "#ffffff")])
    
    if hasattr(app, 'file_menu'):
        menu_list = [app.file_menu, app.appearance_menu, app.export_menu, app.help_menu]
        for m in menu_list:
            m.config(
                bg=colors["entry"], 
                fg=colors["fg"], 
                activebackground=colors["select"], 
                activeforeground="#ffffff",
                activeborderwidth=0,
                borderwidth=1
            )

    style.configure("TCombobox", fieldbackground=colors["entry"], background=colors["btn"], arrowcolor=colors["fg"], foreground=colors["fg"])
    style.map("TCombobox", 
              fieldbackground=[("readonly", colors["entry"])], 
              selectbackground=[("readonly", colors["select"])], 
              selectforeground=[("readonly", "#ffffff")])
              
    app.root.option_add('*TCombobox*Listbox.background', colors["entry"])
    app.root.option_add('*TCombobox*Listbox.foreground', colors["fg"])
    app.root.option_add('*TCombobox*Listbox.selectBackground', colors["select"])
    app.root.option_add('*TCombobox*Listbox.selectForeground', "#ffffff")
    
    def repaint_combobox_dropdowns(widget):
        if isinstance(widget, ttk.Combobox):
            try:
                popdown = widget.tk.eval(f'ttk::combobox::PopdownWindow {widget._w}')
                widget.tk.call(f'{popdown}.f.l', 'configure',
                               '-background', colors["entry"],
                               '-foreground', colors["fg"],
                               '-selectbackground', colors["select"],
                               '-selectforeground', "#ffffff")
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            repaint_combobox_dropdowns(child)
            
    repaint_combobox_dropdowns(app.root)

    style.configure("TEntry", fieldbackground=colors["entry"], foreground=colors["fg"])
    
    style.configure("Treeview", background=colors["entry"], foreground=colors["fg"], fieldbackground=colors["entry"], bordercolor=colors["border"])
    style.map("Treeview", background=[("selected", colors["select"])], foreground=[("selected", "#ffffff")])
    
    style.configure("Treeview.Heading", background=colors["btn"], foreground=colors["fg"], bordercolor=colors["border"])
    style.map("Treeview.Heading", background=[("active", colors["select"])], foreground=[("active", "#ffffff")])
    
    style.configure("TLabelframe", bordercolor=colors["border"])
    style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
    
    style.configure("TProgressbar", background=colors["select"], troughcolor=colors["entry"], bordercolor=colors["border"])
    
    style.configure("Vertical.TScrollbar", background=colors["btn"], troughcolor=colors["bg"], arrowcolor=colors["fg"], bordercolor=colors["border"])
    style.configure("Horizontal.TScrollbar", background=colors["btn"], troughcolor=colors["bg"], arrowcolor=colors["fg"], bordercolor=colors["border"])
    
    style.configure("Sash", background=colors["border"], sashthickness=4)
    
    if hasattr(app, 'queue_canvas'):
        app.queue_canvas.config(bg=colors["bg"], highlightthickness=0)
        app.queue_inner.config(bg=colors["bg"])
        for data in getattr(app, 'active_downloads', {}).values():
            if "frame" in data:
                data["frame"].config(bg=colors["bg"])
                
    app.settings["classic_palette"] = palette_name
    app.db.save_settings(app.settings)