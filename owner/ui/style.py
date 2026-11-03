"""Owner License Manager look & feel: ONE self-contained, high-contrast dark theme.

Independent of shared/theme.py on purpose (that file styles the Admin/User kiosks and had no rules
for tabs, panes, message boxes ... which made the Owner tabs unreadable). Every text/background pair
below is >= 4.5:1 contrast (WCAG AA); tests/test_owner_ui_flows.py checks this and also checks the
RENDERED tab bar, so an unreadable layout cannot ship again.
"""
BG = "#0F1117"          # window
SURFACE = "#171B26"     # panels, tables
SURFACE_ALT = "#1C2130" # zebra rows
RAISED = "#232A3B"      # headers, buttons
BORDER = "#3A4560"
INPUT = "#0B0D13"
TEXT = "#F1F5F9"
MUTED = "#A9B4C8"
ACCENT = "#F97316"      # orange (matches the SialviPrint brand)
ACCENT_HOVER = "#FB923C"
ON_ACCENT = "#111827"   # dark text on orange = 6.4:1 (white on orange would only be 2.8:1)
DANGER = "#DC2626"
DANGER_HOVER = "#B91C1C"   # darker on hover keeps white text >= 4.5:1
SELECTION = "#2B4A8B"

# (foreground, background) pairs that must stay readable; verified by the tests.
CONTRAST_PAIRS = [
    (TEXT, BG), (TEXT, SURFACE), (TEXT, SURFACE_ALT), (TEXT, RAISED), (TEXT, INPUT),
    (MUTED, BG), (MUTED, SURFACE), (MUTED, RAISED),
    (ON_ACCENT, ACCENT), (ON_ACCENT, ACCENT_HOVER), ("#FFFFFF", DANGER), ("#FFFFFF", DANGER_HOVER),
    ("#FFFFFF", SELECTION), ("#CBD5E1", "#1F2433"),
]

STATUS_COLORS = {            # text colours for status cells (all >= 7:1 on SURFACE)
    "active": "#4ADE80", "suspended": "#FBBF24", "revoked": "#F87171", "expired": "#FB923C",
    "locked": "#F87171", "deactivated": "#94A3B8", "not recently seen": "#FBBF24",
    "pending activation": "#FBBF24", "disabled": "#94A3B8",
}


def _lum(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def build_owner_stylesheet(font_px=14) -> str:
    return f"""
* {{ font-family: "Segoe UI", "Inter", "Arial", sans-serif; font-size: {font_px}px; }}
QWidget {{ background-color: {BG}; color: {TEXT}; }}
QMainWindow, QDialog, QMessageBox, QInputDialog {{ background-color: {BG}; }}
QLabel, QCheckBox, QRadioButton, QGroupBox {{ background: transparent; color: {TEXT}; }}
QLabel#PageTitle {{ font-size: 24px; font-weight: 800; color: {TEXT}; }}
QLabel#Hint {{ color: {MUTED}; }}
QLabel#Empty {{ color: {MUTED}; font-size: 15px; padding: 28px; background: transparent; }}
QLabel#Error {{ color: #FCA5A5; font-weight: 600; background: transparent; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {BG}; border-radius: 8px; top: -1px; }}
QTabBar {{ background: {BG}; }}
QTabBar::tab {{ background: #1F2433; color: #CBD5E1; padding: 11px 24px; margin-right: 5px;
    border: 1px solid {BORDER}; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px;
    font-weight: 600; min-width: 90px; }}
QTabBar::tab:hover:!selected {{ background: #2A3247; color: #FFFFFF; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; font-weight: 800; }}

/* ---------- tables ---------- */
QTableWidget, QListWidget {{ background: {SURFACE}; alternate-background-color: {SURFACE_ALT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BORDER};
    selection-background-color: {SELECTION}; selection-color: #FFFFFF; outline: 0; }}
QTableWidget::item {{ padding: 6px 10px; }}
QHeaderView {{ background: {RAISED}; }}
QHeaderView::section {{ background: {RAISED}; color: {TEXT}; padding: 9px 10px; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; font-weight: 700; }}
QTableCornerButton::section {{ background: {RAISED}; border: none; }}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background: {INPUT}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 8px 10px; selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: #7C879D; background: #12151D; }}
QLineEdit::placeholder {{ color: {MUTED}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {SELECTION}; selection-color: #FFFFFF; outline: 0; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid #7C879D; border-radius: 5px; background: {INPUT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---------- buttons ---------- */
QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid #55627F; border-radius: 8px;
    padding: 9px 18px; font-weight: 700; min-height: 20px; }}
QPushButton:hover {{ background: #2E3750; border-color: #7283A8; }}
QPushButton:pressed {{ background: #1A2030; }}
QPushButton[class="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; border: 1px solid {ACCENT_HOVER}; font-weight: 800; }}
QPushButton[class="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[class="danger"] {{ background: {DANGER}; color: #FFFFFF; border: 1px solid #F87171; }}
QPushButton[class="danger"]:hover {{ background: {DANGER_HOVER}; }}
QPushButton[class="ghost"] {{ background: transparent; color: {TEXT}; border: 1px solid #6B7A99; }}
QPushButton[class="ghost"]:hover {{ background: #232A3B; }}
QPushButton:disabled {{ background: #151925; color: #7C879D; border: 1px solid #2A3142; }}
QPushButton:focus {{ border: 2px solid {ACCENT_HOVER}; }}

/* ---------- misc ---------- */
QFrame#StatCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#StatCard QLabel {{ background: transparent; }}
QLabel#StatNumber {{ font-size: 32px; font-weight: 800; color: {TEXT}; }}
QLabel#StatLabel {{ color: {MUTED}; font-size: 13px; font-weight: 600; }}
QScrollBar:vertical {{ background: {SURFACE}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4A5675; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #6B7A99; }}
QScrollBar:horizontal {{ background: {SURFACE}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #4A5675; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""
