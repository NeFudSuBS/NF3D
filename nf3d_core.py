#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nf3d_core.py — NF3D core library (v3)
All subtitle parsing, depth analysis, ASS generation, per-cue override logic,
and OCR issue detection live here.  The GUI imports these directly.
"""
from __future__ import annotations

import dataclasses
import html
import json
import re
import subprocess
import sys
import tempfile
import unicodedata

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from logging_config import get_logger
from config import PROJECT_VERSION, ASS_NL, RegexPatterns

logger = get_logger(__name__)


def _popen_kwargs():
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}

# ─────────────────────────────────────────────────────────────────────────────
# Constants — PROJECT_VERSION, ASS_NL imported from config.py
# ─────────────────────────────────────────────────────────────────────────────

# Aliases for pre-compiled patterns from config.RegexPatterns.
# Keeping the _RE suffix so nothing else in the file needs renaming.
SRT_TIME_RE     = RegexPatterns.SRT_TIME
ITALIC_OPEN_RE  = RegexPatterns.ITALIC_OPEN
ITALIC_CLOSE_RE = RegexPatterns.ITALIC_CLOSE
BOLD_OPEN_RE    = RegexPatterns.BOLD_OPEN
BOLD_CLOSE_RE   = RegexPatterns.BOLD_CLOSE
UNDER_OPEN_RE   = RegexPatterns.UNDER_OPEN
UNDER_CLOSE_RE  = RegexPatterns.UNDER_CLOSE
ANY_TAG_RE      = RegexPatterns.ANY_TAG
# Matches whole words including contractions with either straight (‘)
# or curly (‘) apostrophes: didn’t, didn’t, we’ll, we’ll, I’ve, I’ve.
WORD_TOKEN_RE   = RegexPatterns.WORD_TOKEN

# Characters visually identical to common Latin letters but from other Unicode
# blocks — these are genuine OCR misread signals that require no context.
HOMOGLYPH_MAP: dict = {
    # Cyrillic/Greek/special-Unicode letter lookalikes — genuine OCR errors.
    # Smart quotes, typographic dashes, and accented letters are intentionally
    # excluded: they are legitimate in subtitles, not OCR artefacts.
    "І": "I",   # Ukrainian І → I
    "Ι": "I",   # Greek Ι → I
    "Ⅰ": "I",   # Roman numeral Ⅰ → I
    "ı": "i",   # Dotless ı → i
    "İ": "I",   # I with dot above → I
    "Ӏ": "I",   # Palochka → I
    "е": "e",   # Cyrillic е → e
    "а": "a",   # Cyrillic а → a
    "о": "o",   # Cyrillic о → o
    "р": "r",   # Cyrillic р → r
    "с": "c",   # Cyrillic с → c
    "х": "x",   # Cyrillic х → x
    "у": "y",   # Cyrillic у → y
}

# Characters equivalent to ASCII apostrophe for tokenisation.
# Allows contractions like didn’t, we’ll to be tokenised as one word.
APOSTROPHE_CHARS = {"’", "‘", "'"}

# Characters that should never appear in subtitle text
ILLEGAL_CHARS: set = {
    "ǀ",  # Dental click ǀ
    "∣",  # Divides ∣
    "⏐",  # Vertical line extension ⏐
    "⎮",  # Integral extension
    "|",  # Vertical bar |
    "❘",  # Light vertical bar ❘
}

# Non-ASCII characters that are legitimate in subtitles and must not be flagged.
_ALLOWED_UNICODE: set = {
    "♪", "♫", "♬", "♭", "♮", "♯", "♩",  # music
    "‘", "’",  # curly apostrophes
    "“", "”",  # curly double quotes
    "—", "–",  # em dash, en dash
    "…",            # ellipsis
    "é", "è", "ê", "ë",  # e accents
    "à", "â", "ä",          # a accents
    "î", "ï",                  # i accents
    "ô", "ö",                  # o accents
    "û", "ü",                  # u accents
    "ç", "ñ",                  # c-cedilla, n-tilde
}


# ─────────────────────────────────────────────────────────────────────────────
# Spellchecker singleton (optional — degrades gracefully if not installed)
# ─────────────────────────────────────────────────────────────────────────────

_sc_instance = None
_sc_lock = None
_correction_cache: dict = {}   # word.lower() → suggested correction string


def _get_spellchecker():
    """Lazy-load SpellChecker once; return None if unavailable."""
    global _sc_instance, _sc_lock
    if _sc_lock is None:
        import threading as _thr
        _sc_lock = _thr.Lock()
    with _sc_lock:
        if _sc_instance is None:
            try:
                from spellchecker import SpellChecker
                _sc_instance = SpellChecker(language='en')
            except ImportError as e:
                logger.warning(f"spellchecker package not installed: {e}")
                _sc_instance = False
            except Exception as e:
                logger.warning(f"spellchecker failed to load: {e}")
                _sc_instance = False
    return _sc_instance if _sc_instance else None


def spellchecker_available() -> bool:
    """Return True if the spellchecker loaded successfully."""
    return _get_spellchecker() is not None


def spellchecker_add_words(words):
    """Add words to the live spellchecker so they are not flagged in future scans."""
    global _correction_cache
    sc = _get_spellchecker()
    if sc is not None:
        try:
            sc.word_frequency.load_words(words)
            # Invalidate cached corrections for added words so they re-resolve
            for w in words:
                _correction_cache.pop(w.lower(), None)
        except Exception as e:
            logger.warning(f"Failed to add words to spellchecker: {e}")


def get_spelling_suggestion(word: str) -> str:
    """
    Return the spellchecker's best correction for *word*, caching the result.
    This can take 10–200 ms for unusual words — call from the UI only when the
    user selects a specific flagged issue, not in the bulk scan loop.
    """
    global _correction_cache
    key = word.lower()
    if key not in _correction_cache:
        sc = _get_spellchecker()
        _correction_cache[key] = (sc.correction(word) or "") if sc else ""
    return _correction_cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# OCR issue scanner — character-level checks
# ─────────────────────────────────────────────────────────────────────────────

# ── Dictionary management ─────────────────────────────────────────────────────

USER_DICT_PATH = Path.home() / "nf3d_user_dictionary.json"


def load_persistent_dictionary() -> set:
    try:
        if USER_DICT_PATH.exists():
            return set(json.loads(USER_DICT_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load user dictionary from {USER_DICT_PATH}: {e}")
    return set()


def save_persistent_dictionary(words: set) -> None:
    try:
        USER_DICT_PATH.write_text(json.dumps(sorted(words), indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not save user dictionary to {USER_DICT_PATH}: {e}")


# ── TextIssue ─────────────────────────────────────────────────────────────────

@dataclass
class TextIssue:
    """A single detected issue in subtitle text."""
    kind:        str   # "homoglyph" | "illegal" | "nonascii" | "ocr"
    description: str   # Human-readable description shown in the reviewer
    original:    str   # The problematic text fragment
    suggestion:  str   # Suggested replacement (may be empty)
    position:    int   # Character offset in plain text (-1 if N/A)

    def __str__(self) -> str:
        if self.suggestion:
            return f"{self.description} → suggest: '{self.suggestion}'"
        return self.description


def _strip_html_for_scan(text: str) -> str:
    t = html.unescape(text or "")
    t = ITALIC_OPEN_RE.sub("", t)
    t = ITALIC_CLOSE_RE.sub("", t)
    t = ANY_TAG_RE.sub("", t)
    return t


def scan_text_issues(
    text: str,
    session_dict: set,
    persistent_dict: set,
) -> list:
    """
    Scan a subtitle text string for issues.

    Checks performed:
      1. Homoglyph characters (Cyrillic/Greek lookalikes that appear identical
         to Latin letters but are not — genuine OCR error signal).
      2. Illegal vertical-bar characters.
      3. Non-ASCII characters outside the expected letter/punctuation categories
         (invisible control chars, unusual Unicode).
      4. OCR corruption patterns (mid-word punctuation, bracket/digit clusters).

    Returns a list of TextIssue objects, one per detected problem.
    """
    plain      = _strip_html_for_scan(text)
    issues     = []
    suppressed = session_dict | persistent_dict

    # ── 1. Homoglyphs ─────────────────────────────────────────────────────────
    for bad, good in HOMOGLYPH_MAP.items():
        idx = plain.find(bad)
        while idx != -1:
            issues.append(TextIssue(
                kind="homoglyph",
                description=f"Homoglyph '{bad}' (U+{ord(bad):04X}) looks like '{good}'",
                original=bad, suggestion=good, position=idx,
            ))
            idx = plain.find(bad, idx + 1)

    # ── 2. Illegal characters ─────────────────────────────────────────────────
    for i, ch in enumerate(plain):
        if ch in ILLEGAL_CHARS:
            issues.append(TextIssue(
                kind="illegal",
                description=f"Illegal char U+{ord(ch):04X} '{ch}'",
                original=ch, suggestion="", position=i,
            ))

    # ── 2b. OCR corruption patterns ──────────────────────────────────────────
    # These catch errors that pass homoglyph and spell-check because they
    # don't involve lookalike characters — they're structural corruptions.

    # Pattern A: mid-word colon or semicolon (e.g. "par':" "P':a" "wo:rd")
    # Normal text never has a colon or semicolon embedded inside a word.
    for m in RegexPatterns.MID_WORD_PUNCT.finditer(plain):
        w = m.group()
        # Allow time-like patterns (4:00, 5:30) and ellipsis-like
        if re.match(r'^\d+:\d+$', w):
            continue
        issues.append(TextIssue(
            kind="ocr",
            description=f"Possible OCR error: mid-word punctuation in '{w}'",
            original=w, suggestion="", position=m.start(),
        ))

    # Pattern B: digit immediately followed by quote/bracket clusters — OCR garbage
    # e.g. 39") or 4"") in dialogue; normal text does not combine these.
    for m in RegexPatterns.DIGIT_BRACKET_CLUSTER.finditer(plain):
        w = m.group()
        # Allow standalone numbers followed by inch-mark: 6" pipe
        if re.fullmatch(r'\d+"', w):
            continue
        issues.append(TextIssue(
            kind="ocr",
            description=f"Possible OCR garbage: '{w}' (digit + bracket/quote cluster)",
            original=w, suggestion="", position=m.start(),
        ))

    # Pattern C: apostrophe-colon sequence mid-word ("par':", "it':", "he':")
    # An apostrophe should never be followed by a colon in valid English.
    for m in RegexPatterns.APOS_COLON_SEQUENCE.finditer(plain):
        w = m.group()
        issues.append(TextIssue(
            kind="ocr",
            description=f"Possible OCR error: apostrophe+punctuation in '{w}'",
            original=w, suggestion="", position=m.start(),
        ))

    # Pattern D: bracket/paren after a letter in unlikely positions
    # Catches: "wh)at" or "m)'" — a bracket OCR inserted into dialogue
    for m in RegexPatterns.BRACKET_IN_WORD.finditer(plain):
        w = m.group()
        issues.append(TextIssue(
            kind="ocr",
            description=f"Possible OCR error: unexpected bracket in '{w.strip()}'",
            original=w, suggestion="", position=m.start(),
        ))
    # ── 3. Suspicious non-ASCII ───────────────────────────────────────────────
    # Characters that are legitimate in subtitles and should never be flagged.
    # Musical notes appear at line start/end as song indicators.
    # Smart quotes and dashes are legitimate typography, not OCR errors.
    ALLOWED_UNICODE = {
        "♪", "♫", "♬", "♭", "♮", "♯",  # ♪♫♬♭♮♯ music
        "♩",  # ♩ quarter note
        "’", "‘",  # curly apostrophes — legitimate typography
        "“", "”",  # curly double quotes
        "—", "–",  # em dash, en dash
        "…",  # ellipsis …
        "é", "è", "ê", "ë",  # é è ê ë
        "à", "â", "ä",  # à â ä
        "î", "ï",  # î ï
        "ô", "ö",  # ô ö
        "û", "ü",  # û ü
        "ç",  # ç
        "ñ",  # ñ
    }
    already = {iss.position for iss in issues}
    for i, ch in enumerate(plain):
        if i in already or ord(ch) <= 127 or ch in ALLOWED_UNICODE:
            continue
        cat = unicodedata.category(ch)
        if cat == "Cf":
            issues.append(TextIssue(
                kind="nonascii",
                description=f"Invisible control char U+{ord(ch):04X}",
                original=ch, suggestion="", position=i,
            ))
        elif cat not in ("Ll", "Lu", "Lt", "Lo", "Nd", "Pd", "Po", "Pc", "Pf", "Pi"):
            issues.append(TextIssue(
                kind="nonascii",
                description=f"Unusual char U+{ord(ch):04X} '{ch}' ({unicodedata.name(ch, '?')})",
                original=ch, suggestion="", position=i,
            ))

    # ── 4. Spellcheck ─────────────────────────────────────────────────────────
    # sc.correction() is intentionally NOT called here — it is O(vocabulary)
    # and takes 10–200 ms per word.  Suggestions are computed lazily via
    # get_spelling_suggestion() only when the user selects a specific issue.
    sc = _get_spellchecker()
    if sc is not None:
        already_caught = {iss.original for iss in issues}
        for m in WORD_TOKEN_RE.finditer(plain):
            word = m.group()
            if len(word) <= 2 or word.isupper():
                continue
            w_lower = word.lower()
            if w_lower in suppressed or word in suppressed:
                continue
            if word in already_caught or w_lower in already_caught:
                continue
            if sc.unknown([word]):
                issues.append(TextIssue(
                    kind="spelling",
                    description=f"Possible spelling: '{word}'",
                    original=word, suggestion="",   # populated lazily on selection
                    position=m.start(),
                ))

    return issues


def has_issues(text: str, session_dict: set, persistent_dict: set) -> bool:
    """Quick boolean check — used to filter the cue list."""
    return bool(scan_text_issues(text, session_dict, persistent_dict))



# ─────────────────────────────────────────────────────────────────────────────
# Per-cue override dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CueOverride:
    """
    Holds per-cue values that deviate from global settings.
    Every field is Optional — None means "use the global default".
    """
    depth:      Optional[int]   = None
    x_pct:      Optional[float] = None
    y_pct:      Optional[float] = None
    primary_colour:     Optional[str]   = None
    outline_colour:     Optional[str]   = None
    back_colour:        Optional[str]   = None
    font:               Optional[str]   = None
    font_size:          Optional[int]   = None
    bold:               Optional[bool]  = None
    outline:            Optional[float] = None
    shadow:             Optional[float] = None
    emerge_in_ms:       Optional[int]   = None
    emerge_out_ms:      Optional[int]   = None
    start_opacity:      Optional[int]   = None
    entry_motion_ms:    Optional[int]   = None
    entry_depth_offset: Optional[int]   = None
    raw_ass_tags:       Optional[str]   = None
    note:               str             = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != ""}

    @classmethod
    def from_dict(cls, d: dict) -> "CueOverride":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def is_empty(self) -> bool:
        return self.to_dict() == {}

    def resolve(self, global_val: Any, field_name: str) -> Any:
        v = getattr(self, field_name)
        return global_val if v is None else v


# ─────────────────────────────────────────────────────────────────────────────
# Project: depth map + overrides + versioned saves
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Project:
    """Persists depth map, per-cue overrides, and source paths between sessions."""
    srt_path:   str  = ""
    video_path: str  = ""
    depth_map:  dict = field(default_factory=dict)
    overrides:  dict = field(default_factory=dict)
    version:    int  = PROJECT_VERSION

    def get_depth(self, index: int) -> Optional[dict]:
        return self.depth_map.get(str(index))

    def get_override(self, index: int) -> CueOverride:
        return CueOverride.from_dict(self.overrides.get(str(index), {}))

    def set_override(self, index: int, ov: CueOverride) -> None:
        if ov.is_empty():
            self.overrides.pop(str(index), None)
        else:
            self.overrides[str(index)] = ov.to_dict()

    def clear_override(self, index: int) -> None:
        self.overrides.pop(str(index), None)

    def overridden_indices(self) -> set:
        return {int(k) for k in self.overrides}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "version":    self.version,
            "srt_path":   self.srt_path,
            "video_path": self.video_path,
            "depth_map":  self.depth_map,
            "overrides":  self.overrides,
        }, indent=2), encoding="utf-8")

    def save_version(self, base_path: Path) -> Path:
        """
        Save to base_path (N).nf3d.json where N auto-increments.
        Returns the path actually written.
        """
        stem = base_path.stem
        # Strip any existing " (N)" suffix so we always count from the base name
        stem = re.sub(r"\s*\(\d+\)$", "", stem)
        parent = base_path.parent
        n = 1
        while True:
            candidate = parent / f"{stem} ({n}){base_path.suffix}"
            if not candidate.exists():
                break
            n += 1
        self.save(candidate)
        return candidate

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(path.read_text(encoding="utf-8"))
        v = data.get("version", 1)
        if v > PROJECT_VERSION:
            raise ValueError(
                f"Project version {v} is newer than this NF3D build "
                f"(max: {PROJECT_VERSION}). Please update."
            )
        return cls(
            srt_path   = data.get("srt_path",   ""),
            video_path = data.get("video_path", ""),
            depth_map  = data.get("depth_map",  {}),
            overrides  = data.get("overrides",  {}),
            version    = PROJECT_VERSION,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Text utilities
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for bad, good in HOMOGLYPH_MAP.items():
        s = s.replace(bad, good)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def strip_markup_for_preview(text: str) -> tuple:
    text = normalize_text(html.unescape(text or ""))
    had_italics = bool(ITALIC_OPEN_RE.search(text) or ITALIC_CLOSE_RE.search(text))
    text = ITALIC_OPEN_RE.sub("", text)
    text = ITALIC_CLOSE_RE.sub("", text)
    text = ANY_TAG_RE.sub("", text)
    text = text.replace("\\N", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip(), had_italics


# ─────────────────────────────────────────────────────────────────────────────
# SRT parsing & de-overlap
# ─────────────────────────────────────────────────────────────────────────────

def parse_srt(path: Path) -> list:
    txt = (path.read_text(encoding="utf-8-sig", errors="replace")
           .replace("\r\n", "\n").replace("\r", "\n"))
    blocks = re.split(r"\n\s*\n", txt.strip())
    events = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        if lines[0].strip().isdigit():
            idx_str, time_line, text_lines = lines[0], lines[1], lines[2:]
        else:
            idx_str, time_line, text_lines = "", lines[0], lines[1:]
        m = SRT_TIME_RE.match(time_line)
        if not m:
            continue
        start = f"{m.group(1)}:{m.group(2)}:{m.group(3)},{m.group(4)}"
        end   = f"{m.group(5)}:{m.group(6)}:{m.group(7)},{m.group(8)}"
        text  = "\n".join(text_lines).strip()
        if text:
            events.append({
                "index": int(idx_str) if idx_str.strip().isdigit() else len(events) + 1,
                "start": start, "end": end, "text": text,
            })
    logger.debug(f"parse_srt: {len(events)} cues loaded from {path}")
    return events


def deoverlap_events(events: list) -> list:
    if not events:
        return events
    out = [dict(events[0])]
    for ev in events[1:]:
        prev = out[-1]
        if srt_time_to_ms(ev["start"]) < srt_time_to_ms(prev["end"]):
            prev["end"] = ev["start"]
        out.append(dict(ev))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────────────────

def srt_time_to_ms(t: str) -> int:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h)*3_600_000 + int(m)*60_000 + int(s)*1_000 + int(ms)


def ms_to_srt_time(ms: int) -> str:
    ms = max(0, ms)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_time_to_ffmpeg(t: str) -> str:
    return t.replace(",", ".")


def srt_time_to_ass(t: str) -> str:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    cs = min(99, int(round(int(ms) / 10.0)))
    return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"


def seconds_to_ffmpeg(s: float) -> str:
    s = max(0.0, s)
    hh = int(s // 3600); mm = int((s % 3600) // 60); ss = s - hh*3600 - mm*60
    return f"{hh:02d}:{mm:02d}:{ss:06.3f}"


# ─────────────────────────────────────────────────────────────────────────────
# ASS markup conversion
# ─────────────────────────────────────────────────────────────────────────────

def convert_markup(text: str) -> tuple:
    text = normalize_text(text)
    text = html.unescape(text)
    had_italics = bool(ITALIC_OPEN_RE.search(text) or ITALIC_CLOSE_RE.search(text))
    text = ITALIC_OPEN_RE.sub("{\\\\i1}", text)
    text = ITALIC_CLOSE_RE.sub("{\\\\i0}", text)
    text = BOLD_OPEN_RE.sub("{\\\\b1}", text)
    text = BOLD_CLOSE_RE.sub("{\\\\b0}", text)
    text = UNDER_OPEN_RE.sub("{\\\\u1}", text)
    text = UNDER_CLOSE_RE.sub("{\\\\u0}", text)
    text = ANY_TAG_RE.sub("", text)
    protected = []
    def protect(m):
        protected.append(m.group(0)); return f"\uFFF0{len(protected)-1}\uFFF1"
    text = re.sub(r"\{\\[ibu][01]\}", protect, text)
    text = text.replace("{", r"\{").replace("}", r"\}")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ASS_NL)
    def unprotect(m):
        return protected[int(m.group(1))]
    text = re.sub(r"\uFFF0(\d+)\uFFF1", unprotect, text)
    return text.strip(), had_italics


def visible_ass_text(t: str) -> str:
    return re.sub(r"\{.*?\}", "", t)


def looks_all_caps(t: str) -> bool:
    letters = [c for c in visible_ass_text(t) if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) / len(letters) > 0.80


# ─────────────────────────────────────────────────────────────────────────────
# Tool detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_ffmpeg() -> str:
    import sys as _sys, os as _os
    if _sys.platform == "win32":
        cands = ["ffmpeg",
                 r"C:\ffmpeg\bin\ffmpeg.exe",
                 r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                 r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"]
    elif _sys.platform == "darwin":
        cands = ["ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]
    else:
        cands = ["ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    for cand in cands:
        try:
            if _os.path.isabs(cand):
                if Path(cand).is_file(): return cand
            else:
                p = subprocess.run([cand, "-version"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **_popen_kwargs())
                if p.returncode == 0: return cand
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass
    return ""


def get_video_info(mkvmerge_exe: str, video_path: str) -> dict:
    """
    Return basic video track info from mkvmerge -J.
    Keys: width, height, is_sbs, eye_w, is_hsbs
    Returns empty dict on failure.
    """
    try:
        p = subprocess.run(
            [mkvmerge_exe, "-J", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_popen_kwargs(),
        )
        if p.returncode != 0:
            return {}
        data = json.loads(p.stdout)
        # Container duration is in nanoseconds in mkvmerge -J output
        dur_ns = (data.get("container", {})
                      .get("properties", {})
                      .get("duration", 0))
        duration_s = dur_ns / 1e9 if dur_ns else 0.0
        for track in data.get("tracks", []):
            if track.get("type") == "video":
                props = track.get("properties", {}) or {}
                w = props.get("pixel_dimensions", "")
                if "x" in w:
                    width, height = (int(x) for x in w.split("x"))
                    ratio = width / height if height else 0
                    is_fsbs = ratio > 3.0
                    eye_w   = width // 2
                    info = {
                        "width":      width,
                        "height":     height,
                        "eye_w":      eye_w,
                        "is_fsbs":    is_fsbs,
                        "ratio":      round(ratio, 2),
                        "duration_s": duration_s,
                    }
                    logger.debug(f"get_video_info: {width}x{height}, ratio={round(ratio,2)}, sbs={is_fsbs}")
                    return info
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError) as e:
        logger.warning(f"get_video_info failed for {video_path!r}: {e}")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Depth analysis
# ─────────────────────────────────────────────────────────────────────────────

def _extract_frame(ffmpeg: str, video: str, t: float, out: str) -> None:
    cmd = [ffmpeg, "-y", "-ss", seconds_to_ffmpeg(t), "-i", video, "-frames:v", "1", out]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace",
                       **_popen_kwargs())
    if p.returncode != 0 or not Path(out).is_file():
        raise RuntimeError(p.stdout.strip() or "ffmpeg frame extraction failed")


def _disparity_in_subtitle_zone(frame_path: str) -> tuple:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0.0, 960
    img = cv2.imread(frame_path, cv2.IMREAD_COLOR)
    if img is None:
        return 0.0, 960
    h, w = img.shape[:2]
    eye_w = w // 2
    y0 = int(h * 0.80); y1 = h
    x0 = int(eye_w * 0.08); x1 = int(eye_w * 0.92)
    lr = img[y0:y1, x0:x1]
    rr = img[y0:y1, eye_w + x0:eye_w + x1]
    if lr.size == 0 or rr.size == 0:
        return 0.0, eye_w
    lg = cv2.cvtColor(lr, cv2.COLOR_BGR2GRAY)
    rg = cv2.cvtColor(rr, cv2.COLOR_BGR2GRAY)
    matcher = cv2.StereoSGBM_create(
        minDisparity=-32, numDisparities=128, blockSize=5,
        P1=8*3*5*5, P2=32*3*5*5, disp12MaxDiff=1,
        uniquenessRatio=8, speckleWindowSize=50, speckleRange=2,
        preFilterCap=31, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
    )
    disp = matcher.compute(lg, rg).astype("float32") / 16.0
    valid = disp[~((disp < -64.0) | (disp > 64.0) | ~np.isfinite(disp))]
    if valid.size == 0:
        return 0.0, eye_w
    return float(np.median(valid)), eye_w


def analyse_cue_depths(
    video: str, ffmpeg: str, events: list,
    offset_internal: int = 8, samples_per_cue: int = 6,
    internal_limit: int = 100, out_min: int = -8, out_max: int = 2,
    output_bias: int = -1, output_scale: float = 2.1,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Analyse depth during each subtitle cue window.
    Returns dict keyed by str(event_index).
    Default values reflect empirically good settings for typical 3D SBS content.
    """
    try:
        import cv2   # noqa
        has_cv2 = True
    except ImportError:
        has_cv2 = False

    logger.info(f"analyse_cue_depths: {len(events)} cues, video={video!r}")
    results = {}
    total = len(events)
    with tempfile.TemporaryDirectory(prefix="nf3d_") as td:
        td_path = Path(td)
        for i, ev in enumerate(events):
            if progress_cb:
                progress_cb(i, total)
            key = str(ev["index"])
            start_s = srt_time_to_ms(ev["start"]) / 1000.0
            end_s   = srt_time_to_ms(ev["end"])   / 1000.0
            dur = end_s - start_s
            if not has_cv2 or dur <= 0:
                results[key] = {"depth": 0, "raw": 0.0, "fallback": True}
                continue
            n = max(1, min(samples_per_cue, int(dur * 2)))
            fracs = [0.5] if n == 1 else [j/(n-1) for j in range(n)]
            samples = []
            for j, t in enumerate([start_s + dur*f for f in fracs]):
                fp = str(td_path / f"e{i}f{j}.png")
                try:
                    _extract_frame(ffmpeg, video, t, fp)
                    d, _ = _disparity_in_subtitle_zone(fp)
                    samples.append(d)
                except (RuntimeError, OSError) as e:
                    logger.debug(f"Frame {j} failed for cue {key} at t={t:.2f}s: {e}")
            if not samples:
                results[key] = {"depth": 0, "raw": 0.0, "fallback": True}
                continue
            import statistics
            raw      = statistics.median(samples)
            internal = max(-internal_limit, min(internal_limit,
                           float(-raw) + offset_internal))
            abs_lim  = max(abs(out_min), abs(out_max))
            mapped   = max(out_min, min(out_max,
                           int(round((internal / internal_limit) * abs_lim
                                     * output_scale)) + output_bias))
            results[key] = {"depth": mapped, "raw": round(raw, 3), "fallback": False}
    if progress_cb:
        progress_cb(total, total)
    logger.info(f"analyse_cue_depths: complete — {len(results)} results")
    return results

