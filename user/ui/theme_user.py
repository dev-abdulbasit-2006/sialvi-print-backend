"""Sialvi Print - customer (User) kiosk theme: DARK, matching the Admin app.

Palette mirrors shared/theme.py (charcoal surfaces, orange accent). User-only: shared/theme.py is NOT modified.
All colors, radii, spacing, type sizes and animation timings live here as named constants.
The brand logo has white lettering, which is why the dark theme suits it.
"""
from __future__ import annotations

import os

from shared.resources import asset_path

# ---- Colors (same family as the Admin app) ----------------------------------------------------
NAVY_900 = "#0B0D12"       # Admin NAVY_DEEP
NAVY_700 = "#222630"       # Admin NAVY_LIGHT
BG = "#0F1115"             # Admin BG
SURFACE = "#1A1D24"        # Admin SURFACE (cards)
SURFACE_ALT = "#15181F"    # Admin SURFACE_2 (inputs, canvas)
BORDER = "#2A2F3A"
BORDER_STRONG = "#3A4150"
TEXT = "#E2E8F0"
MUTED = "#94A3B8"
PRIMARY = "#C2410C"        # deep orange (Admin ORANGE_DEEP): white text on it = 5.2:1
PRIMARY_HOVER = "#D1480E"  # white text = 4.5:1
PRIMARY_PRESSED = "#9A3412"
PRIMARY_SOFT = "#261A10"   # dark orange tint (info banner, selected row, drop zone)
ON_PRIMARY = "#FFFFFF"
ACCENT = "#F97316"         # Admin ORANGE: rings, progress, icons
ACCENT_LIGHT = "#FB923C"   # Admin ORANGE_LIGHT: text/links on dark surfaces (7.4:1)
ACCENT_SOFT = "#261A10"
SUCCESS = "#22C55E"
SUCCESS_SOFT = "#0F2A1A"
SUCCESS_TEXT = "#86EFAC"
WARNING_TEXT = "#FCD34D"
WARNING_SOFT = "#2A2010"
DANGER = "#EF4444"
DANGER_TEXT = "#FCA5A5"
DANGER_SOFT = "#2A1215"
INFO_TEXT = "#FDBA74"
ON_NAVY = "#E2E8F0"
DISABLED_BG = "#262B35"
DISABLED_TEXT = "#8794A8"

# ---- Geometry / type --------------------------------------------------------------------------
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
HIT_MIN = 44
HIT_PRIMARY = 52
BASE_PX = 15
FONT_STACK = '"Inter", "Segoe UI Variable Text", "Segoe UI", sans-serif'

# ---- Motion -----------------------------------------------------------------------------------
DUR_FAST = 160
DUR_MED = 240
DUR_SLOW = 340
REDUCE_MOTION = os.environ.get("SIALVI_REDUCE_MOTION", "").strip().lower() in ("1", "true", "yes")


# ---- Helpers ----------------------------------------------------------------------------------
def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, label) pairs that carry text; checked by tests (WCAG AA = 4.5)
TEXT_PAIRS = [
    (TEXT, BG, "body on page"), (TEXT, SURFACE, "body on card"), (TEXT, SURFACE_ALT, "body on input"),
    (MUTED, SURFACE, "muted on card"), (MUTED, BG, "muted on page"), (MUTED, SURFACE_ALT, "muted on input"),
    (ON_PRIMARY, PRIMARY, "primary button"), (ON_PRIMARY, PRIMARY_HOVER, "primary hover"),
    (ON_PRIMARY, PRIMARY_PRESSED, "primary pressed"), (NAVY_900, ACCENT, "step number on accent"),
    (ACCENT_LIGHT, SURFACE, "link / selected text on card"), (ACCENT_LIGHT, SURFACE_ALT, "link on input"),
    (ON_NAVY, NAVY_900, "text on header"), (ON_NAVY, NAVY_700, "text on header end"),
    (INFO_TEXT, PRIMARY_SOFT, "info banner"), (DANGER_TEXT, DANGER_SOFT, "error banner"),
    (SUCCESS_TEXT, SUCCESS_SOFT, "success banner"), (WARNING_TEXT, WARNING_SOFT, "warning banner"),
    (DISABLED_TEXT, DISABLED_BG, "disabled control"), (TEXT, BORDER, "chip on border colour"),
    ("#1B2B3A", "#FFFFFF", "document preview text"),
]


