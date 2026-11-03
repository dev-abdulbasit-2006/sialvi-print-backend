"""Offline-LAN helpers shared by the Admin and User apps.

Everything here works with NO internet, NO DNS and NO Wi-Fi: it only looks at
this PC's own adapters and talks directly to private IP addresses.

Why this module exists
----------------------
On a wired-only shop LAN (Ethernet switch/router, no internet, maybe no
gateway) the old code had three weak points:

* ``connect(("10.255.255.255", 1))`` is used to guess "my IP".  With no default
  route and no 10.x route Windows raises "network unreachable", so the Admin
  "Computers" screen could show nothing useful.
* UDP discovery sent ONE broadcast from an unbound socket.  Windows sends that
  out of a single adapter (the one it prefers, often Wi-Fi/dongle), so a
  broadcast never reached the Ethernet LAN when two adapters were present.
* There was no fallback when broadcasts were blocked.

The helpers below enumerate every local IPv4 address, expose per-adapter
broadcast addresses, test the actual TCP port of the Admin API, and give the
operator a plain-English reason when the Admin cannot be reached.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_API_PORT = 8765


# --------------------------------------------------------------------------
# Local addresses
# --------------------------------------------------------------------------

def _is_usable(ip: str) -> bool:
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_unspecified or addr.is_multicast)


def _is_link_local(ip: str) -> bool:
    return ip.startswith("169.254.")


def local_ipv4_addresses() -> list[str]:
    """Every usable IPv4 address of this PC (Ethernet, Wi-Fi, dongle...).

    No packet leaves the PC. Private/normal addresses are listed first and
    169.254.x.x (Windows "no DHCP" self-assigned addresses) last - they are
    still returned because a switch-only LAN with no DHCP server works with
    them too.
    """
    found: list[str] = []

    def add(ip):
        if _is_usable(ip) and ip not in found:
            found.append(ip)

    host = socket.gethostname()
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(host)[2]:
            add(ip)
    except OSError:
        pass
    # Route-table probes. connect() on UDP sends nothing; it only asks the OS
    # which local address it would use. Each probe only succeeds when this PC
    # has a matching on-link or default route, so try all private ranges.
    for target in ("10.255.255.255", "172.16.255.255", "192.168.255.255", "169.254.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()

    found.sort(key=_is_link_local)  # stable: link-local last
    return found


def subnet_broadcast_guess(ip: str) -> str:
    """Directed broadcast of the /24 containing ``ip`` (the common shop LAN).

    Python's standard library cannot read netmasks, so /24 is assumed. The
    limited broadcast 255.255.255.255 is always sent as well.
    """
    a, b, c, _ = ip.split(".")
    return f"{a}.{b}.{c}.255"


def subnet_hosts_24(ip: str, skip_self: bool = True) -> list[str]:
    """All other host addresses of the /24 containing ``ip``."""
    a, b, c, d = ip.split(".")
    hosts = [f"{a}.{b}.{c}.{i}" for i in range(1, 255)]
    if skip_self and ip in hosts:
        hosts.remove(ip)
    return hosts


def same_24(ip_a: str, ip_b: str) -> bool:
    return ip_a.rsplit(".", 1)[0] == ip_b.rsplit(".", 1)[0]


# --------------------------------------------------------------------------
# Reachability of the real application port (never ICMP ping, never internet)
# --------------------------------------------------------------------------

def tcp_port_open(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    """Try a real TCP connection. Returns (ok, kind).

    kind: "open" | "refused" | "timeout" | "unreachable" | "dns" | "error"
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "open"
    except socket.gaierror:
        return False, "dns"
    except ConnectionRefusedError:
        return False, "refused"
    except (socket.timeout, TimeoutError):
        return False, "timeout"
    except OSError as e:
        winerr = getattr(e, "winerror", None)
        if winerr == 10061 or e.errno == errno.ECONNREFUSED:
            return False, "refused"
        if winerr in (10060,) or e.errno == errno.ETIMEDOUT:
            return False, "timeout"
        if winerr in (10051, 10065) or e.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return False, "unreachable"
        return False, "error"


