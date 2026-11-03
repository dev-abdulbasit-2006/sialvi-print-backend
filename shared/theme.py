"""SialviPrint visual system shared by the Admin and User desktop apps.

The palette and component language mirror the supplied Figma dashboard while
keeping the existing PySide6 application behaviour and real backend intact.
"""
from shared.resources import asset_path

# Figma dashboard palette
NAVY = "#1A1D24"
NAVY_LIGHT = "#222630"
NAVY_DEEP = "#0B0D12"
BG = "#0F1115"
SURFACE = "#1A1D24"
SURFACE_2 = "#15181F"
BORDER = "#2A2F3A"
BORDER_LIGHT = "#3A4150"
TEXT = "#E2E8F0"
MUTED = "#94A3B8"
DIM = "#94A3B8"
ORANGE = "#F97316"
ORANGE_LIGHT = "#FB923C"
ORANGE_DEEP = "#C2410C"
COLOR_SUCCESS = "#22C55E"
COLOR_WARNING = "#F59E0B"
COLOR_DANGER = "#EF4444"
COLOR_INFO = "#3B82F6"
COLOR_MUTED = MUTED

GRAD_NAVY_V = f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {NAVY_LIGHT},stop:1 {NAVY_DEEP})"
GRAD_NAVY_H = f"qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {NAVY_LIGHT},stop:1 {NAVY_DEEP})"
GRAD_GRAY = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5A5353,stop:0.48 #363131,stop:1 #171717)"
GRAD_GRAY_HOVER = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #6B6363,stop:0.48 #454040,stop:1 #222020)"
GRAD_ORANGE = f"qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {ORANGE_LIGHT},stop:1 {ORANGE_DEEP})"
GRAD_ORANGE_HOVER = f"qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #FDBA74,stop:1 {ORANGE})"
GRAD_GREEN = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #34D399,stop:1 #16A34A)"
GRAD_RED = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #F87171,stop:1 #DC2626)"


def build_stylesheet(base_px: int = 14, kiosk: bool = False) -> str:
    arrow_down = asset_path("arrow_down.png").replace("\\", "/")
    return f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; }}
