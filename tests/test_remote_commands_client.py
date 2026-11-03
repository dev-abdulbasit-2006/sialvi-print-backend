"""Remote command channel: the PC side (poll cycle, acknowledgement, background thread) against a real in-process
licensing server on loopback. Run: python -m unittest discover -s tests -v"""
import os, sys, time, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_license_gate import GateBase
from shared import license_gate
from shared.license_client import LicenseClient


class PcSideTests(GateBase):
    def pc(self, name="PC-1"):
        g = license_gate.LicenseGate("User", "1.0")
        self.assertTrue(g.activate(self.key, name).authorized)
        return g

    def cmd(self, ctype, iid=None, reason="Invoice payment pending"):
        return self.svc.issue_command("owner", self.lic["id"], ctype, iid, reason)

    def test_quiet_poll_changes_nothing_and_writes_nothing(self):
        g = self.pc(); iid = self.inst_id(g)
        n = len(self.svc.ip_history(iid))
        for _ in range(4):
            self.assertIsNone(g.client.poll_cycle())
        self.assertEqual(len(self.svc.ip_history(iid)), n)                 # no check-in, no history spam
        self.assertTrue(g.is_authorized())

    def test_suspend_reaches_the_pc_and_is_acknowledged_as_applied(self):
        g = self.pc()
        c = self.cmd("SUSPEND_LICENSE")
        st = g.client.poll_cycle()
        self.assertFalse(st.authorized); self.assertEqual(st.code, "LICENSE_SUSPENDED")
        d = self.svc.command_detail(c["id"])
        self.assertEqual(d["summary"]["overall"], "complete")
        self.assertIn("Applied", d["deliveries"][0]["result"])
        g._set(st)
        self.assertFalse(g.is_authorized())
        g2 = license_gate.LicenseGate("User", "1.0")                       # app restarted, even offline: still refused
        self.assertEqual(g2.state().code, "LICENSE_SUSPENDED")

    def test_reactivate_restores_a_pc_that_was_refused(self):
        g = self.pc()
        self.cmd("SUSPEND_LICENSE"); g._set(g.client.poll_cycle())
        self.assertFalse(g.is_authorized())
        c = self.cmd("REACTIVATE_LICENSE", reason="Paid")
        st = g.client.poll_cycle()
        self.assertTrue(st.authorized)                                     # no reinstall, no new key
        g._set(st); self.assertTrue(g.is_authorized())
        self.assertEqual(self.svc.command_detail(c["id"])["summary"]["overall"], "complete")

    def test_disable_one_pc_leaves_the_other_pc_untouched(self):
        a, b = self.pc("PC-A"), None
        # second PC = separate state dir, same license
        original = license_gate.license_state_dir
        license_gate.license_state_dir = lambda: self.state_dir.parent / "pc2"
        try:
            b = license_gate.LicenseGate("User", "1.0"); self.assertTrue(b.activate(self.key, "PC-B").authorized)
        finally:
            license_gate.license_state_dir = original
        self.cmd("DISABLE_DEVICE", self.inst_id(b), reason="Stolen")
        self.assertIsNone(a.client.poll_cycle())                           # A hears nothing
        st = b.client.poll_cycle()
        self.assertEqual(st.code, "INSTALLATION_LOCKED")
        self.assertTrue(a.is_authorized())

    def test_command_that_did_not_end_in_the_requested_state_is_reported_rejected(self):
        g = self.pc()
        c = self.cmd("SUSPEND_LICENSE")
        # a newer command supersedes it before this PC polls
        self.svc.set_license_status("owner", self.lic["id"], "reactivate", "oops")
        st = g.client.poll_cycle()
        self.assertTrue(st.authorized)
        d = self.svc.command_detail(c["id"])
        self.assertEqual(d["deliveries"][0]["status"], "rejected")
        self.assertIn("superseded", d["deliveries"][0]["result"])

    def test_force_revalidate_is_acknowledged_and_changes_no_state(self):
        g = self.pc()
        c = self.cmd("FORCE_REVALIDATE", reason="")
        st = g.client.poll_cycle()
        self.assertTrue(st.authorized)
        self.assertEqual(self.svc.command_detail(c["id"])["summary"]["overall"], "complete")

    def test_unreachable_server_changes_nothing_and_the_command_is_redelivered_later(self):
        g = self.pc()
        c = self.cmd("SUSPEND_LICENSE")
        with mock.patch.object(LicenseClient, "_post", side_effect=__import__("requests").ConnectionError("down")):
            self.assertIsNone(g.client.poll_cycle())
            self.assertTrue(g.is_authorized())                             # offline grace unchanged; no crash
        self.assertEqual(self.svc.command_detail(c["id"])["summary"]["pending"], 1)
        st = g.client.poll_cycle()                                         # back online
        self.assertEqual(st.code, "LICENSE_SUSPENDED")

    def test_ack_is_retried_when_the_network_drops_between_poll_and_ack(self):
        g = self.pc()
        c = self.cmd("SUSPEND_LICENSE")
        with mock.patch.object(LicenseClient, "ack_command", return_value=False):
            st = g.client.poll_cycle()
        self.assertEqual(st.code, "LICENSE_SUSPENDED")                     # enforced anyway
        self.assertEqual(self.svc.command_detail(c["id"])["summary"]["delivered"], 1)   # still waiting for its ack
        g.client.poll_cycle()                                              # server re-sends it, PC acknowledges now
        self.assertEqual(self.svc.command_detail(c["id"])["summary"]["overall"], "complete")

    def test_old_server_without_poll_endpoint_is_ignored_gracefully(self):
        g = self.pc()
        class R:  # what an older server answers for an unknown URL
            status_code = 404
            def json(self): return {"error": {"code": "NOT_FOUND"}}
        with mock.patch.object(LicenseClient, "_post", return_value=R()):
            self.assertIsNone(g.client.poll())
            self.assertIsNone(g.client.poll_cycle())
        self.assertTrue(g.is_authorized())

    def test_a_forged_poll_answer_can_never_grant_access(self):
        g = self.pc()
        self.cmd("SUSPEND_LICENSE"); g._set(g.client.poll_cycle())
        class R:
            status_code = 200
            def json(self): return {"authorized": True, "code": "OK", "commands": [], "poll_interval_seconds": 30}
        with mock.patch.object(LicenseClient, "poll", return_value=R().json()):
            g.client._last_forced_refresh = 0
            st = g.client.poll_cycle()                                     # the "authorized" hint only triggers a REAL check-in
        self.assertFalse(st.authorized)                                    # the server's signed answer is still a refusal
        self.assertFalse(g.is_authorized())

    def test_poll_interval_comes_from_the_server_and_is_bounded(self):
        g = self.pc()
        self.assertEqual(g.client.poll_interval, 30)
        with mock.patch.object(LicenseClient, "poll", return_value={"authorized": True, "commands": [], "poll_interval_seconds": 1}):
            g.client.poll_cycle()
        self.assertEqual(g.client.poll_interval, 10)                       # never faster than 10 s
        with mock.patch.object(LicenseClient, "poll", return_value={"authorized": True, "commands": [], "poll_interval_seconds": 99999}):
            g.client.poll_cycle()
        self.assertEqual(g.client.poll_interval, 300)

    def test_background_thread_applies_a_command_without_any_user_action(self):
        g = self.pc()
        self.cmd("SUSPEND_LICENSE")
        g.client.poll_interval = 0.1
        g.start_background()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and g.state().authorized:
                time.sleep(0.05)
            self.assertFalse(g.state().authorized)
            self.assertEqual(g.state().code, "LICENSE_SUSPENDED")
            self.assertTrue(g._poll_thread.is_alive())
        finally:
            g.stop()

    def test_unconfigured_build_message_is_accurate(self):
        self.blank_embedded()
        g = license_gate.LicenseGate("Admin")
        self.assertEqual(g.state().code, "NOT_CONFIGURED")
        self.assertIn("no licensing server key embedded", g.state().message)


if __name__ == "__main__":
    unittest.main()
