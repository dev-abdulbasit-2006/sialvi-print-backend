"""
User app's HTTP client to the Admin PC.

Key requirement addressed here: files >200MB must be sent without
loading them fully into RAM. `requests` streams directly from an open
file handle passed as `data=` - it reads and sends in chunks under the
hood rather than buffering the whole file, and computes Content-Length
from the file size automatically. A thin wrapper adds progress
callbacks for the UI's progress bar.

Every network failure mode from requirement 22 is translated into a
specific, human-readable message - never a raw exception or a generic
"Internal Server Error".
"""

import logging
import os

import requests

from user.config.config import config
from shared import winprint

logger = logging.getLogger("user_app.api_client")

CONNECT_TIMEOUT = 5
READ_TIMEOUT_SHORT = 15         # ordinary API calls
READ_TIMEOUT_UPLOAD = 60 * 30   # large uploads need much more room


class ApiClientError(Exception):
    """Message is always safe to display directly to the customer/operator."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _ProgressFile:
    """Wraps an open file object so requests streams it while reporting progress."""

    def __init__(self, file_obj, total_size, on_progress=None):
        self._file = file_obj
        self._total = total_size
        self._sent = 0
        self._on_progress = on_progress

    def read(self, size=-1):
        chunk = self._file.read(size)
        if chunk:
            self._sent += len(chunk)
            if self._on_progress:
                self._on_progress(self._sent, self._total)
        return chunk

    def __len__(self):
        # requests uses len() to determine Content-Length for file-like objects
        return self._total

    def close(self):
        self._file.close()


class UserApiClient:
    def __init__(self):
        self.cfg = config

    def _headers(self, extra=None):
        headers = {"X-LAN-Token": self.cfg.lan_shared_token}
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path):
        return f"{self.cfg.base_url}{path}"

    def _translate_error(self, exc) -> str:
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return "Could not connect to the Admin server. Check the Ethernet cable/network, the Admin PC address, and that the Admin app is running."
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "Admin server is unavailable. Please check that the Admin PC is on and connected to the network."
        if isinstance(exc, requests.exceptions.ReadTimeout):
            return "The Admin server took too long to respond. Please try again."
        if isinstance(exc, requests.exceptions.ChunkedEncodingError):
            return "File upload was interrupted. Please try again."
        return "Network connection lost. Please try again."

    @staticmethod
    def _error_message(body, default):
        """Pull a readable message out of ANY error body (dict, {"error": "text"}, plain string, list)."""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg:
                    return msg
            elif isinstance(err, str) and err:
                return err
            msg = body.get("message")
            if isinstance(msg, str) and msg:
                return msg
        elif isinstance(body, str) and body.strip():
            return body.strip()[:200]
        return default

    def _handle_response(self, resp):
        if resp.status_code >= 400:
            default = f"Request failed ({resp.status_code})."
            try:
                message = self._error_message(resp.json(), default)
            except ValueError:
                message = f"Request failed with status {resp.status_code}."
            if resp.status_code == 404:
                message += (" The program answering at the Admin address is not the current SialviPrint Admin "
                            "(an old copy may still be running). Close all Admin windows and start Admin again.")
            raise ApiClientError(message)
        if resp.content:
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    # ---- Orders ----
    def create_order(self):
        try:
            resp = requests.post(
                self._url("/api/orders"),
                json={"station_id": self.cfg.station_id, "station_key": self.cfg.station_key, "computer_name": self.cfg.computer_name},
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def get_order(self, order_id):
        try:
            resp = requests.get(
                self._url(f"/api/orders/{order_id}"),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def cancel_order(self, order_id):
        try:
            resp = requests.delete(
                self._url(f"/api/orders/{order_id}"),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def upload_file(self, order_id, file_path, sequence_index, on_progress=None):
        """
        Streams file_path to the Admin PC without loading it fully into
        RAM. on_progress(bytes_sent, total_bytes) is called periodically
        for a UI progress bar.
        """
        filename = os.path.basename(file_path)
        total_size = os.path.getsize(file_path)

        try:
            f = open(file_path, "rb")
        except OSError as e:
            raise ApiClientError(f"Could not read file '{filename}': {e}")

        progress_wrapper = _ProgressFile(f, total_size, on_progress)
        try:
            resp = requests.post(
                self._url(f"/api/orders/{order_id}/files"),
                data=progress_wrapper,
                headers=self._headers({
                    "X-Filename": filename,
                    "X-Sequence-Index": str(sequence_index),
                    "Content-Type": "application/octet-stream",
                }),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_UPLOAD),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        finally:
            progress_wrapper.close()

        return self._handle_response(resp)

    def remove_file(self, order_id, file_id):
        try:
            resp = requests.delete(
                self._url(f"/api/orders/{order_id}/files/{file_id}"),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def set_options(self, order_id, printer_name, profile_id, copies, orientation, page_range, print_settings=None):
        try:
            resp = requests.put(
                self._url(f"/api/orders/{order_id}/options"),
                json={
                    "printer_name": printer_name,
                    "station_id": self.cfg.station_id,
                    "computer_name": self.cfg.computer_name,
                    "profile_id": profile_id,
                    "copies": copies,
                    "orientation": orientation,
                    "page_range": page_range,
                    "print_settings": print_settings,
                },
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def pay(self, order_id):
        try:
            resp = requests.post(
                self._url(f"/api/orders/{order_id}/pay"),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def queue_status(self, order_id):
        try:
            resp = requests.get(
                self._url(f"/api/orders/{order_id}/queue-status"),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    # ---- Local printer reference data ----
    def get_printers(self):
        """Enumerate printers installed on THIS User PC, never on the Admin PC."""
        if not winprint.IS_WINDOWS:
            return []
        try:
            names = winprint.list_local_printers()
        except Exception as e:
            raise ApiClientError(f"Could not read the Windows printer list: {e}")
        return [
            {
                "id": name,                 # local identity; not an Admin DB printer id
                "system_name": name,
                "display_name": name,
                "is_network": name.startswith("\\\\"),
            }
            for name in names
        ]

    def get_print_options(self, printer_name, profile_id):
        """Read paper/quality/media options from the selected printer's local Windows driver."""
        profile = next((p for p in getattr(self, "_profiles_cache", []) if p.get("id") == profile_id), None)
        profile_paper = profile.get("paper_size", "") if profile else ""
        profile_media = profile.get("media_type", "") if profile else ""
        try:
            from shared import silent_print
            options = silent_print.get_print_options(printer_name, profile_paper, profile_media)
            options["printer_name"] = printer_name
            return options
        except Exception as e:
            raise ApiClientError(f"Could not read this printer's print options: {e}")

    def cache_profiles(self, profiles):
        self._profiles_cache = profiles or []

    def register_station(self, print_agent_port=8767):
        """Tell Admin where this station's authenticated print agent can receive jobs."""
        try:
            resp = requests.post(
                self._url("/api/stations/register"),
                json={"station_id": self.cfg.station_id, "station_key": self.cfg.station_key, "print_agent_port": int(print_agent_port)},
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)

    def get_profiles(self):
        try:
            resp = requests.get(
                self._url("/api/profiles"), headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT_SHORT),
            )
        except requests.exceptions.RequestException as e:
            raise ApiClientError(self._translate_error(e))
        return self._handle_response(resp)


api_client = UserApiClient()
