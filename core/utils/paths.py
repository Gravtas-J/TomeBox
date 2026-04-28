import os
import sys
import re

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

def parse_dnd_paths(raw_data):
    """
    Extracts file paths from tkinterdnd2 event data.
    Handles Windows curly braces for paths with spaces.
    """
    # Match everything inside {} OR any sequence of non-space characters
    matches = re.findall(r'{([^}]+)}|([^\s]+)', raw_data)
    
    paths = []
    for match in matches:
        # regex returns a tuple: (match_inside_braces, match_outside_braces)
        path = match[0] if match[0] else match[1]
        normalized_path = os.path.normpath(path)
        if os.path.exists(normalized_path):
            paths.append(normalized_path)
            
    return paths