def probe_admin_health(host: str, port: int, timeout: float = 3.0) -> bool:
    """True if ``host:port`` answers like the SialviPrint Admin API.

    /api/health needs no token, and no proxy is used (private LAN address).
    """
    url = f"http://{host}:{int(port)}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = json.loads(resp.read(4096).decode("utf-8", "replace"))
        return isinstance(body, dict) and body.get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return False


def diagnose_admin(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    """Plain-English diagnosis of "can this PC reach the Admin API?".

    Returns (reachable, message). Only the real application port is tested.
    """
    host = (host or "").strip()
    if not host:
        return False, "Please enter the Admin PC address first."

    mine = local_ipv4_addresses()
    if host in mine:
        return False, (
            f"{host} is THIS PC's own address. Enter the Admin PC's address "
            "(shown on the Admin app > Computers screen), not this PC's."
        )

    ok, kind = tcp_port_open(host, port, timeout)
    is_loopback = host.lower() in ("localhost", "::1") or host.startswith("127.")
    if not ok and is_loopback and kind in ("timeout", "unreachable", "error"):
        # Windows reports a refused connection to THIS PC only after ~2 s of retries, so a short timeout
        # looks like "no answer". Nothing can be unreachable on loopback: treat it as "not listening".
        kind = "refused"
    if ok:
        if probe_admin_health(host, port, timeout):
            return True, f"Admin PC found at {host}:{port}."
        return False, (
            f"Something is listening at {host}:{port} but it is not SialviPrint Admin. "
            "Check the address and port."
        )

    if kind == "dns":
        return False, (
            f"The name '{host}' cannot be resolved. Offline networks have no DNS - "
            "enter the Admin PC's IP address (for example 192.168.1.10) instead."
        )
    if kind == "refused":
        return False, (
            f"The PC at {host} answered but nothing is listening on port {port}. "
            "Start the SialviPrint Admin app on the Admin PC and check the port number."
        )

    same = [ip for ip in mine if _is_usable(host) and same_24(ip, host)] if _is_ip(host) else []
    lines = [f"No answer from {host}:{port}."]
    if _is_ip(host) and mine and not same:
        lines.append(
            "This PC's address(es) are " + ", ".join(mine) + " - none is in the same "
            "network as " + host + ". Check that both PCs use the same IP range "
            "(e.g. 192.168.1.x with subnet mask 255.255.255.0) and that the Ethernet cable is connected."
        )
    else:
        lines.append(
            "Check: (1) the Admin app is running, (2) the address is correct, "
            "(3) the Ethernet cable/switch, (4) Windows Firewall on the Admin PC - "
            "an offline Ethernet network is often classed as 'Public'; run "
            "Fix_LAN_Firewall.bat on the Admin PC."
        )
    return False, " ".join(lines)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Fallback discovery without broadcasts
# --------------------------------------------------------------------------

def scan_for_admin(port: int = DEFAULT_API_PORT, timeout: float = 0.4,
                   local_ips: list[str] | None = None, max_workers: int = 64,
                   stop_event: threading.Event | None = None) -> list[dict]:
    """Look for the Admin API on the /24 of every local adapter.

    Used when UDP broadcasts are blocked. Only TCP port ``port`` is tried and
    a host counts only if it answers GET /api/health like SialviPrint Admin.
    """
    ips = [ip for ip in (local_ips if local_ips is not None else local_ipv4_addresses())
           if _is_usable(ip)]
    seen_subnets, targets = set(), []
    for ip in ips:
        net = ip.rsplit(".", 1)[0]
        if net in seen_subnets:
            continue
        seen_subnets.add(net)
        targets.extend(subnet_hosts_24(ip))

    found: dict[tuple, dict] = {}
    lock = threading.Lock()

    def check(host):
        if stop_event is not None and stop_event.is_set():
            return
        ok, _ = tcp_port_open(host, port, timeout)
        if ok and probe_admin_health(host, port, max(timeout, 1.0)):
            with lock:
                found[(host, port)] = {"host": host, "port": port, "name": "SialviPrint Admin"}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(check, targets))
    return sorted(found.values(), key=lambda x: x["host"])
