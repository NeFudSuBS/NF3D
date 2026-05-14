"""
Services package: Wrappers for external tools and libraries.

Provides abstracted interfaces to:
- ffmpeg: Video frame extraction, information querying
- MKVToolNix: MKV parsing, track manipulation, muxing
- Subtitle Edit: OCR, format conversion
- Spellchecker: Spelling corrections
"""

from .base_service import ToolService
from .ffmpeg_service import FFmpegService
from .mkvtoolnix_service import MKVToolNixService
from .subtitle_edit_service import SubtitleEditService
from .spellchecker_service import SpellcheckerService

__all__ = [
    'ToolService',
    'FFmpegService',
    'MKVToolNixService',
    'SubtitleEditService',
    'SpellcheckerService',
]
