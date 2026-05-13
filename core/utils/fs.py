import os

def safe_unlink(path, logger=None):
    """Safely deletes a file if it exists, logging any permission/lock errors."""
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as e:
            if logger:
                # Upgraded to warning so it actually stands out in the logs
                logger.warning(f"Could not remove {path}: {e}")