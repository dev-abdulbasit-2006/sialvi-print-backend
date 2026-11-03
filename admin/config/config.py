"""
Admin configuration.

Never hardcode developer-machine paths. All paths are resolved from
Windows-appropriate application directories (%PROGRAMDATA% for shared
service data), with sane fallbacks for non-Windows dev/test environments.
"""

import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "SialviPrint"

_lock = threading.Lock()


def _base_data_dir() -> Path:
    """
    Returns the root directory for all persistent Admin data
    (database, uploads, logs, config). Uses ProgramData on Windows so
    the service survives user profile changes and works regardless of
    which Windows account launches the app.
    """
    if sys.platform == "win32":
        root = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    else:
        # Dev/test fallback (Linux/Mac) - never used on a real deployment
        root = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(root) / APP_NAME


class AdminConfig:
    """
    Loads/saves persistent JSON configuration. Thread-safe for the
    simple read-modify-write pattern used by the settings screen.
    """

    DEFAULTS = {
        "server_host": "0.0.0.0",   # bind all LAN interfaces
        "server_port": 8765,
        "lan_shared_token": "",     # generated on first run, see ensure_token()
        "max_upload_bytes": 500 * 1024 * 1024,   # 500 MB hard ceiling (>200MB required)
        "upload_chunk_bytes": 4 * 1024 * 1024,   # 4 MB streaming chunks
        "log_level": "INFO",
    }

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or _base_data_dir()
        self.data_dir = self.base_dir / "data"
        self.uploads_dir = self.base_dir / "uploads"
        self.tmp_dir = self.base_dir / "tmp"
        self.logs_dir = self.base_dir / "logs"
        self.db_path = self.data_dir / "printing_system.db"
        self.config_path = self.base_dir / "config.json"

        for d in (self.base_dir, self.data_dir, self.uploads_dir, self.tmp_dir, self.logs_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
                # A previous elevated installer/app run may have created
                # ProgramData\SialviPrint with an ACL that excludes the normal
                # logged-in user. Do not expose pathlib's misleading
                # FileExistsError/PermissionError chain as the root cause.
                raise PermissionError(
                    f"SialviPrint cannot access its Admin data directory '{d}'. "
                    "Run the current Admin installer again so its ProgramData "
                    "folder permissions are repaired, then start the app normally."
                ) from exc

        self._settings = dict(self.DEFAULTS)
        self._load()
        self.ensure_token()

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._settings.update(loaded)
            except (json.JSONDecodeError, OSError):
                # Corrupt config must never crash startup; fall back to defaults
                # and let the admin re-save via the settings screen.
                pass
        else:
            self._save()

    def _save(self):
        with _lock:
            tmp_path = self.config_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
            os.replace(tmp_path, self.config_path)  # atomic on Windows & POSIX

    def ensure_token(self):
        """Generate a shared LAN auth token on first run if missing."""
        if not self._settings.get("lan_shared_token"):
            import secrets
            self._settings["lan_shared_token"] = secrets.token_hex(24)
            self._save()

    def get(self, key, default=None):
        return self._settings.get(key, default)

    def set(self, key, value):
        self._settings[key] = value
        self._save()

    @property
    def server_host(self):
        return self._settings["server_host"]

    @property
    def server_port(self):
        return int(self._settings["server_port"])

    @property
    def lan_shared_token(self):
        return self._settings["lan_shared_token"]

    @property
    def max_upload_bytes(self):
        return int(self._settings["max_upload_bytes"])

    @property
    def upload_chunk_bytes(self):
        return int(self._settings["upload_chunk_bytes"])


# Module-level singleton, imported by server components.
config = AdminConfig()
