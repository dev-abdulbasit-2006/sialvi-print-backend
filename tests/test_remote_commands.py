"""Remote command channel (owner -> PCs): server rules.  Loopback only, reserved documentation IPs.
Run: python -m unittest discover -s tests -v"""
import os, sys, sqlite3, tempfile, unittest
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_licensing import Base, PW
from licensing.server import db as lic_db
from licensing.server.app import create_app
from licensing.server.config import Settings


class Rc(Base):
    """helpers on top of the licensing test base"""

    def poll(self, iid, secret, ip="203.0.113.10"):
        return self.http.post("/api/v1/poll", json={"installation_id": iid, "installation_secret": secret},
                              environ_base={"REMOTE_ADDR": ip})

    def ack(self, iid, secret, cid, status="applied", result="ok", ip="203.0.113.10"):
        return self.http.post("/api/v1/ack-command", json={"installation_id": iid, "installation_secret": secret,
                              "command_id": cid, "status": status, "result": result}, environ_base={"REMOTE_ADDR": ip})

    def command(self, lid, ctype, iid=None, reason="Invoice payment pending"):
        return self.owner("post", "/api/owner/commands", json={"license_id": lid, "command_type": ctype,
                                                               "installation_id": iid, "reason": reason})

    def shop(self, n=3):
        lic, key = self.make_license("Shop", 5)
        return lic, self.fill(key, n)


