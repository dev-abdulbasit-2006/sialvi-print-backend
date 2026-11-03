"""
Thin HTTP client the Admin UI uses to talk to its own local Flask
server (same machine, http://127.0.0.1:<port>). Centralizing this
means every screen gets the same timeout/error handling instead of
each screen rolling its own requests calls.
"""

import logging

import requests

from admin.config.config import config

logger = logging.getLogger("admin_ui.api_client")

DEFAULT_TIMEOUT = 10  # seconds; large file uploads aren't done from this UI


class ApiClientError(Exception):
    """Raised with a human-readable message safe to show directly in the UI."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AdminApiClient:
    def __init__(self):
        self.base_url = f"http://127.0.0.1:{config.server_port}"
        self.token = config.lan_shared_token

    def _headers(self):
        return {"X-Admin-Token": self.token, "X-LAN-Token": self.token}

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=DEFAULT_TIMEOUT, **kwargs
            )
        except requests.exceptions.ConnectionError:
            raise ApiClientError(
                "Cannot reach the Admin server. It may still be starting up, "
                "or has stopped unexpectedly. Check the logs."
            )
        except requests.exceptions.Timeout:
            raise ApiClientError("The Admin server did not respond in time.")

        if resp.status_code >= 400:
            message = f"Request failed with status {resp.status_code}."
            try:
                body = resp.json()
                if isinstance(body, dict):
                    err = body.get("error")
                    if isinstance(err, dict):
                        message = err.get("message", message)
                    elif isinstance(err, str):
                        message = err
                    elif "message" in body and isinstance(body.get("message"), str):
                        message = body.get("message")
            except ValueError:
                if resp.text:
                    message = resp.text.strip()[:200]
            if resp.status_code == 404:
                message += (" - the program answering on this port is not the current SialviPrint Admin "
                            "(an old copy may still be running). Close every Admin window / end SialviPrintAdmin.exe "
                            "in Task Manager, then start Admin again.")
            raise ApiClientError(message)

        if resp.content:
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    # ---- Dashboard ----
    def get_dashboard(self):
        data = self._request("GET", "/api/admin/dashboard")
        if not isinstance(data, dict):
            raise ApiClientError("Unexpected dashboard response from server.")
        return data

    # ---- Payments ----
    def get_pending_payments(self):
        return self._request("GET", "/api/admin/payments/pending")

    def verify_payment(self, payment_id: str, payment_method: str, admin_username: str = "admin"):
        return self._request("POST", f"/api/admin/payments/{payment_id}/verify",
                              json={"admin_username": admin_username, "payment_method": payment_method})

    def reject_payment(self, payment_id: str, reason: str = ""):
        return self._request("POST", f"/api/admin/payments/{payment_id}/reject",
                              json={"reason": reason})


    def get_receipts(self, period: str = "today", method: str = ""):
        params = {"period": period}
        if method and method.upper() not in ("ALL", "ALL METHODS"):
            params["method"] = method.upper()
        return self._request("GET", "/api/admin/receipts", params=params)

    def get_earnings(self, period: str = "today"):
        return self._request("GET", "/api/admin/earnings", params={"period": period})

    # ---- Profiles / Pricing ----
    def get_all_profiles(self):
        return self._request("GET", "/api/admin/profiles")

    def set_price(self, profile_id: int, price_per_page: float):
        return self._request("PUT", f"/api/admin/profiles/{profile_id}/price",
                              json={"price_per_page": price_per_page})

    def set_profile_enabled(self, profile_id: int, enabled: bool):
        return self._request("PUT", f"/api/admin/profiles/{profile_id}/enabled",
                              json={"enabled": enabled})

    def create_profile(self, display_name: str, price_per_page: float, color_mode: str = "COLOR",
                       paper_size: str = "A4", ask_properties: bool = False,
                       enabled: bool = True):
        return self._request("POST", "/api/admin/profiles", json={
            "display_name": display_name, "price_per_page": price_per_page,
            "color_mode": color_mode, "paper_size": paper_size,
            "ask_properties": ask_properties, "enabled": enabled})

    def delete_profile(self, profile_id: int):
        return self._request("DELETE", f"/api/admin/profiles/{profile_id}")

    def set_ask_properties(self, profile_id: int, enabled: bool):
        return self._request("PUT", f"/api/admin/profiles/{profile_id}/ask-properties",
                              json={"enabled": enabled})

    def get_tiers(self, profile_id: int):
        return self._request("GET", f"/api/admin/profiles/{profile_id}/tiers")

    def set_tiers(self, profile_id: int, tiers: list):
        return self._request("PUT", f"/api/admin/profiles/{profile_id}/tiers", json={"tiers": tiers})

    # ---- Printers ----
    def get_printers(self):
        return self._request("GET", "/api/printers")

    def get_printer_capabilities(self, printer_id: int):
        return self._request("GET", f"/api/printers/{printer_id}/capabilities")

    def set_profile_mapping(self, printer_id: int, profile_id: int):
        return self._request("PUT", f"/api/admin/printers/{printer_id}/profile-mapping",
                              json={"profile_id": profile_id})

    # ---- Queue ----
    def get_queue(self, view: str = "active"):
        data = self._request("GET", "/api/admin/queue", params={"view": view})
        if data is None:
            return []
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data["jobs"]
        if not isinstance(data, list):
            raise ApiClientError("Unexpected queue response from server.")
        return data

    def get_queue_status(self):
        return self._request("GET", "/api/admin/queue/status")

    def cancel_queue_job(self, job_id: str):
        return self._request("POST", f"/api/admin/queue/{job_id}/cancel")

    def retry_queue_job(self, job_id: str):
        return self._request("POST", f"/api/admin/queue/{job_id}/retry")

    # ---- Weekly reset ----
    def check_weekly_reset(self):
        return self._request("POST", "/api/admin/weekly-reset/check")


api_client = AdminApiClient()
