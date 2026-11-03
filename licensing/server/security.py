"""Key generation, password hashing and secret handling. Nothing here logs secrets."""
import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from shared.license_token import generate_private_key_pem, load_private_key

# 31 symbols without look-alikes (0/O, 1/I/L): 16 symbols ~ 79 bits of CSPRNG entropy
KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PBKDF2_ITERATIONS = 240_000


def generate_serial_key() -> str:
    """SIALVI-XXXX-XXXX-XXXX-XXXX from the OS CSPRNG (secrets module)."""
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "SIALVI-" + "-".join(groups)


def normalize_key(key: str) -> str:
    return "".join(str(key).upper().split())


def hash_key(key: str, pepper: bytes) -> str:
    """Keyed hash (HMAC-SHA256). The database alone cannot be used to recover keys."""
    return hmac.new(pepper, normalize_key(key).encode("utf-8"), hashlib.sha256).hexdigest()


def key_hint(key: str) -> str:
    return normalize_key(key)[-4:]


def hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS):
    salt = salt or os.urandom(16)
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(raw).decode(), base64.b64encode(salt).decode(), iterations


def verify_password(password: str, stored_hash: str, stored_salt: str, iterations: int) -> bool:
    calc, _, _ = hash_password(password, base64.b64decode(stored_salt), iterations)
    return hmac.compare_digest(calc, stored_hash)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    """Fast hash is fine here: the secrets are 256-bit random values, not passwords."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_matches(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret or ""), stored_hash)


def _write_private(path: Path, data: bytes):
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _decode_pem_env(value: str) -> bytes:
    """Accept a PEM pasted into a hosting dashboard: real newlines, literal \\n, or base64 of the PEM."""
    v = value.strip()
    if "BEGIN" in v:
        return v.replace("\\n", "\n").encode("ascii")
    import base64
    return base64.b64decode(v)


def load_or_create_server_secrets(data_dir: Path, pepper_override: str = "", signing_key_override: str = ""):
    """Return (key_pepper_bytes, signing_private_key). Generated once, stored in data_dir
    (never in source control, never in the DB, never sent to clients)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    pepper_file = data_dir / "key_pepper.secret"
    if pepper_override:
        pepper = pepper_override.encode("utf-8")
    else:
        if not pepper_file.exists():
            _write_private(pepper_file, secrets.token_bytes(32))
        pepper = pepper_file.read_bytes()
    if signing_key_override:
        # Hosts with an ephemeral disk (e.g. Render free tier) keep the identity in an environment secret instead.
        return pepper, load_private_key(_decode_pem_env(signing_key_override))
    sign_file = data_dir / "signing_key.pem"
    if not sign_file.exists():
        _write_private(sign_file, generate_private_key_pem())
    return pepper, load_private_key(sign_file.read_bytes())
