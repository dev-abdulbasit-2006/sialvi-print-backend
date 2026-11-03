"""
SialviPrint User-side print agent.

The Admin PC never enumerates or directly opens the User PC's printers.
After payment approval it sends the prepared print document to this small
authenticated endpoint running inside the User application. The agent then
uses Windows' local printer spooler on THIS PC.

Security:
- bound to the LAN interface (0.0.0.0) because Admin and User may be on a
  router/LAN;
- every request must carry the existing shared LAN token;
- the station ID must exactly match this installation;
- the requested printer must be installed on this same Windows PC;
- only the authenticated Admin knows the order/job identifier.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from user.config.config import config
from shared import winprint
from shared import silent_print

logger = logging.getLogger("user_print_agent")

DEFAULT_PORT = 8767
MAX_METADATA_BYTES = 64 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024


class _AgentServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _json(handler, status: int, payload: dict):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except OSError:
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "SialviPrintUserAgent/1.0"

    def log_message(self, fmt, *args):
        logger.info("Agent %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        if urlparse(self.path).path != "/agent/health":
            _json(self, 404, {"error": "Not found"})
            return
        _json(self, 200, {
            "status": "ok",
            "station_id": config.station_id,
            "port": self.server.server_address[1],
        })

    def do_POST(self):
        if urlparse(self.path).path != "/agent/print":
            _json(self, 404, {"error": "Not found"})
            return

        if self.headers.get("X-LAN-Token", "") != config.lan_shared_token:
            _json(self, 401, {"error": "Unauthorized"})
            return

        if self.headers.get("X-Station-ID", "") != config.station_id:
            _json(self, 403, {"error": "Station mismatch"})
            return
        if self.headers.get("X-Station-Key", "") != config.station_key:
            _json(self, 403, {"error": "Station authentication failed"})
            return

        job_id = self.headers.get("X-Job-ID", "").strip()
        printer_name = self.headers.get("X-Printer-Name", "").strip()
        agent = getattr(self.server, "agent", None)
        if agent is not None and agent.is_completed(job_id):
            _json(self, 200, {
                "status": "SUBMITTED",
                "job_id": job_id,
                "printer_name": printer_name,
                "windows_job_id": -1,
                "deduplicated": True,
            })
            return
        if not job_id or not printer_name:
            _json(self, 400, {"error": "Job ID and printer name are required"})
            return

        # License gate: an unlicensed User PC prints nothing. The Admin keeps the job queued.
        from shared.license_gate import license_ok
        if not license_ok():
            _json(self, 503, {"error": "LICENSE_REQUIRED: this User PC is not licensed right now."})
            return

        try:
            copies = max(1, int(self.headers.get("X-Copies", "1")))
        except ValueError:
            _json(self, 400, {"error": "Invalid copies"})
            return

        orientation = self.headers.get("X-Orientation", "AUTO").upper()
        if orientation not in ("AUTO", "PORTRAIT", "LANDSCAPE"):
            _json(self, 400, {"error": "Invalid orientation"})
            return

        page_range = self.headers.get("X-Page-Range", "")
        profile_paper = self.headers.get("X-Profile-Paper", "")
        color_mode = self.headers.get("X-Color-Mode", "")
        filename = os.path.basename(self.headers.get("X-Filename", "job.pdf")) or "job.pdf"
        settings_raw = self.headers.get("X-Print-Settings", "{}")
        if len(settings_raw.encode("utf-8")) > MAX_METADATA_BYTES:
            _json(self, 400, {"error": "Print settings are too large"})
            return
        try:
            settings = json.loads(settings_raw)
            if not isinstance(settings, dict):
                raise ValueError
        except ValueError:
            _json(self, 400, {"error": "Invalid print settings"})
            return

        content_length = self.headers.get("Content-Length")
        try:
            total = int(content_length)
        except (TypeError, ValueError):
            _json(self, 411, {"error": "Content-Length is required"})
            return
        if total <= 0 or total > MAX_FILE_BYTES:
            _json(self, 413, {"error": "Print document size is invalid"})
            return

        if not winprint.IS_WINDOWS:
            _json(self, 409, {"error": "Windows printing is required"})
            return

        local_printers = winprint.list_local_printers()
        if printer_name not in local_printers:
            # Exact match first. A case-insensitive match is allowed only when
            # Windows has the same installed printer with different casing.
            match = next((p for p in local_printers if p.lower() == printer_name.lower()), None)
            if not match:
                _json(self, 409, {
                    "error": f"Printer '{printer_name}' is not installed/available on this User PC."
                })
                return
            printer_name = match

        suffix = Path(filename).suffix.lower()
        if suffix not in silent_print.IMAGE_EXTS and suffix != ".pdf":
            _json(self, 400, {"error": "User print agent accepts prepared PDF/image jobs only"})
            return

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=f"sialvi_{job_id[:12]}_",
                suffix=suffix or ".pdf",
                dir=str(config.tmp_dir),
            )
            with os.fdopen(fd, "wb") as out:
                remaining = total
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("Connection ended before the complete print document arrived.")
                    out.write(chunk)
                    remaining -= len(chunk)

            logger.info(
                "Received remote print job %s for station=%s printer=%r bytes=%s",
                job_id, config.station_id, printer_name, total,
            )
            spool_id = silent_print.print_file(
                printer_name=printer_name,
                file_path=tmp_path,
                copies=copies,
                orientation=orientation,
                page_range=page_range,
                settings=settings,
                profile_paper=profile_paper,
                color_mode=color_mode,
            )
            if agent is not None:
                agent._remember_completed(job_id)
            _json(self, 200, {
                "status": "SUBMITTED",
                "job_id": job_id,
                "printer_name": printer_name,
                "windows_job_id": spool_id,
            })
        except Exception as exc:
            logger.exception("Remote print job %s failed on printer %r", job_id, printer_name)
            _json(self, 409, {
                "status": "FAILED",
                "job_id": job_id,
                "error": str(exc),
            })
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


class UserPrintAgent:
    """Lifecycle wrapper used by run_user.py."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = int(port)
        self.server: _AgentServer | None = None
        self.thread: threading.Thread | None = None
        self._jobs_lock = threading.Lock()
        self._completed_jobs = self._load_completed_jobs()

    @staticmethod
    def _jobs_path() -> Path:
        return config.base_dir / "completed_print_jobs.json"

    def _load_completed_jobs(self) -> list[str]:
        try:
            data = json.loads(self._jobs_path().read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            # Preserve on-disk insertion order and discard duplicate/corrupt
            # values without changing the meaning of the last-2000 retention.
            seen = set()
            ordered = []
            for value in data:
                if isinstance(value, str) and value and value not in seen:
                    seen.add(value)
                    ordered.append(value)
            return ordered[-2000:]
        except Exception:
            return []

    def _remember_completed(self, job_id: str):
        with self._jobs_lock:
            if job_id in self._completed_jobs:
                return
            self._completed_jobs.append(job_id)
            # Keep the file bounded; the oldest completed IDs are least useful.
            self._completed_jobs = self._completed_jobs[-2000:]
            try:
                self._jobs_path().write_text(json.dumps(self._completed_jobs), encoding="utf-8")
            except OSError:
                logger.warning("Could not persist completed remote print jobs.")

    def is_completed(self, job_id: str) -> bool:
        with self._jobs_lock:
            return job_id in self._completed_jobs

    def start(self):
        if self.thread and self.thread.is_alive():
            return self
        self.server = _AgentServer(("0.0.0.0", self.port), _Handler)
        self.server.agent = self
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="SialviPrintUserPrintAgent",
            daemon=True,
        )
        self.thread.start()
        logger.info("User print agent listening on 0.0.0.0:%s station=%s", self.port, config.station_id)
        return self

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        self.server = None
        self.thread = None
