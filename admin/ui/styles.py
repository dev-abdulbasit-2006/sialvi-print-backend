"""Admin stylesheet - all branding lives in shared/theme.py."""
from shared.theme import (  # noqa: F401  (re-exported for screens)
    build_stylesheet, COLOR_SUCCESS, COLOR_WARNING, COLOR_DANGER, COLOR_MUTED,
)

STYLESHEET = build_stylesheet(base_px=14)
