"""Sialvi Print Owner License Manager - entry point.

Runs on the OWNER's PC only. Connects over HTTPS to the licensing server and signs in with an
owner account created on the server (python -m licensing.server create-owner). No credentials
are stored on disk; only the server address is remembered.
"""
import logging
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout)

from owner.api import ApiError, OwnerApi
from shared.resources import asset_path
from owner.ui.style import build_owner_stylesheet


def log_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    d = Path(base) / "SialviOwnerLicenseManager"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path.home()
    return d / "owner.log"


def install_error_handling():
    """A windowed .exe has no console, so an uncaught error used to make buttons 'do nothing'.
    Now every unexpected error is written to owner.log AND shown in a message box."""
    logging.basicConfig(filename=str(log_file()), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def hook(exc_type, exc, tb):
        logging.getLogger("owner").error("Uncaught error:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Owner License Manager — unexpected error",
                                 f"{exc_type.__name__}: {exc}\n\nDetails were saved to:\n{log_file()}")
        except Exception:
            pass
    sys.excepthook = hook


from shared.cloud_config import CLOUD_LICENSE_URL


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Owner License Manager — Sign in")
        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.setMinimumWidth(440)
        self.settings = QSettings("Sialvi", "OwnerLicenseManager")
        self.api = None
        lay = QVBoxLayout(self)
        t = QLabel("Owner License Manager"); t.setObjectName("PageTitle"); t.setAlignment(Qt.AlignCenter); lay.addWidget(t)
        form = QFormLayout()
        self.server_url = CLOUD_LICENSE_URL                       # fixed: cloud licensing only
        server_label = QLabel(self.server_url); server_label.setObjectName("Hint"); server_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.user = QLineEdit(); self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password)
        form.addRow("Licensing server", server_label); form.addRow("Username", self.user); form.addRow("Password", self.pw)
        lay.addLayout(form)
        wake = QLabel("The cloud server sleeps when idle: the first sign-in can take up to a minute."); wake.setObjectName("Hint"); wake.setWordWrap(True)
        lay.addWidget(wake)
        self.msg = QLabel(""); self.msg.setWordWrap(True); self.msg.setObjectName("Error"); lay.addWidget(self.msg)
        row = QHBoxLayout()
        self.ok = QPushButton("Sign in"); self.ok.setProperty("class", "primary"); self.ok.clicked.connect(self.sign_in)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "ghost"); cancel.clicked.connect(self.reject)
        row.addStretch(); row.addWidget(cancel); row.addWidget(self.ok); lay.addLayout(row)
        self.pw.returnPressed.connect(self.sign_in)

    def sign_in(self):
        try:
            api = OwnerApi(self.server_url)
            api.login(self.user.text().strip(), self.pw.text())
        except ApiError as e:
            self.msg.setText("⚠ " + e.message); self.msg.setObjectName("Error"); self.msg.setStyleSheet(""); self.pw.clear(); return
        except Exception as e:
            logging.getLogger("owner").exception("Login failed unexpectedly")
            self.msg.setText(f"⚠ Unexpected error: {e}"); return
        self.api = api
        self.accept()


def main():
    install_error_handling()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")                       # same look on every Windows theme; our stylesheet does the rest
    app.setStyleSheet(build_owner_stylesheet())
    dlg = LoginDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return 0
    from owner.ui.main_window import MainWindow
    win = MainWindow(dlg.api)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
