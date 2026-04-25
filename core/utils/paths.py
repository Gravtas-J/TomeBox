import os
import sys


def get_resource_path(*relative_parts):
    """
    Resolves a path to a bundled resource correctly in both source and frozen modes.
    
    In source mode: looks relative to the project root.
    In frozen mode: looks inside the PyInstaller temp extraction directory (_MEIPASS).
    """
    if getattr(sys, 'frozen', False):
        # Frozen: PyInstaller extracts bundled resources to _MEIPASS
        base = sys._MEIPASS
    else:
        # Source: walk up from this file (core/utils/paths.py) to project root
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    return os.path.join(base, *relative_parts)