"""
Entry point for the User (kiosk) application. This is what
SialviPrintUser.exe runs once packaged in Phase 4.
"""

import logging
import logging.handlers
import sys
import threading
import time

from PySide6.QtWidgets import QApplication, QDialog

from user.config.config import config


APP_VERSION = "1.0"


def setup_logging():
    log_path = config.base_dir / "user_app.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)


def main():
    setup_logging()
    logger = logging.getLogger("run_user")
    logger.info("Starting SialviPrint User application. station_id=%s", config.station_id)

    # ---- LICENSE GATE: nothing (print agent, heartbeat, UI) starts without a valid license issued
    # from the Owner License Manager. See shared/license_gate.py.
    qt_app = QApplication(sys.argv)
    from user.ui.styles import STYLESHEET
    qt_app.setStyleSheet(STYLESHEET)
    from shared.license_gate import init_gate
    from shared.license_ui import ensure_licensed, LicenseMonitor
    gate = init_gate("User", APP_VERSION)
    if not ensure_licensed(gate):
        logger.info("No valid license - User app exiting.")
        sys.exit(0)

    # The User PC owns printer discovery and the final Windows spooler step.
    # The agent is authenticated with the same LAN token used by the Admin API.
    from user.network.print_agent import UserPrintAgent
    print_agent = UserPrintAgent()
    print_agent.start()

    def _station_heartbeat():
        # Keep the Admin's routing address fresh if DHCP changes the User PC IP.
        from user.network.api_client import api_client, ApiClientError
        while True:
            try:
                if config.is_configured:
                    api_client.register_station(print_agent.port)
            except Exception as exc:
                logger.debug("Station heartbeat unavailable: %s", exc)
            time.sleep(20)

    heartbeat = threading.Thread(target=_station_heartbeat, name="SialviPrintStationHeartbeat", daemon=True)
    heartbeat.start()

    qt_app.aboutToQuit.connect(print_agent.stop)

    if not config.is_configured:
        from user.ui.setup_dialog import SetupDialog
        dialog = SetupDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("Setup was cancelled; exiting.")
            sys.exit(0)

    # Owner sets the exit password once; after that the window can only be
    # minimized unless that password is entered (see MainWindow.closeEvent).
    from user.ui.exit_dialog import ensure_exit_password_exists
    ensure_exit_password_exists()

    from user.ui.main_window import MainWindow
    window = MainWindow()

    # Never block Windows log-off / shutdown / restart.
    def _allow_system_close(*_args):
        window.allow_close = True
    for sig_name in ("commitDataRequest", "saveStateRequest"):
        sig = getattr(qt_app, sig_name, None)
        if sig is not None:
            try:
                sig.connect(_allow_system_close)
            except Exception:
                pass

    window.show()

    # If authorization is lost while running, block the screen with the license dialog.
    def _license_exit():
        window.allow_close = True   # leaving because the license is invalid is not a customer closing the kiosk
        qt_app.quit()
    monitor = LicenseMonitor(gate, _license_exit)  # noqa: F841 (must stay referenced)

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
