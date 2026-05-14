# exception_handlers.py
"""
Custom exception types and error-handling utilities for NF3D.
"""
import logging
import os
from typing import Optional, TypeVar, Callable
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


# ==================== Exception Hierarchy ====================

class NF3DException(Exception):
    """Base exception for all NF3D errors."""
    pass


class VideoProcessingError(NF3DException):
    """Raised when video extraction or analysis fails."""
    pass


class SubtitleProcessingError(NF3DException):
    """Raised when subtitle parsing or conversion fails."""
    pass


class DepthAnalysisError(NF3DException):
    """Raised when depth analysis fails."""
    pass


class OCRError(NF3DException):
    """Raised when OCR (Subtitle Edit) fails."""
    pass


class ToolNotFoundError(NF3DException):
    """Raised when a required external tool (ffmpeg, mkvmerge, etc.) is missing."""

    def __init__(self, tool_name: str, suggested_action: str = None):
        self.tool_name = tool_name
        self.suggested_action = suggested_action
        super().__init__(
            f"{tool_name} not found. {suggested_action or 'Please install it.'}"
        )


# ==================== Utilities ====================

def ensure_tool_exists(tool_name: str, tool_path: Optional[str]) -> str:
    """
    Validate that a required external tool exists at the given path.

    Returns the path if valid; raises ToolNotFoundError otherwise.
    """
    if not tool_path or not isinstance(tool_path, str):
        raise ToolNotFoundError(tool_name)
    if not os.path.isfile(tool_path):
        raise ToolNotFoundError(tool_name, f"Expected at: {tool_path}")
    logger.debug(f"{tool_name} found at: {tool_path}")
    return tool_path


def handle_errors(
    default_return: T = None,
    log_level: int = logging.ERROR,
    reraise: bool = False,
    error_types: tuple = (Exception,)
) -> Callable:
    """
    Decorator for consistent error handling.

    Args:
        default_return: Value to return when an exception is caught.
        log_level:      Logging level for the caught error.
        reraise:        If True, re-raises after logging (default False).
        error_types:    Tuple of exception types to catch.

    Usage:
        @handle_errors(default_return=None, error_types=(IOError, OSError))
        def load_file(path): ...

    Note: prefer narrow error_types over the broad Exception default so that
    unexpected errors still surface rather than being silently swallowed.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_types as e:
                logger.log(
                    log_level,
                    f"Error in {func.__name__}(): {e}",
                    exc_info=(log_level <= logging.DEBUG)
                )
                if reraise:
                    raise
                return default_return
        return wrapper
    return decorator
