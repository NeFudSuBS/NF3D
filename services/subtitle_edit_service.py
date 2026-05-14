"""
Subtitle Edit service: OCR and subtitle format conversion.

Handles:
- OCR (PGS/VobSub to text)
- Format conversion (ASS ↔ SRT, etc.)
- Batch processing
- Image-based subtitle handling
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List

from .base_service import ToolService, ServiceError
from infrastructure.exception_handlers import handle_errors

logger = logging.getLogger(__name__)


class SubtitleEditService(ToolService):
    """
    Wrapper for Subtitle Edit (SubtitleEdit.exe / subtitleedit).
    
    Provides:
    - OCR for image-based subtitles (PGS, VobSub)
    - Format conversion
    - Batch processing
    - Subtitle validation
    
    Note: Subtitle Edit v4.x stable is recommended (not v5.x beta).
    The /convert command is more reliable in v4.x.
    """
    
    TOOL_NAME = "Subtitle Edit"
    REQUIRED = False  # Optional - only needed for PGS/VobSub OCR
    MIN_VERSION = "4.0.0"
    
    def _auto_detect(self) -> Optional[str]:
        """
        Auto-detect Subtitle Edit on the system.
        
        Returns:
            Subtitle Edit path if found, None otherwise
        """
        import sys
        import os
        
        if sys.platform == "win32":
            candidates = [
                "SubtitleEdit.exe",
                "subtitleedit",
                r"C:\Program Files\Subtitle Edit\SubtitleEdit.exe",
                r"C:\Program Files (x86)\Subtitle Edit\SubtitleEdit.exe",
            ]
        elif sys.platform == "darwin":
            candidates = [
                "subtitleedit",
                "/usr/local/bin/subtitleedit",
                "/opt/homebrew/bin/subtitleedit",
            ]
        else:  # Linux
            candidates = [
                "subtitleedit",
                "/usr/bin/subtitleedit",
                "/usr/local/bin/subtitleedit",
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
        Get Subtitle Edit version.
        
        Returns:
            Version string
            
        Raises:
            ServiceError: If version cannot be determined
        """
        if self._version_cache:
            return self._version_cache
        
        try:
            result = self._run_command(
                [self.tool_path, "--version"],
                capture_output=True
            )
            self._version_cache = result.stdout.strip()
            return self._version_cache
        except ServiceError:
            # Try alternative approach
            try:
                result = subprocess.run(
                    [self.tool_path],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    **self._get_popen_kwargs()
                )
                # Parse from stderr if available
                output = result.stderr or result.stdout
                if "Subtitle Edit" in output:
                    self._version_cache = "4.x or 5.x (stable)"
                    return self._version_cache
            except Exception:
                pass
        
        raise ServiceError("Could not determine Subtitle Edit version")
    
    # ==================== OCR ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def ocr_image_subtitles(
        self,
        image_subtitle_file: str,
        output_srt_file: str,
        language: str = "English",
    ) -> Optional[Path]:
        """
        OCR image-based subtitles (PGS, VobSub) to text.
        
        Args:
            image_subtitle_file: Input image-based subtitle file
            output_srt_file: Output SRT file path
            language: OCR language (e.g., "English", "French")
            
        Returns:
            Path to output SRT file or None if failed
            
        Raises:
            ServiceError: If OCR fails
        """
        output_path = Path(output_srt_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            logger.debug(f"OCRing {image_subtitle_file} to {output_srt_file}")
            
            # Subtitle Edit command line OCR
            cmd = [
                self.tool_path,
                str(image_subtitle_file),
                "/ocr",
                "/language", language,
                "/outputformat", "subrip",  # SRT format
                "/outputfile", str(output_path),
            ]
            
            self._run_command(cmd, timeout=300)  # Allow up to 5 minutes
            
            if not output_path.exists():
                raise ServiceError(f"OCR did not produce output file: {output_path}")
            
            # Verify output is not empty
            file_size = output_path.stat().st_size
            if file_size < 10:  # Minimum valid SRT size
                raise ServiceError("OCR produced empty output")
            
            logger.info(f"✓ OCR complete: {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"OCR failed: {e}") from e
    
    # ==================== Format Conversion ====================
    
    @handle_errors(default_return=None, error_types=(ServiceError,))
    def convert_format(
        self,
        input_file: str,
        output_file: str,
        output_format: str = "subrip",
    ) -> Optional[Path]:
        """
        Convert subtitle file between formats.
        
        Args:
            input_file: Input subtitle file
            output_file: Output subtitle file
            output_format: Output format ("subrip", "advancedsub2", etc.)
            
        Returns:
            Path to output file or None if failed
            
        Raises:
            ServiceError: If conversion fails
        """
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            logger.debug(f"Converting {input_file} to {output_format}")
            
            cmd = [
                self.tool_path,
                str(input_file),
                "/convert", output_format,
                "/outputfile", str(output_path),
            ]
            
            self._run_command(cmd, timeout=60)
            
            if not output_path.exists():
                raise ServiceError(f"Conversion did not produce output file: {output_path}")
            
            logger.info(f"✓ Conversion complete: {output_path}")
            return output_path
            
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError(f"Format conversion failed: {e}") from e
    
    def ass_to_srt(self, ass_file: str, srt_file: str) -> Optional[Path]:
        """
        Convert ASS to SRT (convenience method).
        
        Args:
            ass_file: Input ASS file
            srt_file: Output SRT file
            
        Returns:
            Path to output SRT file
        """
        return self.convert_format(ass_file, srt_file, "subrip")
    
    def srt_to_ass(self, srt_file: str, ass_file: str) -> Optional[Path]:
        """
        Convert SRT to ASS (convenience method).
        
        Args:
            srt_file: Input SRT file
            ass_file: Output ASS file
            
        Returns:
            Path to output ASS file
        """
        return self.convert_format(srt_file, ass_file, "advancedsub2")
    
    # ==================== Batch Processing ====================
    
    @handle_errors(default_return=[], error_types=(ServiceError,))
    def batch_ocr(
        self,
        subtitle_files: List[str],
        output_dir: str,
        language: str = "English",
    ) -> List[Path]:
        """
        OCR multiple subtitle files.
        
        Args:
            subtitle_files: List of image subtitle files
            output_dir: Directory for output SRT files
            language: OCR language
            
        Returns:
            List of output file paths
            
        Raises:
            ServiceError: If any OCR fails
        """
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        
        results = []
        
        for i, sub_file in enumerate(subtitle_files, 1):
            try:
                logger.info(f"OCRing {i}/{len(subtitle_files)}: {Path(sub_file).name}")
                
                output_path = output_dir_path / f"{Path(sub_file).stem}.srt"
                result = self.ocr_image_subtitles(sub_file, str(output_path), language)
                
                if result:
                    results.append(result)
                    
            except Exception as e:
                logger.error(f"Failed to OCR {sub_file}: {e}")
                # Continue with next file
        
        logger.info(f"✓ Batch OCR complete: {len(results)}/{len(subtitle_files)} successful")
        return results
    
    # ==================== Validation ====================
    
    @handle_errors(default_return=False, error_types=(ServiceError,))
    def validate_subtitles(self, subtitle_file: str) -> bool:
        """
        Validate subtitle file syntax.
        
        Args:
            subtitle_file: Subtitle file to validate
            
        Returns:
            True if valid, False otherwise
            
        Raises:
            ServiceError: If validation fails
        """
        try:
            # Try to load with Subtitle Edit
            with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tmp:
                tmp_path = tmp.name
            
            # Just try to convert it (validates in process)
            self.convert_format(subtitle_file, tmp_path, "subrip")
            Path(tmp_path).unlink()  # Clean up
            
            logger.info(f"✓ Subtitles valid: {subtitle_file}")
            return True
            
        except Exception as e:
            logger.warning(f"Invalid subtitles: {e}")
            return False
