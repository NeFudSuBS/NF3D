"""
FFmpeg service: Video frame extraction and information.

Handles:
- Frame extraction at specific timestamps
- Video information queries (dimensions, duration, format)
- Stereo SBS video splitting
- Format conversions
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass

from .base_service import ToolService, ServiceError
from infrastructure.exception_handlers import handle_errors

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Video information data class."""
    width: int
    height: int
    duration_ms: int
    fps: float
    is_sbs: bool  # Side-by-side stereo
    pixel_format: str
    bitrate: int
    codec: str
    
    @property
    def duration_seconds(self) -> float:
        """Get duration in seconds."""
        return self.duration_ms / 1000.0
    
    @property
    def eye_width(self) -> int:
        """Get eye width for SBS video."""
        return self.width // 2 if self.is_sbs else self.width
    
    @property
    def aspect_ratio(self) -> float:
        """Get video aspect ratio."""
        return self.width / self.height if self.height else 0


class FFmpegService(ToolService):
    """
    Wrapper for ffmpeg for video processing.
    
    Provides:
    - Frame extraction at specific timestamps
    - Video metadata queries
    - Format information
    - SBS stereo detection
    """
    
    TOOL_NAME = "ffmpeg"
    REQUIRED = True
    MIN_VERSION = "4.0.0"
    
    def _auto_detect(self) -> Optional[str]:
        """
        Auto-detect ffmpeg on the system.
        
        Returns:
            ffmpeg path if found, None otherwise
        """
        import sys
        import os
        
        # Platform-specific search paths
        if sys.platform == "win32":
            candidates = [
                "ffmpeg.exe",
                "ffmpeg",
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            ]
        elif sys.platform == "darwin":
            candidates = [
                "ffmpeg",
                "/usr/local/bin/ffmpeg",
                "/opt/homebrew/bin/ffmpeg",
            ]
        else:  # Linux
            candidates = [
                "ffmpeg",
                "/usr/bin/ffmpeg",
                "/usr/local/bin/ffmpeg",
            ]
        
        for candidate in candidates:
            try:
                if os.path.isabs(candidate):
                    if Path(candidate).is_file():
                        return candidate
                else:
                    # Try to run from PATH
                    result = subprocess.run(
                        [candidate, "-version"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                        **self._get_popen_kwargs()
                    )
                    if result.returncode == 0:
                        return candidate
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        
        return None
    
    def get_version(self) -> str:
        """
        Get ffmpeg version.
        
        Returns:
            Version string (e.g., "4.4.2-1ubuntu2.1")
            
        Raises:
            ServiceError: If version cannot be determined
        """
        if self._version_cache:
            return self._version_cache
        
        result = self._run_command([self.tool_path, "-version"], capture_output=True)
        
        # Parse version from first line: ffmpeg version X.Y.Z
        match = re.search(r'ffmpeg version ([\d.\-\w]+)', result.stdout)
        if match:
            self._version_cache = match.group(1)
            return self._version_cache
        
        raise ServiceError("Could not determine ffmpeg version")
    
    # ==================== Frame Extraction ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def extract_frame(
        self,
        video_path: str,
        timestamp_seconds: float,
        output_path: str,
        scale: Optional[Tuple[int, int]] = None,
    ) -> Path:
        """
        Extract a single frame from video.
        
        Args:
            video_path: Path to video file
            timestamp_seconds: Time of frame to extract (in seconds)
            output_path: Where to save the frame (PNG)
            scale: Optional (width, height) to scale frame to
            
        Returns:
            Path to extracted frame
            
        Raises:
            ServiceError: If extraction fails
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Build filter chain for scaling if needed
        filter_str = ""
        if scale:
            filter_str = f"-vf scale={scale[0]}:{scale[1]}"
        
        cmd = [
            self.tool_path,
            "-y",  # Overwrite output file
            "-ss", str(int(timestamp_seconds)),  # Seek to timestamp
            "-i", str(video_path),
            "-frames:v", "1",  # Extract only 1 frame
            "-f", "image2",  # Output format
        ]
        
        if filter_str:
            cmd.extend(filter_str.split())
        
        cmd.append(str(output_path))
        
        try:
            logger.debug(f"Extracting frame at {timestamp_seconds}s from {video_path}")
            self._run_command(cmd, timeout=30)
            
            if not output_path.exists():
                raise ServiceError(f"Frame extraction did not produce output file: {output_path}")
            
            logger.info(f"✓ Frame extracted: {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Frame extraction failed: {e}") from e
    
    @handle_errors(default_return=[], error_types=(ServiceError,))
    def extract_frame_range(
        self,
        video_path: str,
        start_seconds: float,
        end_seconds: float,
        output_template: str,
        fps: int = 2,
    ) -> list:
        """
        Extract multiple frames from a time range.
        
        Args:
            video_path: Path to video file
            start_seconds: Start time
            end_seconds: End time
            output_template: Output path template (e.g., "/tmp/frame_%03d.png")
            fps: Frames per second to extract
            
        Returns:
            List of extracted frame paths
            
        Raises:
            ServiceError: If extraction fails
        """
        output_dir = Path(output_template).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        
        duration = end_seconds - start_seconds
        
        cmd = [
            self.tool_path,
            "-y",
            "-ss", str(start_seconds),
            "-i", str(video_path),
            "-t", str(duration),
            "-vf", f"fps={fps}",
            "-f", "image2",
            output_template,
        ]
        
        try:
            logger.debug(f"Extracting frames {start_seconds}-{end_seconds}s from {video_path}")
            self._run_command(cmd, timeout=60)
            
            # Find extracted frames
            frames = sorted(output_dir.glob(Path(output_template).name.replace("%03d", "*")))
            logger.info(f"✓ Extracted {len(frames)} frames")
            return frames
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Frame range extraction failed: {e}") from e
    
    # ==================== Video Information ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def get_video_info(self, video_path: str) -> Optional[VideoInfo]:
        """
        Get video information using ffprobe.
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoInfo object or None if unable to get info
            
        Raises:
            ServiceError: If probe fails
        """
        try:
            # Try ffprobe first (more reliable)
            return self._get_video_info_ffprobe(video_path)
        except Exception:
            # Fallback to ffmpeg -i
            return self._get_video_info_ffmpeg(video_path)
    
    def _get_video_info_ffprobe(self, video_path: str) -> VideoInfo:
        """
        Get video info using ffprobe (JSON output).
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoInfo object
            
        Raises:
            ServiceError: If probe fails
        """
        # Try to find ffprobe in same directory as ffmpeg
        ffprobe_path = Path(self.tool_path).parent / "ffprobe"
        if not ffprobe_path.exists():
            ffprobe_path = "ffprobe"  # Try PATH
        
        cmd = [
            str(ffprobe_path),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,duration,r_frame_rate,pix_fmt,codec_name,bit_rate",
            "-of", "json",
            str(video_path),
        ]
        
        result = self._run_command(cmd, timeout=10)
        data = json.loads(result.stdout)
        
        if not data.get("streams"):
            raise ServiceError("No video stream found")
        
        stream = data["streams"][0]
        
        # Parse frame rate
        fps_str = stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = map(int, fps_str.split("/"))
            fps = num / den if den else 30.0
        else:
            fps = float(fps_str) if fps_str else 30.0
        
        # Parse duration
        duration_seconds = float(stream.get("duration", 0))
        duration_ms = int(duration_seconds * 1000)
        
        width = stream["width"]
        height = stream["height"]
        
        # Detect SBS (side-by-side) stereo
        is_sbs = width / height > 2.5  # Aspect ratio > 2.5 indicates SBS
        
        return VideoInfo(
            width=width,
            height=height,
            duration_ms=duration_ms,
            fps=fps,
            is_sbs=is_sbs,
            pixel_format=stream.get("pix_fmt", "yuv420p"),
            bitrate=int(stream.get("bit_rate", 0)) or 0,
            codec=stream.get("codec_name", "h264"),
        )
    
    def _get_video_info_ffmpeg(self, video_path: str) -> VideoInfo:
        """
        Fallback: Get video info from ffmpeg output.
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoInfo object
            
        Raises:
            ServiceError: If extraction fails
        """
        result = self._run_command(
            [self.tool_path, "-i", str(video_path)],
            capture_output=True,
            timeout=10
        )
        
        output = result.stdout + result.stderr
        
        # Parse dimensions
        dim_match = re.search(r"(\d+)x(\d+)", output)
        if not dim_match:
            raise ServiceError("Could not determine video dimensions")
        
        width = int(dim_match.group(1))
        height = int(dim_match.group(2))
        
        # Parse duration
        duration_ms = 0
        duration_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", output)
        if duration_match:
            h, m, s = map(float, duration_match.groups())
            duration_ms = int((h * 3600 + m * 60 + s) * 1000)
        
        # Parse FPS
        fps = 30.0
        fps_match = re.search(r"(\d+(?:\.\d+)?\s*fps)", output, re.IGNORECASE)
        if fps_match:
            fps = float(fps_match.group(1).split()[0])
        
        is_sbs = width / height > 2.5
        
        return VideoInfo(
            width=width,
            height=height,
            duration_ms=duration_ms,
            fps=fps,
            is_sbs=is_sbs,
            pixel_format="unknown",
            bitrate=0,
            codec="unknown",
        )
    
    # ==================== Utility Methods ====================
    
    def get_frame_at_time(self, video_path: str, time_str: str) -> Optional[Path]:
        """
        Extract frame at SRT-format time (HH:MM:SS,mmm).
        
        Args:
            video_path: Path to video file
            time_str: SRT time format (e.g., "00:01:30,500")
            
        Returns:
            Path to extracted frame or None
        """
        # Convert SRT time to seconds
        h, m, rest = time_str.split(":")
        s, ms = rest.split(",")
        seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "frame.png"
            return self.extract_frame(video_path, seconds, str(output))
