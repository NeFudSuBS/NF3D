# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for NF3D
# Run from the 'new claude' parent directory:
#   pyinstaller installer/NF3D.spec

import sys
from pathlib import Path

app_dir = Path(SPECPATH).parent   # 'new claude' folder

# Bundle pyspellchecker English dictionary
try:
    import spellchecker as _sc
    _sc_resources = str(Path(_sc.__file__).parent / 'resources' / 'en.json.gz')
except Exception:
    _sc_resources = None

a = Analysis(
    [str(app_dir / 'nf3d_gui.py')],
    pathex=[str(app_dir)],
    binaries=[],
    datas=[
        (str(app_dir / 'nf3d_core.py'),        '.'),
        (str(app_dir / 'nf3d_gui.py'),         '.'),
        (str(app_dir / 'setup_check.py'),       '.'),
        (str(app_dir / 'config.py'),            '.'),
        (str(app_dir / 'logging_config.py'),    '.'),
        (str(app_dir / 'exception_handlers.py'),'.'),
        (str(app_dir / 'nf3d_icon.ico'),        '.'),
        (str(app_dir / 'nf3d_logo.png'),        '.'),
    ] + ([(_sc_resources, 'spellchecker/resources')] if _sc_resources else []),
    hiddenimports=[
        'PIL._tkinter_finder',
        'spellchecker',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'PIL.ImageTk',
        'numpy',
        'cv2',
        'tkinter',
        'tkinter.ttk',
        'tkinter.colorchooser',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.font',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'scipy', 'pandas', 'jupyter',
        'IPython', 'PyQt5', 'PyQt6', 'wx',
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NF3D',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                         # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(app_dir / 'nf3d_icon.ico'),   # taskbar / exe icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NF3D',
)
