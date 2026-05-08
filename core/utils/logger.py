import logging
from logging.handlers import RotatingFileHandler
import os
import sys

class CallableLogger:
    def __init__(self, logger):
        self._logger = logger

    def __call__(self, msg):
        self._logger.info(msg)

    def error(self, msg, exc_info=False):
        """Passes error logs through, optionally with stack traces."""
        self._logger.error(msg, exc_info=exc_info)
        
    def exception(self, msg):
        """Automatically captures and logs the current exception stack trace."""
        self._logger.exception(msg)
        
    def __getattr__(self, name):
        return getattr(self._logger, name)

def setup_logger(base_dir, debug_mode=False):
    """Configures a rotating, thread-safe logger for the entire application."""
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "tomebox.log")

    # Create the root logger
    logger = logging.getLogger("TomeBox")
    
    # Set global level
    level = logging.DEBUG if debug_mode else logging.INFO
    logger.setLevel(level)

    # Avoid adding multiple handlers if initialized multiple times
    if not logger.handlers:
        # Formatter: [2026-04-24 10:15:30] [ERROR] [DownloadManager] Connection timed out.
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. Rotating File Handler (Max 5MB per file, keep last 3 backups)
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # 2. Console Handler (So you can still see logs in the terminal during dev)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return CallableLogger(logger)