#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_check.py — NF3D dependency checker and first-run wizard.
Run this before launching NF3D to verify all required tools are present.
Can also be run standalone at any time to diagnose missing dependencies.
"""
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
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# ── Dependency definitions ────────────────────────────────────────────────────

SCRIPT_DIR = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent

def _setup_ok_path() -> Path:
    """Return the .setup_ok flag path — always in the user home dir so it's writable."""
    return Path.home() / ".nf3d_setup_ok"

# Python packages: (import_name, pip_name, required, description)
PYTHON_DEPS = [
    ("PIL",        "pillow",           True,  "Image rendering (required)"),
    ("numpy",      "numpy",            True,  "Depth analysis arrays (required)"),
    ("cv2",        "opencv-python",    True,  "Stereo depth analysis (required)"),
]

# External tools: (key, display_name, required, how_to_install)
if sys.platform == "win32":
    TOOL_DEPS = [
        ("ffmpeg",       "ffmpeg",        True,
         "winget install Gyan.FFmpeg",
         "https://ffmpeg.org/download.html"),
        ("mkvtoolnix",   "MKVToolNix",    True,
         "winget install MKVToolNix.MKVToolNix",
         "https://mkvtoolnix.download/"),
        ("subtitleedit", "Subtitle Edit (stable, not beta)", True,
         "winget install Nikse.SubtitleEdit",
         "https://github.com/SubtitleEdit/subtitleedit/releases"),
    ]
else:  # Linux
    TOOL_DEPS = [
        ("ffmpeg",       "ffmpeg",        True,
         "sudo apt install ffmpeg  (or equivalent for your distro)",
         "https://ffmpeg.org/download.html"),
        ("mkvtoolnix",   "MKVToolNix",    True,
         "sudo apt install mkvtoolnix",
         "https://mkvtoolnix.download/"),
        ("subtitleedit", "Subtitle Edit", False,
         "sudo apt install subtitleedit  (requires mono-runtime)",
         "https://www.nikse.dk/subtitleedit/"),
    ]

# ── Detection helpers ─────────────────────────────────────────────────────────

