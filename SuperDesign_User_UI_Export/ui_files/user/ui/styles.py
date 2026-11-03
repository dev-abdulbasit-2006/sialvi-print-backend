"""Kiosk stylesheet - larger controls for touch use. Branding lives in shared/theme.py."""
from shared.theme import build_stylesheet

STYLESHEET = build_stylesheet(base_px=15, kiosk=True)
