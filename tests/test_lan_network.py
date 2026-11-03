"""Offline-LAN tests (run in Linux/Windows dev env with loopback only).

These prove the logic (discovery, diagnosis, scan, installer rules). They do
NOT prove real Ethernet, Windows Firewall profiles or printing - see
documentation/OFFLINE_LAN_SETUP.md for the Windows acceptance tests.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import re
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared import netutil, network_discovery  # noqa: E402


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200 if self.path == "/api/health" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class NetutilTests(unittest.TestCase):
    def test_local_ips_never_loopback(self):
        for ip in netutil.local_ipv4_addresses():
            self.assertFalse(ip.startswith("127."))

    def test_link_local_sorted_last(self):
        orig = socket.getaddrinfo
        try:
            socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("169.254.1.5", 0)), (2, 1, 6, "", ("192.168.1.20", 0))]
            ips = netutil.local_ipv4_addresses()
        finally:
            socket.getaddrinfo = orig
        self.assertLess(ips.index("192.168.1.20"), ips.index("169.254.1.5"))

    def test_broadcast_guess_and_hosts(self):
        self.assertEqual(netutil.subnet_broadcast_guess("192.168.7.33"), "192.168.7.255")
        hosts = netutil.subnet_hosts_24("192.168.7.33")
        self.assertEqual(len(hosts), 253)
        self.assertNotIn("192.168.7.33", hosts)

    def test_health_and_diagnose_ok(self):
        srv = HTTPServer(("127.0.0.1", 0), _Health)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertTrue(netutil.probe_admin_health("127.0.0.1", port))
            ok, msg = netutil.diagnose_admin("127.0.0.1", port)
            self.assertTrue(ok, msg)
        finally:
            srv.shutdown()

    def test_wrong_port_gives_clear_error(self):
        ok, msg = netutil.diagnose_admin("127.0.0.1", _free_port(), timeout=1)
        self.assertFalse(ok)
        self.assertIn("nothing is listening", msg)

    def test_loopback_timeout_is_reported_as_not_listening(self):
        """Windows takes ~2 s to refuse a loopback connection; a 1 s timeout must not say 'wrong network'."""
        from unittest import mock
        with mock.patch.object(netutil, "tcp_port_open", return_value=(False, "timeout")):
            ok, msg = netutil.diagnose_admin("127.0.0.1", 50000, timeout=1)
        self.assertFalse(ok)
        self.assertIn("nothing is listening", msg)

    def test_own_address_detected(self):
        orig = netutil.local_ipv4_addresses
        netutil.local_ipv4_addresses = lambda: ["192.168.1.50"]
        try:
            ok, msg = netutil.diagnose_admin("192.168.1.50", 8765)
        finally:
            netutil.local_ipv4_addresses = orig
        self.assertFalse(ok)
        self.assertIn("THIS PC", msg)

    def test_unresolvable_name_explains_no_dns(self):
        ok, msg = netutil.diagnose_admin("no-such-host.invalid", 8765, timeout=1)
        self.assertFalse(ok)
        self.assertIn("IP address", msg)

    def test_not_sialvi_service_rejected(self):
        class Other(_Health):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"hi")
        srv = HTTPServer(("127.0.0.1", 0), Other)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ok, msg = netutil.diagnose_admin("127.0.0.1", srv.server_address[1])
            self.assertFalse(ok)
            self.assertIn("not SialviPrint", msg)
        finally:
            srv.shutdown()

    def test_scan_finds_admin_without_broadcast(self):
        srv = HTTPServer(("127.0.0.1", 0), _Health)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        orig = netutil.subnet_hosts_24
        netutil.subnet_hosts_24 = lambda ip, skip_self=True: ["127.0.0.1"]
        try:
            found = netutil.scan_for_admin(port=port, local_ips=["192.168.1.2"])
        finally:
            netutil.subnet_hosts_24 = orig
            srv.shutdown()
        self.assertEqual([f["host"] for f in found], ["127.0.0.1"])


class DiscoveryTests(unittest.TestCase):
    def test_responder_answers_and_ignores_garbage(self):
        port = _free_port()
        # UDP port must be free: bind responder on it
        resp = network_discovery.AdminDiscoveryResponder(port=port, server_port=8765, name="T")
        resp.start()
        try:
            import time; time.sleep(0.3)
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.settimeout(2)
            c.sendto(b"garbage", ("127.0.0.1", port))
            c.sendto(network_discovery.REQUEST, ("127.0.0.1", port))
            data, _ = c.recvfrom(4096)
            msg = json.loads(data)
            self.assertEqual(msg["type"], network_discovery.RESPONSE)
            self.assertEqual(msg["port"], 8765)
            c.close()
        finally:
            resp.stop()

    def test_discover_survives_no_adapters_and_down_adapter(self):
        orig = netutil.local_ipv4_addresses
        netutil.local_ipv4_addresses = lambda: ["203.0.113.77"]  # not a local address: bind fails -> skipped
        try:
            self.assertEqual(network_discovery.discover_admins(timeout=0.3), [])
        finally:
            netutil.local_ipv4_addresses = orig


class InstallerRuleTests(unittest.TestCase):
    """Guards the real root cause: rules limited to Private/Domain."""

    def _read(self, name):
        with open(os.path.join(ROOT, "installer", name), encoding="utf-8") as f:
            return f.read()

    def test_no_private_only_profile_in_installers_or_bat(self):
        for name in ("AdminSetup.iss", "UserSetup.iss", "Fix_LAN_Firewall.bat"):
            text = self._read(name)
            self.assertNotIn("profile=private,domain", text, name)
            for line in text.splitlines():
                if "add rule" in line:
                    self.assertIn("profile=any", line, name)
                    # never open to the internet: only LAN/private sources
                    self.assertTrue("remoteip=LocalSubnet" in line or "remoteip=%LAN%" in line, name)
            if name.endswith(".bat"):
                self.assertIn('set "LAN=LocalSubnet,', text)

    def test_admin_opens_api_port_and_discovery(self):
        text = self._read("AdminSetup.iss")
        self.assertRegex(text, r"protocol=TCP localport=8765")
        self.assertRegex(text, r"protocol=UDP localport=8766")
        self.assertIn("Fix_LAN_Firewall.bat", text)

    def test_user_opens_agent_port(self):
        self.assertRegex(self._read("UserSetup.iss"), r"protocol=TCP localport=8767")


class RegressionTests(unittest.TestCase):
    def test_no_internet_dependency_in_network_code(self):
        for rel in ("shared/netutil.py", "shared/network_discovery.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIsNone(re.search(r"8\.8\.8\.8|1\.1\.1\.1|google\.com|ping ", text), rel)

    def test_all_python_compiles(self):
        import compileall
        for d in ("admin", "user", "shared"):
            self.assertTrue(compileall.compile_dir(os.path.join(ROOT, d), quiet=2, force=True), d)


if __name__ == "__main__":
    unittest.main()
