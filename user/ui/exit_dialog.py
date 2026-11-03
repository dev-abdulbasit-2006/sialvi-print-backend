"""Password dialogs for the User app: create the exit password / ask for it."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox

from shared.resources import asset_path
from user.security import (
    MIN_LENGTH, has_exit_password, set_exit_password, verify_exit_password,
)

MAX_ATTEMPTS = 5
LOCK_SECONDS = 30


class ExitPasswordDialog(QDialog):
    """create=True: set a new password (cannot be cancelled).
    create=False: ask for the password to close the app."""

    def __init__(self, create=False, parent=None):
        super().__init__(parent)
        self.create = create
        self._fails = 0
        self.setWindowTitle("SialviPrint — Set Exit Password" if create else "SialviPrint — Password Required")
        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.CustomizeWindowHint
                            | (Qt.WindowStaysOnTopHint))
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)

        title = QLabel("Set Exit Password" if create else "Password Required")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        hint = QLabel(
            "Choose a password. It will be needed to close this application. "
            "Keep it safe - customers must not know it." if create
            else "Enter the password to close SialviPrint. You can still minimize the window."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        lay.addWidget(self.password)
        self.confirm = None
        if create:
            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.Password)
            self.confirm.setPlaceholderText("Confirm password")
            lay.addWidget(self.confirm)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.ok_btn = QPushButton("Save Password" if create else "Close Application")
        self.ok_btn.setProperty("class", "primary")
        self.ok_btn.clicked.connect(self.submit)
        lay.addWidget(self.ok_btn)
        self.password.returnPressed.connect(self.submit)

        if not create:
            cancel = QPushButton("Cancel (keep running)")
            cancel.setProperty("class", "ghost")
            cancel.clicked.connect(self.reject)
            lay.addWidget(cancel)
        self.password.setFocus()

    def reject(self):
        if self.create:  # the password is mandatory; Esc must not skip it
            return
        super().reject()

    def submit(self):
        pw = self.password.text()
        if self.create:
            if len(pw) < MIN_LENGTH:
                QMessageBox.warning(self, "Password too short", f"Use at least {MIN_LENGTH} characters.")
                return
            if pw != self.confirm.text():
                QMessageBox.warning(self, "Passwords do not match", "Please enter the same password twice.")
                return
            set_exit_password(pw)
            self.accept()
            return
        if verify_exit_password(pw):
            self.accept()
            return
        self._fails += 1
        self.password.clear()
        if self._fails >= MAX_ATTEMPTS:
            self._lock()
        else:
            self.status.setText(f"⚠ Wrong password ({MAX_ATTEMPTS - self._fails} tries left before a short wait).")

    def _lock(self):
        self._fails = 0
        self._remaining = LOCK_SECONDS
        self.ok_btn.setEnabled(False)
        self.password.setEnabled(False)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick(first=True)

    def _tick(self, first=False):
        if not first:
            self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.ok_btn.setEnabled(True)
            self.password.setEnabled(True)
            self.status.setText("")
            self.password.setFocus()
        else:
            self.status.setText(f"⚠ Too many wrong tries. Wait {self._remaining}s.")


def ensure_exit_password_exists():
    """Called once at start-up: make the owner create the password if missing."""
    if not has_exit_password():
        ExitPasswordDialog(create=True).exec()


def confirm_exit(parent=None) -> bool:
    return ExitPasswordDialog(create=False, parent=parent).exec() == QDialog.DialogCode.Accepted
