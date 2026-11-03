"""SialviPrint local-network discovery.

Discovery is optional: manual IP/hostname configuration remains supported.
The Admin API stays on TCP 8765. UDP 8766 is used only to discover the Admin
PC on the same local network/router; no authentication secret is broadcast.
"""
import json
import socket
import threading
import time

DISCOVERY_PORT = 8766
REQUEST = b"SIALVIPRINT_DISCOVER_V1"
RESPONSE = "SIALVIPRINT_ADMIN_V1"


def discover_admins(timeout=2.5):
    """Return unique Admin announcements heard on the local network.

    The request is sent from EVERY local IPv4 adapter (Ethernet, Wi-Fi,
    dongle...) - a socket bound to an adapter's address makes Windows send
    the broadcast out of that adapter. A single unbound socket only uses the
    adapter Windows prefers, which on a PC with Wi-Fi + Ethernet is often not
    the wired LAN. Both the limited broadcast (255.255.255.255) and the /24
    directed broadcast of each adapter are used.
    """
    from shared.netutil import local_ipv4_addresses, subnet_broadcast_guess

    socks = []
    for ip in local_ipv4_addresses():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((ip, 0))
            sock.settimeout(0.05)
        except OSError:
            sock.close()
            continue
        socks.append((ip, sock))
    if not socks:  # no usable adapter info: behave like the old single broadcast
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.05)
        socks.append((None, sock))

    found = {}
    try:
        for ip, sock in socks:
            targets = ["255.255.255.255"]
            if ip:
                targets.append(subnet_broadcast_guess(ip))
            for target in targets:
                try:
                    sock.sendto(REQUEST, (target, DISCOVERY_PORT))
                except OSError:
                    pass  # this adapter is down/disconnected - try the others
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            for _ip, sock in socks:
                try:
                    data, addr = sock.recvfrom(8192)
                except (socket.timeout, BlockingIOError):
                    continue
                except OSError:
                    continue
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict) or msg.get("type") != RESPONSE:
                    continue
                host = msg.get("host") or addr[0]
                try:
                    port = int(msg.get("port", 8765))
                except (TypeError, ValueError):
                    continue
                found[(host, port)] = {
                    "host": host,
                    "port": port,
                    "name": msg.get("name", "SialviPrint Admin"),
                }
    finally:
        for _ip, sock in socks:
            sock.close()
    return list(found.values())


class AdminDiscoveryResponder:
    """Small UDP responder owned by the Admin process."""
    def __init__(self, port=DISCOVERY_PORT, server_port=8765, name="SialviPrint Admin"):
        self.port = port
        self.server_port = server_port
        self.name = name
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="SialviPrintDiscovery", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if data.strip() != REQUEST:
                    continue
                payload = json.dumps({
                    "type": RESPONSE,
                    "host": self._local_ip_for(addr[0]),
                    "port": self.server_port,
                    "name": self.name,
                }).encode("utf-8")
                try:
                    sock.sendto(payload, addr)
                except OSError:
                    pass
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None

    @staticmethod
    def _local_ip_for(remote_host):
        # Ask Windows/Linux routing table which local interface would reach
        # the requesting station. This avoids advertising 127.0.0.1.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((remote_host, 1))
            return probe.getsockname()[0]
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            probe.close()
