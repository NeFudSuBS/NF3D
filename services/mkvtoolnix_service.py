"""
MKVToolNix service: MKV file manipulation.

Handles:
- Track listing and information
- Track extraction (audio, subtitle, video)
- Track muxing (adding/replacing tracks)
- Attachment management (fonts)
- Metadata manipulation
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from .base_service import ToolService, ServiceError
from infrastructure.exception_handlers import handle_errors

logger = logging.getLogger(__name__)


@dataclass
class MKVTrack:
    """MKV track information."""
    track_id: int
    track_type: str  # "video", "audio", "subtitles", "attachment"
    codec: str
    language: str
    name: str = ""
    default: bool = False
    forced: bool = False
    enabled: bool = True
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    
    def __str__(self) -> str:
        return f"[{self.track_id}] {self.track_type} ({self.codec}) - {self.language} - {self.name}"


class MKVToolNixService(ToolService):
    """
    Wrapper for MKVToolNix (mkvmerge, mkvextract, mkvinfo).
    
    Provides:
    - Track information queries
    - Track extraction (audio, subtitles, video, attachments)
    - MKV muxing (creating new MKV files)
    - Attachment management (fonts)
    - Metadata editing
    """
    
    TOOL_NAME = "mkvmerge"
    REQUIRED = True
    MIN_VERSION = "50.0.0"
    
    def _auto_detect(self) -> Optional[str]:
        """
        Auto-detect mkvmerge on the system.
        
        Returns:
            mkvmerge path if found, None otherwise
        """
        import sys
        import os
        
        if sys.platform == "win32":
            candidates = [
                "mkvmerge.exe",
                "mkvmerge",
                r"C:\Program Files\MKVToolNix\mkvmerge.exe",
                r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
            ]
        elif sys.platform == "darwin":
            candidates = [
                "mkvmerge",
                "/usr/local/bin/mkvmerge",
                "/opt/homebrew/bin/mkvmerge",
            ]
        else:  # Linux
            candidates = [
                "mkvmerge",
                "/usr/bin/mkvmerge",
                "/usr/local/bin/mkvmerge",
            ]
        
        for candidate in candidates:
            try:
                if os.path.isabs(candidate):
                    if Path(candidate).is_file():
                        return candidate
                else:
                    result = subprocess.run(
                        [candidate, "--version"],
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
        Get mkvmerge version.
        
        Returns:
            Version string (e.g., "54.0.0")
            
        Raises:
            ServiceError: If version cannot be determined
        """
        if self._version_cache:
            return self._version_cache
        
        result = self._run_command([self.tool_path, "--version"], capture_output=True)
        
        # Parse version from output: mkvmerge v54.0.0
        match = re.search(r'v([\d.]+)', result.stdout)
        if match:
            self._version_cache = match.group(1)
            return self._version_cache
        
        raise ServiceError("Could not determine mkvmerge version")
    
    # ==================== Track Information ====================
    
    @handle_errors(default_return=[], error_types=(ServiceError,))
    def list_tracks(self, mkv_path: str) -> List[MKVTrack]:
        """
        List all tracks in an MKV file.
        
        Args:
            mkv_path: Path to MKV file
            
        Returns:
            List of MKVTrack objects
            
        Raises:
            ServiceError: If unable to read MKV
        """
        # Use mkvinfo with JSON output
        mkvinfo_path = Path(self.tool_path).parent / "mkvinfo"
        if not mkvinfo_path.exists():
            mkvinfo_path = "mkvinfo"
        
        try:
            cmd = [
                str(mkvinfo_path),
                "-j",  # JSON output
                str(mkv_path),
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                **self._get_popen_kwargs()
            )
            
            if result.returncode != 0:
                raise ServiceError(f"mkvinfo failed: {result.stderr}")
            
            data = json.loads(result.stdout)
            tracks = []
            
            for track_info in data.get("tracks", []):
                props = track_info.get("properties", {})
                
                track = MKVTrack(
                    track_id=track_info["id"],
                    track_type=track_info["type"],
                    codec=props.get("codec_id", "unknown"),
                    language=props.get("language", "und"),
                    name=props.get("track_name", ""),
                    default=props.get("default_track", False),
                    forced=props.get("forced_track", False),
                    enabled=props.get("enabled_track", True),
                    width=props.get("pixel_width"),
                    height=props.get("pixel_height"),
                    fps=props.get("frame_rate"),
                )
                tracks.append(track)
            
            logger.info(f"✓ Found {len(tracks)} tracks in {mkv_path}")
            return tracks
            
        except json.JSONDecodeError:
            raise ServiceError("Invalid JSON from mkvinfo")
        except Exception as e:
            raise ServiceError(f"Failed to list tracks: {e}") from e
    
    def get_track_by_id(self, mkv_path: str, track_id: int) -> Optional[MKVTrack]:
        """
        Get specific track by ID.
        
        Args:
            mkv_path: Path to MKV file
            track_id: Track ID to retrieve
            
        Returns:
            MKVTrack object or None if not found
        """
        tracks = self.list_tracks(mkv_path)
        for track in tracks:
            if track.track_id == track_id:
                return track
        return None
    
    def get_tracks_by_type(self, mkv_path: str, track_type: str) -> List[MKVTrack]:
        """
        Get all tracks of a specific type.
        
        Args:
            mkv_path: Path to MKV file
            track_type: Type of track ("video", "audio", "subtitles")
            
        Returns:
            List of matching MKVTrack objects
        """
        tracks = self.list_tracks(mkv_path)
        return [t for t in tracks if t.track_type == track_type]
    
    # ==================== Track Extraction ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def extract_track(
        self,
        mkv_path: str,
        track_id: int,
        output_path: str,
    ) -> Optional[Path]:
        """
        Extract a single track from MKV.
        
        Args:
            mkv_path: Path to MKV file
            track_id: Track ID to extract
            output_path: Where to save the extracted track
            
        Returns:
            Path to extracted track or None if failed
            
        Raises:
            ServiceError: If extraction fails
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use mkvextract
        mkvextract_path = Path(self.tool_path).parent / "mkvextract"
        if not mkvextract_path.exists():
            mkvextract_path = "mkvextract"
        
        try:
            cmd = [
                str(mkvextract_path),
                "tracks",
                str(mkv_path),
                f"{track_id}:{str(output_path)}",
            ]
            
            logger.debug(f"Extracting track {track_id} from {mkv_path}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                **self._get_popen_kwargs()
            )
            
            if result.returncode != 0:
                raise ServiceError(f"mkvextract failed: {result.stderr}")
            
            if not output_path.exists():
                raise ServiceError(f"Extraction did not produce output file: {output_path}")
            
            logger.info(f"✓ Extracted track {track_id} to {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Track extraction failed: {e}") from e
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def extract_subtitles(
        self,
        mkv_path: str,
        track_id: int,
        output_path: str,
    ) -> Optional[Path]:
        """
        Extract subtitle track (convenience method).
        
        Args:
            mkv_path: Path to MKV file
            track_id: Subtitle track ID
            output_path: Where to save (will auto-detect format)
            
        Returns:
            Path to extracted subtitles
        """
        return self.extract_track(mkv_path, track_id, output_path)
    
    # ==================== MKV Muxing ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def mux_files(
        self,
        output_path: str,
        video_file: str,
        audio_files: List[str] = None,
        subtitle_files: List[str] = None,
        attachment_files: List[str] = None,
        title: str = "",
    ) -> Optional[Path]:
        """
        Create a new MKV file with specified tracks.
        
        Args:
            output_path: Output MKV file path
            video_file: Path to video file
            audio_files: List of audio file paths
            subtitle_files: List of subtitle file paths
            attachment_files: List of attachment files (fonts, etc.)
            title: Title for the MKV
            
        Returns:
            Path to created MKV file or None if failed
            
        Raises:
            ServiceError: If muxing fails
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        audio_files = audio_files or []
        subtitle_files = subtitle_files or []
        attachment_files = attachment_files or []
        
        cmd = [self.tool_path, "-o", str(output_path)]
        
        # Add video
        cmd.extend([str(video_file)])
        
        # Add audio tracks
        for audio_file in audio_files:
            cmd.extend([str(audio_file)])
        
        # Add subtitle tracks
        for sub_file in subtitle_files:
            cmd.extend([str(sub_file)])
        
        # Add title
        if title:
            cmd.extend(["--title", title])
        
        # Add attachments (fonts, etc.)
        for attach_file in attachment_files:
            cmd.extend(["--attach-file", str(attach_file)])
        
        try:
            logger.debug(f"Muxing to {output_path}")
            self._run_command(cmd, timeout=120)
            
            if not output_path.exists():
                raise ServiceError(f"Muxing did not produce output file: {output_path}")
            
            logger.info(f"✓ MKV created: {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"MKV muxing failed: {e}") from e
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def add_subtitle_track(
        self,
        input_mkv: str,
        subtitle_file: str,
        output_mkv: str,
        language: str = "eng",
        default: bool = True,
        title: str = "",
    ) -> Optional[Path]:
        """
        Add a subtitle track to existing MKV.
        
        Args:
            input_mkv: Source MKV file
            subtitle_file: Subtitle file to add
            output_mkv: Output MKV file path
            language: Language code (e.g., "eng", "fre")
            default: Make this the default subtitle track
            title: Name for the subtitle track
            
        Returns:
            Path to output MKV
            
        Raises:
            ServiceError: If operation fails
        """
        output_path = Path(output_mkv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            self.tool_path,
            "-o", str(output_path),
            str(input_mkv),
            "--language", f"0:{language}",
            str(subtitle_file),
        ]
        
        if default:
            cmd.extend(["--default-track", "0:yes"])
        
        if title:
            cmd.extend(["--track-name", f"0:{title}"])
        
        try:
            logger.debug(f"Adding subtitle track to {input_mkv}")
            self._run_command(cmd, timeout=120)
            
            logger.info(f"✓ Subtitle track added to {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Failed to add subtitle track: {e}") from e
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def add_attachments(
        self,
        input_mkv: str,
        attachment_files: List[str],
        output_mkv: str,
    ) -> Optional[Path]:
        """
        Add attachment files (fonts, etc.) to MKV.
        
        Args:
            input_mkv: Source MKV file
            attachment_files: List of files to attach
            output_mkv: Output MKV file path
            
        Returns:
            Path to output MKV
            
        Raises:
            ServiceError: If operation fails
        """
        output_path = Path(output_mkv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            self.tool_path,
            "-o", str(output_path),
            str(input_mkv),
        ]
        
        # Add each attachment
        for attach_file in attachment_files:
            cmd.extend(["--attach-file", str(attach_file)])
        
        try:
            logger.debug(f"Adding {len(attachment_files)} attachments to {input_mkv}")
            self._run_command(cmd, timeout=120)
            
            logger.info(f"✓ Attachments added to {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Failed to add attachments: {e}") from e
