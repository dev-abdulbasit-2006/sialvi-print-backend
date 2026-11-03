"""HTTP wrapper used by the Owner License Manager. The owner token lives only in memory."""
import requests
from urllib.parse import urlparse


class ApiError(Exception):
    def __init__(self, code, message, extra=None, status=0):
        super().__init__(message)
        self.code, self.message, self.extra, self.status = code, message, extra or {}, status


class OwnerApi:
    """Talks to the CLOUD licensing server only. The owner password is never sent over plain HTTP."""

    def __init__(self, base_url, ca_bundle=None, timeout=(10, 60)):
        # timeout = (connect, read): the free cloud host may need ~30-50 s to wake up after being idle.
        self.base = base_url.rstrip("/")
        self._check_base_url(self.base)
        self.verify = ca_bundle or True
        self.timeout = timeout
        self.token = None
        self.username = ""
        self.policy = {}

    @staticmethod
    def _check_base_url(base):
        u = urlparse(base)
        if u.scheme != "https" or not u.hostname:
            raise ApiError("INSECURE", "The owner password must only be sent over HTTPS to the cloud licensing server.")

    def _call(self, method, path, json=None, params=None, auth=True):
        headers = {"Authorization": f"Bearer {self.token}"} if (auth and self.token) else {}
        try:
            r = requests.request(method, self.base + path, json=json, params=params, headers=headers,
                                 timeout=self.timeout, verify=self.verify)
        except requests.exceptions.SSLError:
            raise ApiError("TLS", "The server's HTTPS certificate could not be verified.")
        except requests.RequestException as e:
            raise ApiError("NETWORK", f"Cannot reach the licensing server ({type(e).__name__}).")
        try:
            data = r.json()
        except ValueError:
            raise ApiError("BAD_RESPONSE", f"Unexpected server response (HTTP {r.status_code}).", status=r.status_code)
        if r.status_code >= 400:
            err = data.get("error", {})
            extra = {k: v for k, v in err.items() if k not in ("code", "message")}
            if r.status_code == 401 and auth:
                self.token = None
            raise ApiError(err.get("code", "ERROR"), err.get("message", "Request failed."), extra, r.status_code)
        return data

    # session
    def login(self, username, password):
        d = self._call("POST", "/api/owner/login", {"username": username, "password": password}, auth=False)
        self.token, self.username = d["token"], d["username"]
        self.policy = self._call("GET", "/api/owner/me").get("policy", {})

    def logout(self):
        try:
            if self.token:
                self._call("POST", "/api/owner/logout")
        except ApiError:
            pass
        self.token = None

    def public_key(self): return self._call("GET", "/api/v1/public-key", auth=False)["public_key"]

    # data
    def overview(self): return self._call("GET", "/api/owner/overview")
    def health(self): return self._call("GET", "/api/v1/health", auth=False)
    def licenses(self, q=""): return self._call("GET", "/api/owner/licenses", params={"q": q} if q else None)["licenses"]
    def create_license(self, **kw): return self._call("POST", "/api/owner/licenses", kw)
    def edit_license(self, lid, **kw): return self._call("PATCH", f"/api/owner/licenses/{lid}", kw)
    def set_limit(self, lid, n, deactivate=None, reason=""):
        return self._call("POST", f"/api/owner/licenses/{lid}/limit",
                          {"max_installations": n, "deactivate_installation_ids": deactivate or [], "reason": reason})
    def license_action(self, lid, action, reason=""): return self._call("POST", f"/api/owner/licenses/{lid}/status", {"action": action, "reason": reason})
    def installations(self, lid=None):
        path = f"/api/owner/licenses/{lid}/installations" if lid else "/api/owner/installations"
        return self._call("GET", path)["installations"]
    def ip_history(self, iid): return self._call("GET", f"/api/owner/installations/{iid}/ip-history")["history"]
    def inst_events(self, iid): return self._call("GET", f"/api/owner/installations/{iid}/events")["events"]
    def inst_action(self, iid, action, reason=""): return self._call("POST", f"/api/owner/installations/{iid}/{action}", {"reason": reason})
    # remote commands
    def commands(self, license_id=None):
        return self._call("GET", "/api/owner/commands", params={"license_id": license_id} if license_id else None)["commands"]
    def command_detail(self, cid): return self._call("GET", f"/api/owner/commands/{cid}")
    def issue_command(self, license_id, command_type, installation_id=None, reason=""):
        return self._call("POST", "/api/owner/commands", {"license_id": license_id, "command_type": command_type,
                                                          "installation_id": installation_id, "reason": reason})
    def restrictions(self): return self._call("GET", "/api/owner/ip-restrictions")["restrictions"]
    def add_restriction(self, **kw): return self._call("POST", "/api/owner/ip-restrictions", kw)
    def restriction_state(self, rid, state, reason=""): return self._call("POST", f"/api/owner/ip-restrictions/{rid}/{state}", {"reason": reason})
    def audit(self, license_id=None, limit=500):
        params = {"limit": limit}
        if license_id:
            params["license_id"] = license_id
        return self._call("GET", "/api/owner/audit", params=params)["events"]
