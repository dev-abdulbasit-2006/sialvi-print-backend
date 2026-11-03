"""Qt screens for the license gate: activation / blocked dialog + runtime monitor."""
import socket
import threading

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QMessageBox)

from shared.resources import asset_path


def _run_responsive(fn):
    """Run a blocking licensing-server call in a worker thread while the dialog keeps repainting.
    The free cloud server can need ~45 s to wake up; calling it on the GUI thread made Windows show "Not responding".
    The caller still gets the result synchronously (same behaviour as before, just without the frozen window)."""
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:          # re-raised below, on the GUI thread
            box["error"] = exc
    t = threading.Thread(target=work, name="SialviLicenseCall", daemon=True)
    t.start()
    while t.is_alive():
        QApplication.processEvents()
        t.join(0.03)
    if "error" in box:
        raise box["error"]
    return box.get("value")


class LicenseDialog(QDialog):
    """Shown at start-up when the PC is not authorized, and again at runtime if authorization is lost.
    The only ways forward: a valid activation, or Exit. Nothing else in the app is reachable."""

    def __init__(self, gate, parent=None):
        super().__init__(parent)
        self.gate = gate
        self._resetting = False
        self.setWindowTitle("SialviPrint — License Required")
        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        title = QLabel("License Required"); title.setObjectName("PageTitle"); title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        self.info = QLabel(); self.info.setWordWrap(True); lay.addWidget(self.info)

        self.key = QLineEdit(); self.key.setPlaceholderText("SIALVI-XXXX-XXXX-XXXX-XXXX")
        self.key.textChanged.connect(lambda t: self.key.setText(t.upper()) if t != t.upper() else None)
        self.name = QLineEdit(socket.gethostname()); self.name.setPlaceholderText("This PC's name")
        lay.addWidget(QLabel("Serial key (given to you by your software provider)")); lay.addWidget(self.key)
        lay.addWidget(QLabel("PC name")); lay.addWidget(self.name)
        self.status = QLabel(""); self.status.setWordWrap(True); lay.addWidget(self.status)

        row = QHBoxLayout()
        self.exit_btn = QPushButton("Exit"); self.exit_btn.setProperty("class", "ghost"); self.exit_btn.clicked.connect(self.reject)
        self.check_btn = QPushButton("Check again"); self.check_btn.clicked.connect(self.check_again)
        self.go = QPushButton("Activate"); self.go.setProperty("class", "primary"); self.go.clicked.connect(self.activate)
        self.reset_btn = QPushButton("Reset License"); self.reset_btn.setProperty("class", "ghost"); self.reset_btn.clicked.connect(self.reset_license)
        row.addWidget(self.exit_btn); row.addStretch(); row.addWidget(self.check_btn); row.addWidget(self.reset_btn); row.addWidget(self.go)
        lay.addLayout(row)
        self.key.returnPressed.connect(self.activate)
        self._refresh_view()

    def _refresh_view(self):
        st = self.gate.state()
        has_install = bool(self.gate.client and self.gate.client.state.get("installation_id"))
        configured = self.gate.configured
        self.check_btn.setVisible(has_install and configured)
        self.go.setEnabled(configured); self.key.setEnabled(configured); self.name.setEnabled(configured)
        if not configured:
            self.info.setText(st.message)
        elif st.authorized:
            self.setWindowTitle("SialviPrint — License")
            self.info.setText("<b>License active.</b><br>" + (st.message or "") +
                              "<br><br>To switch this PC to a different license, click <b>Reset License</b> "
                              "and enter the new serial key. Your current license stays in place unless the new key is accepted.")
            self.exit_btn.setText("Close")
        elif st.code == "NOT_ACTIVATED":
            self.info.setText("This PC is not activated. Enter the serial key you received to start using SialviPrint. "
                              "An internet connection is needed for activation.")
        else:
            self.info.setText(f"<b>Access is currently not allowed.</b><br>{st.message}<br><br>"
                              "Your orders and print jobs are safe and will continue when this is resolved.")
        self.status.setText("")

    def _busy(self, on):
        for w in (self.go, self.check_btn, self.exit_btn, self.reset_btn, self.key, self.name):
            w.setEnabled(not on)
        QApplication.processEvents()

    def _finish(self, st):
        if st.authorized:
            self.accept()
        else:
            self.status.setText("⚠ " + st.message)

    def activate(self):
        if not self.key.text().strip():
            self.status.setText("⚠ Please enter the serial key."); return
        self.status.setText("Contacting the licensing server…"); self._busy(True)
        try:
            key, device = self.key.text(), self.name.text().strip()      # read widgets here, not in the worker thread
            self._finish(_run_responsive(lambda: self.gate.activate(key, device)))
        finally:
            self._busy(False)

    def check_again(self):
        self.status.setText("Contacting the licensing server…"); self._busy(True)
        try:
            self._finish(_run_responsive(self.gate.refresh_now))
        finally:
            self._busy(False)

    def reset_license(self):
        """Ask for a new serial key and validate it with the licensing server. The existing license is
        replaced ONLY after the new one is accepted; cancel / failure leaves it untouched."""
        if self._resetting:
            return
        from PySide6.QtWidgets import QInputDialog
        key, ok = QInputDialog.getText(self, "Reset License", "Enter the new serial key:",
                                       QLineEdit.Normal, "")
        key = (key or "").strip().upper()
        if not ok or not key:
            return
        self._resetting = True
        self.status.setText("Contacting the licensing server…"); self._busy(True)
        try:
            device = self.name.text().strip() or socket.gethostname()
            st = _run_responsive(lambda: self.gate.replace_license(key, device))
        finally:
            self._busy(False); self._resetting = False
        if st.authorized:
            self._finish(st)
        else:
            self._refresh_view()
            self.status.setText("⚠ New key not accepted. Your previous license was kept. " + st.message)


def ensure_licensed(gate) -> bool:
    """Call right after QApplication exists and BEFORE any server / printing work starts.
    Returns False when the user exits without a valid license (caller must quit)."""
    if gate.client and gate.client.state.get("installation_id"):
        gate.refresh_now()                      # tries the server; offline falls back to the grace period
    if gate.is_authorized():
        gate.start_background()
        return True
    if LicenseDialog(gate).exec() == QDialog.DialogCode.Accepted:
        gate.start_background()
        return True
    return False


class LicenseMonitor(QObject):
    """While the app runs: if authorization is lost (locked, suspended, expired grace...), block the
    screen with the license dialog until it is restored or the user exits."""

    def __init__(self, gate, on_exit, interval_ms=5_000):
        super().__init__()
        self.gate, self.on_exit, self._showing = gate, on_exit, False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(interval_ms)

    def tick(self):
        if self._showing or self.gate.is_authorized():
            return
        self._showing = True
        try:
            if LicenseDialog(self.gate).exec() != QDialog.DialogCode.Accepted:
                self.on_exit()
        finally:
            self._showing = False
