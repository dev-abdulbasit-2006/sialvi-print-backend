"""Branded Admin shell using the supplied Figma visual language and real screens."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QStackedWidget, QLabel, QButtonGroup, QFrame,
)
from admin.ui.screens.dashboard import DashboardScreen
from admin.ui.screens.payments import PaymentsScreen
from admin.ui.screens.pricing import PricingScreen
from admin.ui.screens.printers import PrintersScreen
from admin.ui.screens.queue import QueueScreen
from admin.ui.screens.connection import ConnectionScreen
from admin.ui.password_dialog import ChangePasswordDialog
from shared.resources import asset_path


class MainWindow(QMainWindow):
    def reset_license(self):
        """Open the license dialog (shows status + Reset License) so a new serial key can be entered."""
        from PySide6.QtWidgets import QMessageBox
        from shared.license_ui import LicenseDialog
        if self.license_gate is None:
            QMessageBox.warning(self, "License", "Licensing is not available in this session.")
            return
        LicenseDialog(self.license_gate, self).exec()

    def __init__(self, license_gate=None):
        super().__init__()
        self.license_gate = license_gate
        self.setWindowTitle("SialviPrint — Admin")
        self.setWindowIcon(QIcon(asset_path("logo_admin_vertical.png")))
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ------------------------------------------------------------
        # Figma-style dark sidebar + vertical admin logo
        # ------------------------------------------------------------
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(240)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)

        logo_wrap = QWidget()
        logo_lay = QVBoxLayout(logo_wrap)
        logo_lay.setContentsMargins(12, 10, 12, 4)
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pix = QPixmap(asset_path("logo_admin_vertical.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(190, 178, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_lay.addWidget(logo)
        sl.addWidget(logo_wrap)

        section = QLabel("ADMIN CONSOLE")
        section.setObjectName("SidebarSection")
        sl.addWidget(section)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_items = [
            ("▦   Dashboard", DashboardScreen),
            ("₨   Payments", PaymentsScreen),
            ("☰   Print Queue", QueueScreen),
            ("⎙   Printers", PrintersScreen),
            ("▣   Pricing", PricingScreen),
            ("⇄   Computers", ConnectionScreen),
        ]
        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        for i, (label, cls) in enumerate(nav_items):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(btn, i)
            sl.addWidget(btn)
            self.stack.addWidget(cls())
        sl.addStretch(1)

        lic_btn = QPushButton("🔑   Reset License")
        lic_btn.setProperty("class", "ghost")
        lic_btn.setCursor(Qt.PointingHandCursor)
        lic_btn.clicked.connect(self.reset_license)
        sl.addWidget(lic_btn)

        settings = QPushButton("⚙   Change Password")
        settings.setProperty("class", "ghost")
        settings.setCursor(Qt.PointingHandCursor)
        settings.clicked.connect(self.change_password)
        sl.addWidget(settings)

        footer = QLabel("Sialvi Printing Solution\nv4.0.0")
        footer.setObjectName("SidebarFooter")
        sl.addWidget(footer)
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)

        # ------------------------------------------------------------
        # Main area: glassy top bar + screen stack
        # ------------------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        top = QWidget()
        top.setObjectName("Header")
        top.setFixedHeight(58)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 0, 22, 0)

        self.crumb = QLabel("SialviPrint  ›  Dashboard")
        self.crumb.setObjectName("Breadcrumb")
        tl.addWidget(self.crumb)
        tl.addStretch()

        online = QLabel("●  LAN Active")
        online.setStyleSheet("color:#22C55E;font-weight:600;")
        tl.addWidget(online)
        divider = QFrame()
        divider.setFixedSize(1, 18)
        divider.setStyleSheet("background:#5A5353;")
        tl.addSpacing(12)
        tl.addWidget(divider)
        tl.addSpacing(12)
        avatar = QLabel("A")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(30, 30)
        avatar.setStyleSheet("background:#F97316;color:white;border-radius:15px;font-weight:800;")
        tl.addWidget(avatar)

        rl.addWidget(top)
        rl.addWidget(self.stack, 1)
        root.addWidget(sidebar)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        self.nav_group.idClicked.connect(self._update_crumb)
        self._update_crumb(0)

    def _update_crumb(self, index: int):
        titles = ["Dashboard", "Payments", "Print Queue", "Printers", "Pricing", "Computers"]
        title = titles[index] if 0 <= index < len(titles) else "Dashboard"
        self.crumb.setText(f"SialviPrint  ›  {title}")

    def change_password(self):
        ChangePasswordDialog(self).exec()
