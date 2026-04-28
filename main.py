import os
import sys
import ctypes
import argparse
import platform
import time

# Add the root directory to the system path so imports work cleanly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    parser = argparse.ArgumentParser(
        description="TomeBox — Audiobook manager and self-hosted media server"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run as a headless server with no GUI. Web companion only."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the companion web server (default: 8000)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind to (default: 0.0.0.0 for all interfaces)"
    )
    return parser.parse_args()


def run_headless(base_dir, host, port):
    """Runs only the FastAPI companion server with no GUI."""
    import uvicorn
    import asyncio
    from core.utils.logger import setup_logger
    from core.database import DatabaseManager
    from core.controllers.library_manager import LibraryManager
    from api.audible_client import AudibleClient
    from server.web_app import create_server_app
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    logger = setup_logger(base_dir)
    logger("=" * 60)
    logger("TomeBox Headless Server starting...")
    logger(f"Base directory: {base_dir}")
    logger("=" * 60)
    
    # Build a minimal app instance with just the components the web server needs
    class HeadlessApp:
        pass
    
    app = HeadlessApp()
    app.base_dir = base_dir
    app.covers_dir = os.path.join(base_dir, "covers")
    app.db = DatabaseManager(base_dir)
    app.settings = app.db.load_settings()
    app.api = AudibleClient()
    app.library_manager = LibraryManager(
        db_manager=app.db,
        api_client=app.api,
        base_dir=base_dir
    )
    
    # The web server expects these attributes — stub the GUI-only ones
    app.root = None
    app.file_path = None
    app.sync_playhead_from_remote = lambda position: None
    
    # Print pairing info to console
    auth_token = app.settings.get("auth_token", "")
    import socket
    
    def get_all_local_ips():
        """Returns a list of all non-loopback IPv4 addresses on this machine."""
        ips = []
        try:
            # Primary route IP — the one most likely reachable from other devices
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        
        # Also enumerate all bound interfaces in case there are multiple networks
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
        
        return ips or ["127.0.0.1"]
    
    def print_qr_to_terminal(url, logger):
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        
        # Render to terminal
        matrix = qr.get_matrix()
        for row in matrix:
            line = ""
            for cell in row:
                line += "██" if cell else "  "
            logger(line)
    local_ips = get_all_local_ips()

    logger("")
    logger("=" * 60)
    logger(f"Server listening on http://{host}:{port}")
    logger("")
    logger("Connect from any device on your network using one of these URLs:")
    for ip in local_ips:
        logger(f"  http://{ip}:{port}")
    logger("")
    logger("Pairing URLs (use these to link a new device):")
    for ip in local_ips:
        logger(f"  http://{ip}:{port}/auth?token={auth_token}")
    logger("=" * 60)
    logger("Press Ctrl+C to stop the server.")
    logger("")
    logger("Scan this QR code with your phone to pair instantly:")
    logger("")
    logger("If the QR code above is cut off in your terminal, you can:")
    logger(f"  - Open this URL on your phone: http://{ip}:{port}/auth?token={auth_token}")
    logger(f"  - View the QR in the log file: {os.path.join(base_dir, 'logs', 'tomebox.log')}")
    print_qr_to_terminal(f"http://{local_ips[0]}:{port}/auth?token={auth_token}", logger)
    api = create_server_app(app)
    config = uvicorn.Config(api, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)
    
    try:
        server.run()
    except KeyboardInterrupt:
        logger("Shutdown signal received. Stopping server...")
    finally:
        logger("TomeBox Headless Server stopped.")

def setup_tkinter_exception_handler(root, logger):
    def handler(exc, val, tb):
        import traceback
        logger.error(f"Tkinter callback exception:\n{''.join(traceback.format_exception(exc, val, tb))}")
    root.report_callback_exception = handler

def show_splash(root, base_dir):
    """Draws a borderless splash screen centered on the screen."""
    import os
    import tkinter as tk
    from PIL import Image, ImageTk
    from core.utils.paths import get_resource_path

    splash_path = get_resource_path("tomebox-splash.png")
    
    if not os.path.exists(splash_path):
        return None

    splash = tk.Toplevel(root)
    splash.overrideredirect(True)  # Removes the window border and title bar
    
    # Keep the splash on top of other windows
    splash.attributes("-topmost", True)

    try:
        img = Image.open(splash_path)
        img_tk = ImageTk.PhotoImage(img)

        # Calculate exact center coordinates
        window_width = img.width
        window_height = img.height
        screen_width = splash.winfo_screenwidth()
        screen_height = splash.winfo_screenheight()

        x_coordinate = int((screen_width / 2) - (window_width / 2))
        y_coordinate = int((screen_height / 2) - (window_height / 2))

        splash.geometry(f"{window_width}x{window_height}+{x_coordinate}+{y_coordinate}")

        # Place the image
        label = tk.Label(splash, image=img_tk, bg="black")
        label.image = img_tk  # Keep a reference to prevent garbage collection
        label.pack()

        # Force the OS to draw the window immediately before moving on
        splash.update()
        return splash
        
    except Exception:
        splash.destroy()
        return None

def run_gui(base_dir):
    """Runs the standard desktop application."""
    from tkinterdnd2 import TkinterDnD
    from ui.app_window import AAXManagerApp
    
    if platform.system() == 'Windows':
        try:
            myappid = 'tomebox.audiomanager.desktop.1' 
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass
    
    root = TkinterDnD.Tk()
    
    # Hide the main window immediately so it doesn't flash on screen
    root.withdraw()
    
    # 1. Fire up the splash screen
    splash = show_splash(root, base_dir)
    
    # 2. Run the heavy initializations
    app = AAXManagerApp(root, base_dir)
    saved_palette = app.settings.get("classic_palette", "dark")
    app.apply_classic_palette(saved_palette)
    
    # 3. Tear down the splash and reveal the main app
    if splash:
        splash.destroy()
        
    root.deiconify()
    root.mainloop()

def main():
    args = parse_args()
    
    # Resolve base_dir correctly for both source and frozen EXE
    if getattr(sys, 'frozen', False):
        # When installed, route all mutable data to Local AppData
        base_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'TomeBox')
        os.makedirs(base_dir, exist_ok=True)
    else:
        # In source code, keep it portable in the project root
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    if args.headless:
        run_headless(base_dir, args.host, args.port)
    else:
        run_gui(base_dir)


if __name__ == "__main__":
    main()