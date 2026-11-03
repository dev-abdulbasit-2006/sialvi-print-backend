"""
User (kiosk) app configuration.

Stores the Admin PC's address/port, the shared LAN token, and a
persistent station_id unique to this physical PC. station_id is what
guarantees session isolation between User PCs (requirement 17) - it is
generated once and stored on disk, not regenerated per run, so the
Admin dashboard's "active stations" count is meaningful.
"""

import json
import os
import sys
import uuid
import socket
from pathlib import Path

APP_NAME = "SialviPrintUser"


def _base_data_dir() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    else:
        root = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(root) / APP_NAME


class UserConfig:
    DEFAULTS = {
        "admin_host": "",       # must be set on first run via the setup dialog
        "admin_port": 8765,
        "lan_shared_token": "",  # must match the Admin PC's token exactly
        "station_id": "",        # generated once, persisted
        "kiosk_mode": False,     # if True, hides window chrome / disables Alt+F4 (Phase 3.1 nice-to-have)
    }

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or _base_data_dir()
        self.tmp_dir = self.base_dir / "tmp"
        self.config_path = self.base_dir / "config.json"

        for d in (self.base_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

        self._settings = dict(self.DEFAULTS)
        self._load()
        self._ensure_station_id()
        self._ensure_station_key()

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._settings.update(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
        else:
            self._save()

    def _save(self):
        tmp_path = self.config_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)
        os.replace(tmp_path, self.config_path)

    def _ensure_station_id(self):
        if not self._settings.get("station_id"):
            hostname = socket.gethostname()
            self._settings["station_id"] = f"{hostname}-{uuid.uuid4().hex[:8]}"
            self._save()

    def _ensure_station_key(self):
        if not self._settings.get("station_key"):
            self._settings["station_key"] = uuid.uuid4().hex + uuid.uuid4().hex
            self._save()

    def get(self, key, default=None):
        return self._settings.get(key, default)

    def set(self, key, value):
        self._settings[key] = value
        self._save()

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.get("admin_host")) and bool(self._settings.get("lan_shared_token"))

    @property
    def admin_host(self):
        return self._settings["admin_host"]

    @property
    def admin_port(self):
        return int(self._settings["admin_port"])

    @property
    def lan_shared_token(self):
        return self._settings["lan_shared_token"]

    @property
    def station_id(self):
        return self._settings["station_id"]

    @property
    def computer_name(self):
        return socket.gethostname()

    @property
    def station_key(self):
        return self._settings["station_key"]

    @property
    def base_url(self):
        return f"http://{self.admin_host}:{self.admin_port}"


config = UserConfig()
