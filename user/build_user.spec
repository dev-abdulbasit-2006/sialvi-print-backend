# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SialviPrint User (kiosk).

Build with:
    cd <project-root>
    pyinstaller user/build_user.spec --clean

Produces dist/SialviPrintUser/ containing SialviPrintUser.exe.
The User app includes the authenticated local print agent and Windows printer
integration because the final spooler operation now happens on the originating User PC.
The final build must happen on Windows for a correct Windows executable.
"""

from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # SPECPATH is user/, so parent = project root/

hidden_imports = [
    # Owner licensing gate
    "shared.license_gate", "shared.license_client", "shared.license_token", "shared.license_embedded",
    "shared.license_ui", "cryptography", "cryptography.hazmat.primitives.asymmetric.ed25519", "certifi",

    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "fitz",  # PyMuPDF - PDF preview
    "docx",  # python-docx - built-in Word (.docx) preview fallback
    "pptx",  # python-pptx - built-in PowerPoint (.pptx) preview fallback
    "lxml",
    "lxml.etree",
    "shared.winprint",
    "shared.silent_print",
    "win32print",
    "win32con",
    "win32gui",
    "win32ui",
    "pywintypes",
    "PIL",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.ImageWin",
    "requests",
    "urllib3",
    "charset_normalizer",
    "idna",
    "json",
]

a = Analysis(
    [str(PROJECT_ROOT / "user" / "run_user.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "assets"), "assets"),   # logo + emblem used by the UI
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy.testing", "tkinter"],
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
    name="SialviPrintUser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PROJECT_ROOT / "installer" / "assets" / "user_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SialviPrintUser",
)
