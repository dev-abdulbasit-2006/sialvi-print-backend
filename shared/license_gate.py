"""License gate shared by the Admin and User apps (no Qt in this file).

* Without a valid license the apps do not start their work: no orders, no printing.
* The decision is made from a server-signed token (shared/license_client.py); a background
  thread renews it. A server refusal (locked / suspended / revoked / limit...) is enforced at once.
* Pending print jobs are NEVER deleted: when unauthorized, work simply pauses (queued jobs stay
  queued) and resumes when authorization returns.
* Dev bypass exists ONLY when running from source (not a PyInstaller build).
"""
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

from shared import license_embedded
from shared.license_client import AuthState, LicenseClient

logger = logging.getLogger("license_gate")

CHECK_EVERY_SECONDS = 300          # how often the background thread looks (server contact follows the server's interval)
FROZEN = bool(getattr(sys, "frozen", False))


def license_state_dir() -> Path:
    """One license identity per PC, shared by the Admin and User apps (so both on one PC = one slot).
    Falls back to the per-user folder if the machine-wide one is not writable."""
    for base in (os.environ.get("PROGRAMDATA"), os.environ.get("LOCALAPPDATA"), str(Path.home() / ".local" / "share")):
        if not base:
            continue
        d = Path(base) / "SialviPrint" / "license"
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".w"
            probe.write_text("x")
            probe.unlink()
            return d
        except OSError:
            continue
    return Path.home() / ".sialviprint_license"


class LicenseGate:
    def __init__(self, app_role: str, app_version: str = "1.0"):
        self.role = app_role
        url, pem = license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM   # cloud licensing only
        self.configured = bool(url and pem)
        self.client = None
        if self.configured:
            try:
                self.client = LicenseClient(url, license_state_dir(), pem, f"{app_role} {app_version}")
            except ValueError as exc:
                logger.error("Licensing configuration rejected: %s", exc)
                self.configured = False
        self._state = AuthState(False, "NOT_ACTIVATED", "This PC is not activated.")
        self._lock = threading.Lock()
        self._listeners = []
        self._stop = threading.Event()
        self._thread = None
        self._poll_thread = None
        self.refresh_local()

    # ------------------------------------------------------------- state
    def state(self) -> AuthState:
        with self._lock:
            return self._state

    def _set(self, st: AuthState):
        with self._lock:
            changed = (st.authorized != self._state.authorized) or (st.code != self._state.code)
            self._state = st
        if changed:
            logger.warning("License state: authorized=%s code=%s", st.authorized, st.code)
            for cb in list(self._listeners):
                try:
                    cb(st)
                except Exception:
                    logger.exception("license listener failed")

    def refresh_local(self) -> AuthState:
        if not self.client:
            self._set(AuthState(False, "NOT_CONFIGURED",
                                "This copy of SialviPrint has no licensing server key embedded (it was built before the server key was fetched). Contact your software provider."))
        else:
            self._set(self.client.check_authorization())
        return self.state()

    def is_authorized(self) -> bool:
        """Fast check used before every protected operation (order, print). Re-verifies the stored
        token locally each time, so token expiry is noticed immediately even between server contacts."""
        if self.client:
            self._set(self.client.check_authorization())
        return self.state().authorized

    # ------------------------------------------------------- server contact
    def activate(self, serial_key: str, device_name: str | None = None) -> AuthState:
        if not self.client:
            return self.refresh_local()
        st = self.client.activate(serial_key.strip(), device_name or socket.gethostname())
        self._set(st)
        return st

    def replace_license(self, serial_key: str, device_name: str | None = None) -> AuthState:
        """Reset License: validate a new serial key with the licensing server and replace the local
        license ONLY if it is accepted. Otherwise the previous license stays untouched."""
        if not self.client:
            return self.refresh_local()
        st = self.client.replace_license(serial_key.strip(), device_name or socket.gethostname())
        if st.authorized:
            self._set(st)
        else:
            self.refresh_local()                      # state restored by the client; re-evaluate it
        return st

    def refresh_now(self) -> AuthState:
        if not self.client:
            return self.refresh_local()
        st = self.client.refresh()
        self._set(st)
        return st

    def start_background(self):
        if self._thread or not self.client:
            return
        def loop():
            while not self._stop.wait(CHECK_EVERY_SECONDS):
                try:
                    if self.client.state.get("installation_id"):
                        self._set(self.client.refresh_if_due())
                except Exception:
                    logger.exception("license check failed")
        self._thread = threading.Thread(target=loop, name="SialviLicenseCheck", daemon=True)
        self._thread.start()

        def poll_loop():
            # Remote control: every ~30 s ask the server for its current decision + pending owner commands. A suspend /
            # lock / reactivate therefore reaches this PC within about one poll interval instead of the 6-hour check-in.
            while not self._stop.wait(self.client.poll_interval):
                try:
                    if self.client.state.get("installation_id"):
                        st = self.client.poll_cycle()
                        if st is not None:
                            self._set(st)
                except Exception:
                    logger.exception("license poll failed")
        self._poll_thread = threading.Thread(target=poll_loop, name="SialviLicensePoll", daemon=True)
        self._poll_thread.start()

    def stop(self):
        self._stop.set()

    def add_listener(self, cb):
        self._listeners.append(cb)


# --------------------------------------------------------------------------- module singleton
_gate: LicenseGate | None = None


def init_gate(app_role: str, app_version: str = "1.0") -> LicenseGate:
    global _gate
    _gate = LicenseGate(app_role, app_version)
    return _gate


def get_gate():
    return _gate


def license_ok() -> bool:
    """Used by the backends. In a packaged build a missing gate means NOT licensed (fail closed).
    From source (tests / development) with no gate initialised, the check is skipped."""
    if _gate is None:
        return not FROZEN
    return _gate.is_authorized()


def license_message() -> str:
    return _gate.state().message if _gate else "License check not initialised."
