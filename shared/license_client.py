"""Minimal Sialvi licensing client (library + used by licensing/client_demo.py).

It is NOT wired into the printing apps yet (Feature 1 = server + owner tool + proof of
enforcement). To protect a feature later, call ``client.check_authorization()`` before it
and ``client.refresh_if_due()`` from a timer.

Security notes
* Talks HTTPS only (certificates verified). There is no plain-HTTP or local-server mode.
* Authorization is a server-signed Ed25519 token. Editing the local state file cannot
  extend it; a tampered token fails signature verification.
* Offline policy: the token lifetime IS the offline grace period (default 72 h, set by the
  server). After it expires, check_authorization() returns unauthorized until a successful
  check-in. A server refusal (locked / suspended / revoked ...) is remembered and enforced
  immediately, even offline, until a later check-in succeeds.
* A disconnected PC cannot be locked instantly - only at its next check-in or when the
  grace period ends.
"""
import json
import os
import platform
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from urllib.parse import urlparse

from shared.license_token import TokenError, verify_token

CLOCK_TOLERANCE_SECONDS = 300
DEFAULT_POLL_SECONDS = 30            # how often a PC asks the server for commands (the server may change it: 10-300 s)
FORCED_REFRESH_MIN_GAP = 120         # without a command, never force a check-in more often than this


def _locked(fn):
    """Serialize operations that read/write the local license state (UI thread vs. background threads)."""
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


def require_https(url: str):
    """Licensing is cloud-only: an https:// address with a host name is the only thing accepted."""
    p = urlparse(url or "")
    if p.scheme != "https" or not p.hostname:
        raise ValueError("Licensing requires the cloud server's https:// address.")


