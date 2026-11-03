from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QHBoxLayout
from admin.security import has_password, set_password, verify_password
from shared.resources import asset_path


class PasswordDialog(QDialog):
    def __init__(self, first_run=False, parent=None):
        super().__init__(parent)
        self.first_run = first_run
        self.setWindowTitle("SialviPrint Admin — Password")
        self.setModal(True)
        self.setFixedSize(430, 340 if first_run else 300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 25, 30, 25)
        lay.setSpacing(12)

        logo = QLabel()
        pix = QPixmap(asset_path("logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToWidth(170, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)

        title = QLabel("Create Admin Password" if first_run else "Admin Login")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        hint = QLabel("Create a password for this Admin computer." if first_run else "Enter the Admin password to continue.")
        hint.setObjectName("Hint")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        lay.addWidget(self.password)
        self.confirm = None
        if first_run:
            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.Password)
            self.confirm.setPlaceholderText("Confirm password")
            lay.addWidget(self.confirm)

        btn = QPushButton("Create Password" if first_run else "Unlock")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self.submit)
        lay.addWidget(btn)
        self.password.returnPressed.connect(self.submit)

    def submit(self):
        pw = self.password.text()
        if self.first_run:
            if len(pw) < 4:
                QMessageBox.warning(self, "Password too short", "Use at least 4 characters.")
                return
            if pw != self.confirm.text():
                QMessageBox.warning(self, "Passwords do not match", "Please enter the same password twice.")
                return
            try:
                set_password(pw)
            except ValueError as e:
                QMessageBox.warning(self, "Password", str(e))
                return
            self.accept()
        else:
            if verify_password(pw):
                self.accept()
            else:
                self.password.clear()
                QMessageBox.warning(self, "Incorrect password", "The Admin password is incorrect.")


class ChangePasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change Admin Password")
        self.setFixedSize(420, 270)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(10)
        title = QLabel("Change Admin Password")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        self.current = QLineEdit(); self.current.setEchoMode(QLineEdit.Password); self.current.setPlaceholderText("Current password")
        self.new = QLineEdit(); self.new.setEchoMode(QLineEdit.Password); self.new.setPlaceholderText("New password")
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.Password); self.confirm.setPlaceholderText("Confirm new password")
        for w in (self.current, self.new, self.confirm): lay.addWidget(w)
        btn = QPushButton("Change Password"); btn.setProperty("class", "primary"); btn.clicked.connect(self.submit); lay.addWidget(btn)

    def submit(self):
        if not verify_password(self.current.text()):
            QMessageBox.warning(self, "Incorrect password", "Current password is incorrect.")
            return
        if len(self.new.text()) < 4:
            QMessageBox.warning(self, "Password too short", "Use at least 4 characters.")
            return
        if self.new.text() != self.confirm.text():
            QMessageBox.warning(self, "Passwords do not match", "Please enter the same password twice.")
            return
        set_password(self.new.text())
        QMessageBox.information(self, "Password changed", "The Admin password has been changed.")
        self.accept()
