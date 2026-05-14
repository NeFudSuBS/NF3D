# config.py
"""
Centralized configuration for NF3D.
All constants and magic numbers defined here for easy maintenance.
"""
from dataclasses import dataclass, field
from typing import Dict, Set
import re


# ==================== Version & Format ====================
PROJECT_VERSION = 3
ASS_NL = "\\N"
ASS_TIME_CENTISECONDS_FACTOR = 100  # ASS uses centiseconds


# ==================== Video & Frame Analysis ====================
@dataclass
class DepthAnalysisConfig:
    """Configuration for stereoscopic depth analysis."""
    # Sampling
    DEFAULT_SAMPLES_PER_CUE: int = 6
    DEFAULT_OFFSET_INTERNAL: int = 8
    DEFAULT_INTERNAL_LIMIT: int = 100

    # Output mapping
    DEFAULT_OUT_MIN: int = -9
    DEFAULT_OUT_MAX: int = 2
    DEFAULT_OUTPUT_BIAS: int = -3
    DEFAULT_OUTPUT_SCALE: float = 2.3

    # Fallback depths (when analysis fails)
    DEFAULT_BASE_DEPTH: int = 6
    DEFAULT_CAPS_DEPTH: int = 8
    DEFAULT_ITALICS_DEPTH: int = 4

    # Stereo matching parameters (SGBM)
    SGBM_MIN_DISPARITY: int = -32
    SGBM_NUM_DISPARITIES: int = 128
    SGBM_BLOCK_SIZE: int = 5
    SGBM_P1: int = 8 * 3 * 5 * 5
    SGBM_P2: int = 32 * 3 * 5 * 5
    SGBM_DISP12_MAX_DIFF: int = 1
    SGBM_UNIQUENESS_RATIO: int = 8
    SGBM_SPECKLE_WINDOW_SIZE: int = 50
    SGBM_SPECKLE_RANGE: int = 2
    SGBM_PRE_FILTER_CAP: int = 31

    # Subtitle zone detection (normalized percentages)
    SUBTITLE_ZONE_Y_START_PCT: float = 0.80
    SUBTITLE_ZONE_X_MARGIN_PCT: float = 0.08


@dataclass
class EmergenceEffectsConfig:
    """Configuration for subtitle entrance/exit animation effects."""
    DEFAULT_FADE_IN_MS: int = 100
    DEFAULT_FADE_OUT_MS: int = 100
    DEFAULT_START_OPACITY_PCT: int = 40
    DEFAULT_ENTRY_MOTION_MS: int = 200
    DEFAULT_ENTRY_DEPTH_OFFSET: int = 1

    DEFAULT_TEST_MODE: bool = False
    DEFAULT_TEST_SCALE: float = 3.0


@dataclass
class OCRDetectionConfig:
    """Configuration for OCR error detection patterns."""
    MAX_VALID_DISPARITY: float = 64.0
    MIN_VALID_DISPARITY: float = -64.0

    # Use field(default_factory=...) for mutable defaults in dataclasses
    HOMOGLYPH_MAP: Dict[str, str] = field(default_factory=lambda: {
        # Cyrillic/Greek lookalikes
        "І": "I", "Ι": "I", "Ⅰ": "I",
        "ı": "i", "İ": "I", "Ӏ": "I",
        "е": "e", "а": "a", "о": "o",
        "р": "r", "с": "c", "х": "x", "у": "y",
    })

    ILLEGAL_CHARS: Set[str] = field(default_factory=lambda: {
        "ǀ", "∣", "⏐", "⎮", "|", "❘"
    })

    ALLOWED_UNICODE: Set[str] = field(default_factory=lambda: {
        # Music symbols
        "♪", "♫", "♬", "♭", "♮", "♯", "♩",
        # Typography
        "‘", "’", "“", "”", "—", "–", "…",
        # Common accented letters
        "é", "è", "ê", "ë", "à", "â", "ä",
        "î", "ï", "ô", "ö", "û", "ü", "ç", "ñ",
    })