def validate_public_key_pem(pem: str):
    """The server signs with Ed25519, so only an Ed25519 public key can verify its tokens."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = serialization.load_pem_public_key((pem or "").encode("ascii"))
    except Exception as exc:
        raise ValueError("The embedded licensing public key is missing or invalid.") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("The embedded licensing public key must be an Ed25519 key (an RSA key cannot verify the server's tokens).")


@dataclass
class AuthState:
    authorized: bool
    code: str = ""
    message: str = ""
    expires_at: int = 0
    seconds_left: int = 0


class LicenseClient:
    def __init__(self, server_url, state_dir, public_key_pem, app_version="0.0.0",
                 timeout=(10, 45), ca_bundle=None):
        # timeout = (connect, read): the free cloud host may need ~30-50 s to wake up after being idle.
        self.server = server_url.rstrip("/")
        self._check_server_url(self.server)
        validate_public_key_pem(public_key_pem)
        self.state_path = Path(state_dir) / "license_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.public_key = public_key_pem
        self.app_version = app_version
        self.timeout = timeout
        self.verify = ca_bundle or True          # certificate validation stays ON
        self._lock = threading.RLock()
        self.poll_interval = DEFAULT_POLL_SECONDS
        self.last_contact_ok = False             # did the most recent refresh() really reach the server?
        self._last_forced_refresh = 0.0
        self.state = self._load()

    @staticmethod
    def _check_server_url(url):
        require_https(url)

    # ---------------------------------------------------------------- state
    def _load(self):
        try:
            return json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_path)

    def _post(self, path, payload, timeout=None):
        return requests.post(self.server + path, json=payload, timeout=timeout or self.timeout, verify=self.verify,
                             proxies={} if self.server.startswith("http://127.") else None)

    # ------------------------------------------------------------ operations
    @_locked
    def activate(self, serial_key, device_name=None, fresh_installation=False) -> AuthState:
        """fresh_installation=True registers this PC as a NEW installation (used when replacing the
        license with a different serial key; the server refuses to move an installation between licenses)."""
        inst_id = str(uuid.uuid4()) if fresh_installation else (self.state.get("installation_id") or str(uuid.uuid4()))
        payload = {"serial_key": serial_key, "installation_id": inst_id,
                   "device_name": device_name or socket.gethostname(), "app_version": self.app_version,
                   "os_info": f"{platform.system()} {platform.release()}"}
        try:
            resp = self._post("/api/v1/activate", payload)
        except requests.RequestException:
            return AuthState(False, "NETWORK", "Cannot reach the licensing server. Check the internet connection and try again (the cloud server may need up to a minute to wake up).")
        return self._handle(resp, inst_id)

    @_locked
    def refresh(self) -> AuthState:
        """Contact the server and renew the token. Network errors keep the current token."""
        self.last_contact_ok = False
        if not self.state.get("installation_id"):
            return AuthState(False, "NOT_ACTIVATED", "This PC is not activated.")
        payload = {"installation_id": self.state["installation_id"],
                   "installation_secret": self.state.get("installation_secret"), "app_version": self.app_version}
        try:
            resp = self._post("/api/v1/checkin", payload)
        except requests.RequestException:
            st = self.check_authorization()
            if st.authorized:
                st.message = "Server unreachable - working offline within the grace period."
            return st
        self.last_contact_ok = True
        return self._handle(resp, self.state["installation_id"])

    def refresh_if_due(self) -> AuthState:
        interval = self.state.get("interval", 21600)
        if time.time() - self.state.get("last_checkin_attempt", 0) >= interval:
            self.state["last_checkin_attempt"] = int(time.time())
            self._save()
            return self.refresh()
        return self.check_authorization()

    @_locked
    def replace_license(self, serial_key, device_name=None) -> AuthState:
        """Activate a different serial key. The CURRENT license is kept unless the new one is accepted:
        on any failure (invalid/expired/revoked key, device limit, network, bad response) the previous
        local state is restored exactly. On success the old installation slot is released (best effort)."""
        import copy
        snapshot = copy.deepcopy(self.state)
        old_id, old_secret = snapshot.get("installation_id"), snapshot.get("installation_secret")
        self.state = {}
        st = self.activate(serial_key, device_name, fresh_installation=True)
        if not st.authorized:
            self.state = snapshot
            self._save()
            return st
        if old_id and old_secret:
            try:
                self._post("/api/v1/deactivate", {"installation_id": old_id, "installation_secret": old_secret})
            except requests.RequestException:
                pass                                  # the Owner can free the old slot manually
        return st

    # ------------------------------------------------------------ remote commands (poll / acknowledge)
    def poll(self):
        """Ask the server for its current decision and any pending commands. Returns the answer as a dict, or None when
        the server cannot be reached / is busy (then nothing changes: the signed token and its grace period stay in force).
        The answer is only a HINT: it never grants access by itself - any change is confirmed with a signed check-in."""
        if not self.state.get("installation_id"):
            return None
        payload = {"installation_id": self.state["installation_id"], "installation_secret": self.state.get("installation_secret")}
        try:
            resp = self._post("/api/v1/poll", payload, timeout=(5, 20))
        except requests.RequestException:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if resp.status_code == 200 and isinstance(data, dict) and "authorized" in data:
            return data
        if resp.status_code in (401, 403):
            err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            return {"authorized": False, "code": err.get("code", "DENIED"), "commands": []}
        return None                                   # 404 (old server), 429, 5xx ... : try again later

    def ack_command(self, command_id, status, result=""):
        try:
            resp = self._post("/api/v1/ack-command", {"installation_id": self.state.get("installation_id"),
                              "installation_secret": self.state.get("installation_secret"), "command_id": command_id,
                              "status": status, "result": str(result)[:300]}, timeout=(5, 20))
            return resp.status_code == 200
        except requests.RequestException:
            return False                              # the server keeps it 'delivered' and re-sends it at the next poll

    @staticmethod
    def judge_command(command_type, st):
        """Did this PC really end up in the state the command asked for? -> (status, text) for the acknowledgement."""
        expected = {"SUSPEND_LICENSE": "LICENSE_SUSPENDED", "REVOKE_LICENSE": "LICENSE_REVOKED",
                    "DISABLE_DEVICE": "INSTALLATION_LOCKED", "REVOKE_DEVICE": "INSTALLATION_DEACTIVATED"}
        if command_type in expected:
            ok = (not st.authorized) and st.code == expected[command_type]
        elif command_type in ("REACTIVATE_LICENSE", "REENABLE_DEVICE"):
            ok = st.authorized
        else:                                         # FORCE_REVALIDATE: re-checking with the server is the whole job
            ok = True
        now = "authorized" if st.authorized else (st.code or "refused")
        if ok:
            return "applied", f"Applied on this PC (state now: {now})"
        return "rejected", f"State on this PC is {now} (a newer command may have superseded this one)"

    @_locked
    def poll_cycle(self):
        """One remote-control cycle (called every ~30 s by the background thread).
        Returns the new AuthState when something changed on this PC, otherwise None (quiet: no check-in, no writes)."""
        data = self.poll()
        if data is None:
            return None
        try:
            self.poll_interval = max(10, min(300, int(data.get("poll_interval_seconds", self.poll_interval))))
        except (TypeError, ValueError):
            pass
        commands = [c for c in (data.get("commands") or []) if isinstance(c, dict) and c.get("command_id")]
        local = self.check_authorization()
        if bool(data.get("authorized")) == local.authorized and not commands:
            return None
        if not commands and time.time() - self._last_forced_refresh < FORCED_REFRESH_MIN_GAP:
            return None                               # disagreement we already tried to resolve a moment ago
        self._last_forced_refresh = time.time()
        st = self.refresh()                           # the signed, authoritative answer (also persists a refusal)
        if self.last_contact_ok:
            for c in commands:
                status, text = self.judge_command(str(c.get("command_type", "")), st)
                self.ack_command(c["command_id"], status, text)
        return st

    def deactivate(self) -> bool:
        """Free this PC's slot (e.g. before replacing the computer)."""
        resp = self._post("/api/v1/deactivate", {"installation_id": self.state.get("installation_id"),
                                                  "installation_secret": self.state.get("installation_secret")})
        if resp.status_code == 200:
            self.state = {}
            self._save()
            return True
        return False

    def _handle(self, resp, inst_id) -> AuthState:
        try:
            data = resp.json()
        except ValueError:
            return AuthState(False, "BAD_RESPONSE", "Unexpected response from the licensing server.")
        if resp.status_code == 200 and data.get("authorized"):
            try:
                payload = verify_token(data["token"], self.public_key)
            except TokenError:
                return AuthState(False, "BAD_SIGNATURE", "The server's authorization could not be verified.")
            self.state.update({"installation_id": inst_id, "token": data["token"], "denied": None,
                               "interval": int(data.get("checkin_interval_minutes", 360)) * 60,
                               "last_ok": int(time.time())})
            if data.get("installation_secret"):
                self.state["installation_secret"] = data["installation_secret"]
            self._save()
            return self.check_authorization()
        err = (data or {}).get("error", {})
        if resp.status_code in (401, 403):
            # Definitive refusal: remember it so the PC stays refused even while offline.
            self.state.update({"token": None, "denied": {"code": err.get("code", "DENIED"), "message": err.get("message", "Denied")}})
            self._save()
        return AuthState(False, err.get("code", "ERROR"), err.get("message", "Request failed."))

    def check_authorization(self) -> AuthState:
        """Local, offline decision. Call before any protected operation."""
        denied = self.state.get("denied")
        if denied:
            return AuthState(False, denied["code"], denied["message"])
        token = self.state.get("token")
        if not token:
            return AuthState(False, "NOT_ACTIVATED", "This PC is not activated.")
        try:
            payload = verify_token(token, self.public_key)
        except TokenError:
            return AuthState(False, "BAD_SIGNATURE", "The stored authorization is invalid.")
        now = time.time()
        if payload.get("inst") != self.state.get("installation_id"):
            return AuthState(False, "BAD_SIGNATURE", "The stored authorization belongs to another installation.")
        if now + CLOCK_TOLERANCE_SECONDS < self.state.get("last_ok", 0):
            return AuthState(False, "CLOCK_ROLLBACK", "The system clock moved backwards. Connect to the licensing server.")
        if now >= payload["exp"]:
            return AuthState(False, "GRACE_EXPIRED", "Authorization expired. Connect to the licensing server to continue.",
                             payload["exp"], 0)
        return AuthState(True, "OK", "Authorized.", payload["exp"], int(payload["exp"] - now))
