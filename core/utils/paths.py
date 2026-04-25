import os
import sys


def get_resource_path(*relative_parts):
    """
    Resolves a path to a bundled resource correctly in both source and frozen modes.
    
    In source mode: looks relative to the project root.
    In frozen mode: looks inside the PyInstaller temp extraction directory (_MEIPASS).
    """
    """
    Path resolution for bundled resources vs. user data.

    CRITICAL: Use get_resource_path() ONLY for files that ship with the app
    (icons, HTML, CSS, JS, anything inside ui/ or server/static/).

    For USER DATA (database, settings, auth tokens, downloaded audiobooks,
    covers, logs) ALWAYS use base_dir directly. User data must persist next
    to the EXE so it survives across launches and isn't wiped when the
    PyInstaller temp directory is cleaned up.
    """
    if getattr(sys, 'frozen', False):
        # Frozen: PyInstaller extracts bundled resources to _MEIPASS
        base = sys._MEIPASS
    else:
        # Source: walk up from this file (core/utils/paths.py) to project root
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    return os.path.join(base, *relative_parts)