# ==================== Regex Patterns (Pre-compiled) ====================
class RegexPatterns:
    """Pre-compiled regex patterns for better performance."""

    # Time parsing
    SRT_TIME = re.compile(
        r"^\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})(?:.*)?$"
    )

    # HTML/ASS markup
    ITALIC_OPEN  = re.compile(r"(?i)<\s*i\s*>")
    ITALIC_CLOSE = re.compile(r"(?i)<\s*/\s*i\s*>")
    BOLD_OPEN    = re.compile(r"(?i)<\s*b\s*>")
    BOLD_CLOSE   = re.compile(r"(?i)<\s*/\s*b\s*>")
    UNDER_OPEN   = re.compile(r"(?i)<\s*u\s*>")
    UNDER_CLOSE  = re.compile(r"(?i)<\s*/\s*u\s*>")
    ANY_TAG      = re.compile(r"(?is)<[^>]+>")

    # Word tokenization (handles contractions)
    WORD_TOKEN = re.compile(r"[A-Za-z]+(?:['''][A-Za-z]+)*")

    # OCR error detection patterns
    MID_WORD_PUNCT        = re.compile(r'\w+[:\;]\w*')
    DIGIT_BRACKET_CLUSTER = re.compile(r'\d+[)\]"]{1,3}')
    APOS_COLON_SEQUENCE   = re.compile(r"\w+'[:\;]\w*")
    BRACKET_IN_WORD       = re.compile(r'[a-zA-Z][)][^a-zA-Z0-9 ]')

    # ASS parsing
    DIALOGUE_LINE = re.compile(
        r'^(Dialogue:\s*\d+,)'   # prefix
        r'([^,]+),'               # start
        r'([^,]+),'               # end
        r'([^,]*),'               # style
        r'([^,]*),'               # name
        r'([^,]*),'               # mL
        r'([^,]*),'               # mR
        r'([^,]*),'               # mV
        r'([^,]*),'               # effect
        r'(.*)$'                  # text
    )
    CLIP_TAG = re.compile(r'\\clip\(([^)]+)\)')
    POS_TAG  = re.compile(r'\\pos\(([^,]+),([^)]+)\)')
    MOVE_TAG = re.compile(r'\\move\(([^,]+),([^,]+),([^,]+),([^,)]+)[^)]*\)')


# ==================== Error Messages ====================
class ErrorMessages:
    """Centralized error messages."""
    FFMPEG_NOT_FOUND         = "ffmpeg not found. Set its path in Advanced > Tools & paths."
    MKVMERGE_NOT_FOUND       = "mkvmerge not found. Install MKVToolNix."
    MKVEXTRACT_NOT_FOUND     = "mkvextract not found. Install MKVToolNix."
    SUBTITLE_EDIT_NOT_FOUND  = (
        "Subtitle Edit not found. Set its path in Advanced > Tools & paths. "
        "Ensure you have v4.x stable (not v5.x beta)."
    )
    NO_VIDEO_LOADED          = "No video file loaded. Choose a valid MKV or MP4."
    NO_SUBTITLE_PREPARED     = "No subtitle prepared. Run 'Prepare subtitle' first."
    NO_DEPTH_ANALYSIS        = "No depth analysis data. Run 'Analyze depth' first."
    INVALID_PROJECT_VERSION  = "Project version is newer than this NF3D build. Please update NF3D."


# ==================== Default Settings ====================
DEPTH_ANALYSIS_DEFAULTS = {
    "samples_per_cue":  6,
    "offset_internal":  8,
    "internal_limit":   100,
    "out_min":          -9,
    "out_max":          2,
    "output_bias":      -3,
    "output_scale":     2.3,
    "base_depth":       6,
    "caps_depth":       8,
    "italics_depth":    4,
}

STYLE_DEFAULTS = {
    "font":             "Arial",
    "font_size":        72,
    "primary_colour":   "&H00E6E6E6",
    "outline_colour":   "&H00000000",
    "back_colour":      "&H64000000",
    "outline":          4.0,
    "shadow":           2.0,
    "shadow_x":         2,
    "shadow_y":         2,
    "alignment":        2,
    "margin_l":         30,
    "margin_r":         30,
    "margin_v":         45,
}