def rescan_fallback_cues(
    video: str, ffmpeg: str, events: list,
    existing_depth_map: dict,
    offset_internal: int = 8, samples_per_cue: int = 6,
    internal_limit: int = 100, out_min: int = -9, out_max: int = 2,
    output_bias: int = -3, output_scale: float = 2.3,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Re-analyse only cues that are missing from existing_depth_map or marked
    as fallback (i.e. SGBM failed or cv2 was unavailable when they were
    first processed).

    Returns the updated depth_map with new results merged in.
    The existing_depth_map is NOT mutated — a new dict is returned.

    progress_cb(done, total, key) is called after each cue.
    """
    # Identify which cues need re-scanning
    missing_events = [
        ev for ev in events
        if (str(ev["index"]) not in existing_depth_map
            or existing_depth_map[str(ev["index"])].get("fallback", True))
    ]

    if not missing_events:
        if progress_cb:
            progress_cb(0, 0, "no missing cues")
        return dict(existing_depth_map)   # nothing to do

    # Run analysis on just the missing subset
    partial = analyse_cue_depths(
        video, ffmpeg, missing_events,
        offset_internal=offset_internal,
        samples_per_cue=samples_per_cue,
        internal_limit=internal_limit,
        out_min=out_min, out_max=out_max,
        output_bias=output_bias,
        output_scale=output_scale,
        progress_cb=progress_cb,
    )

    # Merge: start from existing, overlay with fresh results
    merged = dict(existing_depth_map)
    merged.update(partial)
    return merged




# ─────────────────────────────────────────────────────────────────────────────
# Effective-value resolver — single source of truth for preview AND output
# ─────────────────────────────────────────────────────────────────────────────

def effective_params(ev: dict, globals_: dict, project: Project) -> dict:
    ov = project.get_override(ev["index"])
    ass_text, had_italics = convert_markup(ev["text"])

    if ov.depth is not None:
        depth = ov.depth
    else:
        dm = project.get_depth(ev["index"])
        if dm and not dm.get("fallback"):
            depth = dm["depth"]
        elif had_italics:
            depth = globals_["italics_depth"]
        elif looks_all_caps(ass_text):
            depth = globals_["caps_depth"]
        else:
            depth = globals_["base_depth"]

    eye_w    = globals_["eye_w"]
    h        = globals_["h"]
    y_px     = globals_.get("y_px", int(h * 0.88))
    x_centre = (int(round(ov.x_pct / 100.0 * eye_w))
                if ov.x_pct is not None else eye_w // 2)
    y_centre = (int(round(ov.y_pct / 100.0 * h))
                if ov.y_pct is not None else y_px)

    return dict(
        ass_text      = ass_text,
        had_italics   = had_italics,
        depth         = depth,
        x_centre      = x_centre,
        y_centre      = y_centre,
        font          = ov.resolve(globals_["font"],           "font"),
        font_size     = ov.resolve(globals_["font_size"],      "font_size"),
        primary_colour= ov.resolve(globals_["primary_colour"], "primary_colour"),
        outline_colour= ov.resolve(globals_["outline_colour"], "outline_colour"),
        back_colour   = ov.resolve(globals_["back_colour"],    "back_colour"),
        outline       = ov.resolve(globals_["outline"],        "outline"),
        shadow        = ov.resolve(globals_["shadow"],         "shadow"),
        bold          = ov.bold,
        emerge_in_ms      = ov.resolve(globals_["emerge_in_ms"],       "emerge_in_ms"),
        emerge_out_ms     = ov.resolve(globals_["emerge_out_ms"],      "emerge_out_ms"),
        start_opacity     = ov.resolve(globals_["start_opacity"],      "start_opacity"),
        entry_motion_ms   = ov.resolve(globals_["entry_motion_ms"],    "entry_motion_ms"),
        entry_depth_offset= ov.resolve(globals_["entry_depth_offset"], "entry_depth_offset"),
        emerge_test_mode  = globals_.get("emerge_test_mode",  False),
        emerge_test_scale = globals_.get("emerge_test_scale", 3.0),
        raw_ass_tags      = ov.raw_ass_tags or "",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Emergence tags
# ─────────────────────────────────────────────────────────────────────────────

def build_emergence_tags(
    left_x, right_x, y, depth, eye_w,
    start_ms, end_ms, left_clip, right_clip,
    emerge_in_ms=100, emerge_out_ms=100,
    start_opacity=40, entry_motion_ms=200, entry_depth_offset=1,
    emerge_test_mode=False, emerge_test_scale=3.0,
) -> tuple:
    cue_ms   = max(1, end_ms - start_ms)
    max_t    = max(0, cue_ms // 3)
    fade_in  = min(max(0, emerge_in_ms),    max_t)
    fade_out = min(max(0, emerge_out_ms),   max_t)
    motion   = min(max(0, entry_motion_ms), max_t if max_t > 0 else entry_motion_ms)
    alpha_val = int(round(255 * (100 - max(0, min(100, start_opacity))) / 100.0))
    alpha_tag = f"\\1a&H{alpha_val:02X}&"

    def toward(d, off):
        if off <= 0: return d
        if d > 0:    return max(0, d - off)
        if d < 0:    return min(0, d + off)
        return 0

    base_start = toward(depth, entry_depth_offset)
    if emerge_test_mode:
        delta = depth - base_start
        if delta == 0 and depth != 0: delta = 1 if depth > 0 else -1
        start_depth = depth - int(round(delta * max(1.0, emerge_test_scale)))
    else:
        start_depth = base_start

    lx0 = (eye_w // 2) - start_depth
    rx0 = eye_w + (eye_w // 2) + start_depth
    at  = f"\\t(0,{fade_in},\\1a&H00&)" if (fade_in > 0 and alpha_val > 0) else ""
    lm  = (f"\\move({lx0},{y},{left_x},{y},0,{motion})" if motion > 0
           else f"\\pos({left_x},{y})")
    rm  = (f"\\move({rx0},{y},{right_x},{y},0,{motion})" if motion > 0
           else f"\\pos({right_x},{y})")
    lt  = f"{{\\an2\\fad(0,{fade_out}){alpha_tag}{lm}{at}\\clip({left_clip})}}"
    rt  = f"{{\\an2\\fad(0,{fade_out}){alpha_tag}{rm}{at}\\clip({right_clip})}}"
    return lt, rt


# ─────────────────────────────────────────────────────────────────────────────
# ASS header & conversion
# ─────────────────────────────────────────────────────────────────────────────

def build_ass_header(
    playresx, playresy, font, fontsize,
    primary, outline_color, back_color,
    outline, shadow, alignment,
    margin_l, margin_r, margin_v,
) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nScaledBorderAndShadow: yes\nWrapStyle: 0\n"
        f"PlayResX: {playresx}\nPlayResY: {playresy}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{fontsize},{primary},&H000000FF,"
        f"{outline_color},{back_color},0,0,0,0,100,100,0,0,1,"
        f"{outline},{shadow},{alignment},{margin_l},{margin_r},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )


def convert_to_stereo_ass(
    srt_path: Path, out_path: Path,
    project: Project, globals_: dict,
    progress_cb: Optional[Callable] = None,
) -> int:
    events = parse_srt(srt_path)
    events = deoverlap_events(events)
    eye_w  = globals_["eye_w"]
    h      = globals_["h"]
    full_w = eye_w * 2
    lclip  = f"0,0,{eye_w},{h}"
    rclip  = f"{eye_w},0,{full_w},{h}"

    lines = [build_ass_header(
        full_w, h,
        globals_["font"], globals_["font_size"],
        globals_["primary_colour"], globals_["outline_colour"],
        globals_["back_colour"], globals_["outline"], globals_["shadow"],
        globals_["alignment"], globals_["margin_l"],
        globals_["margin_r"], globals_["margin_v"],
    )]

    total = len(events)
    for i, ev in enumerate(events):
        if progress_cb:
            vis = strip_markup_for_preview(ev["text"])[0].replace("\n", " ")
            progress_cb(i, total, vis[:60])

        p = effective_params(ev, globals_, project)

        style_tags = ""
        if p["primary_colour"] != globals_["primary_colour"]:
            c = p["primary_colour"].upper().strip()
            if not c.startswith("&H"): c = "&H" + c
            style_tags += f"\\c{c}"
        if p["outline_colour"] != globals_["outline_colour"]:
            style_tags += f"\\3c{p['outline_colour']}"
        if p["back_colour"] != globals_["back_colour"]:
            style_tags += f"\\4c{p['back_colour']}"
        if p["font"] != globals_["font"]:
            style_tags += f"\\fn{p['font']}"
        if p["font_size"] != globals_["font_size"]:
            style_tags += f"\\fs{p['font_size']}"
        if p["bold"] is True:   style_tags += "\\b1"
        elif p["bold"] is False: style_tags += "\\b0"
        if p["outline"] != globals_["outline"]:
            style_tags += f"\\bord{p['outline']}"
        if p["shadow"] != globals_["shadow"]:
            style_tags += f"\\shad{p['shadow']}"
        if p["raw_ass_tags"]:
            raw = p["raw_ass_tags"].strip()
            if raw.startswith("{") and raw.endswith("}"): raw = raw[1:-1]
            style_tags += raw

        style_block = ("{" + style_tags + "}") if style_tags else ""

        depth    = p["depth"]
        x_centre = p["x_centre"]
        y_centre = p["y_centre"]
        left_x   = x_centre - depth
        right_x  = eye_w + x_centre + depth

        start_ass = srt_time_to_ass(ev["start"])
        end_ass   = srt_time_to_ass(ev["end"])
        start_ms  = srt_time_to_ms(ev["start"])
        end_ms    = srt_time_to_ms(ev["end"])

        lt, rt = build_emergence_tags(
            left_x, right_x, y_centre, depth, eye_w,
            start_ms, end_ms, lclip, rclip,
            p["emerge_in_ms"], p["emerge_out_ms"],
            p["start_opacity"], p["entry_motion_ms"],
            p["entry_depth_offset"], p["emerge_test_mode"], p["emerge_test_scale"],
        )

        body = style_block + p["ass_text"]
        lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{lt}{body}\n")
        lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{rt}{body}\n")

    out_path.write_text("".join(lines), encoding="utf-8-sig", newline="\n")
    if progress_cb:
        progress_cb(total, total, "done")
    return len(events)


# ─────────────────────────────────────────────────────────────────────────────
# ASS editor: parse our own format back + targeted line rewrite
# ─────────────────────────────────────────────────────────────────────────────



def ass_time_to_ms(t: str) -> int:
    """Parse ASS timestamp H:MM:SS.cc → milliseconds."""
    t = t.strip()
    h, m, rest = t.split(':')
    s, cs = rest.split('.')
    return int(h)*3_600_000 + int(m)*60_000 + int(s)*1_000 + int(cs)*10


def _final_pos(text: str):
    """Return (x, y) final position from \\move or \\pos tag, or None."""
    m = RegexPatterns.MOVE_TAG.search(text)
    if m:
        return int(float(m.group(3))), int(float(m.group(4)))
    m = RegexPatterns.POS_TAG.search(text)
    if m:
        return int(float(m.group(1))), int(float(m.group(2)))
    return None


def parse_nf3d_ass(path, eye_w: int) -> tuple:
    """
    Parse an ASS file produced by NF3D back into cue dicts plus raw lines.

    Returns (cues, raw_lines) where raw_lines is the list of text lines
    (with line endings) suitable for targeted in-place rewriting.

    Each cue dict contains:
        index         – 1-based cue number
        start_ass     – ASS-format start time string
        end_ass       – ASS-format end time string
        start_ms      – start time in milliseconds
        end_ms        – end time in milliseconds
        text          – plain subtitle text (tags stripped)
        depth         – horizontal parallax in pixels (reconstructed)
        x_centre      – horizontal centre relative to left eye (pixels)
        y_centre      – vertical position (pixels)
        was_edited    – True if Name field was "edited" in the file
        left_line_no  – index into raw_lines for the left-eye Dialogue
        right_line_no – index into raw_lines for the right-eye Dialogue
        left_fields   – parsed field dict for the left line (for rebuilding)
    """
    raw = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    raw_lines = raw.splitlines(keepends=True)

    entries = []
    for i, line in enumerate(raw_lines):
        m = RegexPatterns.DIALOGUE_LINE.match(line.rstrip('\r\n'))
        if not m:
            continue
        entries.append(dict(
            line_no = i,
            start   = m.group(2).strip(),
            end     = m.group(3).strip(),
            style   = m.group(4).strip(),
            name    = m.group(5).strip(),
            mL      = m.group(6), mR = m.group(7), mV = m.group(8),
            effect  = m.group(9),
            text    = m.group(10),
        ))

    cues = []
    i = 0
    cue_idx = 1
    while i + 1 < len(entries):
        e1, e2 = entries[i], entries[i + 1]
        if e1['start'] != e2['start'] or e1['end'] != e2['end']:
            i += 1
            continue

        # Identify left/right by clip — left clip x2 == eye_w
        c1 = RegexPatterns.CLIP_TAG.search(e1['text'])
        c2 = RegexPatterns.CLIP_TAG.search(e2['text'])
        try:
            c1_x2 = int(c1.group(1).split(',')[2]) if c1 else 0
        except (IndexError, ValueError):
            c1_x2 = 0

        left, right = (e1, e2) if c1_x2 == eye_w else (e2, e1)

        pos_l = _final_pos(left['text'])
        pos_r = _final_pos(right['text'])

        if pos_l and pos_r:
            lx, ly = pos_l
            rx, _  = pos_r
            # lx = x_centre - depth
            # rx = eye_w + x_centre + depth
            # → x_centre = (lx + rx - eye_w) / 2
            x_centre = (lx + rx - eye_w) // 2
            depth    = x_centre - lx
            y_centre = ly
        else:
            x_centre = eye_w // 2
            depth    = 0
            y_centre = int(eye_w * 1080 / 960 * 0.88)  # rough fallback

        # Strip tag blocks to recover plain text
        plain = re.sub(r'\{[^}]*\}', '', left['text']).strip()

        was_ed = (left['name'].strip().lower() == 'edited')
        # Extract any inline style overrides from the text so the edit
        # panel can be pre-populated when revisiting edited cues.
        style_so = extract_style_overrides_from_text(left['text']) if was_ed else {}

        cues.append(dict(
            index         = cue_idx,
            start_ass     = left['start'],
            end_ass       = left['end'],
            start_ms      = ass_time_to_ms(left['start']),
            end_ms        = ass_time_to_ms(left['end']),
            text          = plain,
            depth         = depth,
            x_centre      = x_centre,
            y_centre      = y_centre,
            was_edited    = was_ed,
            style_overrides = style_so,   # populated for edited cues on reload
            left_line_no  = left['line_no'],
            right_line_no = right['line_no'],
            left_fields   = left,
        ))
        cue_idx += 1
        i += 2

    return cues, raw_lines


def rebuild_ass_cue_lines(
    cue: dict,
    depth: int,
    x_centre: int,
    y_centre: int,
    eye_w: int,
    h: int,
    globals_: dict,
    style_overrides: dict = None,
    note: str = "",
) -> tuple:
    """
    Rebuild the two Dialogue line strings for a cue with new parameters.
    Returns (left_line, right_line) — each a complete Dialogue: string
    ready to write back into the ASS file, with a trailing newline.

    Emergence parameters are taken from globals_ so they stay consistent
    with the rest of the file.  Style overrides are applied as inline tags
    on top of whatever the ASS header already specifies.
    """
    lx = x_centre - depth
    rx = eye_w + x_centre + depth
    lclip = f"0,0,{eye_w},{h}"
    rclip = f"{eye_w},0,{eye_w * 2},{h}"

    lt, rt = build_emergence_tags(
        lx, rx, y_centre, depth, eye_w,
        cue['start_ms'], cue['end_ms'],
        lclip, rclip,
        globals_.get('emerge_in_ms', 100),
        globals_.get('emerge_out_ms', 100),
        globals_.get('start_opacity', 40),
        globals_.get('entry_motion_ms', 200),
        globals_.get('entry_depth_offset', 1),
        globals_.get('emerge_test_mode', False),
        globals_.get('emerge_test_scale', 3.0),
    )

    # Build inline style-override tag block
    style_tags = ""
    so = style_overrides or {}
    if 'primary_colour' in so:
        c = so['primary_colour'].upper().strip()
        if not c.startswith('&H'): c = '&H' + c
        style_tags += f"\\c{c}"
    if 'outline_colour' in so: style_tags += f"\\3c{so['outline_colour']}"
    if 'back_colour'    in so: style_tags += f"\\4c{so['back_colour']}"
    if 'font'      in so: style_tags += f"\\fn{so['font']}"
    if 'font_size' in so: style_tags += f"\\fs{so['font_size']}"
    if so.get('bold') is True:  style_tags += "\\b1"
    if so.get('bold') is False: style_tags += "\\b0"
    if 'outline' in so: style_tags += f"\\bord{so['outline']}"
    if 'shadow'  in so: style_tags += f"\\shad{so['shadow']}"
    if 'raw_ass_tags' in so and so['raw_ass_tags']:
        raw = so['raw_ass_tags'].strip().strip('{}')
        style_tags += raw

    style_block = ("{" + style_tags + "}") if style_tags else ""
    body = style_block + cue['text']
    f    = cue['left_fields']

    def make(tag_block):
        return (
            f"Dialogue: 0,{cue['start_ass']},{cue['end_ass']},"
            f"{f['style']},edited,{f['mL']},{f['mR']},{f['mV']},"
            f"{f['effect']},{tag_block}{body}\n"
        )

    return make(lt), make(rt)


def extract_style_overrides_from_text(text: str) -> dict:
    """
    Extract any inline style override tags from an ASS text field and return
    them as a dict using the same keys as rebuild_ass_cue_lines/collect_style_overrides.
    Used when loading a previously edited ASS to restore the override panel.
    Parses: c (primary colour), 3c (outline), 4c (back), fn (font),
            fs (font size), bord (outline width), shad (shadow), b0/b1 (bold).
    """
    so = {}
    # Find all {...} blocks
    blocks = re.findall(r'\{([^}]*)\}', text)
    for block in blocks:
        m = re.search(r'\\c(&H[0-9A-Fa-f]+)', block)
        if m: so['primary_colour'] = m.group(1)
        m = re.search(r'\\3c(&H[0-9A-Fa-f]+)', block)
        if m: so['outline_colour'] = m.group(1)
        m = re.search(r'\\4c(&H[0-9A-Fa-f]+)', block)
        if m: so['back_colour'] = m.group(1)
        m = re.search(r'\\fn([^\\]+)', block)
        if m: so['font'] = m.group(1).strip()
        m = re.search(r'\\fs(\d+)', block)
        if m: so['font_size'] = int(m.group(1))
        m = re.search(r'\\bord([0-9.]+)', block)
        if m: so['outline'] = float(m.group(1))
        m = re.search(r'\\shad([0-9.]+)', block)
        if m: so['shadow'] = float(m.group(1))
        if '\\b1' in block: so['bold'] = True
        if '\\b0' in block: so['bold'] = False
    return so


def save_nf3d_ass(raw_lines: list, path) -> None:
    """Write raw_lines back to the ASS file (utf-8-sig, unix line endings)."""
    Path(path).write_text(''.join(raw_lines), encoding='utf-8-sig', newline='\n')
