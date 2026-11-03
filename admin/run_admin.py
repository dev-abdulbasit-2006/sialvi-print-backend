"""
Single entry point for the Admin application.

Starts the Phase 1 Flask server (database, printer manager, queue
worker - everything from admin/server/app.py) on a background thread,
waits for it to become reachable, then launches the Qt desktop UI in
the main thread (Qt requires the GUI event loop to own the main
thread on Windows).

This is what SialviPrintAdmin.exe will run once packaged in Phase 4.
"""

import logging
import sys
import threading
import time

import requests

from admin.config.config import config
from admin.database.db import init_db
from admin.server.helpers import setup_logging
from admin.server.period import ensure_active_period
from admin.queue.queue_manager import queue_manager
from admin.server.app import create_app
from shared.network_discovery import AdminDiscoveryResponder

logger = logging.getLogger("run_admin")

APP_VERSION = "1.0"

STARTUP_TIMEOUT_SECONDS = 15


def start_backend():
    """Runs the Flask app on a background thread. Never returns."""
    app = create_app()
    app.run(host=config.server_host, port=config.server_port,
             threaded=True, debug=False, use_reloader=False)


def _port_is_free(host, port):
    import socket
    probe = "127.0.0.1" if host in ("0.0.0.0", "", None) else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        return sk.connect_ex((probe, int(port))) != 0   # nonzero = nothing accepted the connection


def wait_for_backend_ready():
    url = f"http://127.0.0.1:{config.server_port}/api/health"
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code == 200:
                from admin.server.app import INSTANCE_ID
                try:
                    return r.json().get("instance") == INSTANCE_ID   # a stale/other server on this port is NOT ready
                except ValueError:
                    return False
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.3)
    return False


def main():
    setup_logging()
    logger.info("Starting SialviPrint Admin application...")

    # ---- LICENSE GATE: nothing below (database, queue, server, printing, UI) starts without a
    # valid license issued from the Owner License Manager. See shared/license_gate.py.
    from PySide6.QtWidgets import QApplication, QDialog
    from admin.ui.styles import STYLESHEET
    qt_app = QApplication(sys.argv)
    qt_app.setStyleSheet(STYLESHEET)
    from shared.license_gate import init_gate
    from shared.license_ui import ensure_licensed, LicenseMonitor
    gate = init_gate("Admin", APP_VERSION)
    if not ensure_licensed(gate):
        logger.info("No valid license - Admin exiting.")
        return

    if not _port_is_free(config.server_host, config.server_port):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "SialviPrint Admin",
            f"Port {config.server_port} is already in use by another program - most likely an old copy of "
            "SialviPrint Admin that is still running.\n\nClose all Admin windows, end 'SialviPrintAdmin.exe' in "
            "Task Manager (Details tab), then start Admin again.")
        logger.error("Port %s already in use - exiting.", config.server_port)
        return

    init_db()
    ensure_active_period()
    queue_manager.start()

    # UDP discovery is only for finding the Admin on the same LAN/router.
    # The actual API remains authenticated HTTP on TCP 8765.
    discovery = AdminDiscoveryResponder(server_port=config.server_port)
    discovery.start()

    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()

    if not wait_for_backend_ready():
        logger.error("Backend server did not become ready within %ss.", STARTUP_TIMEOUT_SECONDS)
        # Continue anyway - the UI's own error banners will report
        # "cannot reach Admin server" clearly rather than crashing here.
        # A fully offline backend is still worth showing the UI for,
        # so the admin can see the problem rather than getting nothing.

    # PySide6 import is deferred to here so this file can still be
    # imported/tested on a machine without PySide6 installed (e.g. a
    # CI check of just the backend).
    from admin.ui.main_window import MainWindow
    from admin.ui.password_dialog import PasswordDialog
    from admin.security import has_password

    # First launch asks the owner to create the password; later launches
    # require it before the Admin console is shown.
    pw_dialog = PasswordDialog(first_run=not has_password())
    if pw_dialog.exec() != QDialog.DialogCode.Accepted:
        return

    window = MainWindow(license_gate=gate)
    window.show()

    # If authorization is lost while running (locked/suspended/offline grace over) the screen is
    # blocked by the license dialog; queued print jobs stay queued and resume afterwards.
    monitor = LicenseMonitor(gate, qt_app.quit)  # noqa: F841 (must stay referenced)

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
