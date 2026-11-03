"""Feature 1 acceptance tests: owner license manager backend + client enforcement.
Loopback only; uses reserved documentation IPs (192.0.2.x / 198.51.100.x / 203.0.113.x).
Run: python -m unittest discover -s tests -v
"""
import os, sys, tempfile, threading, time, unittest, uuid, sqlite3
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from werkzeug.serving import make_server
from tests.local_test_support import LocalLicenseClient
from licensing.server.app import create_app
from licensing.server.config import Settings
from licensing.server.service import LicenseError
from shared.license_client import LicenseClient
from shared.license_token import public_key_pem

PW = "correct-horse-battery"


class Base(unittest.TestCase):
    trusted = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(data_dir=Path(self.tmp.name), trusted_proxies=self.trusted,
                                 rate_limit_requests=10_000)
        self.app = create_app(self.settings)
        self.svc = self.app.extensions["license_service"]
        self.svc.create_owner("owner", PW)
        self.http = self.app.test_client()
        r = self.http.post("/api/owner/login", json={"username": "owner", "password": PW},
                           environ_base={"REMOTE_ADDR": "192.0.2.1"})
        self.assertEqual(r.status_code, 200, r.json)
        self.h = {"Authorization": "Bearer " + r.json["token"]}

    def tearDown(self):
        import gc; gc.collect()          # Windows cannot delete a SQLite file that still has an open handle
        self.tmp.cleanup()

    # helpers
    def owner(self, method, url, **kw):
        return getattr(self.http, method)(url, headers=self.h, **kw)

    def make_license(self, label="Printing Shop A", limit=5, **kw):
        r = self.owner("post", "/api/owner/licenses", json={"label": label, "max_installations": limit, **kw})
        self.assertEqual(r.status_code, 201, r.json)
        return r.json["license"], r.json["serial_key"]

    def activate(self, key, iid=None, ip="203.0.113.10", **extra):
        iid = iid or "pc-" + uuid.uuid4().hex[:10]
        r = self.http.post("/api/v1/activate", json={"serial_key": key, "installation_id": iid, "device_name": iid,
                                                      "app_version": "1.0", "os_info": "Windows 11", **extra},
                           environ_base={"REMOTE_ADDR": ip})
        return r, iid

    def checkin(self, iid, secret, ip="203.0.113.10"):
        return self.http.post("/api/v1/checkin", json={"installation_id": iid, "installation_secret": secret, "app_version": "1.1"},
                              environ_base={"REMOTE_ADDR": ip})

    def fill(self, key, n, ip="203.0.113.10"):
        out = []
        for _ in range(n):
            r, iid = self.activate(key, ip=ip)
            self.assertEqual(r.status_code, 200, r.json)
            out.append((iid, r.json["installation_secret"]))
        return out


