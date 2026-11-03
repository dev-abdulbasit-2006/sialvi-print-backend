"""Locate bundled assets both in dev (source tree) and when frozen by PyInstaller."""
import sys
from pathlib import Path


def asset_path(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)