def build_user_stylesheet(base_px: int = BASE_PX) -> str:
    arrow_down = asset_path("arrow_down_light.png").replace("\\", "/")
    s = base_px
    return f"""
* {{ font-family: {FONT_STACK}; }}
QWidget {{ color: {TEXT}; font-size: {s}px; }}
QMainWindow, QDialog, #Content, #Page {{ background-color: {BG}; }}
QLabel {{ background: transparent; }}
QAbstractScrollArea, QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {NAVY_900}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px 8px; }}

/* ---------- header ---------- */
#Header {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {NAVY_700},stop:1 {NAVY_900}); border-bottom: 3px solid {ACCENT}; }}
#HeaderSub {{ color: {ON_NAVY}; font-size: {s - 1}px; font-weight: 600; }}
#Header QPushButton[class="newcustomer"] {{ background: rgba(255,255,255,0.06); color: #FFFFFF; border: 1px solid rgba(255,255,255,0.35); border-radius: {RADIUS_SM + 2}px; padding: 8px 16px; font-weight: 700; min-height: {HIT_MIN - 14}px; }}
#Header QPushButton[class="newcustomer"]:hover {{ background: rgba(249,115,22,0.22); border: 1px solid {ACCENT}; }}
#Header QPushButton[class="newcustomer"]:pressed {{ background: rgba(249,115,22,0.35); }}

/* ---------- typography ---------- */
#PageTitle {{ font-size: {s + 9}px; font-weight: 800; color: {TEXT}; padding: 2px 0 4px 0; }}
#Headline {{ font-size: {s + 19}px; font-weight: 800; color: {TEXT}; }}
#Lead {{ font-size: {s + 3}px; color: {MUTED}; }}
#PageSubtitle, #Hint, #Muted {{ color: {MUTED}; font-size: {s - 1}px; }}
#HintError {{ color: {DANGER_TEXT}; font-size: {s - 1}px; font-weight: 600; }}
#CardTitle {{ font-size: {s + 2}px; font-weight: 800; color: {TEXT}; }}
#FieldLabel {{ color: {MUTED}; font-size: {s - 1}px; font-weight: 700; }}
#Code {{ font-family: Consolas, "Courier New", monospace; color: {TEXT}; background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px; padding: 9px 11px; }}

/* ---------- cards ---------- */
#SectionCard, #PreviewCard, #InfoCard, #StatCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
#StepBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#PreviewCanvas {{ background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; }}
#HintCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; }}
#LogoTile {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {NAVY_700},stop:1 {NAVY_900}); border-radius: 22px; border: 1px solid {BORDER}; border-bottom: 4px solid {ACCENT}; }}
#HintNumber {{ background: {ACCENT}; color: {NAVY_900}; font-weight: 800; border-radius: 15px; }}

/* ---------- drop zone ---------- */
#DropZone {{ background: {PRIMARY_SOFT}; border: 2px dashed {ACCENT}; border-radius: {RADIUS_LG}px; }}
#DropZone[hover="true"] {{ background: #3A2412; border: 2px solid {ACCENT_LIGHT}; }}
#DropZoneTitle {{ font-size: {s + 3}px; font-weight: 800; color: {TEXT}; }}

/* ---------- price ---------- */
#PriceLabel {{ font-size: {s + 25}px; font-weight: 900; color: {TEXT}; }}
#PriceCaption {{ color: {MUTED}; font-weight: 700; font-size: {s - 2}px; letter-spacing: 1px; }}

/* ---------- banners ---------- */
#StatusBanner {{ background: {PRIMARY_SOFT}; color: {INFO_TEXT}; border: 1px solid #4A2A14; border-left: 4px solid {ACCENT}; padding: 10px 14px; border-radius: {RADIUS_SM}px; font-weight: 600; }}
#StatusBannerError {{ background: {DANGER_SOFT}; color: {DANGER_TEXT}; border: 1px solid #4A1D22; border-left: 4px solid {DANGER}; padding: 10px 14px; border-radius: {RADIUS_SM}px; font-weight: 600; }}
#StatusBannerSuccess {{ background: {SUCCESS_SOFT}; color: {SUCCESS_TEXT}; border: 1px solid #1B4D2E; border-left: 4px solid {SUCCESS}; padding: 10px 14px; border-radius: {RADIUS_SM}px; font-weight: 600; }}

/* ---------- status card (waiting / printing / done / failed) ---------- */
#StatusCard {{ border-radius: {RADIUS_MD}px; border: 1px solid #4A2A14; background: {PRIMARY_SOFT}; }}
#StatusCard[kind="success"] {{ background: {SUCCESS_SOFT}; border: 1px solid #1B4D2E; }}
#StatusCard[kind="failed"] {{ background: {DANGER_SOFT}; border: 1px solid #4A1D22; }}
#StatusTitle {{ font-size: {s + 2}px; font-weight: 800; color: {TEXT}; }}
#StatusSub {{ color: #CBD5E1; font-size: {s - 1}px; }}

/* ---------- chips ---------- */
QLabel[chip] {{ border-radius: 9px; padding: 2px 10px; font-size: {s - 3}px; font-weight: 800; }}
QLabel[chip="pending"] {{ background: {BORDER}; color: {TEXT}; }}
QLabel[chip="uploading"] {{ background: {PRIMARY_SOFT}; color: {INFO_TEXT}; }}
QLabel[chip="ready"] {{ background: {SUCCESS_SOFT}; color: {SUCCESS_TEXT}; }}
QLabel[chip="failed"] {{ background: {DANGER_SOFT}; color: {DANGER_TEXT}; }}
#FileBadge {{ background: {BORDER}; color: {TEXT}; border-radius: 8px; font-weight: 800; font-size: {s - 4}px; }}

/* ---------- buttons ---------- */
QPushButton {{ border-radius: {RADIUS_SM + 2}px; padding: 10px 18px; font-weight: 700; background: {NAVY_700}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; min-height: {HIT_MIN - 20}px; }}
QPushButton:hover {{ background: {BORDER}; border: 1px solid {MUTED}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px solid {BORDER}; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; }}
QPushButton[class="primary"] {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: 1px solid {ACCENT}; }}
QPushButton[class="primary"]:hover {{ background: {PRIMARY_HOVER}; border: 1px solid {ACCENT_LIGHT}; }}
QPushButton[class="primary"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[class="primary"]:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; border: 1px solid {BORDER}; }}
QPushButton[class="secondary"], QPushButton[class="ghost"] {{ background: {NAVY_700}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; }}
QPushButton[class="danger"] {{ background: #B91C1C; color: #FFFFFF; border: none; min-width: 72px; }}
QPushButton[class="cash"] {{ background: #15803D; color: #FFFFFF; border: none; min-width: 72px; }}
QPushButton[class="online"] {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: none; min-width: 82px; }}
QPushButton[class="pay"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {PRIMARY_HOVER},stop:1 {PRIMARY_PRESSED}); color: {ON_PRIMARY}; border: none; font-size: {s + 3}px; font-weight: 800; padding: 12px 22px; min-height: {HIT_PRIMARY - 24}px; }}
QPushButton[class="pay"]:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton[class="pay"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[class="pay"]:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; }}
QPushButton[class="hero"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {PRIMARY_HOVER},stop:1 {PRIMARY_PRESSED}); color: {ON_PRIMARY}; border: none; font-size: {s + 5}px; font-weight: 800; padding: 16px 44px; border-radius: {RADIUS_LG}px; min-height: 40px; }}
QPushButton[class="hero"]:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton[class="hero"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[class="link"] {{ background: transparent; border: none; color: {ACCENT_LIGHT}; padding: 4px 6px; }}
QPushButton[class="icon"] {{ padding: 5px 9px; }}
QPushButton[class="step"] {{ font-size: {s + 6}px; font-weight: 800; padding: 0; min-width: {HIT_MIN}px; max-width: {HIT_MIN}px; min-height: {HIT_MIN - 4}px; background: {NAVY_700}; }}
QPushButton[class="seg"] {{ background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 0; padding: 8px 14px; min-height: {HIT_MIN - 20}px; }}
QPushButton[class="seg"]:hover {{ background: {BORDER}; }}
QPushButton[class="seg"]:checked {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: 1px solid {ACCENT}; }}
QPushButton[class="seg"][pos="first"] {{ border-top-left-radius: {RADIUS_SM + 2}px; border-bottom-left-radius: {RADIUS_SM + 2}px; }}
QPushButton[class="seg"][pos="last"] {{ border-top-right-radius: {RADIUS_SM + 2}px; border-bottom-right-radius: {RADIUS_SM + 2}px; }}
QPushButton[class="rowremove"] {{ background: transparent; border: none; color: {MUTED}; font-size: {s + 1}px; padding: 0; min-height: 0; }}
QPushButton[class="rowremove"]:hover {{ background: {DANGER_SOFT}; color: {DANGER_TEXT}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_SM + 2}px; padding: 8px 12px; min-height: {HIT_MIN - 20}px; selection-background-color: {PRIMARY}; selection-color: #FFFFFF; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {ACCENT}; }}
QLineEdit[invalid="true"] {{ border: 2px solid {DANGER}; background: {DANGER_SOFT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: {DISABLED_BG}; color: {DISABLED_TEXT}; }}
QLineEdit {{ placeholder-text-color: {MUTED}; }}
#CopiesValue {{ background: {SURFACE_ALT}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_SM + 2}px; font-size: {s + 4}px; font-weight: 800; color: {TEXT}; }}
QComboBox::drop-down {{ border: none; width: 32px; }}
QComboBox::down-arrow {{ image: url({arrow_down}); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; selection-background-color: {PRIMARY_SOFT}; selection-color: {ACCENT_LIGHT}; outline: 0; }}
QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border: 1px solid {BORDER_STRONG}; border-radius: 5px; background: {SURFACE_ALT}; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border: 1px solid {ACCENT}; }}

/* ---------- lists / tables ---------- */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ background: {SURFACE_ALT}; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; margin: 3px 2px; }}
QListWidget::item:selected {{ background: {PRIMARY_SOFT}; border: 2px solid {ACCENT}; color: {TEXT}; }}
QListWidget::item:hover:!selected {{ border: 1px solid {ACCENT}; }}
QTableWidget {{ background: {SURFACE}; alternate-background-color: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; gridline-color: {BORDER}; selection-background-color: {PRIMARY_SOFT}; selection-color: {TEXT}; }}
QHeaderView::section {{ background: {SURFACE_ALT}; color: {MUTED}; padding: 10px 8px; border: none; border-bottom: 1px solid {BORDER}; font-weight: 700; }}
/* Text documents are previewed as white "paper" with dark text (the HTML the preview builds is dark-on-white). */
QTextBrowser {{ background: #FFFFFF; color: #1B2B3A; border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px; }}
QTextEdit, QPlainTextEdit {{ background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px; }}

/* ---------- progress / scroll ---------- */
QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

/* ---------- dialogs ---------- */
QMessageBox, QInputDialog {{ background: {SURFACE}; color: {TEXT}; }}
#ConfirmDialog {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_LG}px; }}
#ConfirmTitle {{ font-size: {s + 5}px; font-weight: 800; color: {TEXT}; }}
#ConfirmBody {{ font-size: {s + 1}px; color: #CBD5E1; }}
#ConfirmAmount {{ font-size: {s + 15}px; font-weight: 900; color: {TEXT}; }}
"""