def check_python_package(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False

def check_tool(key: str) -> str:
    """Return path if found, else empty string."""
    # Map display keys to autodetect_tools() dict keys
    _key_map = {"mkvtoolnix": "mkvmerge", "ffmpeg": "ffmpeg", "subtitleedit": "subtitleedit"}
    try:
        from nf3d_gui import autodetect_tools
        tools = autodetect_tools()
        return tools.get(_key_map.get(key, key), "")
    except Exception:
        pass
    # Fallback: check known Windows absolute paths directly
    _abs_fallback = {
        "mkvtoolnix":   [r"C:\Program Files\MKVToolNix\mkvmerge.exe",
                         r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe"],
        "subtitleedit": [r"C:\Program Files\Subtitle Edit\SubtitleEdit.exe",
                         r"C:\Program Files (x86)\Subtitle Edit\SubtitleEdit.exe"],
        "ffmpeg":       [r"C:\ffmpeg\bin\ffmpeg.exe",
                         r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"],
    }
    for path in _abs_fallback.get(key, []):
        if os.path.isfile(path):
            return path
    # Last resort: PATH lookup
    cmd = _key_map.get(key, key)
    try:
        r = subprocess.run([cmd, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           **_popen_kwargs())
        return cmd if r.returncode == 0 else ""
    except Exception:
        return ""

def _se_version_warning(se_path: str) -> str:
    """Return a warning string if SE is a 5.x beta (known /convert regression)."""
    try:
        import struct
        with open(se_path, "rb") as f:
            data = f.read(min(1024 * 1024, os.path.getsize(se_path)))
        # Scan for the ProductVersion string in the PE version resource
        marker = b"P\x00r\x00o\x00d\x00u\x00c\x00t\x00V\x00e\x00r\x00s\x00i\x00o\x00n\x00"
        idx = data.find(marker)
        if idx != -1:
            raw = data[idx + len(marker): idx + len(marker) + 40]
            ver = raw.replace(b"\x00", b"").decode("ascii", errors="ignore").strip()
            if ver.startswith("5.") and "beta" in ver.lower():
                return f" — WARNING: {ver} is a beta. SE 5.x beta ignores /convert for PGS OCR. Install SE 4.x stable."
    except Exception:
        pass
    return ""

def pip_install(package: str, on_line: callable, on_done: callable):
    """Install a pip package in a background thread, streaming output."""
    def _run():
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                **_popen_kwargs())
            for line in proc.stdout:
                on_line(line.rstrip())
            proc.wait()
            on_done(proc.returncode == 0)
        except Exception as e:
            on_line(f"Error: {e}")
            on_done(False)
    threading.Thread(target=_run, daemon=True).start()

def run_winget(package: str, on_line: callable, on_done: callable):
    """Run a winget install in a background thread."""
    def _run():
        try:
            proc = subprocess.Popen(
                ["winget", "install", "--accept-package-agreements",
                 "--accept-source-agreements", package],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                **_popen_kwargs())
            for line in proc.stdout:
                on_line(line.rstrip())
            proc.wait()
            on_done(proc.returncode == 0)
        except Exception as e:
            on_line(f"winget not available: {e}")
            on_done(False)
    threading.Thread(target=_run, daemon=True).start()

# ── GUI ───────────────────────────────────────────────────────────────────────

TICK  = "✓"
CROSS = "✗"
WARN  = "⚠"
COL_OK   = "#1a7a1a"
COL_FAIL = "#b00000"
COL_WARN = "#a05000"
COL_GREY = "#888888"

CONFIG_PATH = Path.home() / "nf3d_config.json"

def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_config(cfg: dict):
    try:
        existing = _read_config()
        existing.update(cfg)
        CONFIG_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception:
        pass


class SetupWindow(tk.Tk):

    def __init__(self, installer_mode: bool = False):
        super().__init__()
        self._installer_mode = installer_mode
        self.title("NF3D — First-run Setup" if installer_mode else "NF3D — Setup & dependency check")
        self.resizable(False, False)
        self.geometry("700x680")

        # State tracking
        self._pkg_status  = {}   # import_name → bool
        self._tool_status = {}   # key → str (path or "")
        self._rows        = {}   # key → {"lbl_status": ..., "btn": ..., "lbl_info": ...}
        self._log_lines   = []
        self._ready       = False
        self._has_winget  = False

        self._build()
        self.after(200, self._run_checks)

    def _build(self):
        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg="#111827")
        hdr.pack(fill="x")
        try:
            from PIL import Image, ImageTk
            logo_path = SCRIPT_DIR / "nf3d_logo.png"
            if logo_path.is_file():
                img = Image.open(str(logo_path)).convert("RGBA")
                img.thumbnail((52, 52), Image.LANCZOS)
                self._logo_imgtk = ImageTk.PhotoImage(img)
                tk.Label(hdr, image=self._logo_imgtk, bg="#111827").pack(side="left", padx=12, pady=8)
        except Exception:
            pass
        tk.Label(hdr, text="NF3D Setup Check", font=("Arial", 16, "bold"),
                 fg="white", bg="#111827").pack(side="left", pady=16)
        tk.Label(hdr, text="Verifying all required components are installed",
                 font=("Arial", 9), fg="#aaa", bg="#111827").pack(side="left", padx=12)

        # ── Python packages ───────────────────────────────────────────────────
        self._section("Python packages", "Required libraries — installed via pip")
        for import_name, pip_name, required, desc in PYTHON_DEPS:
            self._dep_row(import_name, desc, required,
                          btn_label="Install",
                          btn_cmd=lambda pn=pip_name, k=import_name: self._pip_install(pn, k))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=4)

        # ── External tools ────────────────────────────────────────────────────
        self._section("External tools", "Applications that NF3D calls externally")
        for key, display, required, install_cmd, url in TOOL_DEPS:
            self._dep_row(key, display, required,
                          btn_label="Install" if sys.platform == "win32" else None,
                          btn_cmd=lambda ic=install_cmd, k=key, u=url: self._tool_install(ic, k, u),
                          extra=install_cmd if sys.platform != "win32" else None,
                          url=url)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=4)

        # ── Log ───────────────────────────────────────────────────────────────
        log_fr = ttk.LabelFrame(self, text="Output")
        log_fr.pack(fill="both", expand=True, padx=12, pady=(0,4))
        self._log_box = tk.Text(log_fr, height=4, font=("Courier New", 8),
                                bg="#f8f8f8", wrap="word", state="disabled")
        sb = ttk.Scrollbar(log_fr, command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sb.set)
        self._log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Workspace folder ──────────────────────────────────────────────────
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(4,0))
        ws_lf = ttk.LabelFrame(self, text="Workspace folder")
        ws_lf.pack(fill="x", padx=12, pady=(4,4))
        ws_lf.columnconfigure(1, weight=1)

        default_ws = _read_config().get("workspace", "") or str(Path.home() / "NF3D")
        self._var_ws = tk.StringVar(value=default_ws)

        ttk.Label(ws_lf,
                  text="NF3D saves depth analyses, processed subtitles, and project files here.\n"
                       "You can change this at any time from the app's workspace field.",
                  font=("Arial", 8), foreground="#555",
                  wraplength=580, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4,2))
        ttk.Label(ws_lf, text="Location:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(ws_lf, textvariable=self._var_ws).grid(
            row=1, column=1, sticky="we", padx=(0,4), pady=4)
        ttk.Button(ws_lf, text="Browse…", command=self._browse_ws).grid(
            row=1, column=2, padx=(0,8), pady=4)

        # ── Bottom buttons ────────────────────────────────────────────────────
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=12, pady=8)
        ttk.Button(btn_row, text="Re-check all", command=self._run_checks).pack(side="left", padx=(0,8))
        if self._installer_mode:
            launch_text = "Finish →"
            skip_text   = "Skip checks"
        else:
            launch_text = "Launch NF3D →"
            skip_text   = "Launch anyway"
        self._btn_launch = ttk.Button(btn_row, text=launch_text,
                                      command=self._launch, state="disabled")
        self._btn_launch.pack(side="right")
        ttk.Button(btn_row, text=skip_text,
                   command=self._launch).pack(side="right", padx=(0,8))

    def _section(self, title: str, subtitle: str):
        fr = ttk.Frame(self)
        fr.pack(fill="x", padx=12, pady=(8,2))
        ttk.Label(fr, text=title, font=("Arial", 10, "bold")).pack(side="left")
        ttk.Label(fr, text=f"  {subtitle}", font=("Arial", 8),
                  foreground="#666").pack(side="left")

    def _dep_row(self, key: str, desc: str, required: bool,
                 btn_label=None, btn_cmd=None, extra=None, url=None):
        fr = ttk.Frame(self)
        fr.pack(fill="x", padx=20, pady=2)

        lbl_status = ttk.Label(fr, text="…", width=3, font=("Arial", 11, "bold"),
                               foreground=COL_GREY)
        lbl_status.pack(side="left")

        badge = "[required]" if required else "[optional]"
        badge_col = "#444" if required else COL_GREY
        ttk.Label(fr, text=f"{desc}", font=("Arial", 9)).pack(side="left", padx=(4,0))
        ttk.Label(fr, text=badge, font=("Arial", 8), foreground=badge_col).pack(side="left", padx=4)

        btn = None
        if btn_label and btn_cmd and sys.platform == "win32":
            btn = ttk.Button(fr, text=btn_label, width=14, command=btn_cmd)
            btn.pack(side="right")
        elif extra:
            ttk.Label(fr, text=extra, font=("Courier New", 8),
                      foreground="#444", wraplength=360).pack(side="right")

        if url:
            def _open(u=url):
                import webbrowser; webbrowser.open(u)
            ttk.Button(fr, text="↗", width=3, command=_open).pack(side="right", padx=2)

        self._rows[key] = {"lbl_status": lbl_status, "btn": btn}

    def _set_status(self, key: str, ok: bool, msg: str = ""):
        row = self._rows.get(key)
        if not row: return
        lbl = row["lbl_status"]
        if ok:
            lbl.config(text=TICK, foreground=COL_OK)
        else:
            lbl.config(text=CROSS, foreground=COL_FAIL)
        if msg:
            self._log(f"{key}: {msg}")

    def _log(self, text: str):
        self._log_box.config(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")
        self.update_idletasks()

    def _run_checks(self):
        self._log("── Checking dependencies ──")
        all_required_ok = True

        # Check winget availability once and relabel install buttons accordingly
        if sys.platform == "win32":
            try:
                r = subprocess.run(["winget", "--version"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **_popen_kwargs())
                self._has_winget = (r.returncode == 0)
            except Exception:
                self._has_winget = False
            lbl = "Install via winget" if self._has_winget else "Download page ↗"
            for key, *_ in TOOL_DEPS:
                row = self._rows.get(key)
                if row and row.get("btn"):
                    row["btn"].config(text=lbl)

        for import_name, pip_name, required, desc in PYTHON_DEPS:
            ok = check_python_package(import_name)
            self._pkg_status[import_name] = ok
            self._set_status(import_name, ok,
                             f"found" if ok else f"NOT found — pip install {pip_name}")
            if required and not ok:
                all_required_ok = False

        for key, display, required, install_cmd, url in TOOL_DEPS:
            path = check_tool(key)
            self._tool_status[key] = path
            if path:
                extra = ""
                if key == "subtitleedit" and sys.platform == "win32":
                    extra = _se_version_warning(path)
                self._set_status(key, True, f"found at {path}{extra}")
            else:
                self._set_status(key, False, "NOT found")
                if required and sys.platform != "darwin" or key != "subtitleedit":
                    if required:
                        all_required_ok = False

        self._ready = all_required_ok
        if all_required_ok:
            self._log("── All required dependencies satisfied ──")
            self._btn_launch.config(state="normal")
        else:
            self._log("── Some required dependencies are missing ──")
            if not self._has_winget and sys.platform == "win32":
                self._log("  Tip: click 'Download page ↗' for each missing tool, install it, then click Re-check all.")
            self._btn_launch.config(state="disabled")

    def _pip_install(self, pip_name: str, import_name: str):
        self._log(f"Installing {pip_name}…")
        def on_line(line): self.after(0, lambda l=line: self._log(l))
        def on_done(ok):
            def _update():
                if ok:
                    self._set_status(import_name, True, "installed successfully")
                    self._log(f"{pip_name} installed. Re-checking…")
                    self._run_checks()
                else:
                    self._log(f"Install failed. Try: pip install {pip_name}")
            self.after(0, _update)
        pip_install(pip_name, on_line, on_done)

    def _tool_install(self, install_cmd: str, key: str, url: str = ""):
        if sys.platform != "win32":
            self._log(f"Run manually: {install_cmd}")
            return
        # Check winget is available
        try:
            r = subprocess.run(["winget", "--version"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               **_popen_kwargs())
            has_winget = (r.returncode == 0)
        except Exception:
            has_winget = False

        parts = install_cmd.split()
        if has_winget and len(parts) >= 3 and parts[0] == "winget" and parts[1] == "install":
            pkg_id = parts[2]
            self._log(f"Running: winget install {pkg_id}")
            def on_line(line): self.after(0, lambda l=line: self._log(l))
            def on_done(ok):
                def _update():
                    if ok:
                        self._log("Installed. Re-checking…")
                    else:
                        self._log("winget install failed or needs a restart to be detected.")
                    self._run_checks()
                self.after(0, _update)
            run_winget(pkg_id, on_line, on_done)
        else:
            # winget not available — open download page in browser
            if url:
                import webbrowser
                self._log(f"winget not available — opening download page for {key}.")
                self._log("  Install the tool, then click 'Re-check all' to continue.")
                webbrowser.open(url)
            else:
                self._log(f"Install manually: {install_cmd}")

    def _browse_ws(self):
        from tkinter import filedialog
        current = self._var_ws.get().strip() or str(Path.home() / "NF3D")
        chosen = filedialog.askdirectory(title="Choose NF3D workspace folder",
                                         initialdir=current if os.path.isdir(current) else str(Path.home()))
        if chosen:
            self._var_ws.set(chosen)

    def _save_workspace(self):
        ws = self._var_ws.get().strip()
        if not ws:
            ws = str(Path.home() / "NF3D")
        try:
            os.makedirs(ws, exist_ok=True)
        except Exception:
            pass
        _write_config({"workspace": ws})
        self._log(f"Workspace set to: {ws}")

    def _launch(self):
        if not self._ready:
            verb = "Finish" if self._installer_mode else "Launch"
            if not messagebox.askyesno("Missing dependencies",
                "Some required dependencies are missing.\n"
                "NF3D may not work correctly.\n\n"
                f"{verb} anyway?"):
                return
        self._save_workspace()
        try:
            _setup_ok_path().write_text("ok", encoding="utf-8")
        except Exception:
            pass
        self.destroy()
        # In frozen mode (installer or first-run), the caller handles what happens next.
        # In dev/script mode, spawn the main app.
        if not self._installer_mode and not getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable, str(SCRIPT_DIR / "nf3d_gui.py")])

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = SetupWindow()
    app.mainloop()
