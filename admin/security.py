"""Local Admin application password protection.

The password is stored as a salted PBKDF2 hash in the Admin SQLite
system_settings table. It is independent from the LAN API token.
"""
import base64
import hashlib
import hmac
import os
from admin.database.db import get_conn, transaction

HASH_KEY = "admin_password_hash"
SALT_KEY = "admin_password_salt"
ITERATIONS = 240_000


def _derive(password: str, salt: bytes) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return base64.b64encode(raw).decode("ascii")


def has_password() -> bool:
    conn = get_conn()
    return bool(conn.execute("SELECT value FROM system_settings WHERE key = ?", (HASH_KEY,)).fetchone())


def set_password(password: str) -> None:
    if len(password) < 4:
        raise ValueError("Password must be at least 4 characters.")
    salt = os.urandom(16)
    digest = _derive(password, salt)
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings(key,value,updated_at) VALUES(?,?,datetime('now'))", (HASH_KEY, digest))
        conn.execute("INSERT OR REPLACE INTO system_settings(key,value,updated_at) VALUES(?,?,datetime('now'))", (SALT_KEY, base64.b64encode(salt).decode("ascii")))


def verify_password(password: str) -> bool:
    conn = get_conn()
    row_hash = conn.execute("SELECT value FROM system_settings WHERE key = ?", (HASH_KEY,)).fetchone()
    row_salt = conn.execute("SELECT value FROM system_settings WHERE key = ?", (SALT_KEY,)).fetchone()
    if not row_hash or not row_salt:
        return False
    try:
        salt = base64.b64decode(row_salt["value"])
    except Exception:
        return False
    return hmac.compare_digest(_derive(password, salt), row_hash["value"])
