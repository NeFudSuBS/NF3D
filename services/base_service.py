"""
Base class for all external tool services.

Defines the common interface and error handling patterns.
"""

import logging
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any

from infrastructure.exception_handlers import ToolNotFoundError, NF3DException

logger = logging.getLogger(__name__)


class ServiceError(NF3DException):
    """Base exception for service errors."""
    pass


class ToolService(ABC):
    """
    Abstract base class for external tool services.
    
    All external tool wrappers should inherit from this class to ensure:
    - Consistent error handling
    - Availability checking
    - Version detection
    - Logging
    """
    
    # Override in subclasses
    TOOL_NAME: str = "UnknownTool"
    REQUIRED: bool = True
    MIN_VERSION: str = "1.0.0"  # Format: "major.minor.patch"
    
    def __init__(self, tool_path: Optional[str] = None):
        """
        Initialize service.
        
        Args:
            tool_path: Path to tool executable. If None, will auto-detect.
            
        Raises:
            ToolNotFoundError: If tool cannot be found and is required.
        """
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.tool_path = tool_path
        self._version_cache: Optional[str] = None
        
        # Try to detect if path not provided
        if not self.tool_path:
            self.tool_path = self._auto_detect()
        
        # Validate existence
        if self.tool_path:
            self._validate_tool_exists()
            self.logger.info(f"✓ {self.TOOL_NAME} initialized: {self.tool_path}")
        elif self.REQUIRED:
            raise ToolNotFoundError(
                self.TOOL_NAME,
                f"Please install {self.TOOL_NAME} or set its path explicitly."
            )
        else:
            self.logger.warning(f"{self.TOOL_NAME} not found (optional)")
    
    # ==================== Abstract Methods ====================
    
    @abstractmethod
    def _auto_detect(self) -> Optional[str]:
        """
        Auto-detect tool path on the system.
        
        Returns:
            Tool path if found, None otherwise
        """
        pass
    
    @abstractmethod
    def get_version(self) -> str:
        """
        Get tool version string.
        
        Returns:
            Version string (e.g., "7.4.2")
            
        Raises:
            ServiceError: If version cannot be determined
        """
        pass
    
    # ==================== Utility Methods ====================
    
    def _validate_tool_exists(self) -> None:
        """
        Check if tool executable exists and is accessible.
        
        Raises:
            ToolNotFoundError: If tool not found
        """
        if not self.tool_path:
            raise ToolNotFoundError(self.TOOL_NAME, "No tool path specified")
        
        path = Path(self.tool_path)
        if not path.exists():
            raise ToolNotFoundError(
                self.TOOL_NAME,
                f"Tool not found at: {self.tool_path}"
            )
        
        if path.is_dir():
            raise ToolNotFoundError(
                self.TOOL_NAME,
                f"Path is directory, not executable: {self.tool_path}"
            )
    
    def is_available(self) -> bool:
        """
        Check if tool is available and accessible.
        
        Returns:
            True if available, False otherwise
        """
        if not self.tool_path:
            return False
        try:
            self._validate_tool_exists()
            return True
        except ToolNotFoundError:
            return False
    
    @staticmethod
    def _get_popen_kwargs() -> Dict[str, Any]:
        """
        Get subprocess kwargs for hiding console on Windows.
        
        Returns:
            Dictionary of kwargs for subprocess.Popen
        """
        if sys.platform != "win32":
            return {}
        
        # Hide console window on Windows
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": si
        }
    
    def _run_command(
        self,
        args: list,
        timeout: int = 30,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Run command with consistent error handling.
        
        Args:
            args: Command arguments (first element should be tool path)
            timeout: Command timeout in seconds
            capture_output: Whether to capture stdout/stderr
            
        Returns:
            CompletedProcess object
            
        Raises:
            ServiceError: If command fails
        """
        try:
            self.logger.debug(f"Running: {' '.join(args)}")
            
            result = subprocess.run(
                args,
                timeout=timeout,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                **self._get_popen_kwargs()
            )
            
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise ServiceError(f"{self.TOOL_NAME} failed: {error_msg}")
            
            return result
            
        except subprocess.TimeoutExpired as e:
            raise ServiceError(f"{self.TOOL_NAME} timed out after {timeout}s") from e
        except FileNotFoundError as e:
            raise ServiceError(f"{self.TOOL_NAME} executable not found: {e}") from e
        except Exception as e:
            raise ServiceError(f"Error running {self.TOOL_NAME}: {e}") from e
    
    def _version_compare(self, v1: str, v2: str) -> int:
        """
        Compare two version strings.
        
        Args:
            v1: First version (e.g., "1.2.3")
            v2: Second version
            
        Returns:
            -1 if v1 < v2, 0 if equal, 1 if v1 > v2
        """
        try:
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]
            
            # Pad with zeros
            max_len = max(len(parts1), len(parts2))
            parts1 += [0] * (max_len - len(parts1))
            parts2 += [0] * (max_len - len(parts2))
            
            if parts1 < parts2:
                return -1
            elif parts1 > parts2:
                return 1
            else:
                return 0
        except (ValueError, AttributeError):
            return 0  # Can't compare, assume equal
