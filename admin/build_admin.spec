# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SialviPrint Admin.

Build with:
    cd <project-root>
    pyinstaller admin/build_admin.spec --clean

Produces dist/SialviPrintAdmin/ containing SialviPrintAdmin.exe and
all dependencies (onedir, not onefile - onefile hides missing-DLL
errors behind a self-extraction step that makes them harder to
diagnose; onedir fails loudly and immediately on a clean PC instead).

IMPORTANT: run this ON WINDOWS with pywin32 installed. It cannot be
built on Linux/Mac since win32print/win32api are Windows-only.
"""

import sys
from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # SPECPATH is admin/, so parent = project root/

# ------------------------------------------------------------
# Hidden imports: PyInstaller's static analysis frequently misses
# these because they're loaded dynamically (pywin32's COM/win32
# extension modules, Flask's werkzeug internals, PySide6 plugins).
# Every module here was a real "missing module" error class in the
# previous project per the client's own requirements doc - listing
# them explicitly here is what prevents that from recurring.
# ------------------------------------------------------------
hidden_imports = [
    # Owner licensing gate
    "shared.license_gate", "shared.license_client", "shared.license_token", "shared.license_embedded",
    "shared.license_ui", "cryptography", "cryptography.hazmat.primitives.asymmetric.ed25519", "certifi",

    # pywin32 - printer integration
    "win32print",
    "win32api",
    "win32con",
    "win32gui",
    "win32ui",
    "win32com",
    "win32com.client",
    "pywintypes",
    "pythoncom",

    # Flask / werkzeug internals sometimes missed by static analysis
    "flask",
    "werkzeug",
    "werkzeug.serving",
    "jinja2.ext",

    # Silent printing (renders pages, draws them on the printer)
    "PIL",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.ImageWin",
    "admin.printer.silent_print",
    "shared.winprint",
    "admin.server.pricing",

    # PDF / Office / design-file page counting
    "fitz",  # PyMuPDF
    "pptx",
    "pptx.oxml",
    "ezdxf",
    "ezdxf.addons.drawing",
    "ezdxf.addons.drawing.matplotlib",
    "matplotlib",
    "matplotlib.backends.backend_agg",

    # PySide6 - explicit submodules PyInstaller's hook sometimes needs help with
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",

    # HTTP client (requests) internals
    "requests",
    "urllib3",
    "charset_normalizer",
    "idna",

    # Standard library modules occasionally missed with --onedir on some configs
    "sqlite3",
    "json",
    "secrets",
]

a = Analysis(
    [str(PROJECT_ROOT / "admin" / "run_admin.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Schema must ship alongside the exe - db.py reads it at runtime
        (str(PROJECT_ROOT / "admin" / "database" / "schema.sql"), "admin/database"),
        (str(PROJECT_ROOT / "assets"), "assets"),   # logo + emblem used by the UI
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim obviously unused heavy packages if present in the venv,
        # to keep the installer size reasonable. Safe to remove any of
        # these lines if something turns out to actually be needed.
        "numpy.testing",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SialviPrintAdmin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX compression has caused false-positive AV flags in the past; leave off
    console=False,       # no console window for the desktop app
    icon=str(PROJECT_ROOT / "installer" / "assets" / "admin_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SialviPrintAdmin",
)
