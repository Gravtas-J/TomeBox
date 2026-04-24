import os
import sys
import ctypes
from tkinterdnd2 import TkinterDnD
import platform

# Add the root directory to the system path so imports work cleanly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ui.app_window import AAXManagerApp

def main():
    # The base directory is the root TomeBox folder
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if platform.system() == 'Windows':
        try:
            # Create a unique string for your app. It can be anything.
            myappid = 'tomebox.audiomanager.desktop.1' 
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass
    # Initialize Tkinter with Drag-and-Drop support
    root = TkinterDnD.Tk()
    
    # Launch the application
    app = AAXManagerApp(root, base_dir)
    
    # Apply the saved color palette if running the classic engine
    saved_palette = app.settings.get("classic_palette", "dark")
    app.apply_classic_palette(saved_palette)
    
    root.mainloop()

if __name__ == "__main__":
    main()