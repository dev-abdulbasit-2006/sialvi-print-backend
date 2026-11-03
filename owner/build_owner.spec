# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: Sialvi Print Owner License Manager (onedir, like the other apps).
Build ON WINDOWS from the project root:   pyinstaller owner/build_owner.spec --clean
Output: dist/SialviOwnerLicenseManager/SialviOwnerLicenseManager.exe
This tool is for the software owner only - do NOT ship it to customers."""
from pathlib import Path
PROJECT_ROOT = Path(SPECPATH).resolve().parent
icon = PROJECT_ROOT / "installer" / "assets" / "admin_icon.ico"
a = Analysis([str(PROJECT_ROOT / "owner" / "run_owner.py")], pathex=[str(PROJECT_ROOT)], binaries=[],
             datas=[(str(PROJECT_ROOT / "assets"), "assets")],
             hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "requests", "urllib3",
                            "charset_normalizer", "idna", "certifi"],
             excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SialviOwnerLicenseManager", debug=False,
          strip=False, upx=False, console=False, icon=str(icon) if icon.exists() else None)
# onedir: the exe MUST stay next to its _internal\\ folder (python312.dll lives there).
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="SialviOwnerLicenseManager")