class LicenseAndLimitTests(Base):
    def test_01_02_create_license_unique_keys_hashed(self):
        lic, key = self.make_license(limit=5)
        self.assertEqual(lic["max_installations"], 5)
        self.assertRegex(key, r"^SIALVI(-[A-Z2-9]{4}){4}$")
        keys = {self.make_license(label=f"S{i}")[1] for i in range(20)} | {key}
        self.assertEqual(len(keys), 21)
        _c = sqlite3.connect(self.svc.db_path)
        try:
            raw = _c.execute("SELECT group_concat(key_hash||key_hint||label) FROM licenses").fetchone()[0]
        finally:
            _c.close()
        self.assertNotIn(key.replace("SIALVI-", "").replace("-", ""), raw)   # plain key never stored
        self.assertNotIn(key, str(self.svc.audit_list()))                      # nor in audit

    def test_03_04_five_ok_sixth_rejected(self):
        lic, key = self.make_license(limit=5)
        self.fill(key, 5)
        r, _ = self.activate(key)
        self.assertEqual((r.status_code, r.json["error"]["code"]), (403, "LIMIT_REACHED"))
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["usage_text"], "5 / 5 PCs")

    def test_05_repeat_checkin_and_reactivation_use_no_slot(self):
        lic, key = self.make_license(limit=2)
        (i1, s1), (i2, s2) = self.fill(key, 2)
        for _ in range(5):
            self.assertEqual(self.checkin(i1, s1).status_code, 200)
        r, _ = self.activate(key, iid=i1)           # reinstall of the same PC
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["registered_pcs"], 2)

    def test_06_increase_limit_allows_three_more(self):
        lic, key = self.make_license(limit=5)
        self.fill(key, 5)
        r = self.owner("post", f"/api/owner/licenses/{lic['id']}/limit", json={"max_installations": 8})
        self.assertEqual(r.json["max_installations"], 8)
        self.fill(key, 3)
        self.assertEqual(self.activate(key)[0].status_code, 403)

    def test_07_lower_limit_conflict_keeps_devices(self):
        lic, key = self.make_license(limit=5)
        pcs = self.fill(key, 5)
        r = self.owner("post", f"/api/owner/licenses/{lic['id']}/limit", json={"max_installations": 3})
        self.assertEqual((r.status_code, r.json["error"]["code"]), (409, "LIMIT_CONFLICT"))
        self.assertEqual(r.json["error"]["must_free"], 2)
        self.assertEqual(len(self.owner("get", f"/api/owner/licenses/{lic['id']}/installations").json["installations"]), 5)
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["max_installations"], 5)
        # explicit decision: deactivate two (rows + history are kept)
        r = self.owner("post", f"/api/owner/licenses/{lic['id']}/limit",
                       json={"max_installations": 3, "deactivate_installation_ids": [pcs[0][0], pcs[1][0]]})
        self.assertEqual(r.status_code, 200)
        insts = {i["id"]: i for i in self.owner("get", f"/api/owner/licenses/{lic['id']}/installations").json["installations"]}
        self.assertEqual(len(insts), 5)
        self.assertEqual(insts[pcs[0][0]]["display_status"], "Deactivated")
        self.assertEqual(self.checkin(*pcs[0]).json["error"]["code"], "INSTALLATION_DEACTIVATED")

    def test_15_simultaneous_activations_never_exceed_limit(self):
        lic, key = self.make_license(limit=3)
        results = []
        def go(n):
            try:
                self.svc.activate(key, f"race-pc-{n:03d}", "pc", "1", "os", "203.0.113.5")
                results.append("ok")
            except LicenseError as e:
                results.append(e.code)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(16)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count("ok"), 3, results)
        self.assertEqual(results.count("LIMIT_REACHED"), 13)

    def test_status_rules_and_revoked_is_final(self):
        lic, key = self.make_license(limit=2)
        (iid, sec), = self.fill(key, 1)
        self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "suspend"})
        self.assertEqual(self.checkin(iid, sec).json["error"]["code"], "LICENSE_SUSPENDED")
        self.assertEqual(self.activate(key)[0].json["error"]["code"], "LICENSE_SUSPENDED")
        self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "reactivate"})
        self.assertEqual(self.checkin(iid, sec).status_code, 200)
        self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "revoke"})
        self.assertEqual(self.checkin(iid, sec).json["error"]["code"], "LICENSE_REVOKED")
        r = self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "reactivate"})
        self.assertEqual(r.status_code, 409)

    def test_expired_license_refused(self):
        lic, key = self.make_license(limit=2, expires_at="2099-01-01")
        (iid, sec), = self.fill(key, 1)
        self.owner("patch", f"/api/owner/licenses/{lic['id']}", json={"expires_at": "2020-01-01"})
        self.assertEqual(self.checkin(iid, sec).json["error"]["code"], "LICENSE_EXPIRED")
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["effective_status"], "expired")