class IssueAndDeliverTests(Rc):
    def test_suspend_changes_state_queues_every_pc_and_poll_reports_it(self):
        lic, pcs = self.shop(3)
        r = self.command(lic["id"], "SUSPEND_LICENSE")
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(r.json["summary"]["total"], 3)
        self.assertEqual(r.json["summary"]["overall"], "waiting")
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["status"], "suspended")
        for iid, sec in pcs:
            p = self.poll(iid, sec)
            self.assertEqual(p.status_code, 200, p.json)
            self.assertFalse(p.json["authorized"])
            self.assertEqual(p.json["code"], "LICENSE_SUSPENDED")
            self.assertEqual([c["command_type"] for c in p.json["commands"]], ["SUSPEND_LICENSE"])
            self.assertEqual(self.checkin(iid, sec).status_code, 403)          # enforced by the server, not by the command

    def test_reason_is_never_sent_to_the_customer_pc(self):
        lic, pcs = self.shop(1)
        self.command(lic["id"], "SUSPEND_LICENSE", reason="SECRET-INTERNAL-NOTE")
        p = self.poll(*pcs[0])
        self.assertNotIn("SECRET-INTERNAL-NOTE", p.get_data(as_text=True))

    def test_delivery_lifecycle_pending_delivered_applied_and_idempotent_ack(self):
        lic, pcs = self.shop(2)
        cid = self.command(lic["id"], "SUSPEND_LICENSE").json["id"]
        self.assertEqual(self.owner("get", f"/api/owner/commands/{cid}").json["summary"]["pending"], 2)
        for iid, sec in pcs:
            cmd = self.poll(iid, sec).json["commands"][0]
            self.assertEqual(cmd["command_id"], cid)
        d = self.owner("get", f"/api/owner/commands/{cid}").json
        self.assertEqual(d["summary"]["delivered"], 2)
        self.assertEqual(self.ack(*pcs[0], cid).json["status"], "applied")
        self.assertEqual(self.ack(*pcs[0], cid, status="rejected").json["status"], "applied")   # idempotent: first answer wins
        self.assertEqual(self.owner("get", f"/api/owner/commands/{cid}").json["summary"]["overall"], "waiting")
        self.ack(*pcs[1], cid, status="applied")
        d = self.owner("get", f"/api/owner/commands/{cid}").json
        self.assertEqual(d["summary"]["overall"], "complete")
        self.assertTrue(all(x["acknowledged_at"] for x in d["deliveries"]))
        # an acknowledged command is not handed out again
        self.assertEqual(self.poll(*pcs[0]).json["commands"], [])

    def test_rejected_ack_marks_command_as_problem(self):
        lic, pcs = self.shop(1)
        cid = self.command(lic["id"], "SUSPEND_LICENSE").json["id"]
        self.poll(*pcs[0]); self.ack(*pcs[0], cid, status="rejected", result="state is NETWORK")
        d = self.owner("get", f"/api/owner/commands/{cid}").json
        self.assertEqual(d["summary"]["overall"], "problem")
        self.assertEqual(d["deliveries"][0]["result"], "state is NETWORK")

    def test_reactivate_is_visible_to_a_pc_that_is_currently_refused(self):
        lic, pcs = self.shop(1)
        self.command(lic["id"], "SUSPEND_LICENSE")
        self.assertFalse(self.poll(*pcs[0]).json["authorized"])
        self.command(lic["id"], "REACTIVATE_LICENSE", reason="Paid")
        p = self.poll(*pcs[0])
        self.assertTrue(p.json["authorized"])
        self.assertEqual(self.checkin(*pcs[0]).status_code, 200)

    def test_disable_device_affects_only_that_pc(self):
        lic, pcs = self.shop(3)
        r = self.command(lic["id"], "DISABLE_DEVICE", pcs[1][0], reason="Stolen PC")
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(r.json["summary"]["total"], 1)
        self.assertEqual(self.poll(*pcs[1]).json["code"], "INSTALLATION_LOCKED")
        self.assertTrue(self.poll(*pcs[0]).json["authorized"])
        self.assertTrue(self.poll(*pcs[2]).json["authorized"])
        self.assertEqual(self.poll(*pcs[0]).json["commands"], [])             # others receive nothing
        self.assertEqual(self.command(lic["id"], "REENABLE_DEVICE", pcs[1][0], reason="Found it").status_code, 201)
        self.assertTrue(self.poll(*pcs[1]).json["authorized"])

    def test_revoke_device_frees_the_slot_and_still_tells_the_pc(self):
        lic, pcs = self.shop(2)
        self.command(lic["id"], "REVOKE_DEVICE", pcs[0][0], reason="PC replaced")
        p = self.poll(*pcs[0])
        self.assertEqual(p.json["code"], "INSTALLATION_DEACTIVATED")
        self.assertEqual(p.json["commands"][0]["command_type"], "REVOKE_DEVICE")
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["registered_pcs"], 1)

    def test_force_revalidate_needs_no_reason_and_changes_no_state(self):
        lic, pcs = self.shop(2)
        r = self.command(lic["id"], "FORCE_REVALIDATE", reason="")
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["status"], "active")
        self.assertEqual(self.poll(*pcs[0]).json["commands"][0]["command_type"], "FORCE_REVALIDATE")

    def test_pc_activated_after_the_command_does_not_receive_old_commands(self):
        lic, key = self.make_license("Shop", 5)
        old = self.fill(key, 1)
        self.command(lic["id"], "FORCE_REVALIDATE")
        new = self.fill(key, 1)
        self.assertEqual(len(self.poll(*old[0]).json["commands"]), 1)
        self.assertEqual(self.poll(*new[0]).json["commands"], [])


