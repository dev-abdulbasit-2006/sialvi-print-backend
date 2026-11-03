"""Exit-password protection for the User (kiosk) app.

The password is stored on THIS User PC only, as a salted PBKDF2-HMAC-SHA256
hash in the User config (keys ``exit_password_hash`` / ``exit_password_salt``).
It is independent of the Admin password and of the LAN connection code, and it
needs no network - so it works on an offline Ethernet LAN too.

Note: this stops a customer from closing the app with the window's X / Alt+F4.
It is not protection against a determined user with Windows tools (Task
Manager, editing config.json). Use Windows standard-user accounts for that.
"""
import base64
import hashlib
import hmac
import os

HASH_KEY = "exit_password_hash"
SALT_KEY = "exit_password_salt"
ITERATIONS = 240_000
MIN_LENGTH = 4


def _store(store=None):
    if store is not None:
        return store
    from user.config.config import config  # lazy: keeps this module testable
    return config


def _derive(password: str, salt: bytes) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return base64.b64encode(raw).decode("ascii")


def has_exit_password(store=None) -> bool:
    s = _store(store)
    return bool(s.get(HASH_KEY)) and bool(s.get(SALT_KEY))


def set_exit_password(password: str, store=None) -> None:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters.")
    s = _store(store)
    salt = os.urandom(16)
    s.set(SALT_KEY, base64.b64encode(salt).decode("ascii"))
    s.set(HASH_KEY, _derive(password, salt))


def verify_exit_password(password: str, store=None) -> bool:
    s = _store(store)
    stored, salt_b64 = s.get(HASH_KEY), s.get(SALT_KEY)
    if not stored or not salt_b64:
        return False
    try:
        salt = base64.b64decode(salt_b64)
    except Exception:
        return False
    return hmac.compare_digest(_derive(password, salt), stored)