class InstallationAndIpTests(Base):
    def test_08_09_installation_list_and_ip_history_preserved(self):
        lic, key = self.make_license()
        (iid, sec), = self.fill(key, 1)
        self.checkin(iid, sec, ip="203.0.113.20")
        self.checkin(iid, sec, ip="198.51.100.7")
        inst = self.owner("get", f"/api/owner/licenses/{lic['id']}/installations").json["installations"][0]
        self.assertEqual((inst["last_ip"], inst["os_info"], inst["display_status"]), ("198.51.100.7", "Windows 11", "Active"))
        hist = self.owner("get", f"/api/owner/installations/{iid}/ip-history").json["history"]
        self.assertEqual([h["ip"] for h in hist], ["198.51.100.7", "203.0.113.20", "203.0.113.10"])
        self.assertTrue(all(h["installation_id"] == iid and h["ts"].endswith("Z") for h in hist))

    def test_ip_is_server_observed_not_client_supplied(self):
        lic, key = self.make_license()
        r, iid = self.activate(key, ip="203.0.113.10", ip_address="1.2.3.4", public_ip="1.2.3.4")
        r2 = self.http.post("/api/v1/checkin", json={"installation_id": iid, "installation_secret": r.json["installation_secret"]},
                            headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "1.2.3.4"}, environ_base={"REMOTE_ADDR": "203.0.113.10"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(self.owner("get", f"/api/owner/installations/{iid}").json["last_ip"], "203.0.113.10")

    def test_not_recently_seen_label(self):
        lic, key = self.make_license()
        (iid, sec), = self.fill(key, 1)
        c = sqlite3.connect(self.svc.db_path)
        try:
            c.execute("UPDATE installations SET last_checkin_at='2020-01-01T00:00:00Z'"); c.commit()
        finally:
            c.close()
        inst = self.owner("get", f"/api/owner/installations/{iid}").json
        self.assertEqual(inst["display_status"], "Not recently seen")
        self.assertIn("days ago", inst["last_seen"])

    def test_10_11_lock_blocks_renewal_and_keeps_history(self):
        lic, key = self.make_license()
        (a, sa), (b, sb) = self.fill(key, 2)
        self.assertEqual(self.owner("post", f"/api/owner/installations/{a}/lock", json={"reason": "nonpayment"}).json["display_status"], "Locked")
        r = self.checkin(a, sa, ip="198.51.100.9")
        self.assertEqual((r.status_code, r.json["error"]["code"]), (403, "INSTALLATION_LOCKED"))
        self.assertIn("contact your software provider", r.json["error"]["message"])
        self.assertEqual(self.checkin(b, sb).status_code, 200)               # other PCs unaffected
        hist = self.owner("get", f"/api/owner/installations/{a}/ip-history").json["history"]
        self.assertEqual(hist[0]["result"], "denied:INSTALLATION_LOCKED")     # refusal recorded, history kept
        self.assertEqual(self.activate(key, iid=a)[0].json["error"]["code"], "INSTALLATION_LOCKED")  # no re-activation trick
        d = self.http.post("/api/v1/deactivate", json={"installation_id": a, "installation_secret": sa})
        self.assertEqual(d.status_code, 403)                                  # no self-unlock by deactivating
        acts = [e["action"] for e in self.owner("get", f"/api/owner/installations/{a}/events").json["events"]]
        self.assertIn("installation_locked", acts)

    def test_12_unlock_does_not_bypass_suspended_license(self):
        lic, key = self.make_license()
        (a, sa), = self.fill(key, 1)
        self.owner("post", f"/api/owner/installations/{a}/lock", json={})
        self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "suspend"})
        r = self.owner("post", f"/api/owner/installations/{a}/unlock", json={})
        self.assertIn("still be refused", r.json["warning"])
        self.assertEqual(self.checkin(a, sa).json["error"]["code"], "LICENSE_SUSPENDED")
        self.owner("post", f"/api/owner/licenses/{lic['id']}/status", json={"action": "reactivate"})
        self.assertEqual(self.checkin(a, sa).status_code, 200)

    def test_customer_can_deactivate_to_replace_pc(self):
        lic, key = self.make_license(limit=1)
        (a, sa), = self.fill(key, 1)
        self.assertEqual(self.activate(key)[0].status_code, 403)
        self.assertEqual(self.http.post("/api/v1/deactivate", json={"installation_id": a, "installation_secret": sa}).status_code, 200)
        self.assertEqual(self.activate(key)[0].status_code, 200)
        self.assertEqual(len(self.owner("get", "/api/owner/installations").json["installations"]), 2)   # old row kept

    def test_bad_secret_and_unknown_installation(self):
        lic, key = self.make_license()
        (a, sa), = self.fill(key, 1)
        self.assertEqual(self.checkin(a, "wrong").status_code, 401)
        self.assertEqual(self.checkin("nope-nope-nope", sa).status_code, 401)


