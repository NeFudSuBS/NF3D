# logging_config.py
"""
Centralized logging configuration for NF3D.
Provides structured logging across all modules.
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from datetime import datetime


class NF3DLogger:
    """Manages logging configuration for the entire application."""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def setup(cls, log_dir: Path = None, level: int = logging.INFO) -> None:
        """
        Initialize logging with file and console handlers.

        Args:
            log_dir: Directory to store log files. Defaults to ~/.nf3d/logs
            level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        if cls._initialized:
            return

        if log_dir is None:
            log_dir = Path.home() / ".nf3d" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers = []

        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Rotating file handler: max 10 MB, keep 5 files
        log_file = log_dir / f"nf3d_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Console handler: less verbose
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        root_logger.addHandler(console_handler)

        cls._initialized = True
        root_logger.info("NF3D logging initialized")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the specified module."""
    return logging.getLogger(name)
