#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nf3d_gui.py — NF3D GUI (v3)
4-tab layout: Convert · Style · Edit cues · Advanced
FontPickerWidget with rendered previews, colour swatches, improved OCR reviewer,
versioned project saves, accurate ffmpeg-composite preview button.
"""
from __future__ import annotations

import ctypes
import functools
import json
import os
import subprocess
import sys

def _popen_kwargs():
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, ttk

from nf3d_core import (
    CueOverride, Project, TextIssue,
    analyse_cue_depths, rescan_fallback_cues, convert_to_stereo_ass, deoverlap_events,
    detect_ffmpeg, effective_params, get_video_info,
    load_persistent_dictionary, save_persistent_dictionary,
    ms_to_srt_time, parse_srt, scan_text_issues, spellchecker_available,
    strip_markup_for_preview, srt_time_to_ffmpeg, srt_time_to_ms,
    # ASS editor
    parse_nf3d_ass, rebuild_ass_cue_lines, save_nf3d_ass,
    extract_style_overrides_from_text,
)

from PIL import Image, ImageDraw, ImageFont, ImageTk
from logging_config import get_logger
from config import PROJECT_VERSION, ASS_NL, RegexPatterns

# ─────────────────────────────────────────────────────────────────────────────
# Window icon — NF3D logo embedded as base64 ICO
# ─────────────────────────────────────────────────────────────────────────────

_NF3D_ICON_B64 = """AAABAAEAEBAAAAAAIABlAgAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAAEAgGAAAAH/P/YQAAAixJREFUeJy1k01IVFEUx3/nvueMOVqj0YcTQ2pYBBaYm4JABtq1cApGaJMEbUJq4SLaDbNtEdGmCAkCIdKFbYp2VpCLyFxlVGhQQvZhjo3O13vvnhaOMdZEEPTf3Hvu4Zwf5+MKNaTgABZgih63h0Zdu69IjilNgF8rbj3Y/NFZQ/IrWSD44OxJxIPZR2+Id+yW8JksQcSCRDCSE53dZeeuKYiAutVkgWDedAxssTK8IB13imKfl6097hm9ahHHgGPRAoABrZw/yXbeaU/Vq1z+qnJQoFyvcumzo0/CmNYopnUBzcXtuxEF0Qq4Um+vADiBJD3V4Xbevtqhc2cNMrnJSnces+zDd3Xrggl63aqq2WAIWiqL62BVcBzNY+5FrD8Y07nrAJQBXgMq6+3bkMBHPsV1JaHSdAsiDTlyxRfb9i3WnX/80Fn1JCjmrZddPMVtyaIqiGjVFFSGt/Y13j2cPJmLbG6Leyttnlfsvn/k2APrhnuDpeUBY+uS6kpCrX+a8P4lMlQlSKshI5YLMydoiHYRadhLKCiZb1/eq+cf0itdSdITrgl3fjSlQp+f6ZwkrUbW6SBqzk1flGhzVNxCXlq278QrHPBzq1M0NqcI+eOE6o9KqXjDDsVukhp1GOsPKj1Ym4p13KeOSIu6IdRxp4Ps5BCNsbDbHB/XcqFJQnbEH4w9q9Qf/HVNayqtG1ZdfnPOjK29pVLQv/ahGMXAGLxMKRmx/0b+X/oBf9nnQpFhegAAAAAASUVORK5CYII="""

logger = get_logger(__name__)


def _set_window_icon(root):
    """Set the NF3D logo as the window icon (title bar + taskbar)."""
    try:
        ico = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent) / "nf3d_icon.ico"
        if ico.is_file():
            root.iconbitmap(str(ico))
            return
        # Fallback: embedded base64 icon from original build
        import base64 as _b64, tempfile as _tmp
        ico_data = _b64.b64decode(_NF3D_ICON_B64)
        ico_path = _tmp.mktemp(suffix=".ico")
        with open(ico_path, "wb") as _f:
            _f.write(ico_data)
        root.iconbitmap(ico_path)
    except OSError:
        logger.warning("_set_window_icon: could not set window icon, using default")


# ─────────────────────────────────────────────────────────────────────────────
# Paths & defaults
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
SCRIPT_DIR  = BASE_DIR
FONTS_DIR   = BASE_DIR / "fonts"
CONFIG_PATH = Path.home() / "nf3d_config.json"

# Curated font lists — only installed fonts appear; order is preserved.
DEFAULT_RECOMMENDED_FONTS = [
    "Arial", "Trebuchet MS", "Verdana", "Calibri", "Tahoma",
    "Segoe UI", "Franklin Gothic Medium", "Gill Sans MT", "Georgia",
    "Helvetica", "Open Sans",
]
DEFAULT_SPECIALIST_FONTS = [
    "Impact", "Agency FB", "Rockwell Extra Bold", "Black Han Sans",
    "Bebas Neue", "Algerian", "Curlz MT", "Papyrus", "Playbill",
    "Showcard Gothic", "Duluthia",
]

DEPTH_DEFAULTS = dict(
    samples_per_cue=6, offset_internal=8, internal_limit=100,
    out_min=-9, out_max=2, output_bias=-3, output_scale=2.3,
    base_depth=6, caps_depth=8, italics_depth=4,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): logger.warning("load_config: could not read config, using defaults")
    return {}

def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# Duluthia font registration (session-only, no admin rights needed)
# ─────────────────────────────────────────────────────────────────────────────

def register_bundled_fonts() -> None:
    """
    Temporarily register any .ttf/.otf files in the fonts/ directory so they
    are available to both PIL and the system font picker for this session.
    Uses AddFontResourceEx on Windows; silently skips on other platforms.
    """
    if not FONTS_DIR.exists():
        return
    try:
        gdi32 = ctypes.WinDLL("gdi32")
        FR_PRIVATE = 0x10
        for font_file in FONTS_DIR.glob("*.ttf"):
            gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, None)
        for font_file in FONTS_DIR.glob("*.otf"):
            gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, None)
    except OSError:
        logger.warning("register_bundled_fonts: GDI32 unavailable or non-Windows, PIL will load fonts directly")

# ─────────────────────────────────────────────────────────────────────────────
# Tool detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect(cands: list) -> str:
    for c in cands:
        try:
            if os.path.isabs(c):
                if os.path.isfile(c): return c
            else:
                p = subprocess.run([c, "-version"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **_popen_kwargs())
                if p.returncode == 0: return c
        except (OSError, subprocess.SubprocessError): pass
    return ""

def autodetect_tools() -> dict:
    if sys.platform == "win32":
        mkv_cands = [r"C:\Program Files\MKVToolNix\mkvmerge.exe",
                     r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe", "mkvmerge"]
        mkx_cands = [r"C:\Program Files\MKVToolNix\mkvextract.exe",
                     r"C:\Program Files (x86)\MKVToolNix\mkvextract.exe", "mkvextract"]
        se_cands  = [r"C:\Program Files\Subtitle Edit\SubtitleEdit.exe",
                     r"C:\Program Files (x86)\Subtitle Edit\SubtitleEdit.exe"]
    elif sys.platform == "darwin":
        mkv_cands = ["/usr/local/bin/mkvmerge", "/opt/homebrew/bin/mkvmerge", "mkvmerge"]
        mkx_cands = ["/usr/local/bin/mkvextract", "/opt/homebrew/bin/mkvextract", "mkvextract"]
        se_cands  = []   # SubtitleEdit not available on macOS
    else:  # Linux
        mkv_cands = ["/usr/bin/mkvmerge", "/usr/local/bin/mkvmerge", "mkvmerge"]
        mkx_cands = ["/usr/bin/mkvextract", "/usr/local/bin/mkvextract", "mkvextract"]
        se_cands  = ["/usr/bin/subtitleedit", "subtitleedit"]
    return {
        "mkvmerge":     _detect(mkv_cands),
        "mkvextract":   _detect(mkx_cands),
        "subtitleedit": _detect(se_cands),
        "ffmpeg":       detect_ffmpeg(),
    }

# ─────────────────────────────────────────────────────────────────────────────
# MKV helpers
# ─────────────────────────────────────────────────────────────────────────────

def mkv_list_sub_tracks(mkvmerge: str, mkv: str) -> list:
    p = subprocess.run([mkvmerge, "-J", mkv], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       **_popen_kwargs())
    if p.returncode != 0: raise RuntimeError(p.stdout + p.stderr)
    data = json.loads(p.stdout)
    tracks = []
    ffmpeg_idx = 0   # 0-based subtitle stream index for ffmpeg [0:s:N]
    for t in data.get("tracks", []):
        if t.get("type") != "subtitles": continue
        pr = t.get("properties", {}) or {}
        tracks.append({"id": t.get("id"),
                       "codec_id": t.get("codec_id","") or t.get("codec",""),
                       "lang": pr.get("language","") or "und",
                       "name": pr.get("track_name","") or "",
                       "default": bool(pr.get("default_track", False)),
                       "forced":  bool(pr.get("forced_track",  False)),
                       "ffmpeg_idx": ffmpeg_idx})
        ffmpeg_idx += 1
    return tracks

def format_track_label(tr: dict) -> str:
    flags = (["default"] if tr["default"] else []) + (["forced"] if tr["forced"] else [])
    return (f"ID {tr['id']}: {tr['codec_id']} [{tr['lang']}]"
            + (f" ({', '.join(flags)})" if flags else "")
            + (f" — {tr['name']}" if tr["name"] else ""))

# ─────────────────────────────────────────────────────────────────────────────
# PIL / drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def ass_to_rgb(c: str, fallback=(230, 230, 230)) -> tuple:
    try:
        s = c.strip().upper().lstrip("&H").zfill(8)
        return (int(s[6:8], 16), int(s[4:6], 16), int(s[2:4], 16))
    except (ValueError, AttributeError): return fallback

def rgb_to_ass(r: int, g: int, b: int) -> str:
    return f"&H00{b:02X}{g:02X}{r:02X}"

@functools.lru_cache(maxsize=256)
def find_font_file(name: str, italic: bool = False) -> str:
    """
    Locate a font file by name.  Checks FONTS_DIR first (for bundled fonts
    like Duluthia), then C:/Windows/Fonts.
    Uses several strategies so that names like "Trebuchet MS" → trebuc.ttf
    and "Franklin Gothic Medium" → framd.ttf are found reliably.
    Returns the file path as string, or empty string if not found.
    """
    name = name.strip()
    name_nospace = name.lower().replace(" ", "")

    # Bundled fonts take priority
    for ext in (".ttf", ".otf"):
        for candidate in FONTS_DIR.glob(f"*{ext}"):
            if candidate.stem.lower().replace(" ", "") == name_nospace:
                return str(candidate)

    if sys.platform == "win32":
        _font_dirs = [Path(r"C:\Windows\Fonts"),
                      Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        _font_dirs = [Path("/Library/Fonts"), Path.home() / "Library" / "Fonts",
                      Path("/System/Library/Fonts")]
    else:
        _font_dirs = [Path("/usr/share/fonts"), Path.home() / ".fonts",
                      Path.home() / ".local" / "share" / "fonts"]

    existing_dirs = [d for d in _font_dirs if d.exists()]
    if not existing_dirs:
        return ""

    # Build a list of candidate stems to try, in priority order
    stems = []
    name_l = name.lower()
    # 1. Exact name without spaces (e.g. "arial")
    stems.append(name_nospace)
    stems.append(name_nospace + "r")
    # 2. Italic variants
    if italic:
        stems.insert(0, name_nospace + "i")
        stems.insert(0, name_nospace + "bd")
    # 3. First word only (handles "Trebuchet MS" → "trebuchet", "Gill Sans MT" → "gill")
    first = name_l.split()[0] if " " in name_l else name_l
    stems.append(first)
    stems.append(first + "b" if not italic else first + "i")
    # 4. First word abbreviated (first 5 chars — handles "trebuchet" → "trebuc")
    stems.append(first[:6])
    stems.append(first[:5])

    for fontdir in existing_dirs:
        for stem in stems:
            for ext in (".ttf", ".otf"):
                p = fontdir / f"{stem}{ext}"
                if p.exists():
                    return str(p)

    # Last resort: scan all .ttf/.otf files in all dirs
    first_word = name_l.split()[0] if " " in name_l else name_l
    for fontdir in existing_dirs:
        for ext in ("*.ttf", "*.otf"):
            for f in fontdir.glob(ext):
                if f.stem.lower().startswith(first_word[:4]):
                    return str(f)

    return ""

def load_pil_font(name: str, size: int, italic: bool = False) -> ImageFont.FreeTypeFont:
    path = find_font_file(name, italic)
    if path:
        try: return ImageFont.truetype(path, size=size)
        except (OSError, IOError): logger.warning("load_pil_font: truetype(%s) failed, trying arial.ttf", path)
    try: return ImageFont.truetype("arial.ttf", size=size)
    except: return ImageFont.load_default()

def split_sbs(img: Image.Image) -> tuple:
    w, h = img.size; ew = w // 2
    return img.crop((0, 0, ew, h)), img.crop((ew, 0, ew*2, h))

def make_anaglyph(l: Image.Image, r: Image.Image) -> Image.Image:
    l = l.convert("RGB"); r = r.convert("RGB")
    lr, *_ = l.split(); _, rg, rb = r.split()
    return Image.merge("RGB", (lr, rg, rb))

def draw_subtitle(draw, x, y, text, font, fill, outl_fill, outl_px,
                  shad_px, shad_fill, shad_dx=None, shad_dy=None,
                  line_spacing=4, anchor="bottom"):
    """
    Render subtitle text using correct ASS layer order: shadow → outline → text.
    Uses PIL's native stroke_width for outline rendering — smoother and faster
    than the 8-direction stamp, and correctly contained within the text bounds.
    Shadow is drawn first (bottom layer), then outline+primary in one pass.
    """
    if shad_dx is None: shad_dx = shad_px
    if shad_dy is None: shad_dy = shad_px
    lines = (text or "").split("\n")
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (font.size, 0)
    lh = asc + desc
    bh = len(lines)*lh + max(0, len(lines)-1)*line_spacing
    y0 = (y - bh) if anchor == "bottom" else (y - bh/2)
    for i, line in enumerate(lines):
        bb = draw.textbbox((0,0), line, font=font, stroke_width=outl_px)
        lw = (bb[2] - bb[0])
        tx = x - lw/2 + outl_px   # textbbox with stroke includes stroke in width
        ty = y0 + i*(lh + line_spacing)
        # Layer 1: Shadow (bottommost — drawn before everything else)
        if shad_px > 0 or shad_dx or shad_dy:
            draw.text((tx + shad_dx, ty + shad_dy), line, font=font,
                      fill=shad_fill, stroke_width=outl_px, stroke_fill=shad_fill)
        # Layer 2+3: Outline and primary text in one PIL pass (correct layering,
        # no gaps at corners, no double-drawing artefacts)
        draw.text((tx, ty), line, font=font, fill=fill,
                  stroke_width=outl_px, stroke_fill=outl_fill)

def render_stereo_preview(left_eye, right_eye, params, canvas_w, canvas_h, bg_dim=0):
    """
    Render a stereo anaglyph preview at full video resolution then thumbnail.
    Coordinates and font sizes are used as-is (video pixel space).
    The thumbnail preserves all proportions uniformly — no separate scaling.
    """
    left  = left_eye.copy()
    right = right_eye.copy()
    if bg_dim > 0:
        ov = Image.new("RGB", left.size, (0,0,0))
        left  = Image.blend(left,  ov, max(0.0, min(1.0, bg_dim/100.0)))
        right = Image.blend(right, ov, max(0.0, min(1.0, bg_dim/100.0)))

    font      = load_pil_font(params["font"], max(8, int(params["font_size"])),
                              params.get("had_italics", False))
    fill      = ass_to_rgb(params["primary_colour"])
    outl_fill = ass_to_rgb(params["outline_colour"], (0,0,0))
    shad_fill = ass_to_rgb(params["back_colour"],    (40,40,40))
    outl_px   = max(0, int(float(params["outline"])))
    shad_px   = max(0, int(float(params["shadow"])))
    shad_dx   = max(0, int(float(params.get("shadow_x", params["shadow"]))))
    shad_dy   = max(0, int(float(params.get("shadow_y", params["shadow"]))))

    x     = params["x_centre"]
    y     = params["y_centre"]
    depth = params["depth"]
    text  = strip_markup_for_preview(params.get("raw_text", ""))[0] or "NF3D preview"

    for img, d_sign in ((left, -1), (right, 1)):
        draw_subtitle(ImageDraw.Draw(img), x + d_sign*depth, y, text,
                      font, fill, outl_fill, outl_px, shad_px, shad_fill,
                      shad_dx, shad_dy, anchor="bottom")

    ana = make_anaglyph(left, right)
    fit = min(canvas_w/ana.width, canvas_h/ana.height)
    nw  = max(1, int(ana.width*fit)); nh = max(1, int(ana.height*fit))
    fitted = ana.resize((nw, nh), Image.LANCZOS)
    stage  = Image.new("RGB", (canvas_w, canvas_h), (17,17,17))
    stage.paste(fitted, ((canvas_w-nw)//2, (canvas_h-nh)//2))
    return stage

def render_subtitle_zone_preview(left_eye, right_eye, params,
                                  canvas_w, canvas_h, bg_dim=0, zone_pct=0.30):
    """
    Crop to the bottom zone_pct of the frame (where subtitles live) before
    fitting to canvas.  Gives much larger, more useful text in the preview.
    """
    left  = left_eye.copy()
    right = right_eye.copy()
    if bg_dim > 0:
        ov = Image.new("RGB", left.size, (0,0,0))
        left  = Image.blend(left,  ov, max(0.0, min(1.0, bg_dim/100.0)))
        right = Image.blend(right, ov, max(0.0, min(1.0, bg_dim/100.0)))

    font      = load_pil_font(params["font"], max(8, int(params["font_size"])),
                              params.get("had_italics", False))
    fill      = ass_to_rgb(params["primary_colour"])
    outl_fill = ass_to_rgb(params["outline_colour"], (0,0,0))
    shad_fill = ass_to_rgb(params["back_colour"],    (40,40,40))
    outl_px   = max(0, int(float(params["outline"])))
    shad_px   = max(0, int(float(params["shadow"])))
    shad_dx   = max(0, int(float(params.get("shadow_x", params["shadow"]))))
    shad_dy   = max(0, int(float(params.get("shadow_y", params["shadow"]))))

    x = params["x_centre"]; y = params["y_centre"]; depth = params["depth"]
    text = strip_markup_for_preview(params.get("raw_text", ""))[0] or "NF3D preview"

    for img, d_sign in ((left, -1), (right, 1)):
        draw_subtitle(ImageDraw.Draw(img), x + d_sign*depth, y, text,
                      font, fill, outl_fill, outl_px, shad_px, shad_fill,
                      shad_dx, shad_dy, anchor="bottom")

    # Crop to subtitle zone of each eye then re-merge
    ew, eh = left.size
    crop_y = int(eh * (1.0 - zone_pct))
    left_crop  = left.crop((0, crop_y, ew, eh))
    right_crop = right.crop((0, crop_y, ew, eh))
    ana = make_anaglyph(left_crop, right_crop)

    fit = min(canvas_w/ana.width, canvas_h/ana.height)
    nw  = max(1, int(ana.width*fit)); nh = max(1, int(ana.height*fit))
    fitted = ana.resize((nw, nh), Image.LANCZOS)
    stage  = Image.new("RGB", (canvas_w, canvas_h), (17,17,17))
    stage.paste(fitted, ((canvas_w-nw)//2, (canvas_h-nh)//2))
    return stage

# ─────────────────────────────────────────────────────────────────────────────
# Colour swatch helper
# ─────────────────────────────────────────────────────────────────────────────

def make_swatch(canvas: tk.Canvas, ass_colour: str) -> None:
    """Update a 24×24 Canvas to show the colour represented by an ASS hex string."""
    try:
        r, g, b = ass_to_rgb(ass_colour)
        canvas.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
    except (ValueError, tk.TclError):
        canvas.configure(bg="#888888")

# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# FontPickerWidget
# A simple, reliable font selector using ttk.Combobox.
# The style sample canvas on the Style tab already shows a live visual preview
# of the selected font, so a rendered dropdown would be redundant.
# Tkinter's Listbox does not support per-item images; the previous approach
# using itemconfig({"image": ...}) crashed on all platforms.
# ─────────────────────────────────────────────────────────────────────────────

class FontPickerWidget(ttk.Frame):
    """
    Reliable font picker using a plain ttk.Combobox.
    Recommended fonts appear first, then a separator entry, then specialist.
    Only fonts that are actually installed (or bundled) are shown.
    The style sample on the Style tab provides the visual preview.
    """
    _SEP = "── Specialist ──"

    def __init__(self, parent, recommended: list, specialist: list,
                 initial: str = "Arial", on_change=None, **kw):
        super().__init__(parent, **kw)
        self._on_change = on_change
        self._var       = tk.StringVar(value=initial)

        self._cmb = ttk.Combobox(self, textvariable=self._var,
                                  state="readonly", width=24)
        self._cmb.pack(side="left", fill="x", expand=True)
        self._cmb.bind("<<ComboboxSelected>>", self._on_select)

        self._build_values(recommended, specialist)

    def _available(self, name: str) -> bool:
        return bool(find_font_file(name))

    def _build_values(self, recommended: list, specialist: list) -> None:
        # Show all curated fonts regardless of whether find_font_file locates
        # them — the user may have added a font name that uses a non-standard
        # file stem. PIL falls back to Arial if a font can't be loaded at
        # render time, so showing unresolvable names is harmless.
        values = list(recommended)
        spec   = list(specialist)
        if spec:
            values += [self._SEP] + spec
        self._cmb["values"] = values
        current = self._var.get()
        if current not in values or current == self._SEP:
            self._var.set(values[0] if values else "")

    def _on_select(self, _=None):
        val = self._var.get()
        if val == self._SEP:
            # Separator selected — revert to previous valid font
            vals = list(self._cmb["values"])
            try:
                idx = vals.index(self._SEP)
                self._var.set(vals[idx - 1] if idx > 0 else "")
            except ValueError:
                pass
            return
        if self._on_change:
            self._on_change(val)

    def get(self) -> str:
        return self._var.get()

    def set(self, name: str) -> None:
        vals = list(self._cmb["values"])
        if name in vals:
            self._var.set(name)

    def configure_lists(self, recommended: list, specialist: list) -> None:
        """Rebuild the picker with new font lists (called from Advanced tab)."""
        current = self._var.get()
        self._build_values(recommended, specialist)
        # Re-apply previous selection if still available
        if current and current in self._cmb["values"]:
            self._var.set(current)


# ─────────────────────────────────────────────────────────────────────────────
# Custom colour picker dialog
# Replaces colorchooser.askcolor which does not persist custom colours reliably
# on Windows across sessions (they live in a per-process memory slot).
# ─────────────────────────────────────────────────────────────────────────────

# ─── Persistent colour palette ───────────────────────────────────────────────
_MAX_RECENT = 30
_RECENT_COLOURS: list = []   # loaded at startup, saved on each OK

_PALETTE_FILENAME = "nf3d_colours.json"


def _vorhees_subdir(sub: str) -> Path:
    """
    Return (and create) a subfolder inside workspace/Vorhees/<sub>.
    Vorhees is the tidy JSON home; subfolders separate file types.
    Falls back to home dir if workspace not configured.
    """
    try:
        cfg = load_config()
        ws  = cfg.get("workspace", "")
        if ws:
            p = Path(ws) / "Vorhees" / sub
            p.mkdir(parents=True, exist_ok=True)
            return p
    except OSError:
        logger.warning("_vorhees_subdir: workspace path error, falling back to home dir")
    fb = Path.home() / "NF3D" / "Vorhees" / sub
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _palette_path() -> Path:
    """Return the palette file inside workspace/Vorhees/colours/."""
    return _vorhees_subdir("colours") / _PALETTE_FILENAME


def load_colour_palette() -> list:
    """Load saved colours from disk; return empty list on any failure."""
    p = _palette_path()
    try:
        if p.exists():
            import json as _j
            data = _j.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(c) for c in data][:_MAX_RECENT]
    except (OSError, json.JSONDecodeError):
        logger.warning("load_colour_palette: could not read palette file")
    return []


def save_colour_palette(colours: list) -> None:
    """Write colours to disk; silently ignore failures."""
    try:
        import json as _j
        p = _palette_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_j.dumps(colours, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("save_colour_palette: could not write palette file")


class ColourPickerDialog(tk.Toplevel):
    """
    A simple, reliable colour picker:
      • RGB/Hex entry fields with live preview swatch
      • Grid of recently used colours — shared across all pickers, persists
        for the duration of the session
      • Falls back to system colorchooser for the full colour wheel
    The chosen colour is written directly to `ass_var` (a tk.StringVar holding
    an ASS &HBBGGRR hex string) and the session recent list is updated.
    """
    def __init__(self, parent, ass_var: tk.StringVar,
                 title="Pick colour", on_close=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._var     = ass_var
        self._on_close = on_close
        self._closed  = False

        # Parse initial colour
        try:
            self._r, self._g, self._b = ass_to_rgb(ass_var.get())
        except (ValueError, AttributeError):
            self._r, self._g, self._b = 230, 230, 230

        self._build()
        self._update_from_rgb()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        p = ttk.Frame(self, padding=12); p.pack(fill="both", expand=True)

        # ── Live preview swatch ───────────────────────────────────────────────
        self._swatch = tk.Canvas(p, width=120, height=40,
                                 highlightthickness=1, highlightbackground="#888")
        self._swatch.grid(row=0, column=0, columnspan=4, sticky="we",
                          pady=(0,10))

        # ── RGB sliders + entries ─────────────────────────────────────────────
        self._rv = tk.IntVar(value=self._r)
        self._gv = tk.IntVar(value=self._g)
        self._bv = tk.IntVar(value=self._b)

        for row_i, (label, var, col) in enumerate([
                ("R", self._rv, "#cc3333"),
                ("G", self._gv, "#33aa33"),
                ("B", self._bv, "#3366cc")], 1):
            ttk.Label(p, text=label, foreground=col, font=("",9,"bold")).grid(
                row=row_i, column=0, sticky="w", padx=(0,4))
            ttk.Scale(p, from_=0, to=255, variable=var, orient="horizontal",
                      length=160,
                      command=lambda *_, v=var: self._on_slider(v)).grid(
                row=row_i, column=1, sticky="we")
            e = ttk.Spinbox(p, from_=0, to=255, textvariable=var, width=5,
                            command=self._update_from_rgb_vars)
            e.grid(row=row_i, column=2, padx=(6,0))
            e.bind("<Return>", lambda _: self._update_from_rgb_vars())
            e.bind("<FocusOut>", lambda _: self._update_from_rgb_vars())

        # ── Hex entry ────────────────────────────────────────────────────────
        ttk.Label(p, text="Hex #").grid(row=4, column=0, sticky="w", padx=(0,4))
        self._hex_var = tk.StringVar()
        hex_e = ttk.Entry(p, textvariable=self._hex_var, width=8)
        hex_e.grid(row=4, column=1, sticky="w", pady=(8,0))
        hex_e.bind("<Return>",   lambda _: self._on_hex_entry())
        hex_e.bind("<FocusOut>", lambda _: self._on_hex_entry())

        # ── Full colour wheel (system dialog) ─────────────────────────────────
        ttk.Button(p, text="Full colour wheel…",
                   command=self._open_system_picker).grid(
            row=4, column=2, padx=(8,0), pady=(8,0))

        # ── Saved palette ─────────────────────────────────────────────────────
        pal_lf = ttk.LabelFrame(p,
            text="Colour palette  (click=use · right-click=remove · saved to workspace)")
        pal_lf.grid(row=5, column=0, columnspan=4, sticky="we", pady=(10,4))
        self._pal_frame = tk.Frame(pal_lf)
        self._pal_frame.pack(fill="x", padx=4, pady=4)
        ttk.Button(pal_lf, text="Add current colour to palette",
                   command=self._save_to_palette).pack(side="left", padx=4, pady=(0,6))
        self._rebuild_palette_swatches()

        # ── OK / Cancel ───────────────────────────────────────────────────────
        btn = ttk.Frame(p); btn.grid(row=7, column=0, columnspan=4,
                                     sticky="e", pady=(12,0))
        ttk.Button(btn, text="OK",     command=self._ok).pack(side="left", padx=(0,6))
        ttk.Button(btn, text="Cancel", command=self._close).pack(side="left")

    def _on_slider(self, var):
        self._update_from_rgb_vars()

    def _update_from_rgb_vars(self):
        try:
            self._r = max(0, min(255, int(self._rv.get())))
            self._g = max(0, min(255, int(self._gv.get())))
            self._b = max(0, min(255, int(self._bv.get())))
        except (ValueError, tk.TclError):
            return
        self._update_from_rgb()

    def _update_from_rgb(self):
        self._rv.set(self._r); self._gv.set(self._g); self._bv.set(self._b)
        hex_col = f"#{self._r:02x}{self._g:02x}{self._b:02x}"
        self._hex_var.set(hex_col[1:].upper())
        try: self._swatch.configure(bg=hex_col)
        except tk.TclError: logger.warning("_update_from_rgb: could not set swatch colour %s", hex_col)

    def _on_hex_entry(self):
        raw = self._hex_var.get().strip().lstrip("#")
        if len(raw) == 6:
            try:
                self._r = int(raw[0:2], 16)
                self._g = int(raw[2:4], 16)
                self._b = int(raw[4:6], 16)
                self._update_from_rgb()
            except ValueError:
                pass

    def _open_system_picker(self):
        init = f"#{self._r:02x}{self._g:02x}{self._b:02x}"
        rgb, _ = colorchooser.askcolor(color=init, parent=self)
        if rgb:
            self._r, self._g, self._b = int(rgb[0]), int(rgb[1]), int(rgb[2])
            self._update_from_rgb()

    def _apply_ass(self, ass_col: str):
        try:
            self._r, self._g, self._b = ass_to_rgb(ass_col)
            self._update_from_rgb()
        except (ValueError, AttributeError):
            logger.warning("_apply_ass: could not parse ASS colour %s", ass_col)

    def _rebuild_palette_swatches(self):
        """Rebuild the swatch grid from the current _RECENT_COLOURS list."""
        for w in self._pal_frame.winfo_children():
            w.destroy()
        cols = 12
        for i, ass_col in enumerate(_RECENT_COLOURS):
            try:
                rr, gg, bb = ass_to_rgb(ass_col)
                hex_col = f"#{rr:02x}{gg:02x}{bb:02x}"
                label   = f"#{rr:02X}{gg:02X}{bb:02X}"
            except (ValueError, AttributeError):
                hex_col = "#888888"; label = "?"
            cv = tk.Canvas(self._pal_frame, width=24, height=24,
                           bg=hex_col, cursor="hand2",
                           highlightthickness=1, highlightbackground="#555")
            cv.grid(row=i // cols, column=i % cols, padx=1, pady=1)
            # Left-click: apply colour
            cv.bind("<Button-1>", lambda e, c=ass_col: self._apply_ass(c))
            # Right-click: remove from palette
            cv.bind("<Button-3>", lambda e, c=ass_col: self._remove_from_palette(c))
            # Tooltip on hover
            cv.bind("<Enter>", lambda e, lbl=label, w=cv: w.config(
                highlightbackground="#000", highlightthickness=2))
            cv.bind("<Leave>", lambda e, w=cv: w.config(
                highlightbackground="#555", highlightthickness=1))
            self._attach_tooltip(cv, label)

    def _attach_tooltip(self, widget, text: str):
        tip = None
        def enter(e):
            nonlocal tip
            tip = tk.Toplevel(widget)
            tip.overrideredirect(True)
            tip.wm_geometry(f"+{e.x_root+10}+{e.y_root+6}")
            tk.Label(tip, text=text, bg="#ffffcc", relief="solid",
                     borderwidth=1, font=("",8)).pack()
        def leave(e):
            nonlocal tip
            if tip:
                try: tip.destroy()
                except tk.TclError: pass
                tip = None
        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")

    def _save_to_palette(self):
        """Add the current colour to the palette without closing."""
        global _RECENT_COLOURS
        ass = rgb_to_ass(self._r, self._g, self._b)
        _RECENT_COLOURS = [c for c in _RECENT_COLOURS if c != ass]
        _RECENT_COLOURS.append(ass)
        if len(_RECENT_COLOURS) > _MAX_RECENT:
            _RECENT_COLOURS = _RECENT_COLOURS[-_MAX_RECENT:]
        save_colour_palette(_RECENT_COLOURS)
        self._rebuild_palette_swatches()

    def _remove_from_palette(self, ass_col: str):
        """Right-click on a swatch removes it from the palette."""
        global _RECENT_COLOURS
        _RECENT_COLOURS = [c for c in _RECENT_COLOURS if c != ass_col]
        save_colour_palette(_RECENT_COLOURS)
        self._rebuild_palette_swatches()

    def _ok(self):
        ass = rgb_to_ass(self._r, self._g, self._b)
        self._var.set(ass)
        global _RECENT_COLOURS
        _RECENT_COLOURS = [c for c in _RECENT_COLOURS if c != ass]
        _RECENT_COLOURS.append(ass)
        if len(_RECENT_COLOURS) > _MAX_RECENT:
            _RECENT_COLOURS = _RECENT_COLOURS[-_MAX_RECENT:]
        save_colour_palette(_RECENT_COLOURS)   # persist to workspace JSON
        self._close()

    def _close(self):
        if self._closed: return
        self._closed = True
        self.grab_release()
        self.destroy()
        if self._on_close: self._on_close()


# ─────────────────────────────────────────────────────────────────────────────
# Universal tooltip helper
# ─────────────────────────────────────────────────────────────────────────────

def tip(widget, text: str, delay_ms: int = 600):
    """
    Attach a hover tooltip to any Tk widget.
    Coordinates are captured at hover-start so the delayed show is correct
    even if the mouse has moved slightly during the delay.
    """
    _state = {"tip": None, "after": None, "x": 0, "y": 0}

    def _show():
        if _state["tip"]:
            return
        t = tk.Toplevel(widget)
        t.wm_overrideredirect(True)   # wm_ prefix works more reliably on Windows
        t.wm_geometry(f"+{_state['x']+16}+{_state['y']+12}")
        t.wm_attributes("-topmost", True)   # stay on top of everything
        lbl = tk.Label(t, text=text, bg="#fffde7", fg="#222",
                       relief="solid", borderwidth=1,
                       font=("Segoe UI", 9) if True else ("Arial", 9),
                       justify="left", wraplength=380, padx=8, pady=5)
        lbl.pack()
        _state["tip"] = t

    def _enter(e):
        # Capture coordinates NOW — they're valid here but may not be in the delayed call
        _state["x"] = e.x_root
        _state["y"] = e.y_root
        if _state["after"]:
            try: widget.after_cancel(_state["after"])
            except tk.TclError: pass
        _state["after"] = widget.after(delay_ms, _show)

    def _leave(e=None):
        if _state["after"]:
            try: widget.after_cancel(_state["after"])
            except tk.TclError: pass
            _state["after"] = None
        if _state["tip"]:
            try: _state["tip"].destroy()
            except tk.TclError: pass
            _state["tip"] = None

    widget.bind("<Enter>",  _enter, add="+")
    widget.bind("<Leave>",  _leave, add="+")
    widget.bind("<Button>", _leave, add="+")


# ─────────────────────────────────────────────────────────────────────────────
# Colour picker row helper (Entry + swatch + Pick button)
# ─────────────────────────────────────────────────────────────────────────────

class ColourRow(ttk.Frame):
    """
    A labelled colour picker row: [Label] [Entry &H...] [■ swatch] [Pick]
    The swatch updates live as the entry changes.
    Custom colours chosen via the dialog are remembered for the session.
    """
    _custom_colours: list[str] = []   # class-level session memory

    def __init__(self, parent, label: str, initial: str = "&H00E6E6E6",
                 on_change=None, **kw):
        super().__init__(parent, **kw)
        self._on_change = on_change
        self._var = tk.StringVar(value=initial)
        ttk.Label(self, text=label, width=14, anchor="w").pack(side="left")
        ttk.Entry(self, textvariable=self._var, width=16).pack(side="left", padx=(0,4))
        self._swatch = tk.Canvas(self, width=24, height=24, bd=1, relief="solid",
                                 highlightthickness=0)
        self._swatch.pack(side="left", padx=(0,4))
        ttk.Button(self, text="Pick", command=self._pick).pack(side="left")
        self._var.trace_add("write", self._on_var_change)
        self._update_swatch()

    def _update_swatch(self):
        make_swatch(self._swatch, self._var.get())

    def _on_var_change(self, *_):
        self._update_swatch()
        if self._on_change:
            self._on_change(self._var.get())

    def _pick(self):
        ColourPickerDialog(self.winfo_toplevel(), self._var,
                           title="Pick colour",
                           on_close=lambda: None)

    def get(self) -> str:
        return self._var.get()

    def set(self, value: str):
        self._var.set(value)

    @property
    def var(self) -> tk.StringVar:
        return self._var


# ─────────────────────────────────────────────────────────────────────────────
# OCR / issues review window
# ─────────────────────────────────────────────────────────────────────────────

class IssueReviewWindow(tk.Toplevel):
    """
    OCR error reviewer with session + persistent dictionaries.
    Detects: homoglyphs, illegal chars, accent errors (ü→iJ etc.),
    ligature splits (rn→m, cl→d), cross-word splits (ff ends→friends).
    """
    def __init__(self, master, srt_path: str,
                 session_dict: set, persistent_dict: set,
                 app=None):
        super().__init__(master)
        self.title("Subtitle issues")
        self.geometry("1060x640")
        self.transient(master); self.grab_set()

        self.srt_path        = Path(srt_path)
        self.session_dict    = session_dict
        self.persistent_dict = persistent_dict
        self.events          = parse_srt(self.srt_path)
        self.flagged         = []
        self.current         = None
        self._current_ev     = None
        self._listbox_events = None
        self.var_status      = tk.StringVar(value="Scanning…")
        self.var_show_all    = tk.BooleanVar(value=False)
        self._app            = app

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)

        import threading as _t
        _t.Thread(target=self._scan_bg, daemon=True).start()

    def _build(self):
        root = ttk.Frame(self, padding=10); root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1); root.rowconfigure(0, weight=1)

        # ── Left: cue list ────────────────────────────────────────────────────
        lf = ttk.Frame(root); lf.grid(row=0, column=0, sticky="nsew", padx=(0,10))
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)

        hdr = ttk.Frame(lf); hdr.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0,3))
        ttk.Label(hdr, text="Flagged cues", font=("",9,"bold")).pack(side="left")
        if not spellchecker_available():
            ttk.Label(hdr, text="(spell check unavailable — pip install pyspellchecker)",
                      font=("",8), foreground="#888").pack(side="left", padx=8)
        ttk.Checkbutton(hdr, text="Show all cues",
                        variable=self.var_show_all,
                        command=self._on_show_all_toggle).pack(side="right")

        self.listbox = tk.Listbox(lf, width=42, activestyle="dotbox",
                                  font=("Courier New", 9))
        sb = ttk.Scrollbar(lf, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # ── Right: issue detail + editor ──────────────────────────────────────
        rf = ttk.Frame(root); rf.grid(row=0, column=1, sticky="nsew")
        rf.columnconfigure(0, weight=1); rf.rowconfigure(2, weight=1)

        # Row 0: detected issues for this cue (clickable)
        issues_frame = ttk.LabelFrame(rf, text="Detected issues")
        issues_frame.grid(row=0, column=0, sticky="we", pady=(0,4))
        issues_frame.columnconfigure(0, weight=1)
        self.issue_listbox = tk.Listbox(issues_frame, height=3, font=("Arial", 9),
                                        selectmode="single", activestyle="dotbox",
                                        fg="#c04000")
        sb_issues = ttk.Scrollbar(issues_frame, orient="horizontal",
                                  command=self.issue_listbox.xview)
        self.issue_listbox.configure(xscrollcommand=sb_issues.set)
        self.issue_listbox.grid(row=0, column=0, sticky="we")
        sb_issues.grid(row=1, column=0, sticky="we")
        self.issue_listbox.bind("<<ListboxSelect>>", self._on_issue_select)
        self._selected_issue_idx = 0

        # Row 1: action bar (Accept / dict buttons / navigate)
        action_row = ttk.Frame(rf)
        action_row.grid(row=1, column=0, sticky="we", pady=(2, 4))

        self.lbl_add_word = ttk.Label(action_row, text="", font=("", 9),
                                      foreground="#555")
        self.lbl_add_word.pack(side="left", padx=(0, 6))

        self.btn_accept = ttk.Button(action_row, text="✓ Accept suggestion",
                                     command=self._accept_suggestion, state="disabled")
        self.btn_accept.pack(side="left", padx=(0, 4))

        ttk.Button(action_row, text="+ Session dict",
                   command=self._add_session).pack(side="left", padx=(0, 2))
        ttk.Button(action_row, text="+ Persistent dict",
                   command=self._add_persistent).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="Edit dict…",
                   command=self._edit_dict).pack(side="left")

        # Row 2: subtitle text editor (expands)
        editor_frame = ttk.LabelFrame(rf, text="Subtitle text  (edit freely, then Save & re-scan)")
        editor_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 4))
        editor_frame.columnconfigure(0, weight=1); editor_frame.rowconfigure(0, weight=1)
        self.editor = tk.Text(editor_frame, height=8, wrap="word", font=("Arial", 12))
        esb = ttk.Scrollbar(editor_frame, command=self.editor.yview)
        self.editor.configure(yscrollcommand=esb.set)
        self.editor.grid(row=0, column=0, sticky="nsew")
        esb.grid(row=0, column=1, sticky="ns")
        self.editor.tag_config("flag", background="#fff3b0")

        # Row 3: navigation / control buttons
        btns = ttk.Frame(rf); btns.grid(row=3, column=0, sticky="we")
        b_rescan = ttk.Button(btns, text="Save & re-scan", command=self._save_rescan)
        b_rescan.pack(side="left")
        b_prev = ttk.Button(btns, text="⬅ Prev", command=self._prev)
        b_prev.pack(side="left", padx=(8, 0))
        b_next = ttk.Button(btns, text="Next ➡", command=self._next)
        b_next.pack(side="left", padx=(4, 0))
        ttk.Button(btns, text="View original",
                   command=self._view_source_frame).pack(side="left", padx=(16, 0))
        ttk.Button(btns, text="Done", command=self._close).pack(side="right")
        self._scan_buttons = [b_rescan, b_prev, b_next]

        ttk.Label(root, textvariable=self.var_status, foreground="#555").grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))

    def _set_scanning(self, scanning: bool):
        """Disable interactive buttons while a background scan is running."""
        state = "disabled" if scanning else "normal"
        try:
            for w in self._scan_buttons:
                w.configure(state=state)
        except tk.TclError:
            logger.warning("_set_scanning: widget configure failed")
        if scanning:
            self.var_status.set("Scanning…")

    def _scan_bg(self):
        """
        Run the full scan in a background thread.
        Progress is reported every 50 cues so the status bar updates.
        """
        try:
            self.after(0, lambda: self._set_scanning(True))
        except tk.TclError:
            return
        flagged = []
        error_msg = ""
        try:
            total = len(self.events)
            cd    = self.session_dict | self.persistent_dict
            for i, ev in enumerate(self.events):
                issues = scan_text_issues(ev["text"], cd, set())
                if issues:
                    flagged.append({"ev": ev, "issues": issues})
                if (i + 1) % 50 == 0 or i == total - 1:
                    n_done = i + 1
                    try:
                        self.after(0, lambda d=n_done, t=total:
                            self.var_status.set(f"Scanning {d}/{t}…"))
                    except tk.TclError:
                        pass
        except Exception as exc:
            error_msg = str(exc)
        try:
            self.after(0, lambda: self._finish_scan(flagged, error_msg))
        except tk.TclError:
            pass

    def _finish_scan(self, flagged: list, error_msg: str = ""):
        """Called on main thread when background scan completes."""
        saved_pos = getattr(self, "_saved_scan_pos", None)
        self._saved_scan_pos = None
        self.flagged = flagged
        n = len(flagged)
        if error_msg:
            self.var_status.set(f"Scan error: {error_msg}")
        elif n == 0:
            self.var_status.set("No issues found — all cues look clean.")
        else:
            self.var_status.set(f"{n} cue{'s' if n!=1 else ''} with issues found.")
        self._set_scanning(False)
        self._populate()
        if not self.var_show_all.get() and self.flagged and saved_pos is not None:
            idx = min(saved_pos, len(self.flagged) - 1)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self._show(idx)

    def _scan(self):
        """Synchronous scan — only used when we know the file is small or
        after a single-cue edit (see _save_rescan)."""
        self.flagged = []
        cd = getattr(self, "_combined_dict", self.session_dict | self.persistent_dict)
        for ev in self.events:
            issues = scan_text_issues(ev["text"], cd, set())
            if issues:
                self.flagged.append({"ev": ev, "issues": issues})
        n = len(self.flagged)
        self.var_status.set(f"{n} subtitle{'s' if n!=1 else ''} with issues.")
        if n == 0:
            self.var_status.set("No issues found.")

    def _populate(self):
        self.listbox.delete(0, "end")
        if self.var_show_all.get():
            flagged_idx = {it["ev"]["index"] for it in self.flagged}
            self._listbox_events = list(self.events)
            for ev in self.events:
                preview = strip_markup_for_preview(ev["text"])[0].replace("\n", " / ")
                preview = (preview[:57] + "…") if len(preview) > 60 else preview
                marker = "! " if ev["index"] in flagged_idx else "  "
                self.listbox.insert("end", f"{ev['index']:4d}: {marker}{preview}")
                if ev["index"] in flagged_idx:
                    self.listbox.itemconfig("end", fg="#c04000")
            # Select first flagged, or first cue
            for i, ev in enumerate(self.events):
                if ev["index"] in flagged_idx:
                    self.listbox.selection_set(i); self.listbox.see(i)
                    self._show_ev(ev); break
            else:
                if self.events:
                    self.listbox.selection_set(0)
                    self._show_ev(self.events[0])
        else:
            self._listbox_events = None
            for item in self.flagged:
                ev      = item["ev"]
                preview = strip_markup_for_preview(ev["text"])[0].replace("\n", " / ")
                preview = (preview[:60] + "…") if len(preview) > 63 else preview
                self.listbox.insert("end", f"{ev['index']:4d}: {preview}")
            if self.flagged:
                self.listbox.selection_set(0); self._show(0)

    def _show(self, idx: int):
        if not (0 <= idx < len(self.flagged)): return
        self.current = idx
        self._current_ev = self.flagged[idx]["ev"]
        self._selected_issue_idx = 0
        self.btn_accept.config(state="disabled")
        item = self.flagged[idx]

        # Populate the clickable issue list
        self.issue_listbox.delete(0, "end")
        for iss in item["issues"]:
            self.issue_listbox.insert("end", f"  {iss}")
        if item["issues"]:
            self.issue_listbox.selection_set(0)
            self._update_add_label(item["issues"][0])

        self.editor.delete("1.0","end")
        self.editor.insert("1.0", item["ev"]["text"])
        # Highlight ALL flagged words
        self.editor.tag_remove("flag", "1.0", "end")
        for iss in item["issues"]:
            orig = iss.original if hasattr(iss, "original") else ""
            if orig:
                start = "1.0"
                while True:
                    pos = self.editor.search(orig, start, "end", nocase=True)
                    if not pos: break
                    end = f"{pos}+{len(orig)}c"
                    self.editor.tag_add("flag", pos, end)
                    start = end

    def _on_issue_select(self, _=None):
        sel = self.issue_listbox.curselection()
        if not sel: return
        self._selected_issue_idx = sel[0]
        # In show-all mode, flagged item lookup uses _current_ev
        if self.current is not None and 0 <= self.current < len(self.flagged):
            item = self.flagged[self.current]
        else:
            return
        if sel[0] < len(item["issues"]):
            issue = item["issues"][sel[0]]
            self._resolve_suggestion(issue)
            self._update_add_label(issue)

    def _resolve_suggestion(self, issue):
        """Compute spelling suggestion lazily (once) when the user selects it."""
        if issue.kind == "spelling" and not issue.suggestion:
            try:
                from nf3d_core import get_spelling_suggestion
                sugg = get_spelling_suggestion(issue.original)
                if sugg and sugg.lower() != issue.original.lower():
                    issue.suggestion = sugg
                    issue.description = f"Possible spelling: '{issue.original}' → '{sugg}'"
                    # Refresh the issue listbox entry
                    sel = self.issue_listbox.curselection()
                    if sel:
                        self.issue_listbox.delete(sel[0])
                        self.issue_listbox.insert(sel[0], f"  {issue}")
                        self.issue_listbox.selection_set(sel[0])
            except (ImportError, AttributeError):
                logger.warning("_resolve_suggestion: could not get spelling suggestion for %s", issue.original)
        has_sugg = bool(issue.suggestion and
                        issue.suggestion.lower() != issue.original.lower())
        self.btn_accept.config(state="normal" if has_sugg else "disabled")

    def _update_add_label(self, issue):
        word = issue.original if issue.original else "?"
        self.lbl_add_word.config(text=f"'{word}' →")

    def _show_ev(self, ev: dict):
        """Load any event into the editor — used in show-all mode."""
        self._current_ev = ev
        flagged_item = next((it for it in self.flagged if it["ev"]["index"] == ev["index"]), None)
        self.current = self.flagged.index(flagged_item) if flagged_item else None
        self._selected_issue_idx = 0
        self.btn_accept.config(state="disabled")
        self.issue_listbox.delete(0, "end")
        if flagged_item:
            for iss in flagged_item["issues"]:
                self.issue_listbox.insert("end", f"  {iss}")
            if flagged_item["issues"]:
                self.issue_listbox.selection_set(0)
                self._update_add_label(flagged_item["issues"][0])
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", ev["text"])
        self.editor.tag_remove("flag", "1.0", "end")
        if flagged_item:
            for iss in flagged_item["issues"]:
                orig = iss.original if hasattr(iss, "original") else ""
                if orig:
                    start = "1.0"
                    while True:
                        pos = self.editor.search(orig, start, "end", nocase=True)
                        if not pos: break
                        end = f"{pos}+{len(orig)}c"
                        self.editor.tag_add("flag", pos, end)
                        start = end

    def _on_show_all_toggle(self):
        self._populate()

    def _on_select(self, _=None):
        sel = self.listbox.curselection()
        if not sel: return
        if self.var_show_all.get() and self._listbox_events:
            self._show_ev(self._listbox_events[sel[0]])
        else:
            self._show(sel[0])

    def _current_flagged_word(self) -> str:
        """
        Return the word to add to the dictionary.
        Priority: (1) the flagged word from the selected issue in the issue panel
                      — this is the primary selection mechanism,
                  (2) manually selected text in the editor (user drag-selection),
                  (3) empty string.
        The issue panel selection takes priority because the editor may have
        residual selection state from clicking; we don't want that to override
        the deliberately selected issue.
        """
        # Primary: word from the currently highlighted issue
        if self.current is not None and 0 <= self.current < len(self.flagged):
            issues = self.flagged[self.current]["issues"]
            if issues and 0 <= self._selected_issue_idx < len(issues):
                orig = issues[self._selected_issue_idx].original
                if orig and orig.strip():
                    return orig.strip()
        # Fallback: manual text selection in the editor
        try:
            sel = self.editor.selection_get().strip()
            if sel: return sel
        except tk.TclError:
            pass
        return ""

    def _add_to_checker(self, word: str):
        """Add a word to the live checker and combined dict without rebuilding."""
        try:
            from nf3d_core import spellchecker_add_words
            spellchecker_add_words([word])
        except (ImportError, AttributeError):
            logger.warning("_add_to_checker: could not add word to spellchecker")
        if hasattr(self, "_combined_dict"):
            self._combined_dict.add(word)
        else:
            self._combined_dict = self.session_dict | self.persistent_dict

    def _accept_suggestion(self):
        """Replace the flagged word with its spelling suggestion in the editor."""
        if self.current is None or not (0 <= self.current < len(self.flagged)):
            return
        issues = self.flagged[self.current]["issues"]
        if not issues or self._selected_issue_idx >= len(issues):
            return
        issue = issues[self._selected_issue_idx]
        if not issue.suggestion:
            return
        text = self.editor.get("1.0", "end-1c")
        # Case-sensitive replace first; fallback to lowercase match
        if issue.original in text:
            new_text = text.replace(issue.original, issue.suggestion, 1)
        else:
            new_text = text.replace(issue.original.lower(), issue.suggestion, 1)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", new_text)
        self._commit()
        self._rescan_current()

    def _add_session(self):
        word = self._current_flagged_word()
        if not word: return
        clean = word.lower()
        self.session_dict.add(clean)
        self._add_to_checker(clean)
        self._sweep_flagged()

    def _add_persistent(self):
        word = self._current_flagged_word()
        if not word: return
        clean = word.lower()
        self.session_dict.add(clean)
        self.persistent_dict.add(clean)
        self._add_to_checker(clean)
        save_persistent_dictionary(self.persistent_dict)
        self._sweep_flagged()

    def _edit_dict(self):
        win = tk.Toplevel(self); win.title("Persistent dictionary"); win.geometry("380x440")
        win.transient(self)

        # Listbox + scrollbar
        lf = ttk.Frame(win, padding=8); lf.pack(fill="both", expand=True)
        lf.rowconfigure(0, weight=1); lf.columnconfigure(0, weight=1)
        lb = tk.Listbox(lf, font=("Arial", 10))
        sb2 = ttk.Scrollbar(lf, command=lb.yview); lb.configure(yscrollcommand=sb2.set)
        lb.grid(row=0, column=0, sticky="nsew")
        sb2.grid(row=0, column=1, sticky="ns")
        for w in sorted(self.persistent_dict): lb.insert("end", w)

        # Add word row
        add_row = ttk.Frame(win, padding=(8,0,8,4)); add_row.pack(fill="x")
        add_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=add_var, width=24).pack(side="left", padx=(0,4))
        def _add_word():
            w = add_var.get().strip().lower()
            if not w: return
            if w not in self.persistent_dict:
                self.persistent_dict.add(w)
                self.session_dict.add(w)
                self._add_to_checker(w)
                save_persistent_dictionary(self.persistent_dict)
                lb.insert("end", w)
                items = list(lb.get(0, "end"))
                items.sort(); lb.delete(0, "end")
                for item in items: lb.insert("end", item)
            add_var.set("")
        ttk.Button(add_row, text="Add word", command=_add_word).pack(side="left")

        # Remove button
        def _remove():
            sel = lb.curselection()
            if not sel: return
            w = lb.get(sel[0])
            self.persistent_dict.discard(w)
            lb.delete(sel[0])
            save_persistent_dictionary(self.persistent_dict)
        btn_row = ttk.Frame(win, padding=(8,0,8,8)); btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Remove selected", command=_remove).pack(side="left")
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")

    def _commit(self):
        ev = getattr(self, '_current_ev', None)
        if ev is None: return
        new = self.editor.get("1.0", "end-1c").replace("\r\n", "\n").replace("\r", "\n")
        ev["text"] = new   # ev is the same dict object in self.events and self.flagged
        if self.current is not None and self.current >= len(self.flagged):
            self.current = None
        self._write_back()

    def _write_back(self):
        parts = []
        for i, ev in enumerate(self.events, 1):
            parts.append(f"{i}\n{ev['start']} --> {ev['end']}\n{ev['text']}")
        self.srt_path.write_text("\n\n".join(parts)+"\n", encoding="utf-8")

    def _sweep_flagged(self):
        """
        After adding a word to the dictionary, re-check ALL currently flagged
        cues and remove any that are now clean.  This is fast because we only
        iterate the already-flagged subset (typically <<total cues), and we
        use the already-loaded checker with the new word already applied.
        This is the correct behaviour: a dictionary add should instantly clear
        every instance of that error across the whole subtitle file.
        """
        cd = getattr(self, "_combined_dict", self.session_dict | self.persistent_dict)
        to_remove = []
        for i, item in enumerate(self.flagged):
            new_issues = scan_text_issues(item["ev"]["text"], cd, set())
            if new_issues:
                item["issues"] = new_issues
            else:
                to_remove.append(i)

        for i in reversed(to_remove):
            self.flagged.pop(i)

        n = len(self.flagged)
        self.var_status.set(f"{n} subtitle{'s' if n!=1 else ''} with issues.")
        if n == 0:
            self.current = None
            self._current_ev = None
            self.var_status.set("No issues found.")
            self.editor.delete("1.0", "end")

        if self.var_show_all.get():
            self._populate(); return

        # Flagged-only mode: update listbox directly
        for i in reversed(to_remove):
            self.listbox.delete(i)
        if n == 0: return
        if self.current is not None:
            if self.current >= n: self.current = n - 1
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
            self._show(self.current)
        elif self.flagged:
            self.listbox.selection_set(0); self._show(0)

    def _rescan_current(self):
        """Re-check the current cue only (used by Save & re-scan path)."""
        if self.current is None or not (0 <= self.current < len(self.flagged)):
            return
        item = self.flagged[self.current]
        ev   = item["ev"]
        cd = getattr(self, "_combined_dict", self.session_dict | self.persistent_dict)
        new_issues = scan_text_issues(ev["text"], cd, set())
        if new_issues:
            item["issues"] = new_issues
            self._show(self.current)
        else:
            saved = self.current
            self.flagged.pop(self.current)
            n = len(self.flagged)
            self.var_status.set(f"{n} subtitle{'s' if n!=1 else ''} with issues.")
            if n == 0:
                self.current = None; self._current_ev = None
                self.var_status.set("No issues found.")
                self.editor.delete("1.0", "end")
            if self.var_show_all.get():
                self._populate(); return
            self.listbox.delete(saved)
            if n == 0: return
            idx = min(saved, n - 1)
            self.current = idx
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self._show(idx)

    def _save_rescan(self):
        """Save edits to the current cue and trigger a full background rescan."""
        saved_pos = self.current
        self._commit()
        import threading as _t
        _t.Thread(target=self._scan_bg, daemon=True).start()
        self._saved_scan_pos = saved_pos

    def _prev(self):
        if self.var_show_all.get() and self._listbox_events:
            sel = self.listbox.curselection(); i = sel[0] if sel else 0
            if i > 0:
                self.listbox.selection_clear(0, "end"); self.listbox.selection_set(i-1)
                self.listbox.see(i-1); self._show_ev(self._listbox_events[i-1])
            return
        if self.current and self.current > 0: self._show(self.current-1)

    def _next(self):
        if self.var_show_all.get() and self._listbox_events:
            sel = self.listbox.curselection(); i = sel[0] if sel else 0
            if i < len(self._listbox_events) - 1:
                self.listbox.selection_clear(0, "end"); self.listbox.selection_set(i+1)
                self.listbox.see(i+1); self._show_ev(self._listbox_events[i+1])
            return
        if self.current is not None and self.current < len(self.flagged)-1:
            self._show(self.current+1)

    def _view_source_frame(self):
        """
        Show the original PGS frame for the current cue.
        Priority:
          1. ffmpeg PGS overlay — two-stage seek so subtitle decoder warms up 30 s
             before target, ensuring multi-object cues render all lines
          2. nf3d_ocr debug PNG — fallback when ffmpeg/MKV unavailable (may only
             show one line of a split-object PGS cue)
          3. Clean video frame — last resort
        """
        ev = getattr(self, '_current_ev', None)
        if ev is None: return

        app = self._app
        video  = app.var_mkv.get().strip() if app else ""
        ffmpeg = (app.var_ffmpeg.get().strip() or None) if app else None
        if not ffmpeg:
            from nf3d_core import detect_ffmpeg
            ffmpeg = detect_ffmpeg()

        # Use cue midpoint so we land well inside the display window
        from nf3d_core import srt_time_to_ms, seconds_to_ffmpeg
        mid_ms  = (srt_time_to_ms(ev["start"]) + srt_time_to_ms(ev["end"])) // 2
        mid_s   = mid_ms / 1000.0

        # Two-stage seek: fast-seek to 30 s before target so subtitle decoder
        # has time to reach correct state; fine-seek the remainder on output side.
        WARMUP  = 30.0
        pre_s   = max(0.0, mid_s - WARMUP)
        fine_s  = mid_s - pre_s          # ≤ 30 s, decoded accurately
        pre_ff  = seconds_to_ffmpeg(pre_s)
        fine_ff = seconds_to_ffmpeg(fine_s)

        td = Path(tempfile.gettempdir()) / "nf3d_ocr"
        td.mkdir(parents=True, exist_ok=True)
        safe_ts     = seconds_to_ffmpeg(mid_s).replace(":", "-").replace(".", "_")
        out_overlay = td / f"ocr_overlay_{safe_ts}.png"
        out_clean   = td / f"ocr_clean_{safe_ts}.png"

        # Debug PNG paths (used only if ffmpeg path unavailable)
        srt_path  = self.srt_path
        debug_dir = srt_path.parent / "nf3d_ocr_debug"
        cue_idx   = ev.get("index", 1) - 1
        raw_png   = debug_dir / f"frame_{cue_idx:04d}_raw.png"

        if not video or not os.path.isfile(video) or not ffmpeg:
            # No video/ffmpeg — try debug PNG, otherwise bail
            if raw_png.exists():
                try:
                    img = Image.open(raw_png).convert("RGB")
                    self._show_frame_window(raw_png, ev, img, source="PGS source image")
                    return
                except OSError:
                    logger.warning("_view_source_frame: could not open debug PNG %s", raw_png)
            messagebox.showwarning("No video / ffmpeg",
                "Load an MKV file on the Convert tab and ensure ffmpeg is set.\n"
                "The frame viewer needs the source video."); return

        # Resolve which subtitle stream this cue came from
        tr = app._track_by_label.get(app.var_track.get()) if app else None
        sub_stream = tr.get("ffmpeg_idx", 0) if tr else 0

        import threading as _t
        def _extract():
            # Try 1: PGS overlay with two-stage seek for full multi-line rendering
            pr_ov = subprocess.run(
                [ffmpeg, "-y",
                 "-ss", pre_ff, "-i", video,
                 "-filter_complex", f"[0:v:0][0:s:{sub_stream}]overlay",
                 "-ss", fine_ff, "-frames:v", "1", str(out_overlay)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                **_popen_kwargs())
            if pr_ov.returncode == 0 and out_overlay.is_file():
                self.after(0, lambda: self._show_frame_window(
                    out_overlay, ev, source="PGS composited"))
                return
            # Try 2: debug PNG (may be incomplete for split-object cues)
            if raw_png.exists():
                try:
                    img = Image.open(raw_png).convert("RGB")
                    self.after(0, lambda i=img: self._show_frame_window(
                        raw_png, ev, i, source="PGS source image"))
                    return
                except OSError:
                    logger.warning("_extract: could not open debug PNG %s", raw_png)
            # Try 3: clean video frame
            pr_cl = subprocess.run(
                [ffmpeg, "-y",
                 "-ss", pre_ff, "-i", video,
                 "-ss", fine_ff, "-frames:v", "1", str(out_clean)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                **_popen_kwargs())
            if pr_cl.returncode != 0 or not out_clean.is_file():
                self.after(0, lambda: messagebox.showerror(
                    "Frame extraction failed",
                    pr_cl.stderr[-800:] if pr_cl.stderr else "ffmpeg produced no output"))
                return
            self.after(0, lambda: self._show_frame_window(
                out_clean, ev, source="video frame"))

        _t.Thread(target=_extract, daemon=True).start()

    def _show_frame_window(self, frame_path, ev: dict,
                            img=None, source: str = "video frame"):
        """
        Display the source frame (or PGS image) clean — no text overlay.
        The OCR output is shown in a label below the image for comparison.
        img may be pre-loaded (e.g. from debug PNG); if None, loads from frame_path.
        """
        if img is None:
            try:
                img = Image.open(frame_path).convert("RGB")
            except Exception as e:
                messagebox.showerror("Could not open frame", str(e)); return

        win = tk.Toplevel(self)
        win.title(f"Source frame ({source}) — cue {ev['index']}  {ev['start']}")
        win.resizable(True, True)

        # Scale to fit 1280×720
        max_w, max_h = 1280, 720
        scale = min(max_w / img.width, max_h / img.height, 1.0)
        dw    = max(1, int(img.width  * scale))
        dh    = max(1, int(img.height * scale))
        disp  = img.resize((dw, dh), Image.LANCZOS)

        # OCR text label at bottom (packed first so canvas fills remaining space)
        raw_text = ev.get("text", "")
        flat = " / ".join(
            l.strip() for l in raw_text.replace("\\N", "\n").split("\n") if l.strip()
        ) or "(empty)"
        ocr_lbl = ttk.Label(win, text=f"OCR output:  {flat}",
                             font=("Arial", 10), foreground="#ddd",
                             background="#1a1a1a", anchor="w", padding=(8, 4))
        ocr_lbl.pack(side="bottom", fill="x")

        canvas = tk.Canvas(win, width=dw, height=dh, bg="black",
                           highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        win._imgtk = ImageTk.PhotoImage(disp)
        canvas.create_image(0, 0, image=win._imgtk, anchor="nw")

        win.geometry(f"{dw}x{dh+34}")

    def _close(self):
        # Only commit if current index is still valid — _sweep_flagged may have
        # cleared the list, leaving self.current pointing at nothing.
        if self.current is not None and self.current < len(self.flagged):
            self._commit()
        self.grab_release(); self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main application
# ─────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        logger.info("App.__init__: starting NF3D GUI")
        self.title("NF3D — 3D subtitle converter")
        _set_window_icon(self)
        try:
            self.state("zoomed")        # Windows
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)   # Linux
            except tk.TclError:
                pass                    # macOS — geometry already set below
        self.geometry("1340x900")
        self.minsize(1000, 700)

        register_bundled_fonts()

        cfg  = load_config()
        auto = autodetect_tools()
        for k, v in auto.items():
            if not cfg.get(k): cfg[k] = v

        # ── Persistent state ─────────────────────────────────────────────────
        self._track_by_label    = {}
        self._cue_by_label      = {}
        self._events            = []
        self.prepared_srt       = cfg.get("last_srt", "")
        self.project            = Project()
        self.preview_left       = None
        self.preview_right      = None
        self._prev_imgtk        = None
        self._fs_imgtk          = None
        self._edit_imgtk        = None
        self._edit_selected_idx = None
        self._filter_overridden = False
        self.session_dict       = set()
        self.persistent_dict    = load_persistent_dictionary()
        # Load persistent colour palette into module-level list
        global _RECENT_COLOURS
        _RECENT_COLOURS = load_colour_palette()
        # ASS editor state
        self._ass_path              = None   # Path to working ASS file
        self._ass_cues              = []     # list of cue dicts from parse_nf3d_ass
        self._ass_lines             = []     # raw text lines of the ASS file
        self._ass_edited_session    = set()  # cue indices edited this session
        self._ass_frame_left        = None   # extracted frame for edit preview (left eye)
        self._ass_frame_right       = None   # extracted frame for edit preview (right eye)
        self._ass_imgtk             = None   # kept alive for Tk
        self._ass_header_colours    = {}     # parsed from ASS Style: line on open
        # Per-cue emergence overrides (-1 = use global)
        self._ae_emerge_in   = tk.IntVar(value=-1)
        self._ae_emerge_out  = tk.IntVar(value=-1)
        self._ae_emerge_op   = tk.IntVar(value=-1)
        self._ae_emerge_mot  = tk.IntVar(value=-1)
        self._ae_emerge_doff = tk.IntVar(value=-1)
        # Per-cue test/effect mode (False = no exaggeration)
        self._ae_test_mode   = tk.BooleanVar(value=False)
        self._ae_test_scale  = tk.DoubleVar(value=3.0)

        # Font lists (loaded from config, with defaults)
        self._recommended_fonts = cfg.get("recommended_fonts", list(DEFAULT_RECOMMENDED_FONTS))
        self._specialist_fonts  = cfg.get("specialist_fonts",  list(DEFAULT_SPECIALIST_FONTS))

        # ── Tk variables ─────────────────────────────────────────────────────
        S=tk.StringVar; I=tk.IntVar; D=tk.DoubleVar; B=tk.BooleanVar

        # Tools
        self.var_mkv          = S(value=cfg.get("mkv",""))
        self.var_project_title = S(value=cfg.get("project_title",""))
        _default_ws = str(Path.home() / "NF3D")
        self.var_workspace    = S(value=cfg.get("workspace", _default_ws))
        self.var_external_sub = S(value=cfg.get("external_sub",""))
        self.var_mkvmerge     = S(value=cfg.get("mkvmerge",""))
        self.var_mkvextract   = S(value=cfg.get("mkvextract",""))
        self.var_subtitleedit  = S(value=cfg.get("subtitleedit",""))
        self.var_ffmpeg       = S(value=cfg.get("ffmpeg",""))
        self.var_track        = S(value="(load MKV to list tracks)")
        self.var_hsbs         = tk.BooleanVar(value=cfg.get("hsbs", False))

        # Style
        self.var_font_size    = I(value=cfg.get("font_size",72))
        self.var_outline      = D(value=cfg.get("outline",4.0))
        self.var_shadow       = D(value=cfg.get("shadow",2.0))
        self.var_shadow_x     = I(value=cfg.get("shadow_x",2))
        self.var_shadow_y     = I(value=cfg.get("shadow_y",2))
        self.var_alignment    = I(value=cfg.get("alignment",2))
        self.var_margin_l     = I(value=cfg.get("margin_l",30))
        self.var_margin_r     = I(value=cfg.get("margin_r",30))
        self.var_margin_v     = I(value=cfg.get("margin_v",45))
        self.var_y_percent    = D(value=cfg.get("y_percent",88.0))
        self.var_sample_bg    = S(value=cfg.get("sample_bg","Dark"))

        # Depth
        dd = DEPTH_DEFAULTS
        self.var_eye_w           = I(value=cfg.get("eye_w",960))
        self.var_h               = I(value=cfg.get("h",1080))
        self.var_samples         = I(value=cfg.get("samples_per_cue",    dd["samples_per_cue"]))
        self.var_offset_internal = I(value=cfg.get("offset_internal",    dd["offset_internal"]))
        self.var_internal_limit  = I(value=cfg.get("internal_limit",     dd["internal_limit"]))
        self.var_out_min         = I(value=cfg.get("out_min",            dd["out_min"]))
        self.var_out_max         = I(value=cfg.get("out_max",            dd["out_max"]))
        self.var_output_bias     = I(value=cfg.get("output_bias",        dd["output_bias"]))
        self.var_output_scale    = D(value=cfg.get("output_scale",       dd["output_scale"]))
        self.var_base_depth      = I(value=cfg.get("base_depth",         dd["base_depth"]))
        self.var_caps_depth      = I(value=cfg.get("caps_depth",         dd["caps_depth"]))
        self.var_italics_depth   = I(value=cfg.get("italics_depth",      dd["italics_depth"]))
        self.var_deoverlap       = B(value=cfg.get("deoverlap", True))
        self.var_sub_offset_ms   = I(value=cfg.get("sub_offset_ms", 0))

        # Emergence
        self.var_emerge_in_ms    = I(value=cfg.get("emerge_in_ms",100))
        self.var_emerge_out_ms   = I(value=cfg.get("emerge_out_ms",100))
        self.var_start_opacity   = I(value=cfg.get("start_opacity",40))
        self.var_entry_motion_ms = I(value=cfg.get("entry_motion_ms",200))
        self.var_entry_depth_off = I(value=cfg.get("entry_depth_offset",1))
        self.var_test_mode       = B(value=cfg.get("emerge_test_mode",False))
        self.var_test_scale      = D(value=cfg.get("emerge_test_scale",3.0))

        # Run
        self.var_output_mode       = S(value=cfg.get("output_mode","ASS"))
        self.var_forced_track      = tk.BooleanVar(value=cfg.get("forced_track", False))
        self.var_save_depth_export = tk.BooleanVar(value=cfg.get("save_depth_export", True))
        self.var_express_review    = tk.BooleanVar(value=cfg.get("express_review", False))
        self.var_use_srt_override  = tk.BooleanVar(value=False)
        self.var_srt_override      = S(value="")
        self.var_debug_json   = S(value=cfg.get("debug_json",""))

        # Preview
        self.var_prev_cue     = S(value="(no subtitles loaded)")
        self.var_prev_depth   = I(value=0)
        self.var_prev_time    = S(value="00:10:00.000")
        self.var_prev_bg      = I(value=0)
        self.var_prev_zone    = B(value=True)   # subtitle-zone crop mode

        self._build()
        self._bind_style_traces()
        self.after(200, self._render_style_sample)
        self.after(300, self._load_cue_list)
        # Pre-warm spellchecker in background so first add_to_dict is instant
        threading.Thread(target=self._warmup_spellchecker, daemon=True).start()

    # ── Globals dict ──────────────────────────────────────────────────────────

    @staticmethod
    def _safe_int(var, default: int) -> int:
        try: return int(var.get())
        except (ValueError, tk.TclError): return default

    @staticmethod
    def _safe_float(var, default: float) -> float:
        try: return float(var.get())
        except (ValueError, tk.TclError): return default

    def _ws_dir(self) -> str:
        """Return the workspace directory, creating it if needed."""
        ws = self.var_workspace.get().strip() or str(Path.home() / "NF3D")
        try: os.makedirs(ws, exist_ok=True)
        except OSError: logger.warning("_ws_dir: could not create workspace directory %s", ws)
        return ws

    def _vorhees(self, sub: str) -> str:
        """
        Return the path to workspace/Vorhees/<sub>, creating it if needed.
        sub should be one of: 'depth', 'styles', 'colours', 'projects'
        All NF3D JSON files live inside these typed folders so save dialogs
        only ever show files of the correct type.
        """
        ws = self.var_workspace.get().strip() or str(Path.home() / "NF3D")
        p  = Path(ws) / "Vorhees" / sub
        try: p.mkdir(parents=True, exist_ok=True)
        except OSError: logger.warning("_vorhees: could not create directory %s", p)
        return str(p)

    def _globals(self) -> dict:
        h = self._safe_int(self.var_h, 1080)
        return dict(
            eye_w=self._safe_int(self.var_eye_w, 960), h=h,
            y_px=int((self._safe_float(self.var_y_percent, 88.0)/100.0)*h),
            font=self._font_picker.get() if hasattr(self,"_font_picker") else "Arial",
            font_size=self._safe_int(self.var_font_size, 72),
            primary_colour=self._col_primary.get(),
            outline_colour=self._col_outline.get(),
            back_colour=self._col_back.get(),
            outline=self._safe_float(self.var_outline, 4.0),
            shadow=self._safe_float(self.var_shadow, 2.0),
            shadow_x=self._safe_int(self.var_shadow_x, 2),
            shadow_y=self._safe_int(self.var_shadow_y, 2),
            alignment=self._safe_int(self.var_alignment, 2),
            margin_l=self._safe_int(self.var_margin_l, 30),
            margin_r=self._safe_int(self.var_margin_r, 30),
            margin_v=self._safe_int(self.var_margin_v, 45),
            base_depth=self._safe_int(self.var_base_depth, 6),
            caps_depth=self._safe_int(self.var_caps_depth, 8),
            italics_depth=self._safe_int(self.var_italics_depth, 4),
            emerge_in_ms=self._safe_int(self.var_emerge_in_ms, 100),
            emerge_out_ms=self._safe_int(self.var_emerge_out_ms, 100),
            start_opacity=self._safe_int(self.var_start_opacity, 40),
            entry_motion_ms=self.var_entry_motion_ms.get(),
            entry_depth_offset=self.var_entry_depth_off.get(),
            emerge_test_mode=self.var_test_mode.get(),
            emerge_test_scale=self.var_test_scale.get(),
        )

    def _current_cfg(self) -> dict:
        g = self._globals()
        return {**g,
            "mkv": self.var_mkv.get(), "workspace": self.var_workspace.get(),
            "project_title": self.var_project_title.get(),
            "external_sub": self.var_external_sub.get(),
            "mkvmerge": self.var_mkvmerge.get(), "mkvextract": self.var_mkvextract.get(),
            "subtitleedit": self.var_subtitleedit.get(),
            "ffmpeg": self.var_ffmpeg.get(),
            "hsbs": self.var_hsbs.get(),
            "y_percent": self.var_y_percent.get(),
            "samples_per_cue": self.var_samples.get(),
            "offset_internal": self.var_offset_internal.get(),
            "internal_limit": self.var_internal_limit.get(),
            "out_min": self.var_out_min.get(), "out_max": self.var_out_max.get(),
            "output_bias": self.var_output_bias.get(),
            "output_scale": self.var_output_scale.get(),
            "deoverlap":           self.var_deoverlap.get(),
            "sub_offset_ms":       self.var_sub_offset_ms.get(),
            "output_mode":         self.var_output_mode.get(),
            "save_depth_export":   self.var_save_depth_export.get(),
            "express_review":      self.var_express_review.get(),
            "debug_json": self.var_debug_json.get(),
            "sample_bg": self.var_sample_bg.get(),
            "last_srt": self.prepared_srt,
            "recommended_fonts": self._recommended_fonts,
            "specialist_fonts":  self._specialist_fonts,
        }

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self); outer.pack(fill="both", expand=True, padx=8, pady=8)
        outer.columnconfigure(0, weight=1); outer.rowconfigure(0, weight=1)
        nb = ttk.Notebook(outer); nb.grid(sticky="nsew"); self.nb = nb

        self.tab_convert  = ttk.Frame(nb); nb.add(self.tab_convert,  text="Convert")
        self.tab_style    = ttk.Frame(nb); nb.add(self.tab_style,    text="Style")
        self.tab_edit     = ttk.Frame(nb); nb.add(self.tab_edit,     text="Edit cues")
        self.tab_advanced = ttk.Frame(nb); nb.add(self.tab_advanced, text="Advanced")

        self._build_convert_tab()
        self._build_style_tab()
        self._build_edit_tab()
        self._build_advanced_tab()

    # ─── Convert tab ─────────────────────────────────────────────────────────

    def _build_convert_tab(self):
        p = self.tab_convert
        # Two-column layout: workflow steps on left, status panel on right
        p.columnconfigure(0, weight=1)   # workflow — takes all extra space
        p.columnconfigure(1, weight=0)   # status + log — does not grow
        p.rowconfigure(0, weight=1)      # both columns expand vertically

        # ── LEFT column: workflow steps ───────────────────────────────────────
        lf = ttk.Frame(p); lf.grid(row=0, column=0, sticky="nsew", padx=(8,4), pady=8)
        lf.columnconfigure(1, weight=1)

        def section(title, row):
            self._section(lf, title, row)

        def entry_row(r, label, var, btn_text=None, btn_cmd=None):
            ttk.Label(lf, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=4)
            ttk.Entry(lf, textvariable=var).grid(row=r, column=1, sticky="we", padx=8, pady=4)
            if btn_text:
                ttk.Button(lf, text=btn_text, command=btn_cmd).grid(
                    row=r, column=2, padx=8, pady=4)

        # Section 1: Open file
        section("1 · Open file", 0)
        lbl_mkv = ttk.Label(lf, text="MKV / video")
        lbl_mkv.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        tip(lbl_mkv,
            "Path to the source MKV (or MP4) file.\n"
            "Browse to select, or paste a path directly.\n"
            "Video info (resolution, eye width) is read automatically on browse.")
        ttk.Entry(lf, textvariable=self.var_mkv).grid(row=1, column=1, sticky="we", padx=8, pady=4)
        ttk.Button(lf, text="Browse", command=self._browse_mkv).grid(row=1, column=2, padx=8, pady=4)
        cb_hsbs = ttk.Checkbutton(lf, text="HSBS", variable=self.var_hsbs,
                                   command=self._on_hsbs_toggle)
        cb_hsbs.grid(row=1, column=3, padx=(4,8), pady=4)
        tip(cb_hsbs,
            "Half Side-by-Side input — tick when the source video encodes both eyes\n"
            "in a single 1920×1080 frame (960px per eye) rather than as full-width\n"
            "separate frames.\n\n"
            "Auto-ticked when 'HSBS' or 'HalfSBS' is detected in the filename.\n"
            "When ticked, eye width is set to half the video width and depth output\n"
            "scale is adjusted accordingly.\n\n"
            "Leave unticked for full SBS (FSBS) sources such as 3840×1080 encodes.")

        ttk.Label(lf, text="Subtitle track").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.cmb_tracks = ttk.Combobox(lf, textvariable=self.var_track, state="readonly")
        self.cmb_tracks.grid(row=2, column=1, sticky="we", padx=8, pady=4)
        tip(self.cmb_tracks,
            "Select the subtitle track embedded in this MKV to work with.\n"
            "• PGS / HDMV image tracks: OCR'd via Subtitle Edit to produce text.\n"
            "• SRT / UTF-8 text tracks: extracted directly, no OCR needed.\n"
            "• ASS tracks from a previous NF3D run: select it here, then go to\n"
            "  the Edit cues tab and click 'Load NF3D track' to open it directly\n"
            "  for editing without re-running the preparation pipeline.\n"
            "Click Refresh after loading a new MKV to update this list.")
        ttk.Button(lf, text="Refresh", command=self._refresh_tracks).grid(row=2, column=2, padx=8, pady=4)

        lbl_ext = ttk.Label(lf, text="External subtitle")
        lbl_ext.grid(row=3, column=0, sticky="nw", padx=8, pady=6)
        tip(lbl_ext,
            "Optional: use a subtitle file from disk instead of extracting from the MKV.\n"
            "Supported formats: SRT (used as-is), SUP/PGS and IDX+SUB (OCR'd via\n"
            "Subtitle Edit), ASS/SSA (converted to SRT via Subtitle Edit).\n"
            "Leave blank to use the embedded track selected above.")
        _ext_frame = ttk.Frame(lf)
        _ext_frame.grid(row=3, column=1, columnspan=2, sticky="we", padx=8, pady=2)
        _ext_frame.columnconfigure(0, weight=1)
        _ext_pick = ttk.Frame(_ext_frame)
        _ext_pick.grid(row=0, column=0, sticky="we")
        _ext_pick.columnconfigure(0, weight=1)
        ttk.Entry(_ext_pick, textvariable=self.var_external_sub).grid(
            row=0, column=0, sticky="we", padx=(0, 4))
        ttk.Button(_ext_pick, text="Browse", command=lambda: self._browse_file(
            self.var_external_sub, [("Subtitles","*.srt *.sup *.idx *.ass *.ssa"),("All","*.*")])).grid(
            row=0, column=1)
        _ext_note = ttk.Frame(_ext_frame)
        _ext_note.grid(row=1, column=0, sticky="we", pady=(3, 2))
        ttk.Label(_ext_note,
                  text="If from a different release, check sync first.  Timing offset (ms):",
                  font=("", 8), foreground="#888").pack(side="left")
        ttk.Spinbox(_ext_note, from_=-120000, to=120000, increment=500,
                    textvariable=self.var_sub_offset_ms, width=8).pack(side="left", padx=4)
        ttk.Label(_ext_note, text="(0 = no shift)",
                  font=("", 8), foreground="#aaa").pack(side="left")

        ttk.Label(lf, text="Workspace").grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(lf, textvariable=self.var_workspace).grid(row=4, column=1, sticky="we", padx=8, pady=4)
        ws_browse = ttk.Button(lf, text="Browse", command=lambda: (
            d := filedialog.askdirectory()) and self.var_workspace.set(d))
        ws_browse.grid(row=4, column=2, padx=8, pady=4)
        tip(ws_browse,
            "The working folder for this project. NF3D creates sub-folders here:\n"
            "• Vorhees/depth/ — saved depth analysis files\n"
            "• Vorhees/styles/ — saved style presets\n"
            "• Vorhees/colours/ — your saved colour palette\n"
            "• NF3D_Subtitles/ — finished 3D ASS subtitle files\n"
            "• stereo/ — temporary ASS files during conversion")

        lbl_title = ttk.Label(lf, text="Project title")
        lbl_title.grid(row=5, column=0, sticky="w", padx=8, pady=4)
        tip(lbl_title,
            "A human-readable name for this project. Used to name output files — "
            "e.g. 'Blade Runner 2049' produces 'Blade Runner 2049_NF3D.ass'.\n"
            "Auto-filled from the MKV filename (release tags removed) when you browse. "
            "Leave blank to use the MKV filename as-is.")
        title_e = ttk.Entry(lf, textvariable=self.var_project_title)
        title_e.grid(row=5, column=1, sticky="we", padx=8, pady=4)
        ttk.Label(lf, text="(used to name output files)",
                  font=("",8), foreground="#888").grid(
            row=5, column=2, sticky="w", padx=4)

        # Section 2: Prepare subtitle
        section("2 · Prepare subtitle", 6)
        prep_row = ttk.Frame(lf); prep_row.grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        btn_prep = ttk.Button(prep_row, text="Prepare subtitle",
                   command=self._prepare_threaded)
        btn_prep.pack(side="left", padx=(0,8))
        tip(btn_prep,
            "Extract and prepare the subtitle track for 3D conversion.\n"
            "For PGS/image tracks: runs OCR via Subtitle Edit to produce text.\n"
            "For SRT/text tracks: extracts directly.\n"
            "Afterwards, the spellchecker opens automatically if any issues are found.")
        btn_rev = ttk.Button(prep_row, text="Review issues",
                   command=self._open_issue_reviewer)
        btn_rev.pack(side="left")
        tip(btn_rev,
            "Open the subtitle reviewer to inspect and correct flagged issues.\n"
            "Flags: possible spelling errors, OCR garbled characters, homoglyphs,\n"
            "and structural oddities like brackets embedded in words.\n"
            "Add words to the session or persistent dictionary to suppress false positives.")

        # Section 3: Depth
        section("3 · Analyse depth  (requires video + ffmpeg)", 8)
        depth_content = ttk.Frame(lf)
        depth_content.grid(row=9, column=0, columnspan=3, sticky="we", padx=8, pady=(4,2))

        # Row 0: depth analysis buttons
        depth_row = ttk.Frame(depth_content)
        depth_row.grid(row=0, column=0, sticky="w")
        btn_depth = ttk.Button(depth_row, text="Run depth analysis",
                   command=self._analyse_threaded)
        btn_depth.pack(side="left", padx=(0,8))
        tip(btn_depth,
            "Analyse the stereo video to measure depth at each subtitle cue.\n"
            "NF3D samples frames from the MKV using ffmpeg, runs SGBM disparity\n"
            "analysis, and maps the result to a parallax value for each cue.\n"
            "Takes 1–3 hours for a feature film (depending on hardware and\n"
            "samples per cue). Save the result immediately afterwards — this\n"
            "is the main reason the Save depth analysis button exists.")
        lbl_spc = ttk.Label(depth_row, text="Samples per cue")
        lbl_spc.pack(side="left", padx=(8,4))
        tip(lbl_spc,
            "How many video frames to sample per subtitle cue when measuring depth.\n"
            "Higher = more accurate but slower. 6 is a good balance.\n"
            "Increase to 9 for short scenes with rapid cuts; decrease to 3 for "
            "very long films where analysis speed matters more than precision.")
        spn_spc = ttk.Spinbox(depth_row, from_=1, to=9,
                               textvariable=self.var_samples, width=4)
        spn_spc.pack(side="left")
        tip(spn_spc,
            "Frames sampled per cue. More samples = better depth accuracy but "
            "longer analysis time. Default: 6.")
        btn_sdepth = ttk.Button(depth_row, text="Save depth analysis",
                   command=self._save_project)
        btn_sdepth.pack(side="left", padx=(16,0))
        tip(btn_sdepth,
            "Save the depth measurements to a .nf3d.json file in Vorhees/depth/.\n"
            "Load it later to skip re-analysis when working on the same video again.\n"
            "An SRT copy is saved alongside it so the subtitle is never lost.")
        btn_ldepth = ttk.Button(depth_row, text="Load depth analysis",
                   command=self._load_project)
        btn_ldepth.pack(side="left", padx=(4,0))
        tip(btn_ldepth,
            "Load a previously saved depth analysis (.nf3d.json).\n"
            "Restores depth measurements and associated SRT — jump straight\n"
            "to Create output or Edit cues without re-running analysis.")
        self.btn_rescan_missing = ttk.Button(
            depth_row, text="Rescan missing cues",
            command=self._rescan_missing_threaded, state="disabled")
        self.btn_rescan_missing.pack(side="left", padx=(12,0))
        tip(self.btn_rescan_missing,
            "Re-analyse only cues that failed the first depth pass.\n"
            "Failures happen when ffmpeg cannot extract a frame at that timestamp "
            "(very short cues, fade-to-black, seek errors). A second attempt "
            "succeeds for most. Only the missing cues are processed — all "
            "successful measurements from the first run are preserved exactly.")

        # Row 1: SRT override — lets you use a different subtitle with the loaded depth analysis
        ovr_row = ttk.Frame(depth_content)
        ovr_row.grid(row=1, column=0, sticky="w", pady=(4, 0))
        cb_ovr = ttk.Checkbutton(ovr_row, text="Use different SRT for export:",
                                 variable=self.var_use_srt_override)
        cb_ovr.pack(side="left", padx=(0,4))
        tip(cb_ovr,
            "Override the SRT loaded with the depth analysis.\n"
            "Useful when you have multiple language versions with the same cue timings\n"
            "— load the depth analysis once, then produce output for each language\n"
            "by ticking this box and browsing to the alternative SRT.\n"
            "Leave unticked to use the SRT that was loaded alongside the depth analysis.")
        ovr_entry = ttk.Entry(ovr_row, textvariable=self.var_srt_override, width=38)
        ovr_entry.pack(side="left", padx=(0,4))
        tip(ovr_entry, "Path to the alternative SRT file to use when exporting.")
        btn_ovr = ttk.Button(ovr_row, text="Browse…",
                             command=lambda: self._browse_file(
                                 self.var_srt_override,
                                 [("SRT subtitles","*.srt"),("All","*.*")]))
        btn_ovr.pack(side="left")
        tip(btn_ovr, "Browse to the alternative SRT file.")

        # Section 4: Emergence
        section("4 · Emergence", 10)
        em = ttk.Frame(lf); em.grid(row=11, column=0, columnspan=3, sticky="we", padx=8, pady=4)
        for c in range(8): em.columnconfigure(c, weight=1)

        _em_tips = {
            "Fade in (ms)":      ("How long the subtitle takes to become fully opaque after appearing.\n"
                                  "200ms feels natural; lower = snappier; higher = slower dissolve."),
            "Fade out (ms)":     ("How long the subtitle takes to become fully transparent before disappearing.\n"
                                  "100ms is slightly faster than the fade-in, which looks correct to the eye."),
            "Start opacity (%)": ("How transparent the subtitle is at the very start of its fade-in.\n"
                                  "0 = fully invisible at start (standard fade); 100 = no fade at all.\n"
                                  "A value of 20–40 gives a subtle 'pop-in' rather than a full dissolve."),
            "Entry motion (ms)": ("Duration of the horizontal slide as the subtitle enters from the side.\n"
                                  "The subtitle enters from its final position with a slight inward drift.\n"
                                  "0 = no motion, subtitle appears in place; 250ms is a gentle glide."),
        }

        def em_spin(c, label, var, lo, hi, inc=1):
            lbl = ttk.Label(em, text=label)
            lbl.grid(row=0, column=c, sticky="w", padx=(8,2))
            if label in _em_tips:
                tip(lbl, _em_tips[label])
            sb = ttk.Spinbox(em, from_=lo, to=hi, increment=inc, textvariable=var, width=7)
            sb.grid(row=0, column=c+1, sticky="w", padx=(0,8))
            if label in _em_tips:
                tip(sb, _em_tips[label])

        em_spin(0, "Fade in (ms)",      self.var_emerge_in_ms,   0, 2000, 10)
        em_spin(2, "Fade out (ms)",     self.var_emerge_out_ms,  0, 2000, 10)
        em_spin(4, "Start opacity (%)", self.var_start_opacity,  0, 100,  5)
        em_spin(6, "Entry motion (ms)", self.var_entry_motion_ms,0, 2000, 10)

        em2 = ttk.Frame(lf); em2.grid(row=12, column=0, columnspan=3, sticky="we", padx=8, pady=(0,4))
        lbl_edo = ttk.Label(em2, text="Entry depth offset")
        lbl_edo.grid(row=0, column=0, sticky="w", padx=(8,2))
        tip(lbl_edo,
            "Extra depth applied to the subtitle during its entry motion animation.\n"
            "The subtitle enters slightly closer to the viewer (lower depth value) "
            "then settles back to its measured depth position, creating a natural "
            "forward-jumping arrival effect. 0 = no depth change during entry.")
        ttk.Spinbox(em2, from_=0, to=12, textvariable=self.var_entry_depth_off,
                    width=7).grid(row=0, column=1, sticky="w", padx=(0,16))
        cb_test = ttk.Checkbutton(em2, text="Test mode", variable=self.var_test_mode)
        cb_test.grid(row=0, column=2, sticky="w", padx=(0,8))
        tip(cb_test,
            "Exaggerates all depth values by the Scale factor so the 3D effect "
            "is easy to see without 3D glasses. Use this to check emergence "
            "timing and entry motion before final export. Never export with this on.")
        lbl_sc = ttk.Label(em2, text="Scale")
        lbl_sc.grid(row=0, column=3, sticky="w", padx=(0,2))
        tip(lbl_sc,
            "How much to multiply depth values in test mode.\n"
            "5× makes the 3D effect plainly visible on a 2D monitor for timing checks.\n"
            "These are global defaults; each cue can also override them individually\n"
            "via the Emergence… button on the Edit cues tab.")
        ttk.Spinbox(em2, from_=1.0, to=6.0, increment=0.5, textvariable=self.var_test_scale,
                    width=6).grid(row=0, column=4, sticky="w")
        ttk.Label(em2, text="(use Emergence… on Edit tab)",
                  font=("",8), foreground="#999").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=(8,0), pady=(0,2))

        # Section 5: Export
        section("5 · Export", 13)

        # Row 1: standard buttons
        run_row = ttk.Frame(lf); run_row.grid(row=14, column=0, columnspan=3, sticky="w", padx=8, pady=(4,2))
        btn_bass = ttk.Button(run_row, text="Create base ASS",
                   command=self._create_base_ass)
        btn_bass.pack(side="left", padx=(0,8))
        tip(btn_bass,
            "Convert the prepared subtitle into a 3D stereo ASS file and open it\n"
            "in the Edit cues tab immediately.\n"
            "USE THIS for a new project — it builds the ASS, saves it to\n"
            "NF3D_Subtitles/, and takes you straight to per-cue editing.\n"
            "Requires: subtitle prepared + depth analysis run (or loaded).")
        btn_pipe = ttk.Button(run_row, text="Create output",
                   command=self._run_threaded)
        btn_pipe.pack(side="left", padx=(0,8))
        tip(btn_pipe,
            "Convert the prepared subtitle + depth analysis to a finished file.\n"
            "Produces ASS, MKV, or both depending on the Output mode selector.\n"
            "Requires: subtitle prepared (step 2) + depth analysis done/loaded (step 3).\n"
            "USE THIS for a no-editing workflow — subtitle prepared, depth loaded,\n"
            "output here. For per-cue editing use 'Create base ASS' instead.")
        lbl_out = ttk.Label(run_row, text="Output")
        lbl_out.pack(side="left", padx=(0,4))
        cmb_out = ttk.Combobox(run_row, textvariable=self.var_output_mode,
                     values=["ASS","MKV","BOTH"], state="readonly", width=8)
        cmb_out.pack(side="left")
        tip(cmb_out,
            "What to produce when running the pipeline:\n"
            "• ASS  — write the 3D subtitle file only (default). Use this when\n"
            "         editing cues before muxing, or to keep the subtitle separate.\n"
            "• MKV  — mux the subtitle directly into a new MKV alongside the source.\n"
            "         The source MKV is not modified — a new file is created.\n"
            "• BOTH — produce both the standalone ASS and the muxed MKV.\n\n"
            "Requires mkvmerge for MKV and BOTH modes.")
        cb_forced = ttk.Checkbutton(run_row, text="forced", variable=self.var_forced_track)
        cb_forced.pack(side="left", padx=(12, 0))
        tip(cb_forced,
            "Set the NF3D subtitle track as 'forced' in the muxed MKV.\n"
            "Forced subtitles auto-display on most players without the viewer\n"
            "needing to select them — useful for foreign-language inserts.\n"
            "Leave unticked for standard 3D-only subtitle tracks.")

        # Separator between standard and express pipelines
        ttk.Separator(lf, orient="horizontal").grid(
            row=15, column=0, columnspan=3, sticky="we", padx=8, pady=(4, 2))

        # Row 2: Express pipeline options
        exp_row = ttk.Frame(lf); exp_row.grid(row=16, column=0, columnspan=3, sticky="w", padx=8, pady=(0,2))
        btn_express = ttk.Button(exp_row, text="Express pipeline",
                     command=self._express_threaded)
        btn_express.pack(side="left", padx=(0,8))
        tip(btn_express,
            "One-click: prepare subtitle → depth analysis → export.\n\n"
            "Runs all stages automatically using current style and depth settings.\n"
            "No per-cue editing — ideal once you are happy with your setup.\n\n"
            "Optionally shows the OCR reviewer before analysis (tick 'with review').\n"
            "Optionally saves depth analysis automatically (tick 'save depth').\n\n"
            "Express is most useful after confirming OCR quality on a sample subtitle.")
        cb_rev = ttk.Checkbutton(exp_row, text="with OCR review",
                     variable=self.var_express_review)
        cb_rev.pack(side="left", padx=(0,8))
        tip(cb_rev,
            "Open the spelling/OCR reviewer before depth analysis.\n"
            "Untick to skip directly to depth analysis — fastest option\n"
            "once you trust OCR quality on your content.")
        cb_save = ttk.Checkbutton(exp_row, text="save depth",
                     variable=self.var_save_depth_export)
        cb_save.pack(side="left")
        tip(cb_save,
            "Automatically save the depth analysis to Vorhees/depth/ after\n"
            "it completes, using the project title as the filename.\n"
            "Saves the same file as 'Save depth analysis' would — no dialog.")

        # Spacer row pushes everything up
        lf.rowconfigure(17, weight=1)

        # ── RIGHT column: status checklist + log ──────────────────────────────
        rf = ttk.Frame(p, width=260)
        rf.grid(row=0, column=1, sticky="nsew", padx=(4,8), pady=8)
        rf.grid_propagate(False)   # hard-cap width — children cannot push column wider
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(3, weight=1)   # log expands below logo

        # Status panel with per-step checklist
        status_frame = ttk.LabelFrame(rf, text="Status")
        status_frame.grid(row=0, column=0, sticky="we", pady=(0,8))
        status_frame.columnconfigure(1, weight=1)

        # Step indicators: coloured Canvas circles
        # State: "idle" = grey, "running" = amber, "done" = green, "error" = red
        self._step_indicators = {}   # key → Canvas widget
        self._step_labels     = {}   # key → StringVar
        steps = [
            ("file",    "1 · File loaded"),
            ("prepare", "2 · Subtitle prepared"),
            ("depth",   "3 · Depth analysed"),
            ("export",  "5 · Exported"),
        ]
        INDICATOR_COLOURS = {
            "idle":    "#cccccc",
            "running": "#f0a000",
            "done":    "#30b030",
            "error":   "#d03030",
        }
        for r, (key, label) in enumerate(steps):
            cv = tk.Canvas(status_frame, width=14, height=14,
                           bg=self.cget("background"),
                           highlightthickness=0)
            cv.grid(row=r, column=0, padx=(10,4), pady=4, sticky="w")
            cv.create_oval(2, 2, 12, 12, fill=INDICATOR_COLOURS["idle"],
                           outline="", tags="dot")
            self._step_indicators[key] = cv

            lv = tk.StringVar(value=label)
            self._step_labels[key] = lv
            ttk.Label(status_frame, textvariable=lv, font=("", 9),
                      anchor="w").grid(row=r, column=1, sticky="we", padx=(0,8), pady=4)

        # Current activity label
        self.lbl_status = ttk.Label(status_frame, text="Open a file to begin.",
                                    font=("", 9), foreground="#555", wraplength=220)
        self.lbl_status.grid(row=len(steps), column=0, columnspan=2,
                             sticky="we", padx=8, pady=(4,8))
        # lbl_depth_status kept as a no-op label for backward compat
        # (depth progress is now in the step indicator + log)
        self.lbl_depth_status = ttk.Label(status_frame, text="")  # hidden

        # Logo — between status and log
        logo_frame = ttk.Frame(rf)
        logo_frame.grid(row=1, column=0, columnspan=2, sticky="we", pady=(4,4))
        try:
            from PIL import Image as _Img, ImageTk as _ITk
            _logo_path = BASE_DIR / "nf3d_logo.png"
            if _logo_path.is_file():
                _logo_img = _Img.open(str(_logo_path)).convert("RGBA")
                self._logo_imgtk = _ITk.PhotoImage(_logo_img)
                ttk.Label(logo_frame, image=self._logo_imgtk).pack()
        except (OSError, Exception):
            logger.warning("_build_convert_tab: could not load NF3D logo PNG, using text fallback")
            ttk.Label(logo_frame, text="NF3D", font=("Arial", 18, "bold"),
                      foreground="#1a3a6a").pack()

        # Log — fixed height, scrollable
        ttk.Label(rf, text="Log", font=("", 9, "bold")).grid(
            row=2, column=0, sticky="w", pady=(0,2))
        self.log_box = tk.Text(rf, height=8, wrap="word", font=("Courier New", 8),
                               state="normal", bg="#f8f8f8")
        sb_log = ttk.Scrollbar(rf, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb_log.set)
        self.log_box.grid(row=3, column=0, sticky="nsew")
        sb_log.grid(row=3, column=1, sticky="ns")

    # ── Step indicator helpers ────────────────────────────────────────────────

    _STEP_COLOURS = {
        "idle":    "#cccccc",
        "running": "#f0a000",
        "done":    "#30b030",
        "error":   "#d03030",
    }
    _STEP_BASE_LABELS = {
        "file":    "1 · File loaded",
        "prepare": "2 · Subtitle prepared",
        "depth":   "3 · Depth analysed",
        "export":  "5 · Exported",
    }

    def _set_step(self, key: str, state: str = "done", detail: str = ""):
        """
        Update a step indicator.
        state: "idle" | "running" | "done" | "error"
        detail: optional short text appended to the step label (cleared if empty).
        """
        if not hasattr(self, "_step_indicators"): return
        colour = self._STEP_COLOURS.get(state, "#cccccc")
        cv = self._step_indicators.get(key)
        if cv:
            try: cv.itemconfig("dot", fill=colour)
            except tk.TclError: pass
        lv = self._step_labels.get(key)
        if lv:
            base = self._STEP_BASE_LABELS.get(key, key)
            lv.set(f"{base}: {detail}" if detail else base)
        # Also clear the main status text when resetting to idle
        if state == "idle" and key == "file":
            try: self.lbl_status.config(text="Open a file to begin.")
            except tk.TclError: pass

    def _section(self, parent, title: str, row: int):
        """Draw a thin section separator with label."""
        f = ttk.Frame(parent)
        f.grid(row=row, column=0, columnspan=3, sticky="we", padx=8, pady=(8,0))
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text=title, font=("",9,"bold"), foreground="#333").grid(
            row=0, column=0, sticky="w")
        ttk.Separator(f, orient="horizontal").grid(
            row=0, column=1, sticky="we", padx=(8,0))

    # ─── Style tab ───────────────────────────────────────────────────────────

    def _build_style_tab(self):
        p = self.tab_style
        p.columnconfigure(1, weight=1); p.columnconfigure(3, weight=1)
        p.rowconfigure(8, weight=1)

        _style_tips = {
            "Size": (
                "Font size in pixels. This is the size used in the ASS file header and "
                "applies to all cues that do not have a per-cue font size override.\n"
                "Larger values make text bigger and easier to read but can overflow "
                "the safe area on widescreen content. 60–80 is typical for 1080p SBS."),
            "Shadow": (
                "Drop shadow depth in pixels. The shadow is offset by Shadow X and Shadow Y.\n"
                "0 = no shadow. A value of 1–3 adds subtle depth without distraction.\n"
                "Shadows render behind the text and outline, adding legibility on bright scenes."),
            "Shadow X": (
                "Horizontal shadow offset in pixels. Positive = shadow shifts right; "
                "negative = shadow shifts left. Combined with Shadow Y to control direction.\n"
                "2 (slight right offset) is conventional and matches typical light-source direction."),
            "Shadow Y": (
                "Vertical shadow offset in pixels. Positive = shadow drops down; "
                "negative = shadow rises. –2 gives a shadow that drops downward (most natural).\n"
                "Combine with Shadow X to get a diagonal shadow effect."),
            "Outline": (
                "Outline (border) width around each letter, in pixels.\n"
                "The outline is drawn in the Outline colour and surrounds the text fill.\n"
                "2–4 is typical. Heavier outlines improve legibility on complex backgrounds "
                "but can make thin fonts look blocky. 0 = no outline."),
            "Margin L": (
                "Left margin in pixels — minimum distance from the left edge of the frame "
                "to the subtitle text. Prevents subtitles from touching the screen edge.\n"
                "30 is a safe default. Increase for content with burned-in left-side graphics."),
            "Margin R": (
                "Right margin in pixels — minimum distance from the right edge of the frame.\n"
                "30 is a safe default. Increase for content with burned-in right-side graphics."),
            "Margin V": (
                "Vertical margin in pixels — minimum distance from the bottom (or top, for "
                "top-aligned subtitles) of the frame.\n"
                "45 keeps subtitles clear of the very bottom edge and any letterbox bars."),
        }

        def spin(r, c, label, var, lo, hi, inc=1):
            lbl = ttk.Label(p, text=label)
            lbl.grid(row=r, column=c, sticky="w", padx=6, pady=3)
            sb = ttk.Spinbox(p, from_=lo, to=hi, increment=inc, textvariable=var, width=9)
            sb.grid(row=r, column=c+1, sticky="w", padx=6, pady=3)
            if label in _style_tips:
                tip(lbl, _style_tips[label])
                tip(sb,  _style_tips[label])

        # Row 0: Font picker
        lbl_font = ttk.Label(p, text="Font")
        lbl_font.grid(row=0, column=0, sticky="w", padx=6, pady=3)
        tip(lbl_font,
            "The typeface used for all subtitles. This sets the default for every cue; "
            "individual cues can override it on the Edit cues tab.\n"
            "The font must be installed on this system or placed in the fonts/ folder "
            "alongside the NF3D scripts. If the font is not found, Arial is used as fallback.\n"
            "Specialist fonts (separated in the list) are for specific artistic purposes "
            "such as signs, title cards, or character-specific voices.")
        self._font_picker = FontPickerWidget(
            p, self._recommended_fonts, self._specialist_fonts,
            initial=load_config().get("font","Arial"),
            on_change=self._on_font_change,
        )
        self._font_picker.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        spin(0, 2, "Size",    self.var_font_size, 20, 160)
        # Outline is placed at row 3 cols 4-5 — below the style canvas
        # (canvas occupies rows 0-2 at cols 4-5, so row 3 is free)

        # Rows 1–3: Colour pickers with swatches
        cfg = load_config()
        self._col_primary = ColourRow(p, "Text colour",
            cfg.get("primary_colour","&H00E6E6E6"),
            on_change=lambda _: self._style_changed())
        self._col_primary.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        tip(self._col_primary,
            "The fill colour of the subtitle text itself.\n"
            "Default: near-white (&H00E6E6E6) — slightly warm white is easier "
            "on the eye than pure white and reads well against dark backgrounds.\n"
            "Click Pick to open the colour picker, or type an ASS &HBBGGRR hex value directly.\n"
            "Note: ASS colour format is Blue-Green-Red, not RGB.")

        self._col_outline = ColourRow(p, "Outline colour",
            cfg.get("outline_colour","&H00000000"),
            on_change=lambda _: self._style_changed())
        self._col_outline.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        tip(self._col_outline,
            "The colour of the border drawn around each letter.\n"
            "Default: black (&H00000000) — a black outline makes white text legible "
            "on virtually any background, light or dark.\n"
            "For stylised subtitles (e.g. coloured character voice), matching the "
            "outline to a darker shade of the text colour gives a clean look.")

        self._col_back = ColourRow(p, "Shadow / back",
            cfg.get("back_colour","&H64000000"),
            on_change=lambda _: self._style_changed())
        self._col_back.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        tip(self._col_back,
            "The shadow colour and the background box colour (if used).\n"
            "Default: semi-transparent black (&H64000000) — the 64 prefix sets "
            "alpha transparency (00=opaque, FF=invisible). A partially transparent "
            "shadow gives soft depth without a harsh black block.\n"
            "If you want a solid background box behind the text, set this to an "
            "opaque colour and enable the background box in the ASS style settings.")

        spin(1, 2, "Shadow",   self.var_shadow,   0,  12, 0.5)
        spin(2, 2, "Shadow X", self.var_shadow_x, -12, 12)
        spin(3, 2, "Shadow Y", self.var_shadow_y, -12, 12)
        spin(3, 4, "Outline",  self.var_outline,   0,   12, 0.5)

        # Style sample
        self.style_canvas = tk.Canvas(p, width=240, height=54, bg="#202020",
                                      highlightthickness=1, highlightbackground="#444")
        self.style_canvas.grid(row=0, column=4, rowspan=4, sticky="ne", padx=8, pady=4,
                               columnspan=2)

        # Row 4: Alignment + Margins
        lbl_align = ttk.Label(p, text="Alignment (ASS)")
        lbl_align.grid(row=4, column=0, sticky="w", padx=6, pady=3)
        tip(lbl_align,
            "Controls where the subtitle is anchored and its default screen position.\n"
            "ASS uses a numpad layout — the number corresponds to the position on a "
            "phone keypad:\n"
            "  7 8 9  ← top row (top-left, top-centre, top-right)\n"
            "  4 5 6  ← middle row\n"
            "  1 2 3  ← bottom row (bottom-left, bottom-centre, bottom-right)\n"
            "2 = bottom-centre — the standard subtitle position.\n"
            "8 = top-centre — use for forced subtitles when the speaker is at the top,\n"
            "    or when bottom subtitles would overlap action.\n"
            "The Vertical position slider fine-tunes the exact Y coordinate within "
            "the chosen alignment zone.")
        cmb_align = ttk.Combobox(p, textvariable=self.var_alignment, width=6,
            values=[1,2,3,4,5,6,7,8,9], state="readonly")
        cmb_align.grid(row=4, column=1, sticky="w", padx=6, pady=3)
        tip(cmb_align,
            "2 = bottom-centre (standard). 8 = top-centre. "
            "Think of a phone keypad — each number maps to that screen position.")
        spin(4, 2, "Margin L", self.var_margin_l, 0, 200)
        spin(4, 4, "Margin R", self.var_margin_r, 0, 200)

        # Row 5: Vertical position + Margin V
        lbl_vert = ttk.Label(p, text="Vertical position (%)")
        lbl_vert.grid(row=5, column=0, sticky="w", padx=6, pady=3)
        tip(lbl_vert,
            "Fine-tune vertical subtitle position as a percentage of frame height.\n"
            "88% places the subtitle near the bottom (default).\n"
            "Increase towards 100% to move lower; decrease to move up.\n"
            "This works alongside Alignment: if alignment is 2 (bottom-centre), "
            "100% places text at the very bottom edge; 70% raises it significantly.\n"
            "Adjust if subtitles clash with lower-third graphics, credits, or "
            "burned-in content at the bottom of the frame.")
        ttk.Scale(p, from_=0, to=100, variable=self.var_y_percent,
                  orient="horizontal", command=lambda *_: self._style_changed()).grid(
            row=5, column=1, columnspan=2, sticky="we", padx=6)
        ttk.Label(p, textvariable=self.var_y_percent).grid(row=5, column=3, sticky="w", padx=6)
        spin(5, 4, "Margin V", self.var_margin_v, 0, 200)

        # Row 6: Save/reset + Sample BG
        btn_row = ttk.Frame(p); btn_row.grid(row=6, column=0, columnspan=3, sticky="w",
                                             padx=6, pady=(4,2))
        btn_ss = ttk.Button(btn_row, text="Save style…",
                   command=self._save_style_preset)
        btn_ss.pack(side="left", padx=(0,4))
        tip(btn_ss,
            "Save the current font, colours, sizes, and margin settings to a "
            ".nf3ds.json file in Vorhees/styles/.\n"
            "Saved styles can be shared between projects or used as starting "
            "points — e.g. one style for dialogue, another for title cards.")
        btn_ls = ttk.Button(btn_row, text="Load style…",
                   command=self._load_style_preset)
        btn_ls.pack(side="left", padx=(0,6))
        tip(btn_ls,
            "Load a previously saved style preset from Vorhees/styles/.\n"
            "Applies all saved values (font, colours, sizes, margins) at once. "
            "Per-cue overrides on the Edit tab are not affected.")
        btn_rs = ttk.Button(btn_row, text="Reset to defaults",
                   command=self._reset_style)
        btn_rs.pack(side="left")
        tip(btn_rs,
            "Reset all style settings to NF3D defaults: Arial, white text, "
            "black outline, semi-transparent shadow, standard margins.\n"
            "Does not affect per-cue edits already applied in the Edit tab.")
        lbl_bg = ttk.Label(p, text="Sample BG")
        lbl_bg.grid(row=6, column=3, sticky="w", padx=6)
        tip(lbl_bg,
            "Background colour for the style sample preview box (top-right).\n"
            "• Dark: simulate subtitles on a dark scene (most common).\n"
            "• Light: check legibility on bright backgrounds — "
            "a dark outline is essential here.\n"
            "• Checkerboard: shows any transparency in the subtitle style.\n"
            "Does not affect the actual output — preview only.")
        cmb_bg = ttk.Combobox(p, textvariable=self.var_sample_bg, width=14,
            values=["Dark","Light","Checkerboard"], state="readonly")
        cmb_bg.grid(row=6, column=4, sticky="w", padx=6)
        tip(cmb_bg,
            "Background for the style sample: Dark / Light / Checkerboard. Preview only.")

        # Row 7: Preview controls
        pv = ttk.LabelFrame(p, text="Frame preview")
        pv.grid(row=7, column=0, columnspan=6, padx=6, pady=(4,4), sticky="we")
        pv.columnconfigure(1, weight=1)

        tb = ttk.Frame(pv); tb.grid(row=0, column=0, columnspan=2, sticky="we", padx=4, pady=4)
        tb.columnconfigure(1, weight=1)
        ttk.Label(tb, text="Cue").grid(row=0, column=0, sticky="w", padx=4)
        self.cmb_prev_cue = ttk.Combobox(tb, textvariable=self.var_prev_cue, state="readonly")
        self.cmb_prev_cue.grid(row=0, column=1, sticky="we", padx=4)
        self.cmb_prev_cue.bind("<<ComboboxSelected>>", lambda _: self._render_preview())
        btn_rld = ttk.Button(tb, text="Reload subs",  command=self._load_cue_list)
        btn_rld.grid(row=0,column=2,padx=3)
        tip(btn_rld, "Reload the cue list from the current SRT file. Use after preparing "
            "a new subtitle so the preview cue dropdown shows the latest content.")
        btn_cs = ttk.Button(tb, text="Cue start", command=self._cue_start)
        btn_cs.grid(row=0,column=3,padx=3)
        tip(btn_cs, "Copy the selected cue's start timestamp into the Timestamp field below, "
            "ready for frame extraction.")
        btn_ec = ttk.Button(tb, text="Extract cue", command=self._extract_cue_frame)
        btn_ec.grid(row=0,column=4,padx=3)
        tip(btn_ec, "Set the timestamp to the selected cue's start time and extract that "
            "frame from the MKV in one step. Shortcut for Cue start + Extract frame.")
        ttk.Label(tb, text="Timestamp").grid(row=1, column=0, sticky="w", padx=4, pady=(4,0))
        ttk.Entry(tb, textvariable=self.var_prev_time, width=18).grid(
            row=1, column=1, sticky="w", padx=4, pady=(4,0))
        ttk.Button(tb, text="Extract frame", command=self._extract_frame).grid(
            row=1, column=2, padx=3, pady=(4,0))
        ttk.Button(tb, text="Accurate preview",
                   command=self._accurate_preview).grid(row=1, column=3, padx=3, pady=(4,0))
        ttk.Button(tb, text="Fullscreen", command=self._show_fullscreen).grid(
            row=1, column=4, padx=3, pady=(4,0))

        ctrl = ttk.Frame(pv); ctrl.grid(row=1, column=0, sticky="nsw", padx=(6,10), pady=6)
        self._preview_debounce_id = None
        def _debounced_render(*_):
            # Cancel previous pending render and wait 120ms for user to stop dragging
            if self._preview_debounce_id:
                try: self.after_cancel(self._preview_debounce_id)
                except tk.TclError: pass
            self._preview_debounce_id = self.after(
                120, lambda: (self._render_preview(), self._render_fullscreen()))

        def slider(r, label, var, lo, hi):
            ttk.Label(ctrl, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Scale(ctrl, from_=lo, to=hi, variable=var, orient="horizontal",
                command=_debounced_render).grid(
                row=r, column=1, sticky="we", padx=6)
            ttk.Label(ctrl, textvariable=var).grid(row=r, column=2, sticky="w")
        slider(0, "Depth",  self.var_prev_depth, -20, 20)
        slider(1, "BG dim", self.var_prev_bg,      0, 80)
        _sld_tips = {
            0: ("Preview depth offset — temporarily shifts all subtitles in front of or behind "
                "the screen to check how different depths will look on a real 3D display.\n"
                "This does NOT change the depth values in the ASS file. Reset to 0 when done."),
            1: ("Dims the background frame in the preview to simulate a darker cinema environment "
                "or to make the subtitle style easier to evaluate.\n"
                "0 = no dimming (full brightness). 80 = nearly black background.\n"
                "This does NOT affect the output — preview only."),
        }
        for _ri, _lbl in [(0, ctrl.grid_slaves(row=0, column=0)),
                          (1, ctrl.grid_slaves(row=1, column=0))]:
            for _w in _lbl:
                if isinstance(_w, ttk.Label) and _ri in _sld_tips:
                    tip(_w, _sld_tips[_ri])
        cb_zone = ttk.Checkbutton(ctrl, text="Subtitle zone crop",
                        variable=self.var_prev_zone,
                        command=lambda: (self._render_preview(), self._render_fullscreen()))
        cb_zone.grid(row=2, column=0, columnspan=3, sticky="w", pady=3)
        tip(cb_zone,
            "Crop the preview to show only the bottom portion of the frame where "
            "subtitles appear. Makes it easier to evaluate text legibility and "
            "position without the distraction of the full scene.\n"
            "Equivalent to zooming into the subtitle area.")

        # Row 8: Canvas — expands
        p.rowconfigure(8, weight=1)
        self.prev_canvas = tk.Canvas(pv, width=860, height=320, bg="#111", highlightthickness=0)
        self.prev_canvas.grid(row=1, column=1, sticky="nsew", padx=6, pady=6)
        pv.rowconfigure(1, weight=1)
        self.prev_canvas.bind("<Configure>", lambda _: self._render_preview())

    # ─── Edit cues tab ───────────────────────────────────────────────────────

    # ─── Edit cues tab — ASS editor ──────────────────────────────────────────

    def _build_edit_tab(self):
        p = self.tab_edit
        p.columnconfigure(1, weight=1); p.rowconfigure(1, weight=1)

        # ── Top toolbar ───────────────────────────────────────────────────────
        tb = ttk.Frame(p); tb.grid(row=0, column=0, columnspan=2,
                                    sticky="we", padx=8, pady=(8,4))

        # File operations
        btn_oass = ttk.Button(tb, text="Open ASS…", command=self._open_ass)
        btn_oass.pack(side="left", padx=(0,4))
        tip(btn_oass,
            "Open an existing NF3D ASS subtitle file for editing.\n"
            "The file must have been produced by NF3D — it relies on the\n"
            "specific \\clip() and \\pos() tag structure NF3D writes.\n"
            "Loading is done in the background; large files take a few seconds.")
        btn_ltrack = ttk.Button(tb, text="Load NF3D track", command=self._load_nf3d_track)
        btn_ltrack.pack(side="left", padx=(0,8))
        tip(btn_ltrack,
            "Extract the currently selected ASS subtitle track from the loaded MKV\n"
            "and open it directly in the editor — skipping the full preparation\n"
            "pipeline entirely.\n"
            "Use this when re-editing a previously processed NF3D file that is\n"
            "already embedded in an MKV. Select the track in the Convert tab first.")
        btn_sass = ttk.Button(tb, text="Save ASS", command=self._save_ass)
        btn_sass.pack(side="left", padx=(0,4))
        tip(btn_sass,
            "Save all edits to the ASS file.\n"
            "First save: opens a dialog defaulting to NF3D_Subtitles/ with the\n"
            "project title as the filename.\n"
            "Subsequent saves: overwrites the same file silently.\n"
            "Previously edited cues are marked in the file (Name=edited) so\n"
            "they are recognisable when the file is reopened next session.")
        btn_emkv = ttk.Button(tb, text="Export to MKV", command=self._export_ass_to_mkv)
        btn_emkv.pack(side="left", padx=(0,16))
        self._btn_export_mkv = btn_emkv
        tip(btn_emkv,
            "Mux the current ASS into the source MKV as a new subtitle track.\n"
            "The MKV is not modified in place — a new file is created alongside it.\n"
            "If there are unsaved edits you will be asked to save first.\n"
            "Requires mkvmerge (set path in Advanced > Tools & paths).")

        # Preview operations (act on current cue)
        btn_efr = ttk.Button(tb, text="Extract cue frame", command=self._ass_extract_frame)
        btn_efr.pack(side="left", padx=(0,4))
        tip(btn_efr,
            "Extract a video frame at the current cue's start time and use it\n"
            "as the preview background for this editing session.\n"
            "The frame is taken from the loaded MKV using ffmpeg.\n"
            "Extract a new frame each time you move to a different scene,\n"
            "or leave it if the scene background is similar across cues.")
        btn_apr = ttk.Button(tb, text="Accurate preview", command=self._ass_accurate_preview)
        btn_apr.pack(side="left", padx=(0,4))
        tip(btn_apr,
            "Render the current cue using ffmpeg's libass engine — the same\n"
            "renderer used by media players — for a pixel-accurate preview.\n"
            "Shows exactly what the subtitle will look like in the final MKV,\n"
            "including custom fonts, colours, and position.\n"
            "Requires ffmpeg and an extracted cue frame. Slower than the live preview.")
        self._var_anaglyph = tk.BooleanVar(value=False)
        cb_ana = ttk.Checkbutton(tb, text="Anaglyph", variable=self._var_anaglyph)
        cb_ana.pack(side="left", padx=(0,8))
        tip(cb_ana,
            "When ticked, Accurate preview composites both eye frames into a\n"
            "red-cyan anaglyph so you can judge the 3D depth with standard\n"
            "red-cyan glasses — no 3D display required.\n\n"
            "Left eye → red channel.  Right eye → cyan (green + blue).\n"
            "Untick to see the standard side-by-side rendered frame.")
        btn_fs = ttk.Button(tb, text="Fullscreen", command=self._ass_show_fullscreen)
        btn_fs.pack(side="left", padx=(0,8))
        tip(btn_fs,
            "Show the current frame and subtitle in fullscreen for positioning checks.\n"
            "Useful when adjusting vertical position or checking that the subtitle\n"
            "does not overlap action or lower-third graphics.\n"
            "Press Escape or click Exit to return to the editor.")

        self.lbl_ass_path = ttk.Label(tb, text="No ASS loaded — open or create one",
                                       font=("",8,"italic"), foreground="#888")
        self.lbl_ass_path.pack(side="left")

        # ── Left: cue list ────────────────────────────────────────────────────
        lf = ttk.Frame(p, width=300)
        lf.grid(row=1, column=0, sticky="nsew", padx=(8,4), pady=(0,8))
        lf.rowconfigure(2, weight=1); lf.columnconfigure(0, weight=1)

        # Cue info sits at top of the left column — logical flow: list then detail
        self.lbl_ass_cue = ttk.Label(lf, text="Select a cue to edit",
                                      font=("",9,"bold"), foreground="#444")
        self.lbl_ass_cue.grid(row=0, column=0, columnspan=2,
                               sticky="w", padx=4, pady=(0,2))

        filter_row = ttk.Frame(lf)
        filter_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0,2))
        ttk.Label(filter_row, text="Cues").pack(side="left")
        self._ass_filter_edited = tk.BooleanVar(value=False)
        cb_edited = ttk.Checkbutton(filter_row, text="Edited only",
                        variable=self._ass_filter_edited,
                        command=self._refresh_ass_list)
        cb_edited.pack(side="left", padx=(8,0))
        tip(cb_edited,
            "Show only cues that have been manually edited this session or in a\n"
            "previous session (marked orange in the list).\n"
            "Useful for quickly reviewing your changes before saving.")

        self.ass_listbox = tk.Listbox(lf, width=36, activestyle="dotbox",
                                       font=("Courier New", 9))
        sb_l = ttk.Scrollbar(lf, command=self.ass_listbox.yview)
        self.ass_listbox.configure(yscrollcommand=sb_l.set)
        self.ass_listbox.grid(row=2, column=0, sticky="nsew")
        sb_l.grid(row=2, column=1, sticky="ns")
        self.ass_listbox.bind("<<ListboxSelect>>", self._on_ass_select)
        self.ass_listbox.bind("<FocusOut>",
            lambda _: self.after_idle(self._reselect_ass))
        self._ass_sel_idx = None

        # Colour legend
        leg = ttk.Frame(lf); leg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4,0))
        for colour, label in (("#e07020","edited this session"), ("#2060d0","previously edited")):
            f = tk.Frame(leg, width=10, height=10, bg=colour)
            f.pack(side="left", padx=(4,2))
            ttk.Label(leg, text=label, font=("",8)).pack(side="left", padx=(0,8))

        # ── Right: PanedWindow — edit fields (top) / preview + buttons (bottom) ─
        rpaned = tk.PanedWindow(p, orient=tk.VERTICAL, sashpad=2, sashwidth=6,
                                sashrelief=tk.RAISED, bg="#555")
        rpaned.grid(row=1, column=1, sticky="nsew", padx=(4,8), pady=(0,8))

        # Top pane: scrollable edit fields
        rf_top = ttk.Frame(rpaned)
        rf_top.columnconfigure(0, weight=1); rf_top.rowconfigure(0, weight=1)

        fscroll = tk.Canvas(rf_top, highlightthickness=0, bd=0)
        fsb = ttk.Scrollbar(rf_top, orient="vertical", command=fscroll.yview)
        fscroll.configure(yscrollcommand=fsb.set)
        fscroll.grid(row=0, column=0, sticky="nsew")
        fsb.grid(row=0, column=1, sticky="ns")

        fields_frame = ttk.Frame(fscroll)
        fields_frame.columnconfigure(0, weight=1)
        _fwin = fscroll.create_window((0, 0), window=fields_frame, anchor="nw")
        def _on_fields_resize(event):
            fscroll.configure(scrollregion=fscroll.bbox("all"))
        def _on_fscroll_resize(event):
            fscroll.itemconfig(_fwin, width=event.width)
        fields_frame.bind("<Configure>", _on_fields_resize)
        fscroll.bind("<Configure>", _on_fscroll_resize)

        # Enable mouse-wheel scrolling on the fields pane
        def _on_wheel(event):
            fscroll.yview_scroll(int(-1 * (event.delta / 120)), "units")
        fscroll.bind("<MouseWheel>", _on_wheel)
        fields_frame.bind("<MouseWheel>", _on_wheel)

        self._build_ass_edit_fields(fields_frame)
        rpaned.add(rf_top, minsize=200, sticky="nsew")

        # Bottom pane: preview canvas + buttons
        rf_bot = ttk.Frame(rpaned)
        rf_bot.columnconfigure(0, weight=1); rf_bot.rowconfigure(0, weight=1)

        self.ass_canvas = tk.Canvas(rf_bot, bg="#111", highlightthickness=0, height=280)
        self.ass_canvas.grid(row=0, column=0, sticky="nsew", pady=(0,4))
        self.ass_canvas.bind("<Configure>", lambda _: self._render_ass_preview())

        rpaned.add(rf_bot, minsize=250, sticky="nsew")

        # Buttons (inside bottom pane)
        btn = ttk.Frame(rf_bot); btn.grid(row=1, column=0, sticky="we")
        btn_app = ttk.Button(btn, text="Apply edit", command=self._apply_ass_edit)
        btn_app.pack(side="left", padx=(0,8))
        tip(btn_app,
            "Write the current panel values (depth, position, font, colours) into\n"
            "the in-memory ASS for this cue. The cue turns orange in the list.\n"
            "Changes are NOT saved to disk until you click Save ASS.\n"
            "You can apply edits to many cues before saving.")
        btn_rst = ttk.Button(btn, text="Reset to original", command=self._ass_reset_cue)
        btn_rst.pack(side="left", padx=(0,8))
        tip(btn_rst,
            "Restore this cue's depth, position, and style overrides to the values\n"
            "that were in the ASS file when it was opened.\n"
            "Does NOT write to disk — you still need Apply edit + Save ASS\n"
            "if you want to commit the reset.")
        btn_em = ttk.Button(btn, text="Emergence…", command=self._ass_emergence_popup)
        btn_em.pack(side="left", padx=(0,8))
        tip(btn_em,
            "Override the fade and motion animation for this individual cue.\n"
            "By default every cue uses the global emergence settings from the\n"
            "Convert tab. Set any value here to –1 to keep the global default.\n"
            "Useful for a single cue that needs a different timing (e.g. a\n"
            "very short cue that needs a faster fade-in).")
        ttk.Button(btn, text="← Prev",
                   command=self._ass_prev).pack(side="left", padx=(0,4))
        ttk.Button(btn, text="Next →",
                   command=self._ass_next).pack(side="left")
        self.lbl_ass_status = ttk.Label(btn, text="", foreground="#888")
        self.lbl_ass_status.pack(side="right")

    def _build_ass_edit_fields(self, p):
        """
        Edit fields in priority order: Font (most commonly changed per-cue),
        then Depth & position, then Style overrides (colours, sizes).
        All fields are visible without scrolling at normal window sizes.
        """
        p.columnconfigure(1, weight=1); p.columnconfigure(3, weight=1)

        def sep(row, title):
            ttk.Separator(p, orient="horizontal").grid(
                row=row, column=0, columnspan=6, sticky="we", padx=6, pady=(6,2))
            ttk.Label(p, text=title, font=("",9,"bold")).grid(
                row=row+1, column=0, columnspan=6, sticky="w", padx=8, pady=(0,3))
            return row + 2

        def spin(r, c, label, var, lo, hi, inc=1, cmd=None):
            ttk.Label(p, text=label).grid(row=r, column=c, sticky="w", padx=8, pady=2)
            sb = ttk.Spinbox(p, from_=lo, to=hi, increment=inc, textvariable=var,
                             width=9, command=cmd or self._render_ass_preview)
            sb.grid(row=r, column=c+1, sticky="w", padx=4, pady=2)
            sb.bind("<FocusOut>", lambda _: self.after_idle(self._reselect_ass))
            return sb

        row = 0
        # ── Font — first and most prominent ───────────────────────────────────
        row = sep(row, "Font override")
        self._ae_font_en  = tk.BooleanVar()
        cb_font = ttk.Checkbutton(p, text="Override font", variable=self._ae_font_en,
            command=self._render_ass_preview)
        cb_font.grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=3)
        tip(cb_font,
            "Tick to use a different font for this cue only.\n"
            "Useful for character-specific fonts (e.g. a robotic sans-serif for "
            "a cyborg, or a handwritten font for a letter on screen).\n"
            "The font must be installed on the system or in the fonts/ folder. "
            "The status line below shows whether it was found.")
        self._ae_font_picker = FontPickerWidget(
            p, self._recommended_fonts, self._specialist_fonts,
            on_change=lambda name: (self._ae_font_en.set(True),
                                    self._update_font_status(),
                                    self._render_ass_preview()))
        self._ae_font_picker.grid(row=row, column=2, columnspan=4,
                                   sticky="we", padx=4, pady=3)
        row += 1
        self._lbl_font_status = ttk.Label(p, text="", font=("", 8))
        self._lbl_font_status.grid(row=row, column=2, columnspan=4,
                                    sticky="w", padx=8, pady=(0,2))
        row += 1

        # ── Depth & position ──────────────────────────────────────────────────
        row = sep(row, "Depth & position")
        self._ae_depth = tk.IntVar(value=0)
        sb_depth = spin(row, 0, "Depth (px)", self._ae_depth, -20, 20)
        tip(sb_depth,
            "Horizontal parallax for this cue in pixels.\n"
            "• Negative = in FRONT of the screen (crossed disparity). "
            "This is the comfortable, natural position for subtitles — "
            "the text appears to float just ahead of the picture plane.\n"
            "• Zero = exactly at the screen surface.\n"
            "• Positive = BEHIND the screen (uncrossed). Avoid for subtitles — "
            "the viewer must diverge their eyes to read text pushed into the image, "
            "which causes fatigue and conflicts with foreground objects.\n"
            "Typical comfortable range: –2 to –8. The depth analysis sets this "
            "automatically from the scene content; adjust here if needed.")

        self._ae_x_pct = tk.DoubleVar(value=50.0)
        lbl_x = ttk.Label(p, text="X centre (%)")
        lbl_x.grid(row=row, column=2, sticky="w", padx=8, pady=2)
        tip(lbl_x,
            "Horizontal position of the subtitle centre, as a percentage of the "
            "single-eye frame width. 50% = screen centre (default).\n"
            "Adjust if a subtitle needs to move left or right to avoid overlapping "
            "action, or for speaker attribution in dialogue scenes.")
        ttk.Scale(p, from_=0, to=100, variable=self._ae_x_pct, orient="horizontal",
                  command=lambda *_: self._render_ass_preview()).grid(
            row=row, column=3, sticky="we", padx=4, pady=2)
        ttk.Label(p, textvariable=self._ae_x_pct).grid(row=row, column=4, sticky="w")
        row += 1

        self._ae_y_pct = tk.DoubleVar(value=88.0)
        lbl_y = ttk.Label(p, text="Y position (%)")
        lbl_y.grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tip(lbl_y,
            "Vertical position of the subtitle bottom edge, as a percentage of "
            "the frame height. 88% places the subtitle near the bottom (default).\n"
            "Increase to move up (e.g. 70%) if the subtitle overlaps a lower-third "
            "graphic or credits. Decrease only for top-of-screen subtitles.")
        ttk.Scale(p, from_=0, to=100, variable=self._ae_y_pct, orient="horizontal",
                  command=lambda *_: self._render_ass_preview()).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=4, pady=2)
        ttk.Label(p, textvariable=self._ae_y_pct).grid(row=row, column=3, sticky="w")
        row += 1

        # ── Style overrides ────────────────────────────────────────────────────
        row = sep(row, "Style overrides  (unchecked = use ASS header value)")

        self._ae_primary_en = tk.BooleanVar()
        self._ae_primary = ColourRow(p, "Text colour",
                                     on_change=lambda _: self._render_ass_preview())
        cb_tc = ttk.Checkbutton(p, text="Text colour", variable=self._ae_primary_en,
            command=self._render_ass_preview)
        cb_tc.grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tip(cb_tc,
            "Tick to override the text fill colour for this cue only.\n"
            "Leave unticked to use the colour defined in the ASS style header "
            "(set on the Style tab) — the safest choice for most cues.\n"
            "Use sparingly: colour changes draw the eye and can be distracting.")
        self._ae_primary.grid(row=row, column=1, columnspan=3, sticky="w", pady=2)
        row += 1

        self._ae_outline_en = tk.BooleanVar()
        self._ae_outline_col = ColourRow(p, "Outline colour",
                                          on_change=lambda _: self._render_ass_preview())
        cb_outl = ttk.Checkbutton(p, text="Outline colour", variable=self._ae_outline_en,
            command=self._render_ass_preview)
        cb_outl.grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tip(cb_outl,
            "Tick to override the outline (border) colour for this cue only.\n"
            "Leave unticked to use the colour defined in the ASS style header.\n"
            "Changing the outline colour can help text stand out on backgrounds\n"
            "that are close in colour to the default black outline.")
        self._ae_outline_col.grid(row=row, column=1, columnspan=3, sticky="w", pady=2)
        row += 1

        self._ae_back_en = tk.BooleanVar()
        self._ae_back_col = ColourRow(p, "Shadow / back",
                                       on_change=lambda _: self._render_ass_preview())
        cb_back = ttk.Checkbutton(p, text="Shadow / back", variable=self._ae_back_en,
            command=self._render_ass_preview)
        cb_back.grid(row=row, column=0, sticky="w", padx=8, pady=2)
        tip(cb_back,
            "Tick to override the shadow / background box colour for this cue only.\n"
            "Leave unticked to use the ASS style header default.\n"
            "The alpha component of the colour controls transparency — &H64 is\n"
            "semi-transparent; &H00 is fully opaque.")
        self._ae_back_col.grid(row=row, column=1, columnspan=3, sticky="w", pady=2)
        row += 1

        self._ae_fsize_en = tk.BooleanVar(); self._ae_fsize = tk.IntVar(value=72)
        self._ae_outl_en  = tk.BooleanVar(); self._ae_outl  = tk.DoubleVar(value=4.0)
        self._ae_shad_en  = tk.BooleanVar(); self._ae_shad  = tk.DoubleVar(value=2.0)
        self._ae_bold_en  = tk.BooleanVar(); self._ae_bold  = tk.BooleanVar(value=False)
        self._ae_ital_en  = tk.BooleanVar(); self._ae_ital  = tk.BooleanVar(value=False)

        spin(row, 0, "Font size", self._ae_fsize, 12, 160, cmd=self._render_ass_preview)
        cb_fsize = ttk.Checkbutton(p, text="", variable=self._ae_fsize_en,
            command=self._render_ass_preview)
        cb_fsize.grid(row=row, column=0, sticky="e", padx=(0,2))
        tip(cb_fsize, "Tick to override font size for this cue only.")
        spin(row, 2, "Outline", self._ae_outl, 0, 12, 0.5)
        cb_outls = ttk.Checkbutton(p, text="", variable=self._ae_outl_en,
            command=self._render_ass_preview)
        cb_outls.grid(row=row, column=2, sticky="e", padx=(0,2))
        tip(cb_outls, "Tick to override outline width for this cue only.")
        row += 1

        spin(row, 0, "Shadow", self._ae_shad, 0, 12, 0.5)
        cb_shads = ttk.Checkbutton(p, text="", variable=self._ae_shad_en,
            command=self._render_ass_preview)
        cb_shads.grid(row=row, column=0, sticky="e", padx=(0,2))
        tip(cb_shads, "Tick to override shadow depth for this cue only.")

        cb_bold = ttk.Checkbutton(p, text="Bold", variable=self._ae_bold,
            command=lambda: (self._ae_bold_en.set(True), self._render_ass_preview()))
        cb_bold.grid(row=row, column=2, sticky="w", padx=8, pady=2)
        tip(cb_bold, "Override bold for this cue only.")
        ttk.Checkbutton(p, text="", variable=self._ae_bold_en,
            command=self._render_ass_preview).grid(row=row, column=2, sticky="e", padx=(0,2))
        cb_ital = ttk.Checkbutton(p, text="Italic", variable=self._ae_ital,
            command=lambda: (self._ae_ital_en.set(True), self._render_ass_preview()))
        cb_ital.grid(row=row, column=3, sticky="w", padx=8, pady=2)
        tip(cb_ital, "Override italic for this cue only.")
        ttk.Checkbutton(p, text="", variable=self._ae_ital_en,
            command=self._render_ass_preview).grid(row=row, column=4, sticky="w", padx=(0,2))
        row += 1

        # ── Raw ASS tags ───────────────────────────────────────────────────────
        row = sep(row, "Raw ASS tags")
        _TAG_SNIPPETS = [
            ("— insert snippet —",          ""),
            ("Blur (soft glow)",             "\\blur4\\be1"),
            ("Letter spacing",              "\\fsp3"),
            ("Rotation (degrees)",          "\\frz15"),
            ("Horizontal stretch",          "\\fscx150\\fscy100"),
            ("Teletype (reveal over time)", "\\t(0,{dur_ms},\\alpha&H00&)"),
            ("Animate any tag",             "\\t(0,500,\\tag_value)"),
        ]
        snip_var = tk.StringVar(value="— insert snippet —")
        snip_cmb = ttk.Combobox(p, textvariable=snip_var,
                                 values=[s[0] for s in _TAG_SNIPPETS],
                                 state="readonly", width=32)
        snip_cmb.grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(0,4))
        tip(snip_cmb,
            "Pick a tag template to insert into the field below.\n"
            "The snippet is inserted at the cursor position — edit numeric\n"
            "values as needed, then click Apply edit to apply to the cue.\n\n"
            "Position, colour, and fade are handled by the dedicated controls\n"
            "above; these snippets cover effects not otherwise available:\n"
            "• Blur      — \\blur softens edges; \\be adds an additional edge blur\n"
            "• Spacing   — \\fsp adds letter-spacing in pixels\n"
            "• Rotation  — \\frz rotates text (z-axis, degrees)\n"
            "• Stretch   — \\fscx/\\fscy scale text width/height independently\n"
            "• Teletype  — animates alpha from transparent to opaque; replace\n"
            "              {dur_ms} with the cue duration in milliseconds\n"
            "• Animate   — \\t() wrapper for animating any tag over a time range")

        def _insert_snippet(*_):
            label = snip_var.get()
            tag   = next((t for l, t in _TAG_SNIPPETS if l == label), "")
            if tag:
                self._ae_raw.insert("insert", tag)
                self._render_ass_preview()
            snip_var.set("— insert snippet —")

        snip_cmb.bind("<<ComboboxSelected>>", _insert_snippet)
        row += 1

        self._ae_raw = tk.Text(p, height=2, wrap="word", font=("Courier New", 9))
        self._ae_raw.grid(row=row, column=0, columnspan=6, sticky="we", padx=8, pady=(0,4))
        self._ae_raw.bind("<KeyRelease>", lambda _: self._render_ass_preview())
        tip(self._ae_raw,
            "Raw ASS inline tags for effects not covered by the controls above.\n"
            "Tags are written without the surrounding braces — NF3D adds them\n"
            "on export. Example: \\blur5\\be1\n\n"
            "Use the snippet picker above to insert common templates.\n"
            "Tags here are appended after any other style overrides.")
        row += 1

        self._ae_note = tk.Text(p, height=1, wrap="word", font=("Arial", 9),
                                foreground="#888")
        ttk.Label(p, text="Note (not saved):", font=("",8)).grid(
            row=row, column=0, sticky="w", padx=8)
        self._ae_note.grid(row=row, column=1, columnspan=5, sticky="we",
                           padx=(0,8), pady=(0,6))


    # ── ASS editor logic ──────────────────────────────────────────────────────

    def _open_ass(self, path: str = None):
        if not path:
            path = filedialog.askopenfilename(
                initialdir=self._ws_dir(),   # ASS lives in stereo/ subfolder
                filetypes=[("ASS subtitles","*.ass"),("All","*.*")],
                title="Open ASS for editing")
        if not path: return
        # Parse in background thread so the main thread never freezes
        self.lbl_ass_path.config(text=f"Loading {Path(path).name}…")
        self.nb.select(self.tab_edit)
        threading.Thread(target=self._open_ass_bg, args=(path,), daemon=True).start()

    def _open_ass_bg(self, path: str):
        """Background thread: parse ASS, then hand results to main thread."""
        try:
            eye_w = self._safe_int(self.var_eye_w, 960)
            cues, raw_lines = parse_nf3d_ass(path, eye_w)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Open failed", str(e)))
            self.after(0, lambda: self.lbl_ass_path.config(text="Load failed"))
            return
        self.after(0, lambda: self._open_ass_finish(path, cues, raw_lines))

    def _open_ass_finish(self, path: str, cues: list, raw_lines: list):
        """Main thread: apply parsed results and update UI."""
        self._ass_path           = path
        self._ass_cues           = cues
        self._ass_lines          = raw_lines
        self._ass_edited_session = set()
        self._ass_sel_idx        = None

        # Extract header colours so the edit panel can show correct defaults
        self._ass_header_colours = self._parse_ass_header_colours(raw_lines)

        self.lbl_ass_path.config(text=f"Editing: {Path(path).name}")
        self._refresh_ass_list()
        n_edited = sum(1 for c in cues if c['was_edited'])
        self._log(f"Opened ASS for editing: {path}  ({len(cues)} cues, {n_edited} previously edited)")

    def _parse_ass_header_colours(self, raw_lines: list) -> dict:
        """
        Extract PrimaryColour, OutlineColour, BackColour from the Style: line
        so the edit panel shows the correct defaults rather than hardcoded grey.
        Returns a dict with keys matching the style override dict.
        """
        import re as _re
        for line in raw_lines:
            if line.startswith("Style:"):
                parts = line.strip().split(",")
                # Style format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
                #               OutlineColour, BackColour, Bold, ...
                try:
                    return {
                        'primary_colour': parts[3].strip(),
                        'outline_colour': parts[5].strip(),
                        'back_colour':    parts[6].strip(),
                        'font':           parts[1].strip(),
                        'font_size':      int(parts[2].strip()),
                        'outline':        float(parts[16].strip()),
                        'shadow':         float(parts[17].strip()),
                    }
                except (IndexError, ValueError):
                    pass
        return {}

    def _visible_ass_cues(self) -> list:
        if self._ass_filter_edited.get():
            return [c for c in self._ass_cues
                    if c['was_edited'] or c['index'] in self._ass_edited_session]
        return self._ass_cues

    def _refresh_ass_list(self):
        sel    = self._ass_sel_idx
        visible = self._visible_ass_cues()

        # Build all label strings first, then insert in a single batch call.
        # Individual insert+itemconfig per row is O(n²) due to Tk redraws;
        # batch insert is O(n) and avoids freezing on large files.
        labels = []
        for cue in visible:
            prev = (cue['text'] or "").replace("\n", " / ")
            prev = (prev[:46] + "…") if len(prev) > 49 else prev
            marker = "✎" if (cue['index'] in self._ass_edited_session
                             or cue['was_edited']) else " "
            labels.append(f"{cue['index']:4d} {marker} {cue['start_ass'][:8]} {prev}")

        self.ass_listbox.delete(0, "end")
        if labels:
            self.ass_listbox.insert("end", *labels)   # single batch call

        # Apply colours in a second pass (fast — no layout recalculation)
        for i, cue in enumerate(visible):
            if cue['index'] in self._ass_edited_session:
                self.ass_listbox.itemconfig(i, fg="#e07020")
            elif cue['was_edited']:
                self.ass_listbox.itemconfig(i, fg="#2060d0")

        if sel is not None:
            try:
                self.ass_listbox.selection_set(sel)
                self.ass_listbox.see(sel)
            except tk.TclError:
                pass

    def _reselect_ass(self):
        if self._ass_sel_idx is None: return
        try:
            self.ass_listbox.selection_clear(0, "end")
            self.ass_listbox.selection_set(self._ass_sel_idx)
        except tk.TclError: pass

    def _current_ass_cue(self):
        if self._ass_sel_idx is None: return None
        visible = self._visible_ass_cues()
        if 0 <= self._ass_sel_idx < len(visible):
            return visible[self._ass_sel_idx]
        return None

    def _on_ass_select(self, _=None):
        sel = self.ass_listbox.curselection()
        if not sel: return
        self._ass_sel_idx = sel[0]
        cue = self._current_ass_cue()
        if not cue: return
        self._load_cue_into_panel(cue)

    def _load_cue_into_panel(self, cue: dict):
        g    = self._globals()
        eye_w = g["eye_w"]; h = g["h"]
        self.lbl_ass_cue.config(
            text=f"Cue {cue['index']}:  {cue['start_ass']} → {cue['end_ass']}")
        # Capture original parsed values on first visit (before any edits)
        # so "Reset to original" can always restore to the ASS-file state.
        if 'original_depth' not in cue:
            cue['original_depth']    = cue['depth']
            cue['original_x_centre'] = cue['x_centre']
            cue['original_y_centre'] = cue['y_centre']
        self.lbl_ass_status.config(
            text=("Edited ✎" if cue['index'] in self._ass_edited_session
                  else ("Prev. edited" if cue['was_edited'] else "")))

        self._ae_depth.set(cue['depth'])
        self._ae_x_pct.set(round(cue['x_centre'] / eye_w * 100.0, 1))
        self._ae_y_pct.set(round(cue['y_centre'] / h * 100.0, 1))

        # Restore stored style overrides if this cue was previously edited
        # (either this session via _apply_ass_edit, or loaded from the Name field)
        so = cue.get('style_overrides') or {}

        # Get header defaults so uninherited fields show the real ASS colour
        hdr = getattr(self, '_ass_header_colours', {})

        def _set_colour(en_var, col_widget, key):
            if key in so:
                en_var.set(True); col_widget.set(so[key])
            else:
                en_var.set(False)
                # Reset widget to the ASS header default (not hardcoded grey)
                if key in hdr:
                    col_widget.set(hdr[key])

        _set_colour(self._ae_primary_en, self._ae_primary, 'primary_colour')
        _set_colour(self._ae_outline_en, self._ae_outline_col, 'outline_colour')
        _set_colour(self._ae_back_en,    self._ae_back_col, 'back_colour')

        if 'font' in so:
            self._ae_font_en.set(True); self._ae_font_picker.set(so['font'])
        else:
            self._ae_font_en.set(False)
            if 'font' in hdr: self._ae_font_picker.set(hdr['font'])

        if 'font_size' in so:
            self._ae_fsize_en.set(True); self._ae_fsize.set(so['font_size'])
        else:
            self._ae_fsize_en.set(False)
            if 'font_size' in hdr: self._ae_fsize.set(hdr['font_size'])

        if 'outline' in so:
            self._ae_outl_en.set(True); self._ae_outl.set(so['outline'])
        else:
            self._ae_outl_en.set(False)
            if 'outline' in hdr: self._ae_outl.set(hdr['outline'])

        if 'shadow' in so:
            self._ae_shad_en.set(True); self._ae_shad.set(so['shadow'])
        else:
            self._ae_shad_en.set(False)
            if 'shadow' in hdr: self._ae_shad.set(hdr['shadow'])

        if 'bold' in so:
            self._ae_bold_en.set(True); self._ae_bold.set(bool(so['bold']))
        else:
            self._ae_bold_en.set(False); self._ae_bold.set(False)

        if 'italic' in so:
            self._ae_ital_en.set(True); self._ae_ital.set(bool(so['italic']))
        else:
            self._ae_ital_en.set(False); self._ae_ital.set(False)

        self._ae_raw.delete("1.0", "end")
        if 'raw_ass_tags' in so:
            self._ae_raw.insert("1.0", so['raw_ass_tags'])
        self._ae_note.delete("1.0", "end")
        # Show font availability indicator
        self._update_font_status()

        # Restore per-cue emergence overrides if previously applied, else reset to global
        eo = cue.get('emerge_overrides') or {}
        self._ae_emerge_in.set(eo.get('emerge_in_ms', -1))
        self._ae_emerge_out.set(eo.get('emerge_out_ms', -1))
        self._ae_emerge_op.set(eo.get('start_opacity', -1))
        self._ae_emerge_mot.set(eo.get('entry_motion_ms', -1))
        self._ae_emerge_doff.set(eo.get('entry_depth_offset', -1))
        self._ae_test_mode.set(eo.get('test_mode', False))
        self._ae_test_scale.set(eo.get('test_scale', 3.0))
        self._render_ass_preview()

    def _update_font_status(self):
        """
        Show a small label next to the font picker indicating whether the
        currently selected font file can actually be found on this system.
        This makes it immediately obvious when a font selection won't render.
        """
        if not hasattr(self, '_lbl_font_status'): return
        if not self._ae_font_en.get():
            self._lbl_font_status.config(text="", foreground="#888")
            return
        name = self._ae_font_picker.get()
        found = find_font_file(name)
        if found:
            fname = Path(found).name
            self._lbl_font_status.config(
                text=f"✓ {fname}", foreground="#208020")
        else:
            self._lbl_font_status.config(
                text=f"✗ not found — will render as Arial",
                foreground="#c03000")

    def _ass_prev(self):
        if self._ass_sel_idx is None: return
        idx = self._ass_sel_idx - 1
        if idx < 0: return
        self._ass_sel_idx = idx
        self.ass_listbox.selection_clear(0, "end")
        self.ass_listbox.selection_set(idx); self.ass_listbox.see(idx)
        self._on_ass_select()

    def _ass_next(self):
        n   = self.ass_listbox.size()
        idx = (self._ass_sel_idx + 1) if self._ass_sel_idx is not None else 0
        if idx >= n: return
        self._ass_sel_idx = idx
        self.ass_listbox.selection_clear(0, "end")
        self.ass_listbox.selection_set(idx); self.ass_listbox.see(idx)
        self._on_ass_select()

    def _collect_style_overrides(self) -> dict:
        """Collect style override fields that have been enabled."""
        so = {}
        if self._ae_primary_en.get(): so['primary_colour'] = self._ae_primary.get()
        if self._ae_outline_en.get(): so['outline_colour']  = self._ae_outline_col.get()
        if self._ae_back_en.get():    so['back_colour']     = self._ae_back_col.get()
        if self._ae_fsize_en.get():   so['font_size']       = self._ae_fsize.get()
        if self._ae_outl_en.get():    so['outline']         = self._ae_outl.get()
        if self._ae_shad_en.get():    so['shadow']          = self._ae_shad.get()
        if self._ae_font_en.get():    so['font']            = self._ae_font_picker.get()
        if self._ae_bold_en.get():    so['bold']            = self._ae_bold.get()
        if self._ae_ital_en.get():    so['italic']          = self._ae_ital.get()
        raw = self._ae_raw.get("1.0", "end-1c").strip()
        if raw: so['raw_ass_tags'] = raw
        return so

    def _apply_ass_edit(self):
        cue = self._current_ass_cue()
        if not cue:
            messagebox.showwarning("No cue", "Select a cue first."); return
        if not self._ass_path:
            messagebox.showwarning("No ASS", "Open an ASS file first."); return

        g     = self._globals()
        eye_w = self._safe_int(self.var_eye_w, 960)
        h     = self._safe_int(self.var_h, 1080)
        depth    = self._ae_depth.get()
        x_centre = int(round(self._ae_x_pct.get() / 100.0 * eye_w))
        y_centre = int(round(self._ae_y_pct.get() / 100.0 * h))

        # Build emergence globals, substituting per-cue overrides where set
        emerge_g = dict(g)
        for var, key in [(self._ae_emerge_in,   'emerge_in_ms'),
                         (self._ae_emerge_out,  'emerge_out_ms'),
                         (self._ae_emerge_op,   'start_opacity'),
                         (self._ae_emerge_mot,  'entry_motion_ms'),
                         (self._ae_emerge_doff, 'entry_depth_offset')]:
            v = var.get()
            if v >= 0:
                emerge_g[key] = v
        # Per-cue test/effect mode
        if self._ae_test_mode.get():
            emerge_g['emerge_test_mode']  = True
            emerge_g['emerge_test_scale'] = self._ae_test_scale.get()
        else:
            emerge_g['emerge_test_mode']  = False

        left_line, right_line = rebuild_ass_cue_lines(
            cue, depth, x_centre, y_centre, eye_w, h,
            emerge_g, self._collect_style_overrides())

        # Write directly into raw_lines
        self._ass_lines[cue['left_line_no']]  = left_line
        self._ass_lines[cue['right_line_no']] = right_line

        # Update cue dict so future edits and previews see the new values
        cue['depth']    = depth
        cue['x_centre'] = x_centre
        cue['y_centre'] = y_centre
        cue['was_edited'] = True

        # Persist per-cue emergence overrides so a second Apply doesn't clobber them
        cue['emerge_overrides'] = {
            'emerge_in_ms':       self._ae_emerge_in.get(),
            'emerge_out_ms':      self._ae_emerge_out.get(),
            'start_opacity':      self._ae_emerge_op.get(),
            'entry_motion_ms':    self._ae_emerge_mot.get(),
            'entry_depth_offset': self._ae_emerge_doff.get(),
            'test_mode':          self._ae_test_mode.get(),
            'test_scale':         self._ae_test_scale.get(),
        }
        # Store style overrides so _load_cue_into_panel can restore them
        # when the user navigates away and comes back to this cue
        cue['style_overrides'] = self._collect_style_overrides()

        # Mirror every edit into self.project.overrides so that
        # convert_to_stereo_ass (Run / Express) picks them up if the user
        # re-runs the full conversion after editing individual cues.
        so = cue['style_overrides']
        em = cue['emerge_overrides']
        ov = CueOverride(
            depth               = depth,
            x_pct               = self._ae_x_pct.get(),
            y_pct               = self._ae_y_pct.get(),
            primary_colour      = so.get('primary_colour'),
            outline_colour      = so.get('outline_colour'),
            back_colour         = so.get('back_colour'),
            font                = so.get('font'),
            font_size           = so.get('font_size'),
            bold                = so.get('bold'),
            outline             = so.get('outline'),
            shadow              = so.get('shadow'),
            raw_ass_tags        = so.get('raw_ass_tags'),
            emerge_in_ms        = em['emerge_in_ms']       if em.get('emerge_in_ms',       -1) >= 0 else None,
            emerge_out_ms       = em['emerge_out_ms']      if em.get('emerge_out_ms',      -1) >= 0 else None,
            start_opacity       = em['start_opacity']      if em.get('start_opacity',      -1) >= 0 else None,
            entry_motion_ms     = em['entry_motion_ms']    if em.get('entry_motion_ms',    -1) >= 0 else None,
            entry_depth_offset  = em['entry_depth_offset'] if em.get('entry_depth_offset', -1) >= 0 else None,
        )
        self.project.set_override(cue['index'], ov)

        self._ass_edited_session.add(cue['index'])
        self.lbl_ass_status.config(text="Applied ✓ (unsaved)")
        self._refresh_ass_list()
        self._reselect_ass()

    def _save_ass(self):
        if not self._ass_path or not self._ass_lines:
            messagebox.showwarning("Nothing to save", "Open an ASS file first."); return
        # If the current ASS is still in a temp/stereo folder, offer to save
        # a proper named copy to NF3D_Subtitles/ using the project title.
        current = Path(self._ass_path)
        ws      = self.var_workspace.get().strip()
        named_dir = Path(ws) / "NF3D_Subtitles" if ws else current.parent
        named_dir.mkdir(parents=True, exist_ok=True)
        import re as _re
        title = self.var_project_title.get().strip()
        if not title:
            title = current.stem.replace("_NF3D","")
        safe  = _re.sub(r'[<>:"/\\|?*]', '_', title).strip()
        # Offer to save to named location if current path is not already there
        if current.parent != named_dir:
            dest = filedialog.asksaveasfilename(
                title="Save 3D subtitle ASS",
                initialdir=str(named_dir),
                initialfile=safe + "_NF3D",
                defaultextension=".ass",
                filetypes=[("ASS subtitles","*.ass"),("All","*.*")])
            if not dest: return
            self._ass_path = dest
        path  = self._ass_path
        lines = list(self._ass_lines)   # snapshot — safe to read from bg thread
        n     = len(self._ass_edited_session)
        self.lbl_ass_path.config(text=f"Saving {Path(path).name}…")

        def _bg():
            try:
                save_nf3d_ass(lines, path)
                self.after(0, lambda: self._save_ass_done(path, n))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Save failed", str(e)))
                self.after(0, lambda: self.lbl_ass_path.config(
                    text=f"Save failed: {Path(path).name}"))

        threading.Thread(target=_bg, daemon=True).start()

    def _save_ass_done(self, path: str, n_edits: int):
        self.lbl_ass_path.config(text=f"Saved: {Path(path).name}")
        self._log(f"ASS saved: {path}  ({n_edits} edits this session)")
        self._set_step("export", "done", Path(path).name)
        messagebox.showinfo("Saved", f"ASS saved:\n{path}")

    def _export_ass_to_mkv(self):
        """Mux the current working ASS into the source MKV (runs in background thread)."""
        if not self._ass_path:
            messagebox.showwarning("No ASS", "Open or create an ASS file first."); return
        if self._ass_edited_session:
            if messagebox.askyesno("Unsaved edits", "Save ASS before muxing?"):
                self._save_ass()

        # Disable button to prevent double-click while running
        btn = getattr(self, '_btn_export_mkv', None)
        if btn:
            btn.config(state="disabled", text="Muxing…")
        self._set_step("export", "running", "muxing…")
        self._log("Muxing ASS into MKV — please wait…")

        def _bg():
            try:
                out = self._mux(self._ass_path)
                def _done():
                    if btn: btn.config(state="normal", text="Export to MKV")
                    self._set_step("export", "done", Path(out).name)
                    messagebox.showinfo("Done", f"MKV created:\n{out}")
                self.after(0, _done)
            except Exception as e:
                def _err(msg=str(e)):
                    if btn: btn.config(state="normal", text="Export to MKV")
                    self._set_step("export", "error", "mux failed")
                    messagebox.showerror("Export failed", msg)
                self.after(0, _err)

        import threading as _t
        _t.Thread(target=_bg, daemon=True).start()

    def _render_ass_preview(self):
        """Live preview using current panel values.
        Uses the edit tab's own extracted frame if available,
        falls back to the Style tab frame."""
        left  = self._ass_frame_left  or self.preview_left
        right = self._ass_frame_right or self.preview_right
        if left is None or not self._ass_cues:
            self.ass_canvas.delete("all")
            self.ass_canvas.create_text(
                max(1, self.ass_canvas.winfo_width()) // 2,
                max(1, self.ass_canvas.winfo_height()) // 2,
                text="Use 'Extract cue frame' to load a preview frame",
                fill="#aaa", font=("Arial", 11))
            return
        cue = self._current_ass_cue()
        if not cue: return

        g     = self._globals()
        eye_w = self._safe_int(self.var_eye_w, 960)
        h     = self._safe_int(self.var_h, 1080)
        depth    = self._ae_depth.get()
        x_centre = int(round(self._ae_x_pct.get() / 100.0 * eye_w))
        y_centre = int(round(self._ae_y_pct.get() / 100.0 * h))
        so = self._collect_style_overrides()

        params = dict(
            font         = so.get('font', g['font']),
            font_size    = so.get('font_size', g['font_size']),
            primary_colour = so.get('primary_colour', g['primary_colour']),
            outline_colour = so.get('outline_colour', g['outline_colour']),
            back_colour    = so.get('back_colour',    g['back_colour']),
            outline      = so.get('outline', g['outline']),
            shadow       = so.get('shadow',  g['shadow']),
            shadow_x     = g.get('shadow_x', g['shadow']),
            shadow_y     = g.get('shadow_y', g['shadow']),
            depth        = depth,
            x_centre     = x_centre,
            y_centre     = y_centre,
            had_italics  = False,
            raw_text     = cue['text'],
        )
        cw = max(100, self.ass_canvas.winfo_width())
        ch = max(100, self.ass_canvas.winfo_height())
        stage = render_subtitle_zone_preview(
            left, right, params, cw, ch, 0)
        self._edit_imgtk = ImageTk.PhotoImage(stage)
        self.ass_canvas.delete("all")
        self.ass_canvas.create_image(0, 0, image=self._edit_imgtk, anchor="nw")

    # ── Edit tab: frame extraction & preview ─────────────────────────────────

    def _ass_extract_frame(self):
        """Extract a frame at the current cue's start time directly from the MKV."""
        cue = self._current_ass_cue()
        if not cue:
            messagebox.showwarning("No cue", "Select a cue first."); return
        video  = self.var_mkv.get().strip()
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        if not video or not os.path.isfile(video):
            messagebox.showerror("No video", "Load an MKV file on the Convert tab first."); return
        if not ffmpeg:
            messagebox.showerror("No ffmpeg", "ffmpeg not found."); return

        # Convert ASS time to ffmpeg-compatible timestamp (H:MM:SS.cc → H:MM:SS.mmm)
        ts_ass = cue['start_ass']  # e.g. "0:01:47.86"
        try:
            h, m, rest = ts_ass.split(':')
            s, cs = rest.split('.')
            ms = int(cs) * 10
            ts = f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{ms:03d}"
        except (ValueError, IndexError):
            logger.warning("_ass_extract_frame: could not parse ASS timestamp %s, using raw", ts_ass)
            ts = ts_ass

        td  = Path(tempfile.gettempdir()) / "nf3d_preview"; td.mkdir(parents=True, exist_ok=True)
        out = td / f"ass_frame_{ts.replace(':','-').replace('.','_')}.png"
        pr  = subprocess.run([ffmpeg,"-y","-ss",ts,"-i",video,"-frames:v","1",str(out)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             **_popen_kwargs())
        if pr.returncode != 0 or not out.is_file():
            messagebox.showerror("Frame extraction failed", pr.stderr or "ffmpeg failed"); return

        img = Image.open(out).convert("RGB")
        self._ass_frame_left, self._ass_frame_right = split_sbs(img)
        self.lbl_ass_status.config(text=f"Frame at {ts}")
        self._render_ass_preview()

    def _ass_accurate_preview(self):
        """
        Accurate preview using ffmpeg's ass filter on the current working ASS.
        Writes the complete working ASS (including all applied edits) to a temp
        file and composites it onto the current cue's frame — true WYSIWYG.
        """
        left  = self._ass_frame_left  or self.preview_left
        right = self._ass_frame_right or self.preview_right
        if left is None:
            messagebox.showwarning("No frame", "Extract a cue frame first."); return
        if not self._ass_lines:
            messagebox.showwarning("No ASS", "Open an ASS file first."); return
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        if not ffmpeg:
            messagebox.showwarning("No ffmpeg", "ffmpeg not found."); return
        threading.Thread(
            target=self._ass_run_accurate_preview,
            args=(ffmpeg, left, right),
            daemon=True).start()

    def _ass_run_accurate_preview(self, ffmpeg, left, right):
        try:
            import nf3d_core as core
            td = Path(tempfile.gettempdir()) / "nf3d"
            td.mkdir(parents=True, exist_ok=True)
            ass_path   = td / "ass_edit_preview.ass"
            frame_path = td / "ass_edit_frame.png"
            out_path   = td / "ass_edit_out.png"

            # Build a minimal single-cue ASS with fixed timestamps
            # (the static frame has no time context; real timestamps produce no output)
            cue = self._current_ass_cue()
            if not cue: return

            g     = self._globals()
            eye_w = self._safe_int(self.var_eye_w, 960)
            h_px  = self._safe_int(self.var_h, 1080)
            depth    = self._ae_depth.get()
            x_centre = int(round(self._ae_x_pct.get() / 100.0 * eye_w))
            y_centre = int(round(self._ae_y_pct.get() / 100.0 * h_px))

            lx    = x_centre - depth
            rx    = eye_w + x_centre + depth
            lclip = f"0,0,{eye_w},{h_px}"
            rclip = f"{eye_w},0,{eye_w*2},{h_px}"
            lt    = "{" + f"\\an2\\pos({lx},{y_centre})\\clip({lclip})" + "}"
            rt    = "{" + f"\\an2\\pos({rx},{y_centre})\\clip({rclip})" + "}"

            # Style overrides as inline tags
            so = self._collect_style_overrides()
            tags = ""
            if 'primary_colour' in so:
                c = so['primary_colour'].upper()
                if not c.startswith('&H'): c = '&H' + c
                tags += "\\c" + c
            if 'outline_colour' in so: tags += "\\3c" + so['outline_colour']
            if 'back_colour'    in so: tags += "\\4c" + so['back_colour']
            if 'font'      in so: tags += "\\fn" + so['font']
            if 'font_size' in so: tags += "\\fs" + str(so['font_size'])
            if 'outline'   in so: tags += "\\bord" + str(so['outline'])
            if 'shadow'    in so: tags += "\\shad" + str(so['shadow'])
            if so.get('bold') is True:   tags += "\\b1"
            if so.get('bold') is False:  tags += "\\b0"
            if so.get('italic') is True:  tags += "\\i1"
            if so.get('italic') is False: tags += "\\i0"
            style_block = ("{" + tags + "}") if tags else ""
            body = style_block + cue['text']

            header = core.build_ass_header(
                eye_w * 2, h_px, g['font'], g['font_size'],
                g['primary_colour'], g['outline_colour'], g['back_colour'],
                g['outline'], g['shadow'], g['alignment'],
                g['margin_l'], g['margin_r'], g['margin_v'])

            content = (header
                       + f"Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{lt}{body}\n"
                       + f"Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{rt}{body}\n")
            ass_path.write_text(content, encoding="utf-8-sig", newline="\n")

            # Write the frame
            eye_w = self._safe_int(self.var_eye_w, 960)
            h     = self._safe_int(self.var_h, 1080)
            full_img = Image.new("RGB", (eye_w * 2, h))
            full_img.paste(left,  (0, 0))
            full_img.paste(right, (eye_w, 0))
            full_img.save(str(frame_path))

            cmd = [ffmpeg, "-y", "-i", str(frame_path),
                   "-vf", "ass=ass_edit_preview.ass",
                   "-frames:v", "1", str(out_path)]
            pr = subprocess.run(cmd, cwd=str(td),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                **_popen_kwargs())

            if not out_path.is_file():
                tail = "\n".join((pr.stdout or "").strip().splitlines()[-8:])
                messagebox.showerror("Accurate preview failed", tail or "ffmpeg produced no output")
                return

            full_rendered = Image.open(out_path).convert("RGB")
            anaglyph_mode = self._var_anaglyph.get()
            if anaglyph_mode:
                # Split rendered SBS frame back into eyes
                rw = full_rendered.width // 2
                rh = full_rendered.height
                import numpy as _np
                la = _np.array(full_rendered.crop((0,   0, rw,    rh)))
                ra = _np.array(full_rendered.crop((rw,  0, rw*2,  rh)))
                ana = _np.zeros_like(la)
                ana[:, :, 0] = la[:, :, 0]   # R from left eye
                ana[:, :, 1] = ra[:, :, 1]   # G from right eye
                ana[:, :, 2] = ra[:, :, 2]   # B from right eye
                display_img = Image.fromarray(ana.astype("uint8"))
                title = "Accurate preview — anaglyph (red-cyan)"
            else:
                display_img = full_rendered
                title = "Accurate preview — libass rendered (edit)"

            def _show():
                img    = display_img
                win    = tk.Toplevel(self)
                win.title(title)
                max_w, max_h = 1440, 900
                scale  = min(max_w / img.width, max_h / img.height, 1.0)
                dw     = max(1, int(img.width  * scale))
                dh     = max(1, int(img.height * scale))
                img    = img.resize((dw, dh), Image.LANCZOS)
                canvas = tk.Canvas(win, width=dw, height=dh, bg="black", highlightthickness=0)
                canvas.pack()
                self._ass_accurate_imgtk = ImageTk.PhotoImage(img)
                canvas.create_image(dw//2, dh//2,
                                    image=self._ass_accurate_imgtk, anchor="center")
                win.geometry(f"{dw}x{dh}")
            self.after(0, _show)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _ass_show_fullscreen(self):
        """Fullscreen anaglyph preview from the edit tab."""
        left  = self._ass_frame_left  or self.preview_left
        right = self._ass_frame_right or self.preview_right
        if left is None:
            messagebox.showwarning("No frame", "Extract a cue frame first."); return
        cue = self._current_ass_cue()
        if not cue: return

        if hasattr(self,"_ass_fs_win") and self._ass_fs_win.winfo_exists():
            self._ass_fs_win.destroy()
        win = tk.Toplevel(self)
        win.title("NF3D edit fullscreen")
        win.attributes("-fullscreen", True)
        win.configure(bg="black")
        tk.Button(win, text="Exit", command=win.destroy,
                  bg="#333", fg="white").place(relx=1.0, rely=0, anchor="ne")
        canvas = tk.Canvas(win, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._ass_fs_win = win

        def _render_fs(e=None):
            cw = max(300, canvas.winfo_width())
            ch = max(300, canvas.winfo_height())
            g     = self._globals()
            eye_w = self._safe_int(self.var_eye_w, 960)
            h_px  = self._safe_int(self.var_h, 1080)
            depth    = self._ae_depth.get()
            x_centre = int(round(self._ae_x_pct.get() / 100.0 * eye_w))
            y_centre = int(round(self._ae_y_pct.get() / 100.0 * h_px))
            so = self._collect_style_overrides()
            params = dict(
                font         = so.get('font', g['font']),
                font_size    = so.get('font_size', g['font_size']),
                primary_colour = so.get('primary_colour', g['primary_colour']),
                outline_colour = so.get('outline_colour', g['outline_colour']),
                back_colour    = so.get('back_colour',    g['back_colour']),
                outline      = so.get('outline', g['outline']),
                shadow       = so.get('shadow',  g['shadow']),
                shadow_x     = g.get('shadow_x', g['shadow']),
                shadow_y     = g.get('shadow_y', g['shadow']),
                depth        = depth,
                x_centre     = x_centre,
                y_centre     = y_centre,
                had_italics  = False,
                raw_text     = cue['text'],
            )
            stage = render_stereo_preview(left, right, params, cw, ch, 0)
            self._ass_fs_imgtk = ImageTk.PhotoImage(stage)
            canvas.delete("all")
            canvas.create_image(0, 0, image=self._ass_fs_imgtk, anchor="nw")

        canvas.bind("<Configure>", _render_fs)
        win.bind("<Escape>", lambda _: win.destroy())
        win.after(100, _render_fs)

    # ── Load NF3D track directly from MKV ────────────────────────────────────

    def _load_nf3d_track(self):
        """
        Extract the currently selected ASS track from the loaded MKV and open
        it directly in the edit tab — bypasses the subtitle preparation pipeline
        entirely when working with a previously processed NF3D file.
        """
        mkv = self.var_mkv.get().strip()
        if not mkv or not os.path.isfile(mkv):
            messagebox.showwarning("No MKV", "Load an MKV file first."); return

        tr = self._track_by_label.get(self.var_track.get())
        if not tr:
            messagebox.showwarning("No track", "Select a subtitle track first."); return

        codec = (tr.get("codec_id") or "").upper()
        if not ("ASS" in codec or "SSA" in codec or "SUBSTATIONALPHA" in codec):
            messagebox.showwarning(
                "Not an ASS track",
                f"Selected track codec is '{codec}'.\n"
                "Only ASS/SSA tracks can be opened directly.\n"
                "For PGS/SRT tracks, use Prepare subtitle first."); return

        tid  = tr["id"]
        mkvx = self.var_mkvextract.get().strip() or "mkvextract"
        ws   = self.var_workspace.get().strip()
        os.makedirs(ws, exist_ok=True)
        ext  = ".ssa" if "SSA" in codec and "ASS" not in codec else ".ass"
        dest = os.path.join(ws, f"track_{tid}_extracted{ext}")

        self._log(f"Extracting ASS track {tid} from MKV…")
        self.lbl_ass_path.config(text="Extracting track — please wait…")
        self.nb.select(self.tab_edit)
        threading.Thread(
            target=self._load_nf3d_track_bg,
            args=(mkvx, mkv, tid, dest),
            daemon=True).start()

    def _load_nf3d_track_bg(self, mkvx, mkv, tid, dest):
        """Background thread: mkvextract then parse ASS."""
        rc, out = self._run_cmd([mkvx, "tracks", mkv, f"{tid}:{dest}"])
        if rc != 0 or not os.path.isfile(dest):
            self.after(0, lambda: messagebox.showerror(
                "Extraction failed", f"mkvextract failed:\n{out}"))
            self.after(0, lambda: self.lbl_ass_path.config(text="Extraction failed"))
            return
        self._log(f"Track extracted: {dest}")
        # Now parse — this also runs in background via _open_ass_bg
        self.after(0, lambda: self._open_ass(dest))
        self.after(0, lambda: self._set_step("file",    "done", Path(mkv).name))
        self.after(0, lambda: self._set_step("prepare", "done", f"track {tid} extracted"))

    # ── Emergence popup ────────────────────────────────────────────────────────

    def _ass_reset_cue(self):
        """
        Reset the current cue's panel values to the original depth/position
        as parsed from the ASS file, and clear all style overrides.
        Does NOT write to the ASS — call Apply edit to commit.
        """
        cue = self._current_ass_cue()
        if not cue: return
        g     = self._globals()
        eye_w = self._safe_int(self.var_eye_w, 960)
        h_px  = self._safe_int(self.var_h, 1080)

        orig_d = cue.get('original_depth',    cue['depth'])
        orig_x = cue.get('original_x_centre', cue['x_centre'])
        orig_y = cue.get('original_y_centre', cue['y_centre'])

        self._ae_depth.set(orig_d)
        self._ae_x_pct.set(round(orig_x / eye_w * 100.0, 1))
        self._ae_y_pct.set(round(orig_y / h_px  * 100.0, 1))

        # Clear all style overrides
        for en in [self._ae_primary_en, self._ae_outline_en, self._ae_back_en,
                   self._ae_fsize_en,  self._ae_outl_en,    self._ae_shad_en,
                   self._ae_font_en]:
            en.set(False)
        self._ae_raw.delete("1.0", "end")

        # Reset colour widgets to ASS header defaults
        hdr = getattr(self, '_ass_header_colours', {})
        if 'primary_colour' in hdr: self._ae_primary.set(hdr['primary_colour'])
        if 'outline_colour' in hdr: self._ae_outline_col.set(hdr['outline_colour'])
        if 'back_colour'    in hdr: self._ae_back_col.set(hdr['back_colour'])

        # Clear stored override so the cue shows clean on next load
        cue.pop('style_overrides', None)
        # Also clear the project override so that re-running the conversion
        # returns this cue to auto-computed depth rather than stale values.
        self.project.clear_override(cue['index'])

        self.lbl_ass_status.config(text="Reset to original (not yet applied)")
        self._render_ass_preview()

    def _ass_emergence_popup(self):
        """
        Popup window for editing emergence values for the current cue.
        These are applied when 'Apply edit' is clicked, alongside the
        other panel values.
        """
        win = tk.Toplevel(self)
        win.title("Emergence settings for this cue")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        p = ttk.Frame(win, padding=16); p.pack(fill="both", expand=True)
        p.columnconfigure(1, weight=1); p.columnconfigure(3, weight=1)

        def spin(r, c, label, var, lo, hi, inc=1):
            ttk.Label(p, text=label).grid(row=r, column=c, sticky="w", padx=8, pady=5)
            ttk.Spinbox(p, from_=lo, to=hi, increment=inc, textvariable=var,
                        width=9).grid(row=r, column=c+1, sticky="w", padx=4, pady=5)

        ttk.Label(p, text="Override emergence for this cue only.\n"
                           "Leave at -1 to use global settings.",
                  font=("",8,"italic"), foreground="#555").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0,8))

        spin(1, 0, "Fade in (ms)",      self._ae_emerge_in,  -1, 5000, 10)
        spin(1, 2, "Fade out (ms)",     self._ae_emerge_out, -1, 5000, 10)
        spin(2, 0, "Start opacity (%)", self._ae_emerge_op,  -1, 100,   5)
        spin(2, 2, "Entry motion (ms)", self._ae_emerge_mot, -1, 5000, 10)
        spin(3, 0, "Depth offset",      self._ae_emerge_doff,-1, 20)

        # Test mode — only available per-cue for special effect purposes
        ttk.Separator(p, orient="horizontal").grid(
            row=4, column=0, columnspan=4, sticky="we", pady=(12,4))
        test_frame = ttk.Frame(p); test_frame.grid(row=5, column=0, columnspan=4, sticky="w")
        self._ae_test_mode  = getattr(self, "_ae_test_mode",  tk.BooleanVar(value=False))
        self._ae_test_scale = getattr(self, "_ae_test_scale", tk.DoubleVar(value=3.0))
        cb_test = ttk.Checkbutton(test_frame, text="Test/effect mode",
                                  variable=self._ae_test_mode)
        cb_test.pack(side="left", padx=(0,8))
        tip(cb_test,
            "Exaggerates depth by the Scale factor for this cue only.\n\n"
            "Primary use: creates an ethereal or dreamlike floating effect "
            "when applied selectively to cues like title cards, song lyrics, "
            "or stylised sequences.\n\n"
            "Think carefully before enabling — exaggerated depth on ordinary "
            "dialogue looks wrong and can cause eye strain. It works best "
            "when used sparingly on a handful of intentionally stylised cues.")
        ttk.Label(test_frame, text="Scale:").pack(side="left", padx=(0,4))
        ttk.Spinbox(test_frame, from_=1.0, to=8.0, increment=0.5,
                    textvariable=self._ae_test_scale, width=7).pack(side="left")

        def reset():
            for v in [self._ae_emerge_in, self._ae_emerge_out,
                      self._ae_emerge_op, self._ae_emerge_mot,
                      self._ae_emerge_doff]:
                v.set(-1)
            self._ae_test_mode.set(False)
            self._ae_test_scale.set(3.0)

        btn = ttk.Frame(p); btn.grid(row=6, column=0, columnspan=4, pady=(12,0))
        ttk.Button(btn, text="Use global defaults",
                   command=reset).pack(side="left", padx=(0,8))
        ttk.Button(btn, text="Close",
                   command=win.destroy).pack(side="left")


    # ── Create base ASS (Convert tab) ─────────────────────────────────────────

    def _create_base_ass(self):
        """
        Run the ASS conversion (no MKV mux) and immediately load the result
        into the Edit cues tab.  This is the natural entry point for ASS-based
        editing.
        """
        threading.Thread(target=self._create_base_ass_bg, daemon=True).start()

    def _create_base_ass_bg(self):
        try:
            save_config(self._current_cfg())
            ws = self.var_workspace.get().strip()
            os.makedirs(ws, exist_ok=True)
            stereo = os.path.join(ws, "stereo"); os.makedirs(stereo, exist_ok=True)

            srt = self._effective_srt()
            if not srt or not os.path.isfile(srt):
                srt = self._prepare_impl()
                self.prepared_srt = srt
                self.project.srt_path = srt

            # Use project title if set, otherwise MKV stem
            title = self.var_project_title.get().strip()
            if not title:
                title = Path(self.var_mkv.get().strip() or srt).stem
            # Sanitise for use as a filename
            import re as _re
            safe_title = _re.sub(r'[<>:"/\\|?*]', '_', title).strip()
            # Save to workspace/NF3D_Subtitles/ (dedicated ASS output folder)
            ass_dir = Path(ws) / "NF3D_Subtitles"
            ass_dir.mkdir(parents=True, exist_ok=True)
            ass_out = ass_dir / (safe_title + "_NF3D.ass")
            self._log(f"Creating base ASS: {ass_out}")

            def prog(i, total, label=""):
                self._log(f"  [{i}/{total}] {label}")

            n = convert_to_stereo_ass(
                Path(srt), ass_out,
                self.project, self._globals(), progress_cb=prog)
            self._log(f"Base ASS created: {n} cues → {ass_out}")
            self._set_step("export", "running", "opening for editing…")

            # Load into editor on main thread
            self.after(0, lambda: self._open_ass(str(ass_out)))
            self.after(200, lambda: self._set_step("export", "done", ass_out.name))
        except Exception as e:
            self._log(f"ERROR creating base ASS: {e}")
            messagebox.showerror("Error", str(e))

    # ── Style traces & helpers ────────────────────────────────────────────────

    def _bind_style_traces(self):
        style_vars = [
            self.var_font_size, self.var_outline, self.var_shadow,
            self.var_shadow_x, self.var_shadow_y, self.var_sample_bg,
        ]
        for var in style_vars:
            var.trace_add("write", lambda *_: self._style_changed())

    def _warmup_spellchecker(self):
        try:
            from nf3d_core import _get_spellchecker
            _get_spellchecker()
        except (ImportError, Exception):
            logger.warning("_warmup_spellchecker: could not pre-warm spellchecker")

    # ─── Advanced tab ─────────────────────────────────────────────────────────

    def _build_advanced_tab(self):
        p = self.tab_advanced
        nb2 = ttk.Notebook(p); nb2.pack(fill="both", expand=True, padx=8, pady=8)

        t_tools = ttk.Frame(nb2); nb2.add(t_tools, text="Tools & paths")
        t_depth = ttk.Frame(nb2); nb2.add(t_depth, text="Depth parameters")
        t_fonts = ttk.Frame(nb2); nb2.add(t_fonts, text="Font lists")

        self._build_adv_tools(t_tools)
        self._build_adv_depth(t_depth)
        self._build_adv_fonts(t_fonts)

    def _build_adv_tools(self, p):
        p.columnconfigure(1, weight=1)
        def row(r, label, var, ftypes=None):
            ttk.Label(p, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=5)
            ttk.Entry(p, textvariable=var).grid(row=r, column=1, sticky="we", padx=8, pady=5)
            ttk.Button(p, text="Browse",
                command=lambda v=var, ft=ftypes: self._browse_file(v, ft or [("All","*.*")])).grid(
                row=r, column=2, padx=8, pady=5)
        row(0, "mkvmerge",      self.var_mkvmerge,     [("Exe","*.exe"),("All","*.*")])
        row(1, "mkvextract",    self.var_mkvextract,   [("Exe","*.exe"),("All","*.*")])
        se_lbl = ttk.Label(p, text="Subtitle Edit")
        se_lbl.grid(row=2, column=0, sticky="w", padx=8, pady=5)
        tip(se_lbl,
            "Path to SubtitleEdit.exe — required for PGS/VobSub OCR and ASS→SRT conversion.")
        ttk.Entry(p, textvariable=self.var_subtitleedit).grid(
            row=2, column=1, sticky="we", padx=8, pady=5)
        ttk.Button(p, text="Browse", command=lambda: self._browse_file(
            self.var_subtitleedit, [("Exe","*.exe"),("All","*.*")])).grid(
            row=2, column=2, padx=8, pady=5)
        row(3, "ffmpeg",        self.var_ffmpeg,       [("Exe","*.exe"),("All","*.*")])

        ttk.Label(p, text="Workspace").grid(row=4, column=0, sticky="w", padx=8, pady=5)
        ttk.Entry(p, textvariable=self.var_workspace).grid(
            row=4, column=1, sticky="we", padx=8, pady=5)
        ttk.Button(p, text="Browse", command=lambda: (
            d := filedialog.askdirectory()) and self.var_workspace.set(d)).grid(
            row=4, column=2, padx=8, pady=5)

        ttk.Separator(p, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="we", padx=8, pady=8)
        ttk.Label(p, text="Debug JSON path (saves depth map JSON after each run):").grid(
            row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(4,2))
        ttk.Entry(p, textvariable=self.var_debug_json).grid(
            row=7, column=0, columnspan=2, sticky="we", padx=8, pady=(0,4))
        ttk.Button(p, text="Browse", command=lambda: self._browse_save(
            self.var_debug_json, [("JSON","*.json")])).grid(row=7, column=2, padx=8)
        ttk.Separator(p, orient="horizontal").grid(
            row=8, column=0, columnspan=3, sticky="we", padx=8, pady=8)

        ttk.Button(p, text="Save all settings",
                   command=self._save_all).grid(row=9, column=0, padx=8, pady=4)

    def _build_adv_depth(self, p):
        p.columnconfigure(1, weight=1); p.columnconfigure(3, weight=1)
        _depth_tips = {
            "Single-eye width (px)": (
                "Width of one eye's image in pixels. For full-SBS 1080p this is 960; "
                "for full-SBS 4K this is 1920. Auto-filled from the MKV when you browse.\n"
                "Getting this wrong will misplace every subtitle horizontally."),
            "Frame height (px)": (
                "Full frame height in pixels. 1080 for 1080p, 2160 for 4K.\n"
                "Auto-filled from the MKV. Affects vertical subtitle placement."),
            "Base depth": (
                "Fallback depth used when no video analysis data is available.\n"
                "NEGATIVE = in FRONT of the screen (crossed disparity, comfortable). "
                "–6 places subtitles clearly in front of the picture plane, which is "
                "the standard comfortable position — text floats ahead of the image.\n"
                "POSITIVE = behind the screen (avoid for subtitles — causes eye strain)."),
            "CAPS depth": (
                "Fallback depth for ALL-CAPS cues (title cards, shouting, emphasis).\n"
                "Typically set slightly more negative than base (further in front) "
                "so bold text doesn't visually compete with foreground objects."),
            "Italics depth": (
                "Fallback depth for italicised text (off-screen voices, thoughts).\n"
                "Slightly less negative than base — a subtle distinction that "
                "reflects the indirect nature of off-screen dialogue."),
            "Internal offset": (
                "Shift applied to the raw SGBM disparity measurement before mapping.\n"
                "Increase if subtitles are consistently placed too close to the screen; "
                "decrease if they are pushed too far in front.\n"
                "Leave at default (8) unless you have a specific tuning need."),
            "Internal limit": (
                "Maximum raw SGBM disparity accepted as valid. Values above this are "
                "treated as measurement errors (lens flare, screen edges) and discarded.\n"
                "Lower this if analysis is being thrown off by bright artefacts."),
            "Output min": (
                "Most negative output depth allowed, in pixels of parallax.\n"
                "–9 means subtitles can be placed at most 9px in front of the screen. "
                "More negative = further in front, which is generally fine and comfortable. "
                "Very large values (–15+) may cause discomfort on small screens."),
            "Output max": (
                "Most positive output depth allowed.\n"
                "POSITIVE values place subtitles BEHIND the screen — avoid where possible. "
                "A max of 2 means subtitles will drift at most 2px behind the screen plane "
                "for very deep background scenes. Keep this low (0–2)."),
            "Output bias": (
                "Constant offset added to every depth value after scaling.\n"
                "–3 pulls all subtitles 3px in front of where the raw analysis places them. "
                "This is the primary control for ensuring subtitles sit comfortably in front "
                "of the screen rather than blending into the image background.\n"
                "Make more negative to push subtitles further forward; towards 0 to let "
                "them sit closer to the measured scene depth."),
            "Output scale": (
                "Multiplier applied to normalised disparity before adding the bias.\n"
                "Higher = subtitles spread across more of the allowed depth range "
                "(more dynamic — deep scenes go deep, shallow scenes stay shallow).\n"
                "Lower = all subtitles cluster near the bias value (more uniform).\n"
                "2.3 gives good dynamic range while keeping extremes comfortable."),
        }

        def spin(r, c, label, var, lo, hi, inc=1):
            lbl = ttk.Label(p, text=label)
            lbl.grid(row=r, column=c, sticky="w", padx=8, pady=5)
            sb = ttk.Spinbox(p, from_=lo, to=hi, increment=inc, textvariable=var, width=10)
            sb.grid(row=r, column=c+1, sticky="w", padx=8, pady=5)
            if label in _depth_tips:
                tip(lbl, _depth_tips[label])
                tip(sb,  _depth_tips[label])

        ttk.Label(p, text="Video dimensions (auto-filled from MKV when possible)",
                  font=("",8,"italic")).grid(row=0, column=0, columnspan=4,
                  sticky="w", padx=8, pady=(8,2))
        spin(1, 0, "Single-eye width (px)", self.var_eye_w, 320, 3840)
        spin(1, 2, "Frame height (px)",     self.var_h,     240, 2160)

        ttk.Separator(p, orient="horizontal").grid(
            row=2, column=0, columnspan=4, sticky="we", padx=8, pady=8)
        ttk.Label(p, text="Fallback depth (when no video analysis)",
                  font=("",8,"italic")).grid(row=3, column=0, columnspan=4,
                  sticky="w", padx=8, pady=(0,2))
        spin(4, 0, "Base depth",    self.var_base_depth,    0, 20)
        spin(4, 2, "CAPS depth",    self.var_caps_depth,    0, 20)
        spin(5, 0, "Italics depth", self.var_italics_depth, 0, 20)

        ttk.Separator(p, orient="horizontal").grid(
            row=6, column=0, columnspan=4, sticky="we", padx=8, pady=8)
        ttk.Label(p, text="Depth analysis tuning",
                  font=("",8,"italic")).grid(row=7, column=0, columnspan=4,
                  sticky="w", padx=8, pady=(0,2))
        spin(8,  0, "Internal offset", self.var_offset_internal, -50, 50)
        spin(8,  2, "Internal limit",  self.var_internal_limit,   20, 300)
        spin(9,  0, "Output min",      self.var_out_min,          -20,  0)
        spin(9,  2, "Output max",      self.var_out_max,            0, 20)
        spin(10, 0, "Output bias",     self.var_output_bias,      -10, 10)
        spin(10, 2, "Output scale",    self.var_output_scale,     0.1, 4.0, 0.1)

        ttk.Button(p, text="Reset depth settings to defaults",
                   command=self._reset_depth).grid(
            row=11, column=0, columnspan=2, sticky="w", padx=8, pady=12)

        cb_deov = ttk.Checkbutton(p, text="Auto-correct overlapping cues",
                                   variable=self.var_deoverlap)
        cb_deov.grid(row=12, column=0, columnspan=4, sticky="w", padx=8, pady=(0,8))
        tip(cb_deov,
            "When ticked, cues whose start time overlaps the previous cue's end time\n"
            "are automatically shortened before depth analysis and export.\n"
            "Untick if you want to handle overlapping timing yourself in the Edit tab.")

    def _build_adv_fonts(self, p):
        p.columnconfigure(0, weight=1); p.columnconfigure(1, weight=1)

        ttk.Label(p, text="Recommended fonts (shown first in picker):").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8,2))
        ttk.Label(p, text="Specialist fonts (shown after separator):").grid(
            row=0, column=1, sticky="w", padx=8, pady=(8,2))

        lf1 = ttk.Frame(p); lf1.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        lf1.rowconfigure(0, weight=1)
        self._rec_listbox = tk.Listbox(lf1, height=12, font=("Arial",10))
        self._rec_listbox.pack(fill="both", expand=True)

        lf2 = ttk.Frame(p); lf2.grid(row=1, column=1, sticky="nsew", padx=8, pady=4)
        lf2.rowconfigure(0, weight=1)
        self._spec_listbox = tk.Listbox(lf2, height=12, font=("Arial",10))
        self._spec_listbox.pack(fill="both", expand=True)

        p.rowconfigure(1, weight=1)

        for name in self._recommended_fonts: self._rec_listbox.insert("end", name)
        for name in self._specialist_fonts:  self._spec_listbox.insert("end", name)

        _all_fonts = sorted(set(tkfont.families()))

        btn1 = ttk.Frame(p); btn1.grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self._adv_font_entry1 = ttk.Combobox(btn1, values=_all_fonts, width=22)
        self._adv_font_entry1.pack(side="left", padx=(0,4))
        ttk.Button(btn1, text="Add to recommended",
                   command=lambda: self._adv_add_font(self._rec_listbox,
                       self._adv_font_entry1, self._recommended_fonts)).pack(side="left", padx=(0,4))
        ttk.Button(btn1, text="Remove selected",
                   command=lambda: self._adv_remove_font(self._rec_listbox,
                       self._recommended_fonts)).pack(side="left")

        btn2 = ttk.Frame(p); btn2.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        self._adv_font_entry2 = ttk.Combobox(btn2, values=_all_fonts, width=22)
        self._adv_font_entry2.pack(side="left", padx=(0,4))
        ttk.Button(btn2, text="Add to specialist",
                   command=lambda: self._adv_add_font(self._spec_listbox,
                       self._adv_font_entry2, self._specialist_fonts)).pack(side="left", padx=(0,4))
        ttk.Button(btn2, text="Remove selected",
                   command=lambda: self._adv_remove_font(self._spec_listbox,
                       self._specialist_fonts)).pack(side="left")

        ttk.Button(p, text="Apply font lists to pickers",
                   command=self._apply_font_lists).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(4,8))


    def _style_changed(self):
        self._render_style_sample()
        self._render_preview()
        self._render_fullscreen()

    def _on_font_change(self, name: str):
        self._style_changed()

    def _render_style_sample(self):
        if not hasattr(self, "style_canvas"): return
        mode = self.var_sample_bg.get().strip().lower()
        bg   = (220,220,220) if mode=="light" else (32,32,32)
        img  = Image.new("RGB", (240,54), bg)
        if mode == "checkerboard":
            d = ImageDraw.Draw(img)
            for yy in range(0,54,14):
                for xx in range(0,240,14):
                    if ((xx//14)+(yy//14))%2==0: d.rectangle([xx,yy,xx+13,yy+13],fill=(120,120,120))
        g    = self._globals()
        sz   = max(10, min(int(g["font_size"]*0.42), 26))
        fnt  = load_pil_font(g["font"], sz)
        d    = ImageDraw.Draw(img)
        kw = dict(
            outl_fill = ass_to_rgb(g["outline_colour"], (0,0,0)),
            outl_px   = max(0, int(float(g["outline"]))),
            shad_px   = max(0, int(float(g["shadow"]))),
            shad_fill = ass_to_rgb(g["back_colour"], (40,40,40)),
            shad_dx   = int(g.get("shadow_x", g["shadow"])),
            shad_dy   = int(g.get("shadow_y", g["shadow"])),
            anchor    = "center",
        )
        fill = ass_to_rgb(g["primary_colour"])
        draw_subtitle(d, 120, 27, "NF3D Sample", fnt, fill, **kw)
        self._sample_img = ImageTk.PhotoImage(img)
        self.style_canvas.delete("all")
        self.style_canvas.create_image(0,0,image=self._sample_img,anchor="nw")

    def _save_style(self):
        save_config(self._current_cfg())

    # ── File helpers ──────────────────────────────────────────────────────────

    def _browse_file(self, var, ftypes):
        p = filedialog.askopenfilename(filetypes=ftypes)
        if p: var.set(p)

    def _browse_save(self, var, ftypes):
        p = filedialog.asksaveasfilename(filetypes=ftypes, confirmoverwrite=False)
        if p: var.set(p)

    def _browse_mkv(self):
        p = filedialog.askopenfilename(
            filetypes=[("Video","*.mkv *.mp4"),("All","*.*")])
        if not p: return
        self.var_mkv.set(p)
        self.var_external_sub.set("")   # clear stale external subtitle
        # Reset all steps — new video, fresh start
        for _k in ("prepare", "depth", "export"):
            self._set_step(_k, "idle", "")
        self.prepared_srt = ""          # clear so Style tab shows sample cue
        self._load_cue_list()           # revert combobox to sample cue immediately
        self.lbl_status.config(text="Open a file to begin.")
        self._set_step("file", "done", Path(p).name)
        # Derive project title from filename
        self.var_project_title.set("")
        stem = Path(p).stem
        import re as _re
        clean = _re.sub(
            r'[\s._-]*(1080[pP]|720[pP]|2160[pP]|4[kK]|'
            r'[Bb]oth|[Ss]bs|[Ll]rf|[Ff]ull|[Vv]\d+[\d.]*|'
            r'NF3D|HEVC|x265|x264|BluRay|WEB-?DL).*$', '', stem).strip()
        if clean:
            self.var_project_title.set(clean)
        self._refresh_tracks()
        mkvmerge = self.var_mkvmerge.get().strip() or "mkvmerge"
        def _get_info(mv=mkvmerge, pv=p):
            info = get_video_info(mv, pv)
            if not info:
                return
            import re as _re2
            is_hsbs = bool(_re2.search(r'(?i)\bh[_-]?sbs\b|half[_-]?sbs|hsbs', Path(pv).name))
            eye_w = info["width"] // 2 if is_hsbs else info["eye_w"]
            mode  = "FSBS" if info["is_fsbs"] else ("HSBS" if is_hsbs else "standard")
            msg   = f"Video info: {info['width']}\u00d7{info['height']} ({mode}, eye_w={eye_w})"
            dur   = info.get("duration_s", 0.0)
            def _apply(h=info["height"], hsbs=is_hsbs, ew=eye_w, m=msg, d=dur, v=pv):
                self.var_h.set(h)
                self.var_hsbs.set(hsbs)
                self.var_eye_w.set(ew)
                self._log(m)
                # Auto-extract style-preview frame at 25% of duration
                ffmpeg_path = self.var_ffmpeg.get().strip() or detect_ffmpeg()
                if ffmpeg_path and d > 30:
                    threading.Thread(
                        target=self._auto_extract_style_frame,
                        args=(v, ffmpeg_path, d * 0.25),
                        daemon=True,
                    ).start()
            self.after(0, _apply)
        threading.Thread(target=_get_info, daemon=True).start()

    def _auto_extract_style_frame(self, video: str, ffmpeg: str, ts: float):
        """Background: extract frame at *ts* seconds and show in Style tab preview."""
        try:
            td  = Path(tempfile.gettempdir()) / "nf3d_preview"
            td.mkdir(parents=True, exist_ok=True)
            out = td / "auto_style_frame.png"
            pr  = subprocess.run(
                [ffmpeg, "-y", "-ss", str(int(ts)), "-i", video,
                 "-frames:v", "1", str(out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                **_popen_kwargs())
            if pr.returncode != 0 or not out.is_file():
                return
            img  = Image.open(str(out)).convert("RGB")
            left, right = split_sbs(img)
            def _apply():
                self.preview_left  = left
                self.preview_right = right
                self._cache_preview_thumbnails()
                self._render_preview()
                self._log("Style preview: auto-extracted frame at 25% of duration.")
            self.after(0, _apply)
        except (OSError, subprocess.SubprocessError):
            logger.warning("_auto_extract_style_frame: could not extract or open frame")

    def _on_hsbs_toggle(self):
        """Adjust eye_w when the HSBS checkbox is toggled manually."""
        mkvmerge = self.var_mkvmerge.get().strip() or "mkvmerge"
        p = self.var_mkv.get().strip()
        if not p or not os.path.isfile(p):
            return
        hsbs = self.var_hsbs.get()
        def _bg(mv=mkvmerge, pv=p, h=hsbs):
            info = get_video_info(mv, pv)
            if not info:
                return
            ew = info["width"] // 2 if h else info["eye_w"]
            self.after(0, lambda: self.var_eye_w.set(ew))
        threading.Thread(target=_bg, daemon=True).start()

    def _pick_colour(self, var):
        try: r, g, b = ass_to_rgb(var.get()); init = f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, AttributeError): init = "#e6e6e6"
        rgb, _ = colorchooser.askcolor(color=init)
        if rgb:
            var.set(rgb_to_ass(int(rgb[0]), int(rgb[1]), int(rgb[2])))

    # ── Track listing ─────────────────────────────────────────────────────────

    def _refresh_tracks(self):
        mkv = self.var_mkv.get().strip()
        if not mkv or not os.path.isfile(mkv):
            self.cmb_tracks["values"] = ["(load MKV)"]
            self.var_track.set("(load MKV)")
            return
        if not mkv.lower().endswith(".mkv"):
            self.cmb_tracks["values"] = ["(use external subtitle)"]
            return
        mkvmerge = self.var_mkvmerge.get().strip() or "mkvmerge"
        def _bg():
            try:
                tracks = mkv_list_sub_tracks(mkvmerge, mkv)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Track listing failed", str(e)))
                return
            self._track_by_label = {}
            labels = []
            for tr in tracks:
                lab = format_track_label(tr)
                labels.append(lab)
                self._track_by_label[lab] = tr
            labels = labels or ["(no subtitle tracks found)"]
            def _update(labs=labels):
                self.cmb_tracks["values"] = labs
                self.var_track.set(labs[0])
            self.after(0, _update)
        threading.Thread(target=_bg, daemon=True).start()

    # ── Subtitle preparation ───────────────────────────────────────────────────

    def _prepare_threaded(self):
        threading.Thread(target=self._prepare, daemon=True).start()

    def _prepare(self):
        try:
            srt = self._prepare_impl()
            self.prepared_srt = srt
            self.project.srt_path = srt
            self.lbl_status.config(text=f"Ready: {Path(srt).name}")
            self._set_step("prepare", "done", Path(srt).name)
            self._load_cue_list()
            self._open_issue_reviewer_auto(srt)
        except Exception as e:
            self._log(f"ERROR: {e}"); messagebox.showerror("Error", str(e))

    def _open_issue_reviewer_auto(self, srt: str):
        from nf3d_core import scan_text_issues, parse_srt
        events = parse_srt(Path(srt))
        has_any = any(
            scan_text_issues(ev["text"], self.session_dict, self.persistent_dict)
            for ev in events
        )
        if not has_any:
            self.after(0, lambda: messagebox.showinfo(
                "Prepared", f"No issues found.\nSubtitle ready:\n{srt}"))
            return
        done = threading.Event()
        def _open():
            try:
                w = IssueReviewWindow(self, srt, self.session_dict,
                                      self.persistent_dict, app=self)
                self.wait_window(w)
            finally:
                done.set()
        self.after(0, _open)
        done.wait()
        self._load_cue_list()
        messagebox.showinfo("Prepared", f"Subtitle ready:\n{srt}")

    def _open_issue_reviewer(self):
        if not self.prepared_srt or not Path(self.prepared_srt).is_file():
            messagebox.showwarning("No subtitle", "Prepare a subtitle first."); return
        IssueReviewWindow(self, self.prepared_srt, self.session_dict,
                          self.persistent_dict, app=self)

    def _apply_srt_offset(self, srt_path: str, offset_ms: int, out_dir: str) -> str:
        """Shift all cue timings in srt_path by offset_ms, write to out_dir, return new path."""
        events = parse_srt(Path(srt_path))
        out = os.path.join(out_dir, "sub_shifted.srt")
        with open(out, "w", encoding="utf-8") as f:
            for ev in events:
                s = ms_to_srt_time(srt_time_to_ms(ev["start"]) + offset_ms)
                e = ms_to_srt_time(srt_time_to_ms(ev["end"])   + offset_ms)
                f.write(f"{ev['index']}\n{s} --> {e}\n{ev['text']}\n\n")
        self._log(f"Timing offset {offset_ms:+d} ms applied → sub_shifted.srt")
        return out

    def _prepare_impl(self) -> str:
        mkv = self.var_mkv.get().strip()
        ext = self.var_external_sub.get().strip()
        ws  = self.var_workspace.get().strip()
        os.makedirs(ws, exist_ok=True)
        demux = os.path.join(ws,"demux"); os.makedirs(demux, exist_ok=True)
        ocr   = os.path.join(ws,"ocr");   os.makedirs(ocr,   exist_ok=True)
        if ext and os.path.isfile(ext):
            ext_l = ext.lower()
            if ext_l.endswith(".srt"):
                result_srt = ext
            else:
                se_path = self.var_subtitleedit.get().strip()
                if not se_path or not os.path.isfile(se_path):
                    raise RuntimeError(
                        "Subtitle Edit not found. Set its path in Advanced > Tools & paths.")
                result_srt = self._run_se_convert(se_path, ext, ocr)
        else:
            if not mkv or not os.path.isfile(mkv):
                raise RuntimeError("Choose a valid MKV or provide an external subtitle.")
            if not mkv.lower().endswith(".mkv"):
                raise RuntimeError("Embedded extraction supports MKV only.")
            tr = self._track_by_label.get(self.var_track.get())
            if not tr: raise RuntimeError("Select a valid subtitle track.")
            codec = (tr["codec_id"] or "").upper(); tid = tr["id"]
            mkvx  = self.var_mkvextract.get().strip() or "mkvextract"
            se    = self.var_subtitleedit.get().strip()
            if "PGS" in codec or "VOBSUB" in codec or "HDMV" in codec:
                sup = os.path.join(demux, f"sub_{tid}.sup")
                rc, out = self._run_cmd([mkvx, "tracks", mkv, f"{tid}:{sup}"])
                if rc != 0 or not os.path.isfile(sup):
                    raise RuntimeError(f"mkvextract failed (rc={rc}):\n{out[-800:]}")
                if not se or not os.path.isfile(se):
                    raise RuntimeError(
                        "Subtitle Edit not found. Set its path in Advanced > Tools & paths.")
                result_srt = self._run_se_convert(se, sup, ocr)
            elif "UTF8" in codec or "SUBRIP" in codec or "S_TEXT" in codec:
                srt = os.path.join(ocr, f"sub_{tid}.srt")
                rc, out = self._run_cmd([mkvx,"tracks",mkv,f"{tid}:{srt}"])
                if rc != 0 or not os.path.isfile(srt):
                    raise RuntimeError(f"mkvextract failed (rc={rc}):\n{out[-800:]}")
                result_srt = srt
            elif "ASS" in codec or "SSA" in codec or "SUBSTATIONALPHA" in codec:
                ext2 = ".ssa" if "SSA" in codec else ".ass"
                af   = os.path.join(demux, f"sub_{tid}{ext2}")
                rc, out = self._run_cmd([mkvx,"tracks",mkv,f"{tid}:{af}"])
                if rc != 0 or not os.path.isfile(af):
                    raise RuntimeError(f"mkvextract failed (rc={rc}):\n{out[-800:]}")
                result_srt = self._run_se_convert(se, af, ocr)
            else:
                raise RuntimeError(f"Unsupported codec: {codec}")
        offset_ms = self.var_sub_offset_ms.get()
        if offset_ms != 0:
            result_srt = self._apply_srt_offset(result_srt, offset_ms, ocr)
        return result_srt

    def _newest_srt(self, folder: str) -> str:
        srts = [os.path.join(folder, f) for f in os.listdir(folder)
                if f.lower().endswith(".srt")]
        if not srts: raise RuntimeError("No SRT produced.")
        srts.sort(key=os.path.getmtime, reverse=True); return srts[0]

    def _run_se_convert(self, se_path: str, input_file: str, out_folder: str) -> str:
        """Run Subtitle Edit /convert hidden; return expected SRT path, raise on failure."""
        se_args  = ["/convert", input_file, "srt", f"/outputfolder:{out_folder}", "/overwrite"]
        expected = os.path.join(out_folder, Path(input_file).stem + ".srt")
        self._log("RUN: " + " ".join([se_path] + se_args))
        if sys.platform == "win32":
            rc = self._se_run_win32(se_path, se_args)
        else:
            rc, out = self._run_cmd([se_path] + se_args)
            if rc != 0:
                raise RuntimeError(f"Subtitle Edit failed:\n{out}")
        if not os.path.isfile(expected):
            raise RuntimeError(
                f"Subtitle Edit produced no SRT output (exit {rc}).\n\n"
                "SE 5.x beta is known to ignore /convert for PGS/VobSub files.\n"
                "Install SE 4.x stable from:\n"
                "  https://github.com/SubtitleEdit/subtitleedit/releases\n"
                "(choose the latest tag without 'beta' in the name)")
        return expected

    def _se_run_win32(self, se_path: str, se_args: list) -> int:
        """Launch SE on Windows; suppress its window via ctypes and enforce a timeout."""
        import ctypes
        import ctypes.wintypes as wt
        import threading
        import time

        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE — respected by console apps; .NET 5+ may ignore it
        proc = subprocess.Popen(
            [se_path] + se_args,
            startupinfo=si,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        pid = proc.pid

        # Background thread: hide any window SE creates (handles SE 5.x which ignores SW_HIDE)
        stop = threading.Event()
        def _hide():
            u32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def _cb(hwnd, _):
                pid_buf = wt.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
                if pid_buf.value == pid and u32.IsWindowVisible(hwnd):
                    u32.ShowWindow(hwnd, 0)  # SW_HIDE
                return True
            cb = WNDENUMPROC(_cb)  # keep reference alive to prevent GC
            while not stop.is_set():
                u32.EnumWindows(cb, 0)
                time.sleep(0.05)
        threading.Thread(target=_hide, daemon=True).start()

        self._log("OCR started — Subtitle Edit is running in the background.")
        self._log("This may take 1–5 minutes for a feature-length movie. Please wait…")
        try:
            self.after(0, lambda: self.lbl_status.config(text="OCR in progress…"))
        except tk.TclError:
            pass

        warned          = False
        start           = time.monotonic()
        last_progress   = start
        PROGRESS_INTERVAL = 10.0  # seconds between status updates
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now     = time.monotonic()
            elapsed = now - start
            if now - last_progress >= PROGRESS_INTERVAL:
                self._log(f"OCR running… {elapsed:.0f}s elapsed")
                try:
                    self.after(0, lambda e=elapsed: self.lbl_status.config(
                        text=f"OCR in progress… {e:.0f}s"))
                except tk.TclError:
                    pass
                last_progress = now
            if not warned and elapsed > 20:
                self._log(
                    "Note: if OCR takes more than a few minutes, check that "
                    "SE 4.x stable is installed (SE 5.x beta does not support "
                    "headless PGS/VobSub OCR).")
                warned = True
            if elapsed > 600:  # 10-minute hard limit
                self._log("Subtitle Edit timed out — terminating.")
                proc.kill()
                rc = proc.wait()
                break
            time.sleep(0.5)

        stop.set()
        total = time.monotonic() - start
        self._log(f"Subtitle Edit finished after {total:.0f}s.")
        try:
            self.after(0, lambda: self.lbl_status.config(text="OCR complete — verifying…"))
        except tk.TclError:
            pass
        return rc

    def _run_cmd(self, cmd: list) -> tuple:
        self._log("RUN: " + " ".join(str(x) for x in cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             **_popen_kwargs())
        lines = []
        while True:
            line = p.stdout.readline()
            if not line and p.poll() is not None: break
            if line: lines.append(line.rstrip()); self._log(line.rstrip())
        return p.wait(), "\n".join(lines)

    # ── Depth analysis ────────────────────────────────────────────────────────

    def _update_rescan_button(self):
        """Enable 'Rescan missing cues' only when there are fallback entries."""
        if not hasattr(self, "btn_rescan_missing"): return
        n_fallback = sum(
            1 for v in self.project.depth_map.values() if v.get("fallback"))
        if n_fallback > 0:
            self.btn_rescan_missing.config(
                state="normal",
                text=f"Rescan {n_fallback} missing cue{'s' if n_fallback != 1 else ''}")
        else:
            self.btn_rescan_missing.config(
                state="disabled", text="Rescan missing cues")

    def _rescan_missing_threaded(self):
        threading.Thread(target=self._rescan_missing, daemon=True).start()

    def _rescan_missing(self):
        """Re-analyse only the cues that came back as fallback."""
        if not self.prepared_srt:
            messagebox.showwarning("No subtitle", "Prepare a subtitle first."); return
        video  = self.var_mkv.get().strip()
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        if not video or not os.path.isfile(video):
            messagebox.showwarning("No video", "Choose a valid video file."); return
        if not ffmpeg:
            messagebox.showwarning("No ffmpeg", "ffmpeg not found."); return

        events = self._deoverlap_events(parse_srt(Path(self.prepared_srt)))
        # Count exactly how many need rescanning before we start
        n_missing = sum(
            1 for ev in events
            if (str(ev["index"]) not in self.project.depth_map
                or self.project.depth_map[str(ev["index"])].get("fallback", True)))
        total_events = len(events)

        if n_missing == 0:
            messagebox.showinfo("Nothing to rescan",
                "All cues have valid depth data — nothing to rescan."); return

        self._log(f"Rescanning {n_missing} missing/fallback cues "
                  f"(of {total_events} total)…")
        self._set_step("depth", "running", f"rescanning 0 / {n_missing}…")
        self.after(0, lambda: self.btn_rescan_missing.config(state="disabled"))

        done_count = [0]
        def progress(i, t, key=""):
            done_count[0] = i
            if i % 5 == 0 or i == t:
                self._set_step("depth", "running",
                               f"rescanning {i} / {n_missing}…")
                self._log(f"  Rescan: {i} / {n_missing} cues")
                self.update_idletasks()

        try:
            updated_map = rescan_fallback_cues(
                video, ffmpeg, events,
                existing_depth_map=self.project.depth_map,
                offset_internal=self.var_offset_internal.get(),
                samples_per_cue=self.var_samples.get(),
                internal_limit=self.var_internal_limit.get(),
                out_min=self.var_out_min.get(), out_max=self.var_out_max.get(),
                output_bias=self.var_output_bias.get(),
                output_scale=self.var_output_scale.get(),
                progress_cb=progress)

            self.project.depth_map = updated_map
            n_good = sum(1 for v in updated_map.values() if not v.get("fallback"))
            n_still_missing = sum(1 for v in updated_map.values() if v.get("fallback"))
            self._set_step("depth", "done", f"{n_good}/{total_events} cues")
            self._log(f"Rescan complete: {n_good}/{total_events} cues now have "
                      f"depth data. {n_still_missing} still fallback.")
            self._update_rescan_button()
        except Exception as e:
            self._set_step("depth", "error", "rescan failed")
            self._log(f"Rescan error: {e}")
            self.after(0, lambda: self.btn_rescan_missing.config(state="normal"))

    def _deoverlap_events(self, events: list) -> list:
        """Conditionally deoverlap events per user setting, logging any changes."""
        if not self.var_deoverlap.get():
            return events
        fixed = deoverlap_events(events)
        n = sum(1 for a, b in zip(events, fixed) if a["end"] != b["end"])
        if n:
            self._log(f"Auto-corrected {n} overlapping cue(s) — "
                      "disable in Advanced > Depth Analysis if unwanted.")
        return fixed

    def _analyse_threaded(self):
        threading.Thread(target=self._analyse, daemon=True).start()

    def _analyse(self):
        if not self.prepared_srt:
            messagebox.showwarning("No subtitle","Prepare a subtitle first."); return
        video  = self.var_mkv.get().strip()
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        if not video or not os.path.isfile(video):
            messagebox.showwarning("No video","Choose a valid video file."); return
        if not ffmpeg:
            messagebox.showwarning("No ffmpeg","ffmpeg not found."); return
        events = self._deoverlap_events(parse_srt(Path(self.prepared_srt)))
        total  = len(events)
        self._set_step("depth", "running", f"0 / {total}\u2026")
        self._log(f"Depth analysis: starting {total} cues\u2026")
        def progress(i, t):
            if i % 10 == 0 or i == t:
                self._set_step("depth", "running", f"{i} / {t}")
                self._log(f"  Depth: {i} / {t} cues")
                self.update_idletasks()
        try:
            self.project.depth_map = analyse_cue_depths(
                video, ffmpeg, events,
                offset_internal=self.var_offset_internal.get(),
                samples_per_cue=self.var_samples.get(),
                internal_limit=self.var_internal_limit.get(),
                out_min=self.var_out_min.get(), out_max=self.var_out_max.get(),
                output_bias=self.var_output_bias.get(),
                output_scale=self.var_output_scale.get(),
                progress_cb=progress)
            n = sum(1 for v in self.project.depth_map.values() if not v.get("fallback"))
            self._set_step("depth", "done", f"{n}/{total} cues")
            self._log(f"Depth analysis complete: {n}/{total} cues analysed.")
            self._refresh_ass_list() if self._ass_cues else None
            self._update_rescan_button()
        except Exception as e:
            self._set_step("depth", "error", "failed")
            self._log(f"Depth analysis error: {e}")

    # ── Cue list ──────────────────────────────────────────────────────────────

    def _load_cue_list(self):
        p = self.prepared_srt
        if not p or not Path(p).is_file():
            # Pre-load a sample cue so style/position can be previewed before
            # a subtitle file has been prepared (e.g. during Express pipeline setup)
            sample_ev = {
                "index": 0, "start": "00:01:00,000", "end": "00:01:05,000",
                "text": "-I must not fear.\n<i>-Fear is the mind-killer.</i>",
                "depth": None, "overrides": {},
            }
            lab = "   0: (sample cue — extract a frame to preview)"
            self._cue_by_label = {lab: sample_ev}
            self.cmb_prev_cue["values"] = [lab]
            self.var_prev_cue.set(lab)
            return
        self._events = deoverlap_events(parse_srt(Path(p)))
        labels = []; self._cue_by_label = {}
        for ev in self._events:
            prev = strip_markup_for_preview(ev["text"])[0].replace("\n"," / ")
            prev = (prev[:58]+"\u2026") if len(prev) > 61 else prev
            lab  = f"{ev['index']:4d}: {ev['start']} \u2014 {prev}"
            labels.append(lab); self._cue_by_label[lab] = ev
        self.cmb_prev_cue["values"] = labels or ["(no subtitles loaded)"]
        self.var_prev_cue.set((labels or ["(no subtitles loaded)"])[0])
        self._render_preview()

    # ── Preview (Style tab) ────────────────────────────────────────────────────

    def _current_prev_event(self):
        return self._cue_by_label.get(self.var_prev_cue.get())

    def _cue_start(self):
        ev = self._current_prev_event()
        if ev: self.var_prev_time.set(srt_time_to_ffmpeg(ev["start"]))

    def _extract_cue_frame(self):
        self._cue_start(); self._extract_frame()

    def _extract_frame(self):
        video  = self.var_mkv.get().strip()
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        ts     = self.var_prev_time.get().strip()
        if not video or not os.path.isfile(video):
            messagebox.showerror("Missing video","Choose a valid video file."); return
        if not ffmpeg:
            messagebox.showerror("Missing ffmpeg","ffmpeg not found."); return
        td  = Path(tempfile.gettempdir()) / "nf3d_preview"; td.mkdir(parents=True, exist_ok=True)
        out = td / f"frame_{ts.replace(':','-').replace('.','_')}.png"
        pr  = subprocess.run([ffmpeg,"-y","-ss",ts,"-i",video,"-frames:v","1",str(out)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             **_popen_kwargs())
        if pr.returncode != 0 or not out.is_file():
            messagebox.showerror("Frame extraction failed", pr.stderr or "ffmpeg failed"); return
        img = Image.open(out).convert("RGB")
        self.preview_left, self.preview_right = split_sbs(img)
        self._cache_preview_thumbnails()
        self._render_preview()

    def _make_params(self, ev):
        g  = self._globals()
        ep = effective_params(ev, g, self.project)
        ep["raw_text"]  = ev["text"]
        ep["shadow_x"]  = g.get("shadow_x", g["shadow"])
        ep["shadow_y"]  = g.get("shadow_y", g["shadow"])
        return ep

    def _cache_preview_thumbnails(self, zone_pct: float = 0.30):
        if self.preview_left is None: return
        cw = max(200, self.prev_canvas.winfo_width())
        ch = max(100, self.prev_canvas.winfo_height())
        ew, eh = self.preview_left.size
        fit = min((cw / 2) / ew, ch / eh)
        tw  = max(1, int(ew * fit)); th = max(1, int(eh * fit))
        self._thumb_left  = self.preview_left.resize((tw, th),  Image.BILINEAR)
        self._thumb_right = self.preview_right.resize((tw, th), Image.BILINEAR)
        self._thumb_scale = fit
        crop_y = int(th * (1.0 - zone_pct))
        self._zone_left  = self._thumb_left.crop((0, crop_y, tw, th))
        self._zone_right = self._thumb_right.crop((0, crop_y, tw, th))

    def _render_preview(self):
        if self.preview_left is None:
            self.prev_canvas.delete("all")
            self.prev_canvas.create_text(
                max(1,self.prev_canvas.winfo_width())//2,
                max(1,self.prev_canvas.winfo_height())//2,
                text="Extract a frame to preview", fill="#aaa", font=("Arial",14))
            return
        ev = self._current_prev_event()
        if ev is None: return
        cw = max(100, self.prev_canvas.winfo_width())
        ch = max(100, self.prev_canvas.winfo_height())
        params        = self._make_params(ev)
        params["depth"] += self.var_prev_depth.get()
        use_zone = self.var_prev_zone.get()
        if use_zone:
            stage = render_subtitle_zone_preview(
                self.preview_left, self.preview_right, params,
                cw, ch, self.var_prev_bg.get())
        else:
            stage = render_stereo_preview(
                self.preview_left, self.preview_right, params,
                cw, ch, self.var_prev_bg.get())
        self._prev_imgtk = ImageTk.PhotoImage(stage)
        self.prev_canvas.delete("all")
        self.prev_canvas.create_image(0, 0, image=self._prev_imgtk, anchor="nw")

    def _accurate_preview(self):
        if self.preview_left is None:
            messagebox.showwarning("No frame","Extract a frame first."); return
        if not self.prepared_srt or not Path(self.prepared_srt).is_file():
            messagebox.showwarning("No subtitle","Prepare a subtitle first."); return
        ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
        if not ffmpeg:
            messagebox.showwarning("No ffmpeg","ffmpeg not found."); return
        threading.Thread(target=self._run_accurate_preview, args=(ffmpeg,), daemon=True).start()

    def _run_accurate_preview(self, ffmpeg: str):
        try:
            import nf3d_core as core
            g  = self._globals()
            ev = self._current_prev_event()
            if ev is None: return
            td = Path(tempfile.gettempdir()) / "nf3d"
            td.mkdir(parents=True, exist_ok=True)
            ass_path   = td / "ap.ass"
            frame_path = td / "ap_frame.png"
            out_path   = td / "ap_out.png"
            single_project = Project(depth_map=self.project.depth_map,
                                     overrides=self.project.overrides)
            p      = core.effective_params(ev, g, single_project)
            eye_w  = g["eye_w"]; h = g["h"]; full_w = eye_w * 2
            lclip  = f"0,0,{eye_w},{h}"; rclip = f"{eye_w},0,{full_w},{h}"
            lx     = p["x_centre"] - p["depth"]
            rx     = eye_w + p["x_centre"] + p["depth"]
            y      = p["y_centre"]
            lt = "{" + f"\\an2\\pos({lx},{y})\\clip({lclip})" + "}"
            rt = "{" + f"\\an2\\pos({rx},{y})\\clip({rclip})" + "}"
            style_tags = ""
            if p["primary_colour"] != g["primary_colour"]:
                c = p["primary_colour"].upper().strip()
                if not c.startswith("&H"): c = "&H" + c
                style_tags += "\\c" + c
            if p["outline_colour"] != g["outline_colour"]: style_tags += "\\3c" + p["outline_colour"]
            if p["back_colour"]    != g["back_colour"]:    style_tags += "\\4c" + p["back_colour"]
            if p["font"]      != g["font"]:      style_tags += "\\fn" + p["font"]
            if p["font_size"] != g["font_size"]: style_tags += "\\fs" + str(p["font_size"])
            if p["bold"] is True:    style_tags += "\\b1"
            elif p["bold"] is False: style_tags += "\\b0"
            if p["raw_ass_tags"]:
                raw = p["raw_ass_tags"].strip().strip("{}")
                style_tags += raw
            style_block = ("{" + style_tags + "}") if style_tags else ""
            body = style_block + p["ass_text"]
            header = core.build_ass_header(
                full_w, h, g["font"], g["font_size"],
                g["primary_colour"], g["outline_colour"], g["back_colour"],
                g["outline"], g["shadow"], g["alignment"],
                g["margin_l"], g["margin_r"], g["margin_v"])
            content = (header
                       + f"Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{lt}{body}\n"
                       + f"Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{rt}{body}\n")
            ass_path.write_text(content, encoding="utf-8-sig", newline="\n")
            full_img = Image.new("RGB", (full_w, h))
            full_img.paste(self.preview_left,  (0, 0))
            full_img.paste(self.preview_right, (eye_w, 0))
            full_img.save(str(frame_path))
            ass_str = str(ass_path).replace("\\", "/")
            if len(ass_str) > 1 and ass_str[1] == ":":
                ass_str = ass_str[0] + "\\:" + ass_str[2:]
            cmd = [ffmpeg, "-y", "-i", str(frame_path),
                   "-vf", "ass=ap.ass", "-frames:v", "1", str(out_path)]
            pr = subprocess.run(cmd, cwd=str(td), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                **_popen_kwargs())
            combined = pr.stdout or ""
            if not out_path.is_file():
                self._log("Accurate preview failed:\n" + combined)
                tail = "\n".join(combined.strip().splitlines()[-8:])
                messagebox.showerror("Accurate preview failed", tail or "ffmpeg produced no output")
                return
            def _show():
                img    = Image.open(out_path).convert("RGB")
                win    = tk.Toplevel(self)
                win.title("Accurate preview \u2014 libass rendered")
                max_w, max_h = 1440, 900
                scale  = min(max_w / img.width, max_h / img.height, 1.0)
                dw     = max(1, int(img.width  * scale))
                dh     = max(1, int(img.height * scale))
                img    = img.resize((dw, dh), Image.LANCZOS)
                canvas = tk.Canvas(win, width=dw, height=dh, bg="black", highlightthickness=0)
                canvas.pack()
                self._accurate_imgtk = ImageTk.PhotoImage(img)
                canvas.create_image(dw//2, dh//2, image=self._accurate_imgtk, anchor="center")
                win.geometry(f"{dw}x{dh}")
            self.after(0, _show)
        except Exception as e:
            self._log(f"Accurate preview error: {e}")
            messagebox.showerror("Error", str(e))

    def _show_fullscreen(self):
        if hasattr(self,"_fs_win") and self._fs_win.winfo_exists(): return
        self._fs_win = tk.Toplevel(self)
        self._fs_win.title("NF3D fullscreen preview")
        self._fs_win.attributes("-fullscreen", True)
        self._fs_win.configure(bg="black")
        tk.Button(self._fs_win, text="Exit", command=self._fs_win.destroy,
                  bg="#333", fg="white").place(relx=1.0, rely=0, anchor="ne")
        self._fs_canvas = tk.Canvas(self._fs_win, bg="black", highlightthickness=0)
        self._fs_canvas.pack(fill="both", expand=True)
        self._fs_canvas.bind("<Configure>", lambda _: self._render_fullscreen())
        self._fs_win.bind("<Escape>", lambda _: self._fs_win.destroy())
        self.after(120, self._render_fullscreen)

    def _render_fullscreen(self):
        if not (hasattr(self,"_fs_win") and self._fs_win.winfo_exists()): return
        if self.preview_left is None: return
        ev = self._current_prev_event()
        if ev is None: return
        cw = max(300, self._fs_canvas.winfo_width())
        ch = max(300, self._fs_canvas.winfo_height())
        stage = render_stereo_preview(
            self.preview_left, self.preview_right,
            self._make_params(ev), cw, ch, self.var_prev_bg.get())
        self._fs_imgtk = ImageTk.PhotoImage(stage)
        self._fs_canvas.delete("all")
        self._fs_canvas.create_image(0,0,image=self._fs_imgtk,anchor="nw")

    # ── Style preset save/load ─────────────────────────────────────────────────

    def _style_to_dict(self) -> dict:
        return {
            "font":           self._font_picker.get() if hasattr(self, "_font_picker") else "Arial",
            "font_size":      self._safe_int(self.var_font_size, 72),
            "primary_colour": self._col_primary.get(),
            "outline_colour": self._col_outline.get(),
            "back_colour":    self._col_back.get(),
            "outline":        self._safe_float(self.var_outline, 4.0),
            "shadow":         self._safe_float(self.var_shadow, 2.0),
            "shadow_x":       self._safe_int(self.var_shadow_x, 2),
            "shadow_y":       self._safe_int(self.var_shadow_y, 2),
            "alignment":      self._safe_int(self.var_alignment, 2),
            "y_percent":      self._safe_float(self.var_y_percent, 88.0),
            "margin_l":       self._safe_int(self.var_margin_l, 30),
            "margin_r":       self._safe_int(self.var_margin_r, 30),
            "margin_v":       self._safe_int(self.var_margin_v, 45),
            "sample_bg":      self.var_sample_bg.get(),
        }

    def _apply_style_dict(self, d: dict):
        if "font" in d: self._font_picker.set(d["font"])
        if "font_size"      in d: self.var_font_size.set(d["font_size"])
        if "primary_colour" in d: self._col_primary.set(d["primary_colour"])
        if "outline_colour" in d: self._col_outline.set(d["outline_colour"])
        if "back_colour"    in d: self._col_back.set(d["back_colour"])
        if "outline"   in d: self.var_outline.set(d["outline"])
        if "shadow"    in d: self.var_shadow.set(d["shadow"])
        if "shadow_x"  in d: self.var_shadow_x.set(d["shadow_x"])
        if "shadow_y"  in d: self.var_shadow_y.set(d["shadow_y"])
        if "alignment" in d: self.var_alignment.set(d["alignment"])
        if "y_percent" in d: self.var_y_percent.set(d["y_percent"])
        if "margin_l"  in d: self.var_margin_l.set(d["margin_l"])
        if "margin_r"  in d: self.var_margin_r.set(d["margin_r"])
        if "margin_v"  in d: self.var_margin_v.set(d["margin_v"])
        if "sample_bg" in d: self.var_sample_bg.set(d["sample_bg"])
        self._style_changed()

    def _save_style_preset(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".nf3ds.json",
            initialdir=self._vorhees("styles"),
            filetypes=[("NF3D Style","*.nf3ds.json"),("JSON","*.json"),("All","*.*")],
            title="Save style preset", confirmoverwrite=True)
        if not p: return
        import json as _json
        Path(p).write_text(_json.dumps(self._style_to_dict(), indent=2), encoding="utf-8")
        save_config(self._current_cfg())
        messagebox.showinfo("Saved", f"Style saved to:\n{Path(p).name}")

    def _load_style_preset(self):
        p = filedialog.askopenfilename(
            initialdir=self._vorhees("styles"),
            filetypes=[("NF3D Style","*.nf3ds.json"),("JSON","*.json"),("All","*.*")],
            title="Load style preset")
        if not p: return
        try:
            import json as _json
            d = _json.loads(Path(p).read_text(encoding="utf-8"))
            self._apply_style_dict(d)
            messagebox.showinfo("Loaded", f"Style loaded from:\n{Path(p).name}")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def _reset_style(self):
        if not messagebox.askyesno("Reset style", "Reset all style settings to defaults?"): return
        self._font_picker.set("Arial")
        self._col_primary.set("&H00E6E6E6")
        self._col_outline.set("&H00000000")
        self._col_back.set("&H64000000")
        self.var_font_size.set(72); self.var_outline.set(4.0); self.var_shadow.set(2.0)
        self.var_shadow_x.set(2); self.var_shadow_y.set(2)
        self.var_alignment.set(2); self.var_y_percent.set(88.0)
        self.var_margin_l.set(30); self.var_margin_r.set(30); self.var_margin_v.set(45)

    def _reset_depth(self):
        if not messagebox.askyesno("Reset depth", "Reset depth settings to defaults?"): return
        for k, v in DEPTH_DEFAULTS.items():
            var = getattr(self, f"var_{k}", None)
            if var: var.set(v)

    def _save_all(self):
        save_config(self._current_cfg())
        messagebox.showinfo("Saved", f"All settings saved to:\n{CONFIG_PATH}")

    # ── Font list management ───────────────────────────────────────────────────

    def _adv_add_font(self, lb: tk.Listbox, entry: ttk.Entry, lst: list):
        name = entry.get().strip()
        if not name: return
        if name not in lst:
            lst.append(name)
            lb.insert("end", name)
        entry.delete(0, "end")

    def _adv_remove_font(self, lb: tk.Listbox, lst: list):
        sel = lb.curselection()
        if not sel: return
        name = lb.get(sel[0])
        lst.remove(name) if name in lst else None
        lb.delete(sel[0])

    def _apply_font_lists(self):
        self._font_picker.configure_lists(self._recommended_fonts, self._specialist_fonts)
        self._ae_font_picker.configure_lists(self._recommended_fonts, self._specialist_fonts)
        save_config(self._current_cfg())
        messagebox.showinfo("Applied", "Font lists updated and saved.")



    # ── Project save/load ─────────────────────────────────────────────────────

    def _copy_srt_alongside(self, json_path: Path) -> str:
        """
        Copy the current SRT to sit alongside the depth JSON with the same stem.
        e.g. MyFilm.nf3d.json → MyFilm.srt
        Returns the path of the copied SRT, or the original path if copy fails.
        This prevents workspace sub_N.srt files from being silently overwritten
        by a subsequent project that happens to use the same track number.
        """
        if not self.prepared_srt or not Path(self.prepared_srt).is_file():
            return self.prepared_srt
        # Strip all extensions from the JSON stem to get a clean base name
        # e.g. "MyFilm.nf3d.json" → "MyFilm"
        stem = json_path.name
        for suffix in (".json", ".nf3d"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
        dest = json_path.parent / (stem + ".srt")
        try:
            import shutil
            shutil.copy2(self.prepared_srt, dest)
            self._log(f"SRT archived alongside depth analysis: {dest.name}")
            return str(dest)
        except Exception as e:
            self._log(f"Warning: could not archive SRT: {e}")
            return self.prepared_srt

    def _save_project(self):
        # Suggest a filename based on project title (same stem as output ASS)
        import re as _re
        title = self.var_project_title.get().strip()
        if not title:
            title = Path(self.var_mkv.get().strip() or "project").stem
        safe  = _re.sub(r'[<>:"/\\|?*]', '_', title).strip()
        p = filedialog.asksaveasfilename(
            defaultextension=".nf3d.json",
            initialfile=safe + "_depth",
            initialdir=self._vorhees("depth"),
            filetypes=[("NF3D project","*.nf3d.json"),("JSON","*.json"),("All","*.*")])
        if not p: return
        json_path = Path(p)
        # Copy SRT alongside the JSON so it can't be clobbered by another project
        archived_srt = self._copy_srt_alongside(json_path)
        self.project.srt_path   = archived_srt
        self.project.video_path = self.var_mkv.get().strip()
        self.project.save(json_path)
        self._last_project_path = json_path
        messagebox.showinfo("Saved", f"Depth analysis saved:\n{p}")

    def _save_project_version(self):
        if not hasattr(self, "_last_project_path") or not self._last_project_path:
            self._save_project(); return
        self.project.srt_path   = self.prepared_srt
        self.project.video_path = self.var_mkv.get().strip()
        versioned = self.project.save_version(self._last_project_path)
        # Copy SRT alongside the versioned JSON too
        self._copy_srt_alongside(versioned)
        messagebox.showinfo("Version saved", f"Version saved:\n{versioned}")

    def _load_project(self):
        p = filedialog.askopenfilename(
            initialdir=self._vorhees("depth"),
            filetypes=[("NF3D project","*.nf3d.json"),("JSON","*.json"),("All","*.*")])
        if not p: return
        try:
            proj = Project.load(Path(p))
        except Exception as e:
            messagebox.showerror("Load failed", str(e)); return

        # Reset all state before applying the project so nothing bleeds over
        self.preview_left  = None
        self.preview_right = None
        self._prev_imgtk   = None
        for key in ("file", "prepare", "depth", "export"):
            self._set_step(key, "idle", "")

        self.project = proj
        self._last_project_path = Path(p)

        # Restore video path only if the file still exists at the saved location.
        # If it has been moved or renamed, keep whatever MKV the user already has
        # loaded so that loading depth data doesn't clobber a manually-set path.
        if proj.video_path:
            if os.path.isfile(proj.video_path):
                self.var_mkv.set(proj.video_path)
                mkvmerge = self.var_mkvmerge.get().strip() or "mkvmerge"
                info = get_video_info(mkvmerge, proj.video_path)
                if info:
                    self.var_eye_w.set(info["eye_w"])
                    self.var_h.set(info["height"])
                self._set_step("file", "done", Path(proj.video_path).name)
            else:
                self._log(f"Saved MKV not found: {proj.video_path}")
                current = self.var_mkv.get().strip()
                if current and os.path.isfile(current):
                    self._log(f"Keeping current MKV: {Path(current).name}")
                    self._set_step("file", "done", Path(current).name)

        # Restore subtitle path.
        # Priority: (1) co-located SRT (same stem as JSON, same folder)
        #           (2) stored path in the JSON
        # This prevents workspace sub_N.srt from being used if it has been
        # overwritten by a subsequent project.
        json_path   = Path(p)
        stem        = json_path.name
        for suffix in (".json", ".nf3d"):
            if stem.endswith(suffix): stem = stem[:-len(suffix)]
        colocated_srt = json_path.parent / (stem + ".srt")

        resolved_srt = ""
        if colocated_srt.is_file():
            resolved_srt = str(colocated_srt)
            self._log(f"Using co-located SRT: {colocated_srt.name}")
        elif proj.srt_path and Path(proj.srt_path).is_file():
            resolved_srt = proj.srt_path
            self._log(f"Using stored SRT path: {Path(proj.srt_path).name}")

        if resolved_srt:
            self.prepared_srt = resolved_srt
            self.lbl_status.config(text=f"Loaded: {Path(resolved_srt).name}")
            self._set_step("prepare", "done", Path(resolved_srt).name)
        else:
            self.prepared_srt = ""
            self.lbl_status.config(
                text="Loaded project — subtitle not found. Prepare subtitle to continue.")

        n_d = len(proj.depth_map); n_o = len(proj.overrides)
        if n_d > 0:
            self._set_step("depth", "done", f"{n_d} entries loaded")
            self._update_rescan_button()

        self._load_cue_list()
        messagebox.showinfo("Loaded",
            f"Project loaded: {n_d} depth entries, {n_o} overrides.")

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def _express_threaded(self):
        threading.Thread(target=self._express, daemon=True).start()

    def _express(self):
        """
        Express pipeline: prepare → (optional review) → depth → export.
        Runs everything automatically using current settings.
        No per-cue editing.
        """
        try:
            save_config(self._current_cfg())
            ws = self.var_workspace.get().strip()
            os.makedirs(ws, exist_ok=True)

            # ── Step 1: Prepare subtitle ──────────────────────────────────────
            self._set_step("prepare", "running", "preparing…")
            self._log("Express: preparing subtitle…")
            if not self.prepared_srt or not os.path.isfile(self.prepared_srt):
                self.prepared_srt = self._prepare_impl()
                self.project.srt_path = self.prepared_srt
            self._set_step("prepare", "done", Path(self.prepared_srt).name)
            self._log(f"Express: subtitle ready — {self.prepared_srt}")

            # ── Step 2: Optional OCR review ───────────────────────────────────
            if self.var_express_review.get():
                self._log("Express: opening OCR reviewer…")
                review_done = threading.Event()
                def _open_review():
                    w = IssueReviewWindow(self, self.prepared_srt,
                                         self.session_dict, self.persistent_dict,
                                         app=self)
                    self.wait_window(w)
                    review_done.set()
                self.after(0, _open_review)
                review_done.wait()
                self._log("Express: OCR review complete.")

            # ── Step 3: Depth analysis ────────────────────────────────────────
            video  = self.var_mkv.get().strip()
            ffmpeg = self.var_ffmpeg.get().strip() or detect_ffmpeg()
            if not video or not os.path.isfile(video):
                raise RuntimeError("No video file — cannot run depth analysis.")
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found.")

            events = self._deoverlap_events(parse_srt(Path(self.prepared_srt)))
            total  = len(events)
            self._set_step("depth", "running", f"0 / {total}…")
            self._log(f"Express: depth analysis — {total} cues…")

            def _prog(i, t):
                if i % 10 == 0 or i == t:
                    self._set_step("depth", "running", f"{i} / {t}")
                    self._log(f"  Depth: {i} / {t} cues")
                    self.update_idletasks()

            self.project.depth_map = analyse_cue_depths(
                video, ffmpeg, events,
                offset_internal=self.var_offset_internal.get(),
                samples_per_cue=self.var_samples.get(),
                internal_limit=self.var_internal_limit.get(),
                out_min=self.var_out_min.get(), out_max=self.var_out_max.get(),
                output_bias=self.var_output_bias.get(),
                output_scale=self.var_output_scale.get(),
                progress_cb=_prog)

            n_good = sum(1 for v in self.project.depth_map.values()
                         if not v.get("fallback"))
            self._set_step("depth", "done", f"{n_good}/{total} cues")
            self._log(f"Express: depth complete — {n_good}/{total} cues.")

            # ── Step 3b: Auto-save depth if requested ────────────────────────
            if self.var_save_depth_export.get():
                import re as _re
                title = self.var_project_title.get().strip()
                if not title:
                    title = Path(video).stem
                safe  = _re.sub(r'[<>:"/\\|?*]', '_', title).strip()
                depth_path = Path(self._vorhees("depth")) / (safe + "_depth.nf3d.json")
                archived   = self._copy_srt_alongside(depth_path)
                self.project.srt_path   = archived
                self.project.video_path = video
                self.project.save(depth_path)
                self._last_project_path = depth_path
                self._log(f"Express: depth saved → {depth_path.name}")

            # ── Step 4: Convert + export ──────────────────────────────────────
            stereo  = os.path.join(ws, "stereo"); os.makedirs(stereo, exist_ok=True)
            title   = self.var_project_title.get().strip()
            if not title:
                title = Path(video).stem
            import re as _re
            safe    = _re.sub(r'[<>:"/\\|?*]', '_', title).strip()
            ass_dir = Path(ws) / "NF3D_Subtitles"; ass_dir.mkdir(exist_ok=True)
            ass_out = ass_dir / (safe + "_NF3D.ass")

            self._log(f"Express: building ASS → {ass_out.name}")

            def _conv_prog(i, total, label=""):
                if i % 50 == 0 or i == total:
                    self._log(f"  [{i}/{total}] {label}")

            n = convert_to_stereo_ass(
                Path(self.prepared_srt), ass_out,
                self.project, self._globals(), progress_cb=_conv_prog)
            self._log(f"Express: {n} cues written.")

            mode   = self.var_output_mode.get().upper()
            muxed  = None
            if mode in ("MKV", "BOTH"):
                self._set_step("export", "running", "muxing…")
                muxed = self._mux(str(ass_out))
                if mode == "MKV" and ass_out.is_file():
                    try: os.remove(str(ass_out))
                    except OSError: logger.warning("_express: could not remove temp ASS %s", ass_out)

            parts = []
            if mode in ("ASS", "BOTH") and ass_out.is_file():
                parts.append(f"ASS: {ass_out}")
            if muxed:
                parts.append(f"MKV: {muxed}")
            self._set_step("export", "done",
                           Path(muxed or str(ass_out)).name)
            self._log("Express pipeline complete.")
            messagebox.showinfo("Express complete",
                                "\n".join(parts) if parts else "Express complete.")

        except Exception as e:
            self._log(f"Express ERROR: {e}")
            messagebox.showerror("Express failed", str(e))

    def _effective_srt(self) -> str:
        """Return the SRT to use for export: override if set and checked, else prepared_srt."""
        if self.var_use_srt_override.get():
            ov = self.var_srt_override.get().strip()
            if ov and os.path.isfile(ov):
                return ov
        return self.prepared_srt

    def _run_threaded(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            save_config(self._current_cfg())
            ws = self.var_workspace.get().strip(); os.makedirs(ws, exist_ok=True)
            stereo = os.path.join(ws,"stereo"); os.makedirs(stereo, exist_ok=True)

            srt = self._effective_srt()
            if not srt or not os.path.isfile(srt):
                self.after(0, lambda: messagebox.showwarning(
                    "Subtitle not ready",
                    "No prepared subtitle found.\n\n"
                    "Run 'Prepare subtitle' (step 2) first, or tick 'Use different SRT'\n"
                    "to specify an SRT file directly."))
                return
            if not self.project.depth_map:
                self.after(0, lambda: messagebox.showwarning(
                    "Depth analysis not ready",
                    "No depth analysis data found.\n\n"
                    "Run 'Run depth analysis' (step 3) or load a saved depth analysis first."))
                return
            stem = Path(self.var_mkv.get().strip() or srt).stem
            ass_out = Path(stereo) / (stem + "_NF3D.ass")
            self._log(f"Converting {srt} → {ass_out}")

            def prog(i, total, label):
                self._log(f"  [{i}/{total}] {label}")

            n = convert_to_stereo_ass(
                Path(srt), ass_out, self.project, self._globals(), progress_cb=prog)
            self._log(f"Wrote {n} events → {ass_out}")

            if self.var_debug_json.get().strip():
                self.project.save(Path(self.var_debug_json.get().strip()))

            mode  = self.var_output_mode.get().upper()
            muxed = None
            if mode in ("MKV","BOTH"):
                muxed = self._mux(str(ass_out))
                if mode == "MKV" and ass_out.is_file():
                    try: os.remove(str(ass_out))
                    except OSError: logger.warning("_run: could not remove temp ASS %s", ass_out)

            parts = []
            if mode in ("ASS","BOTH") and ass_out.is_file(): parts.append(f"ASS: {ass_out}")
            if muxed: parts.append(f"MKV: {muxed}")
            messagebox.showinfo("Done", "\n".join(parts) if parts else "Done.")
        except Exception as e:
            self._log(f"ERROR: {e}"); messagebox.showerror("Error", str(e))

    def _collect_ass_fonts(self, ass_path: str) -> list:
        """
        Scan the ASS file for all font references (Style header + inline \fn tags)
        and return a list of (font_name, file_path) tuples for fonts found on disk.
        Fonts that cannot be located are logged as warnings.
        """
        import re as _re
        font_names = set()
        try:
            text = Path(ass_path).read_text(encoding='utf-8-sig', errors='replace')
        except OSError:
            logger.warning("_collect_ass_fonts: could not read ASS file %s", ass_path)
            return []
        # Style: column 2 is the font name
        for m in _re.finditer(r'^Style:[^,]+,([^,]+),', text, _re.MULTILINE):
            font_names.add(m.group(1).strip())
        # Inline \fn overrides in Dialogue lines
        for m in _re.finditer(r'\fn([^\}]+)', text):
            font_names.add(m.group(1).strip())

        self._log(f"  Font references in ASS: {', '.join(sorted(font_names)) or '(none)'}")
        results = []
        for name in sorted(font_names):
            if not name:
                continue
            path = find_font_file(name)
            if path and os.path.isfile(path):
                results.append((name, path))
                self._log(f"  Font found: {name} → {Path(path).name}")
            else:
                self._log(f"  WARNING: font '{name}' not found on this system — "
                          f"viewers may need it installed separately.")
        return results

    def _mux(self, ass_path: str) -> str:
        mkv      = self.var_mkv.get().strip()
        mkvmerge = self.var_mkvmerge.get().strip() or "mkvmerge"
        ws       = self.var_workspace.get().strip()
        out      = os.path.join(ws, Path(mkv).stem + "_NF3D.mkv")

        rc, raw = self._run_cmd([mkvmerge, "-J", mkv])
        if rc != 0: raise RuntimeError("mkvmerge -J failed")
        info     = json.loads(raw); tracks = info.get("tracks", [])
        sub_ids  = [str(t["id"]) for t in tracks if t.get("type") == "subtitles"]
        non_subs = [f"0:{t['id']}" for t in tracks if t.get("type") != "subtitles"]
        subs_ord = [f"0:{t['id']}" for t in tracks if t.get("type") == "subtitles"]
        order    = non_subs + ["1:0"] + subs_ord

        cmd = [mkvmerge, "-o", out]
        for sid in sub_ids:
            cmd += ["--default-track-flag", f"{sid}:no",
                    "--forced-display-flag", f"{sid}:no"]
        cmd += [mkv,
                "--language", "0:eng",
                "--track-name", "0:NF3D SBS",
                "--default-track-flag", "0:yes",
                "--forced-display-flag", f"0:{'yes' if self.var_forced_track.get() else 'no'}",
                ass_path,
                "--track-order", ",".join(order)]

        # Attach every font referenced in the ASS so viewers don't need
        # the fonts installed locally. mpv, MPC-HC, VLC and Kodi all load
        # MKV font attachments automatically for ASS rendering.
        self._log("Scanning ASS for font references to attach…")
        fonts      = self._collect_ass_fonts(ass_path)
        seen_paths = set()
        for _name, font_path in fonts:
            if font_path in seen_paths:
                continue
            seen_paths.add(font_path)
            mime = ("font/otf" if font_path.lower().endswith(".otf") else "font/ttf")
            cmd += ["--attachment-name",      Path(font_path).name,
                    "--attachment-mime-type",  mime,
                    "--attach-file",           font_path]
        if seen_paths:
            self._log(f"Attaching {len(seen_paths)} font file(s) to MKV.")
        else:
            self._log("No custom fonts to attach (all fonts are system defaults).")

        rc, _ = self._run_cmd(cmd)
        if rc != 0 or not os.path.isfile(out):
            raise RuntimeError("mkvmerge mux failed")
        self.after(0, lambda o=out: self._set_step("export", "done", Path(o).name))
        return out

    # ── Logging ──────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_box.insert("end", msg+"\n")
        self.log_box.see("end")
        self.update_idletasks()


if __name__ == "__main__":
    if "--setup" in sys.argv:
        import setup_check
        setup_check.SetupWindow(installer_mode=True).mainloop()
    else:
        _setup_ok = Path.home() / ".nf3d_setup_ok"
        if not _setup_ok.exists():
            import setup_check
            setup_check.SetupWindow(installer_mode=False).mainloop()
        App().mainloop()
