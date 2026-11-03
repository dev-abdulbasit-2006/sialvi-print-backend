"""Signed authorization tokens (Ed25519) shared by the licensing server and clients.

The server signs; the client only needs the PUBLIC key to verify. A customer
cannot forge or extend a token by editing local files because they do not
hold the private key.
Token format:  base64url(JSON payload) + "." + base64url(signature)
"""
import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class TokenError(Exception):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_private_key_pem() -> bytes:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(pem, password=None)


def public_key_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def sign_token(payload: dict, private_key: Ed25519PrivateKey) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64e(body) + "." + _b64e(private_key.sign(body))


def verify_token(token: str, public_pem: str) -> dict:
    """Return the payload if the signature is valid; raise TokenError otherwise.
    Expiry is checked by the caller (it needs the clock policy)."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body, sig = _b64d(body_b64), _b64d(sig_b64)
        pub = serialization.load_pem_public_key(public_pem.encode("ascii"))
        if not isinstance(pub, Ed25519PublicKey):
            raise TokenError("wrong key type")
        pub.verify(sig, body)
        return json.loads(body)
    except TokenError:
        raise
    except (ValueError, InvalidSignature, TypeError, UnicodeError) as exc:
        raise TokenError("invalid token") from exc