class SafetyTests(Rc):
    def test_validation_and_invalid_transitions_queue_nothing(self):
        lic, pcs = self.shop(2)
        other, okey = self.make_license("Other", 2)
        oth = self.fill(okey, 1)
        bad = [
            (lic["id"], "SUSPEND_LICENSE", None, ""),                       # reason required
            (lic["id"], "EXPLODE", None, "x y z"),                          # unknown type
            (lic["id"], "DISABLE_DEVICE", None, "reason"),                  # needs a PC
            (lic["id"], "SUSPEND_LICENSE", pcs[0][0], "reason"),            # license command with a PC
            (lic["id"], "DISABLE_DEVICE", oth[0][0], "reason"),             # PC of another license
            (lic["id"], "REACTIVATE_LICENSE", None, "reason"),              # license already active
            (999999, "SUSPEND_LICENSE", None, "reason"),                    # unknown license
        ]
        for lid, t, iid, reason in bad:
            r = self.command(lid, t, iid, reason)
            self.assertIn(r.status_code, (400, 404, 409), (t, r.status_code, r.json))
        self.assertEqual(self.owner("get", "/api/owner/commands").json["commands"], [])
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["status"], "active")

    def test_revoked_license_cannot_be_reactivated_by_command(self):
        lic, pcs = self.shop(1)
        self.assertEqual(self.command(lic["id"], "REVOKE_LICENSE").status_code, 201)
        self.assertEqual(self.command(lic["id"], "REACTIVATE_LICENSE", reason="please").status_code, 409)

    def test_only_a_logged_in_owner_can_issue_or_read_commands(self):
        lic, pcs = self.shop(1)
        body = {"license_id": lic["id"], "command_type": "SUSPEND_LICENSE", "reason": "hacker"}
        self.assertEqual(self.http.post("/api/owner/commands", json=body).status_code, 401)
        self.assertEqual(self.http.get("/api/owner/commands").status_code, 401)
        # a customer PC's installation secret is not an owner credential
        h = {"Authorization": "Bearer " + pcs[0][1]}
        self.assertEqual(self.http.post("/api/owner/commands", json=body, headers=h).status_code, 401)
        self.assertEqual(self.owner("get", f"/api/owner/licenses/{lic['id']}").json["status"], "active")

    def test_poll_and_ack_need_the_right_installation_secret(self):
        lic, pcs = self.shop(2)
        cid = self.command(lic["id"], "DISABLE_DEVICE", pcs[0][0], reason="test").json["id"]
        self.assertEqual(self.poll(pcs[0][0], "wrong-secret").status_code, 401)
        self.assertEqual(self.poll("no-such-pc-12345", "x").status_code, 401)
        self.assertEqual(self.ack(pcs[0][0], "wrong-secret", cid).status_code, 401)
        # PC #2 (valid secret) cannot acknowledge PC #1's command
        self.assertEqual(self.ack(pcs[1][0], pcs[1][1], cid).status_code, 404)
        self.assertEqual(self.owner("get", f"/api/owner/commands/{cid}").json["summary"]["applied"], 0)

    def test_bad_ack_status_and_unknown_command(self):
        lic, pcs = self.shop(1)
        cid = self.command(lic["id"], "FORCE_REVALIDATE").json["id"]
        self.assertEqual(self.ack(*pcs[0], cid, status="pwned").status_code, 400)
        self.assertEqual(self.ack(*pcs[0], "deadbeef").status_code, 404)

    def test_expired_commands_are_not_delivered_but_state_stays_enforced(self):
        lic, pcs = self.shop(1)
        cid = self.command(lic["id"], "SUSPEND_LICENSE").json["id"]
        c = lic_db.connect(self.svc.db_target)
        c.execute("UPDATE remote_commands SET expires_at='2020-01-01T00:00:00Z' WHERE id=?", (cid,)); c.close()
        d = self.owner("get", f"/api/owner/commands/{cid}").json
        self.assertEqual(d["summary"]["overall"], "expired")                  # visible even before a poll
        p = self.poll(*pcs[0])
        self.assertEqual(p.json["commands"], [])
        self.assertEqual(p.json["code"], "LICENSE_SUSPENDED")                 # the suspension itself does not expire
        self.assertEqual(self.ack(*pcs[0], cid).json["status"], "expired")

    def test_poll_writes_ip_history_only_when_the_public_ip_changes(self):
        lic, pcs = self.shop(1)
        iid, sec = pcs[0]
        n0 = len(self.owner("get", f"/api/owner/installations/{iid}/ip-history").json["history"])
        for _ in range(5):
            self.poll(iid, sec)                                               # same IP as activation: nothing written
        self.assertEqual(len(self.owner("get", f"/api/owner/installations/{iid}/ip-history").json["history"]), n0)
        self.poll(iid, sec, ip="198.51.100.7")
        hist = self.owner("get", f"/api/owner/installations/{iid}/ip-history").json["history"]
        self.assertEqual(len(hist), n0 + 1)
        self.assertEqual((hist[0]["ip"], hist[0]["event"]), ("198.51.100.7", "poll"))
        self.assertEqual(self.owner("get", f"/api/owner/installations/{iid}").json["last_ip"], "198.51.100.7")

    def test_audit_records_issue_and_ack(self):
        lic, pcs = self.shop(1)
        cid = self.command(lic["id"], "SUSPEND_LICENSE").json["id"]
        self.poll(*pcs[0]); self.ack(*pcs[0], cid)
        acts = [e["action"] for e in self.owner("get", f"/api/owner/audit?license_id={lic['id']}").json["events"]]
        self.assertIn("command_issued", acts)
        self.assertIn("command_applied", acts)
        self.assertIn("license_suspended", acts)

    def test_poll_has_its_own_higher_rate_limit(self):
        s = Settings(data_dir=Path(self.tmp.name) / "rl", trusted_proxies=(), rate_limit_requests=3, poll_rate_limit_requests=6)
        app = create_app(s); svc = app.extensions["license_service"]; svc.create_owner("rlowner", PW)
        http = app.test_client()
        h = {"Authorization": "Bearer " + http.post("/api/owner/login", json={"username": "rlowner", "password": PW},
                                                    environ_base={"REMOTE_ADDR": "192.0.2.9"}).json["token"]}
        lic = http.post("/api/owner/licenses", json={"label": "x", "max_installations": 3}, headers=h).json
        r = http.post("/api/v1/activate", json={"serial_key": lic["serial_key"], "installation_id": "pc-ratelimit1", "device_name": "p"},
                      environ_base={"REMOTE_ADDR": "203.0.113.50"})
        sec = r.json["installation_secret"]
        codes = [http.post("/api/v1/poll", json={"installation_id": "pc-ratelimit1", "installation_secret": sec},
                           environ_base={"REMOTE_ADDR": "203.0.113.50"}).status_code for _ in range(8)]
        self.assertEqual(codes[:6], [200] * 6)
        self.assertEqual(codes[6:], [429, 429])
        # activation/check-in budget (3/min) is a separate bucket and was only used once
        self.assertEqual(http.post("/api/v1/checkin", json={"installation_id": "pc-ratelimit1", "installation_secret": sec},
                                   environ_base={"REMOTE_ADDR": "203.0.113.50"}).status_code, 200)


class MigrationTests(unittest.TestCase):
    def test_existing_v1_database_upgrades_without_losing_data(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(tmp.name) / "old.db"
        con = sqlite3.connect(path)
        for stmt in [s for s in lic_db.MIGRATIONS[0].split(";") if s.strip()]:
            con.execute(stmt)
        con.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        con.execute("INSERT INTO schema_version VALUES (1)")
        con.execute("INSERT INTO licenses(key_hash,key_hint,label,status,max_installations,created_at,updated_at) "
                    "VALUES ('h','ABCD','Old shop','active',3,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
        con.commit(); con.close()
        self.assertEqual(lic_db.migrate(path), len(lic_db.MIGRATIONS))
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("SELECT label FROM licenses").fetchone()[0], "Old shop")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM remote_commands").fetchone()[0], 0)
        con.execute("SELECT * FROM remote_command_deliveries").fetchall()
        self.assertEqual(con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], len(lic_db.MIGRATIONS))
        con.close(); lic_db.migrate(path)                                      # running it again is harmless
        import gc; gc.collect(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