class IpRestrictionTests(Base):
    def add(self, **kw):
        return self.owner("post", "/api/owner/ip-restrictions", json=kw)

    def test_13_scopes_apply_only_where_chosen(self):
        la, ka = self.make_license("A"); lb, kb = self.make_license("B")
        (ia, sa), = self.fill(ka, 1, ip="203.0.113.50")
        r = self.add(network="198.51.100.77", scope="block_activation", license_id=la["id"], reason="abuse")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.activate(ka, ip="198.51.100.77")[0].json["error"]["code"], "IP_BLOCKED")   # scoped license A
        self.assertEqual(self.activate(kb, ip="198.51.100.77")[0].status_code, 200)                       # license B unaffected
        self.assertEqual(self.checkin(ia, sa, ip="198.51.100.77").status_code, 200)                       # activation-only scope: renewals ok
        self.add(network="198.51.100.77", scope="block_authorization", license_id=la["id"])
        self.assertEqual(self.checkin(ia, sa, ip="198.51.100.77").json["error"]["code"], "IP_BLOCKED")
        self.assertEqual(self.checkin(ia, sa, ip="203.0.113.50").status_code, 200)                        # other IP fine

    def test_global_requires_confirmation_and_cidr_ipv6(self):
        r = self.add(network="198.51.100.0/24", scope="block_activation")
        self.assertEqual((r.status_code, r.json["error"]["code"]), (409, "GLOBAL_CONFIRMATION_REQUIRED"))
        self.assertEqual(self.add(network="198.51.100.0/24", scope="block_activation", confirm_global=True).status_code, 201)
        self.assertEqual(self.add(network="2001:db8::/48", scope="block_activation", confirm_global=True).status_code, 201)
        self.assertEqual(self.add(network="not-an-ip", scope="block_activation", confirm_global=True).status_code, 400)
        self.assertEqual(self.add(network="0.0.0.0/0", scope="block_activation", confirm_global=True).status_code, 400)
        lic, key = self.make_license()
        self.assertEqual(self.activate(key, ip="198.51.100.200")[0].json["error"]["code"], "IP_BLOCKED")

    def test_14_removing_ip_restriction_keeps_device_lock(self):
        lic, key = self.make_license()
        (a, sa), = self.fill(key, 1)
        rid = self.add(network="198.51.100.9", scope="block_authorization", license_id=lic["id"]).json["id"]
        self.owner("post", f"/api/owner/installations/{a}/lock", json={})
        self.assertEqual(self.owner("post", f"/api/owner/ip-restrictions/{rid}/disable", json={"reason": "ok"}).json["active"], 0)
        self.assertEqual(self.checkin(a, sa, ip="198.51.100.9").json["error"]["code"], "INSTALLATION_LOCKED")
        self.assertEqual(len(self.owner("get", "/api/owner/ip-restrictions").json["restrictions"]), 1)    # history kept
        actions = [e["action"] for e in self.owner("get", "/api/owner/audit").json["events"]]
        self.assertIn("ip_restriction_added", actions); self.assertIn("ip_restriction_disabled", actions)