QWidget {{ color: {TEXT}; font-size: {base_px}px; }}
QMainWindow, QDialog, #Content {{ background-color: {BG}; }}
QLabel {{ background: transparent; }}
QAbstractScrollArea, QScrollArea, QScrollArea#PricingScroll {{ background: {BG}; border: none; }}
QScrollArea#PricingScroll QWidget#PricingScrollContent {{ background: {BG}; }}
QToolTip {{ background: #363131; color: white; border: 1px solid {BORDER}; padding: 6px 8px; }}

#Header {{ background: rgba(23,23,23,0.96); border-bottom: 1px solid {BORDER}; }}
#HeaderTitle {{ color: {TEXT}; font-size: {base_px + 5}px; font-weight: 800; }}
#HeaderSub {{ color: {MUTED}; font-size: {base_px - 1}px; }}
#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
#Sidebar QLabel {{ background: transparent; }}
#SidebarSection {{ color: #4B5563; font-size: {base_px - 4}px; font-weight: 700; letter-spacing: 1px; padding: 8px 18px 6px 18px; }}
#Sidebar QPushButton {{ color: {MUTED}; background: transparent; border: none; border-radius: 8px; text-align: left; padding: 11px 14px; margin: 2px 10px; font-size: {base_px - 1}px; }}
#Sidebar QPushButton:hover {{ background: #454040; color: {TEXT}; }}
#Sidebar QPushButton:checked {{ background: rgba(249,115,22,0.12); color: {ORANGE}; border-left: 2px solid {ORANGE}; font-weight: 700; }}
#SidebarFooter {{ color: #4B5563; font-size: {base_px - 4}px; padding: 12px 18px; }}
#PageTitle {{ font-size: {base_px + 9}px; font-weight: 800; color: {TEXT}; padding: 2px 0 4px 0; }}
#PageSubtitle, #Hint, #Muted {{ color: {MUTED}; font-size: {base_px - 1}px; }}
#Breadcrumb {{ color: {MUTED}; font-size: {base_px - 2}px; }}
#CardTitle {{ font-size: {base_px + 1}px; font-weight: 700; color: {TEXT}; }}
#StatCard, #SectionCard, #PreviewCard, #InfoCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#StatCard {{ border-left: 3px solid {ORANGE}; }}
#StatCardValue {{ font-size: {base_px + 10}px; font-weight: 800; color: {TEXT}; }}
#StatCardLabel {{ font-size: {base_px - 2}px; color: {MUTED}; font-weight: 600; }}
#PriceLabel {{ font-size: {base_px + 16}px; font-weight: 900; color: {ORANGE}; }}
#PriceCaption {{ color: {MUTED}; font-weight: 600; font-size: {base_px - 2}px; }}
#Code {{ font-family: Consolas, "Courier New", monospace; color: {TEXT}; background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 9px 11px; }}
#StatusBanner {{ background: #21180F; color: #FDBA74; border: 1px solid #5A3416; border-left: 4px solid {ORANGE}; padding: 10px 14px; border-radius: 8px; font-weight: 600; }}
#StatusBannerError {{ background: #251214; color: #FCA5A5; border: 1px solid #5F2024; border-left: 4px solid {COLOR_DANGER}; padding: 10px 14px; border-radius: 8px; font-weight: 600; }}
#StatusBannerSuccess {{ background: #0D2118; color: #86EFAC; border: 1px solid #194C2E; border-left: 4px solid {COLOR_SUCCESS}; padding: 10px 14px; border-radius: 8px; font-weight: 600; }}
QPushButton {{ border-radius: 8px; padding: 9px 16px; font-weight: 700; background: #3a3333; color: #E2E8F0; border: 1px solid #5A5353; }}
QPushButton:hover {{ background: #4a4040; border: 1px solid #706868; }}
QPushButton:pressed {{ background: #2a2323; }}
QPushButton:disabled {{ background: #292626; color: #475569; border: 1px solid #1A1D2B; }}
QPushButton[class="primary"] {{ background: #F97316; color: #FFFFFF; border: 1px solid #FB923C; }}
QPushButton[class="primary"]:hover {{ background: #FB923C; border: 1px solid #FDBA74; }}
QPushButton[class="primary"]:pressed {{ background: #C2410C; }}
QPushButton[class="secondary"] {{ background: #3a3333; color: #E2E8F0; border: 1px solid #5A5353; }}
QPushButton[class="danger"] {{ min-width: 72px; padding-left: 8px; padding-right: 8px; background: #EF4444; color: #FFFFFF; border: none; }}
QPushButton[class="cash"] {{ min-width: 72px; padding-left: 8px; padding-right: 8px; background: #22C55E; color: #FFFFFF; border: 1px solid #4ADE80; }}
QPushButton[class="online"] {{ min-width: 82px; padding-left: 8px; padding-right: 8px; background: #3B82F6; color: #FFFFFF; border: 1px solid #60A5FA; }}
QPushButton[class="pay"] {{ background: {GRAD_ORANGE}; color: white; border: none; font-size: 15px; font-weight: 800; padding: 11px 20px; }}
QPushButton[class="pay"]:hover {{ background: {GRAD_ORANGE_HOVER}; }}
QPushButton[class="newcustomer"] {{ background: transparent; color: {ORANGE}; border: 1px solid rgba(249,115,22,.35); padding: 8px 14px; }}
QPushButton[class="ghost"] {{ background: #2a2323; border: 1px solid #5A5353; color: #E2E8F0; }}
QPushButton[class="link"] {{ background: transparent; border: none; color: {ORANGE}; padding: 4px 6px; }}
QPushButton[class="icon"] {{ padding: 5px 9px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 10px; min-height: 22px; selection-background-color: {ORANGE}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ORANGE}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url({arrow_down}); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; selection-background-color: #252840; selection-color: white; }}
QCheckBox {{ color: {MUTED}; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {BORDER}; border-radius: 5px; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ORANGE}; border: 1px solid {ORANGE}; }}
QTableWidget {{ background: {SURFACE_2}; alternate-background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: #181B2B; selection-background-color: #252840; selection-color: white; outline: none; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; padding: 10px 8px; border: none; border-bottom: 1px solid {BORDER}; font-weight: 700; }}
QTableWidget::item {{ padding: 6px; }}
QProgressBar {{ background: #2F5A6E; border: none; border-radius: 5px; height: 9px; text-align: center; color: white; }}
QProgressBar::chunk {{ background: {ORANGE}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2F5A6E; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2A2D46; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QDialog {{ background: {BG}; }}
QMessageBox {{ background: {SURFACE}; color: {TEXT}; }}
"""
