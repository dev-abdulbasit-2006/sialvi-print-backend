"""Licensing-server settings. All values can be set with environment variables
(see licensing/server.env.example). No secrets are hard-coded."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    base = os.environ.get("PROGRAMDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "SialviLicenseServer"


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"           # bind publicly only behind HTTPS (reverse proxy / --cert)
    port: int = 8780
    # Comma-separated IPs/CIDRs of reverse proxies whose X-Forwarded-For is trusted. Default: none.
    trusted_proxies: tuple = ()
    # Client policy (documented in documentation/LICENSING_FEATURE1.md)
    checkin_interval_minutes: int = 360     # how often a client should renew
    offline_grace_hours: int = 72           # token lifetime == how long a PC works offline
    not_recent_minutes: int = 720           # dashboard: "Not recently seen" after this long
    # Owner login
    owner_session_hours: int = 8
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    # Client endpoint rate limit (per observed IP)
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    # Remote command channel: PCs poll this often (seconds); a command not delivered within this many hours expires.
    poll_interval_seconds: int = 30
    command_ttl_hours: int = 72
    poll_rate_limit_requests: int = 300     # separate, higher per-IP limit for the light poll/ack calls (many PCs share one shop IP)
    # Optional secrets override (otherwise generated into data_dir on first run)
    key_pepper: str = ""
    # postgresql://... : PERSISTENT storage (licenses, PCs, owner login survive restarts). Empty = local SQLite file.
    database_url: str = ""
    signing_key_pem: str = ""         # LICENSE_SIGNING_KEY_PEM (PEM or base64 of it) for hosts without a persistent disk

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        if os.environ.get("LICENSE_DATA_DIR"):
            s.data_dir = Path(os.environ["LICENSE_DATA_DIR"])
        s.host = os.environ.get("LICENSE_HOST", s.host)
        s.port = _int("LICENSE_PORT", s.port)
        s.trusted_proxies = tuple(p.strip() for p in os.environ.get("LICENSE_TRUSTED_PROXIES", "").split(",") if p.strip())
        s.checkin_interval_minutes = _int("LICENSE_CHECKIN_INTERVAL_MINUTES", s.checkin_interval_minutes)
        s.offline_grace_hours = _int("LICENSE_OFFLINE_GRACE_HOURS", s.offline_grace_hours)
        s.not_recent_minutes = _int("LICENSE_NOT_RECENT_MINUTES", s.not_recent_minutes)
        s.owner_session_hours = _int("LICENSE_OWNER_SESSION_HOURS", s.owner_session_hours)
        s.poll_interval_seconds = max(10, min(300, _int("LICENSE_POLL_INTERVAL_SECONDS", s.poll_interval_seconds)))
        s.command_ttl_hours = max(1, _int("LICENSE_COMMAND_TTL_HOURS", s.command_ttl_hours))
        s.poll_rate_limit_requests = _int("LICENSE_POLL_RATE_LIMIT", s.poll_rate_limit_requests)
        s.key_pepper = os.environ.get("LICENSE_KEY_PEPPER", "")
        s.signing_key_pem = os.environ.get("LICENSE_SIGNING_KEY_PEM", "")
        s.database_url = (os.environ.get("DATABASE_URL") or os.environ.get("LICENSE_DATABASE_URL") or "").strip()
        return s