class TrustedProxyTests(Base):
    trusted = ("10.0.0.0/8",)

    def test_forwarded_header_trusted_only_from_configured_proxy(self):
        lic, key = self.make_license()
        # request from an UNtrusted peer: header ignored
        r = self.http.post("/api/v1/activate", json={"serial_key": key, "installation_id": "proxy-test-1"},
                           headers={"X-Forwarded-For": "1.2.3.4"}, environ_base={"REMOTE_ADDR": "203.0.113.77"})
        self.assertEqual(self.owner("get", "/api/owner/installations/proxy-test-1").json["last_ip"], "203.0.113.77")
        # from the trusted proxy: right-most untrusted hop is used (spoofed left entries ignored)
        self.http.post("/api/v1/activate", json={"serial_key": key, "installation_id": "proxy-test-2"},
                       headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.20, 10.1.1.1"}, environ_base={"REMOTE_ADDR": "10.0.0.5"})
        self.assertEqual(self.owner("get", "/api/owner/installations/proxy-test-2").json["last_ip"], "198.51.100.20")


class SecurityTests(Base):
    def test_16_unauthorized_cannot_manage(self):
        lic, key = self.make_license()
        (a, sa), = self.fill(key, 1)
        for method, url, body in [("post", "/api/owner/licenses", {"label": "x", "max_installations": 99}),
                                  ("post", f"/api/owner/licenses/{lic['id']}/limit", {"max_installations": 99}),
                                  ("post", f"/api/owner/installations/{a}/lock", {}),
                                  ("get", "/api/owner/licenses", None), ("get", "/api/owner/audit", None),
                                  ("post", "/api/owner/ip-restrictions", {"network": "1.2.3.4", "scope": "block_activation"})]:
            self.assertEqual(getattr(self.http, method)(url, json=body).status_code, 401, url)
            # an installation secret / serial key is not an owner credential
            bad = getattr(self.http, method)(url, json=body, headers={"Authorization": "Bearer " + sa})
            self.assertEqual(bad.status_code, 401, url)
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["max_installations"], 5)

    def test_login_lockout_and_no_default_owner(self):
        for _ in range(5):
            r = self.http.post("/api/owner/login", json={"username": "owner", "password": "bad"}, environ_base={"REMOTE_ADDR": "192.0.2.9"})
            self.assertEqual(r.status_code, 401)
        r = self.http.post("/api/owner/login", json={"username": "owner", "password": PW}, environ_base={"REMOTE_ADDR": "192.0.2.9"})
        self.assertEqual((r.status_code, r.json["error"]["code"]), (429, "LOGIN_LOCKED"))
        self.assertEqual(self.http.post("/api/owner/login", json={"username": "admin", "password": "admin"}).status_code, 401)
        with self.assertRaises(LicenseError):
            self.svc.create_owner("weak", "short")

    def test_logout_invalidates_session(self):
        self.assertEqual(self.owner("post", "/api/owner/logout").status_code, 200)
        self.assertEqual(self.owner("get", "/api/owner/licenses").status_code, 401)

    def test_malformed_input_rejected(self):
        for payload in ("not json", "[1,2]"):
            r = self.http.post("/api/v1/activate", data=payload, content_type="application/json")
            self.assertEqual(r.status_code, 400)
        lic, key = self.make_license()
        self.assertEqual(self.http.post("/api/v1/activate", json={"serial_key": key, "installation_id": "x'; DROP TABLE licenses;--"}).status_code, 400)
        self.assertEqual(self.http.post("/api/v1/activate", json={"serial_key": "' OR 1=1 --", "installation_id": "valid-id-12345"}).status_code, 403)
        self.assertEqual(self.owner("post", "/api/owner/licenses", json={"label": "x", "max_installations": 0}).status_code, 400)
        self.assertEqual(self.owner("post", "/api/owner/licenses", json={"label": "", "max_installations": 2}).status_code, 400)
        self.assertEqual(self.owner("get", "/api/owner/licenses?q=%27%20OR%201%3D1").json["licenses"], [])
        self.assertEqual(len(self.owner("get", "/api/owner/licenses").json["licenses"]), 1)

    def test_rate_limit_on_client_endpoints(self):
        self.app.config["TESTING"] = True
        s2 = Settings(data_dir=Path(self.tmp.name) / "rl", rate_limit_requests=3)
        c = create_app(s2).test_client()
        codes = [c.post("/api/v1/activate", json={"serial_key": "SIALVI-AAAA", "installation_id": "valid-id-12345"},
                        environ_base={"REMOTE_ADDR": "203.0.113.99"}).status_code for _ in range(5)]
        self.assertEqual(codes, [403, 403, 403, 429, 429])

    def test_overview_counts_and_audit(self):
        lic, key = self.make_license()
        (a, sa), (b, sb) = self.fill(key, 2)
        self.owner("post", f"/api/owner/installations/{a}/lock", json={})
        ov = self.owner("get", "/api/owner/overview").json
        self.assertEqual((ov["total_licenses"], ov["registered_installations"], ov["locked_installations"], ov["recently_seen_installations"]), (1, 2, 1, 1))
        self.assertTrue(any(e["action"] == "license_created" and e["actor"] == "owner" for e in self.owner("get", "/api/owner/audit").json["events"]))


class ClientEnforcementTests(Base):
    """17/18: real HTTP round trips with the client library + offline policy."""

    def setUp(self):
        super().setUp()
        self.srv = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.pub = public_key_pem(self.svc.signing_key)

    def tearDown(self):
        self.srv.shutdown()
        super().tearDown()

    def client(self, name="pc1", url=None):
        return LocalLicenseClient(url or self.url, Path(self.tmp.name) / name, self.pub, "9.9", timeout=3)

    def test_https_required_by_default(self):
        with self.assertRaises(ValueError):
            LicenseClient("http://license.example.com", self.tmp.name, self.pub)

    def test_activate_lock_unlock_end_to_end(self):
        lic, key = self.make_license(limit=2)
        c1, c2, c3 = self.client("a"), self.client("b"), self.client("c")
        self.assertTrue(c1.activate(key).authorized)
        self.assertTrue(c2.activate(key).authorized)
        st = c3.activate(key)
        self.assertEqual((st.authorized, st.code), (False, "LIMIT_REACHED"))
        iid = c1.state["installation_id"]
        self.owner("post", f"/api/owner/installations/{iid}/lock", json={"reason": "test"})
        st = c1.refresh()
        self.assertEqual((st.authorized, st.code), (False, "INSTALLATION_LOCKED"))
        self.assertIn("contact your software provider", st.message)
        self.assertTrue(c2.refresh().authorized)
        self.owner("post", f"/api/owner/installations/{iid}/unlock", json={})
        self.assertTrue(c1.refresh().authorized)

    def test_17_offline_policy(self):
        lic, key = self.make_license()
        c = self.client()
        self.assertTrue(c.activate(key).authorized)
        self.srv.shutdown()                                     # server becomes unreachable
        st = c.refresh()
        self.assertTrue(st.authorized)                          # inside grace: keeps working
        self.assertIn("offline", st.message)
        self.assertGreater(st.seconds_left, 71 * 3600)
        real = time.time
        try:
            time.time = lambda: real() + 73 * 3600              # grace (72h) over
            self.assertEqual(c.check_authorization().code, "GRACE_EXPIRED")
            time.time = lambda: real() - 3600                   # clock rolled back
            self.assertEqual(c.check_authorization().code, "CLOCK_ROLLBACK")
        finally:
            time.time = real

    def test_denial_persists_offline_and_tamper_fails(self):
        lic, key = self.make_license()
        c = self.client()
        c.activate(key)
        self.owner("post", f"/api/owner/installations/{c.state['installation_id']}/lock", json={})
        self.assertFalse(c.refresh().authorized)
        self.srv.shutdown()
        self.assertEqual(self.client().check_authorization().code, "INSTALLATION_LOCKED")   # fresh process, offline
        # editing the saved token (to fake a longer grace) breaks the signature
        c2 = self.client("tamper")
        c2.state = {"installation_id": "x", "token": c.state.get("token") or "AAAA.BBBB"}
        self.assertEqual(c2.check_authorization().code, "BAD_SIGNATURE")

    def test_client_cannot_use_owner_api(self):
        lic, key = self.make_license()
        c = self.client()
        c.activate(key)
        import requests
        r = requests.post(self.url + "/api/owner/licenses", json={"label": "evil", "max_installations": 999},
                          headers={"Authorization": "Bearer " + c.state["installation_secret"]}, proxies={})
        self.assertEqual(r.status_code, 401)

    def test_18_licensing_touches_only_the_gate_points(self):
        # The license gate may appear ONLY at the documented entry/enforcement points; pricing, payments,
        # page counting, printer drivers and print settings must stay free of licensing code.
        allowed = {"admin/run_admin.py", "admin/server/app.py", "admin/queue/queue_manager.py",
                   "user/run_user.py", "user/network/print_agent.py",
                   "admin/ui/main_window.py"}  # Reset License button: opens the shared LicenseDialog only
        for rel in ("admin", "user"):
            for dp, _, files in os.walk(os.path.join(ROOT, rel)):
                for f in files:
                    if f.endswith(".py"):
                        full = os.path.join(dp, f)
                        key = os.path.relpath(full, ROOT).replace(os.sep, "/")
                        src = open(full, encoding="utf-8").read()
                        uses = "license_gate" in src or "license_ui" in src or "license_client" in src
                        self.assertEqual(uses, key in allowed, key)


if __name__ == "__main__":
    unittest.main()
