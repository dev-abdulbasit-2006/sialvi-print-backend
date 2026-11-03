# Offline Ethernet-only LAN setup (no internet, no Wi-Fi)

## What was wrong (CODE-READ + known Windows behaviour; not yet confirmed on your PCs)

1. **Firewall profile (most likely cause).** The installers opened the firewall only for the
   *Private/Domain* profiles. Windows puts a wired network that has no internet/gateway into the
   **"Unidentified network" = Public** profile, so every SialviPrint rule (TCP 8765, UDP 8766,
   TCP 8767) was inactive. Wi-Fi worked because you had chosen "Private" for it.
2. **Discovery used one broadcast from one adapter.** With Wi-Fi + Ethernet in the same PC, Windows
   sent it out of only one adapter. Now it is sent from every adapter, plus a scan fallback.
3. **"Computers" screen address guess** used a route probe that fails when there is no default route.

## What changed

| File | Change |
|---|---|
| `installer/AdminSetup.iss`, `UserSetup.iss` | Rules now `profile=any` but `remoteip=LocalSubnet` + private ranges only (internet stays blocked); old rules are deleted first so upgrades fix them; explicit TCP 8765 rule added |
| `installer/Fix_LAN_Firewall.bat` (new, copied into each install folder) | One-click repair for already-installed PCs |
| `shared/netutil.py` (new) | Local IPs, real-port reachability test, plain-English diagnosis, broadcast-free LAN scan |
| `shared/network_discovery.py` | Broadcast from every adapter (limited + /24 directed) |
| `user/ui/setup_dialog.py` | "Find Admin" falls back to scan; "Test Connection" explains the exact problem |
| `admin/ui/screens/connection.py` | Reliable IP list; offline hint |
| `user/network/api_client.py` | Clearer connect error text |
| `tests/test_lan_network.py` (new) | 16 loopback/unit tests |

Nothing about printing, pricing, payments, database, tokens or the API was changed.
The architecture is unchanged: User PC -> Admin API (8765) and Admin -> User Print Agent (8767).

## Setup on an offline wired shop (do once)

1. Connect every PC (and the Admin PC) to the same router/switch by Ethernet. Disconnect Wi-Fi if you want.
2. Give each PC an address in the same range, e.g. Admin `192.168.1.10`, users `192.168.1.11`, `.12`...
   all with subnet mask `255.255.255.0` and **different last numbers** (two PCs must never share the same address).
   Best: reserve the Admin's address in the router (DHCP reservation) or set it static.
3. Install the new Admin and User setups (or, on already-installed PCs, right-click
   `Fix_LAN_Firewall.bat` in the SialviPrint install folder > **Run as administrator**; the old
   installed version needs the file copied from `installer\`).
4. Start Admin. Open **Computers**: it shows the Admin address, port 8765 and the connection code.
5. On each User PC: **Find Admin on Same Router / LAN**, or type the Admin IP, enter the code,
   **Test Connection**. The message tells you exactly what is wrong if it fails.
6. Optional hardening: set Ethernet to Private (PowerShell as Admin):
   `Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private`

## Troubleshooting (Ethernet-only)

| Test message | Meaning / fix |
|---|---|
| "none is in the same network" | PCs are in different IP ranges. Fix addresses/mask. |
| "No answer from ..." | Cable/switch, wrong IP, Admin app not running, or firewall -> run `Fix_LAN_Firewall.bat` on the Admin PC |
| "nothing is listening on port" | PC reachable; Admin app not running or wrong port |
| "cannot be resolved" | Offline LAN has no DNS: use the IP address, not the PC name |
| "this PC's own address" | You typed the User PC's IP; use the Admin's |

Manual check from a User PC (PowerShell): `Test-NetConnection 192.168.1.10 -Port 8765`
(`TcpTestSucceeded : True` means the real port is reachable; ping can be blocked and does not matter).
Ethernet metric note: if both Wi-Fi and Ethernet are connected, make sure both PCs' Ethernet are in
the same range; the app now tries every adapter.

Limits: scan and directed-broadcast assume a /24 mask (255.255.255.0). For other masks, type the Admin IP.
DHCP address change: re-run Find Admin (or use a router reservation).

## Evidence levels

- **SANDBOX-VERIFIED (Linux, loopback only):** `python -m unittest discover -s tests -v` -> 16 tests OK:
  diagnosis messages, health probe, scan, responder, installer rules no longer Private-only. Whole project compiles.
- **NOT VERIFIED:** real Ethernet, Windows Firewall profile behaviour, installers (`iscc`), PySide6 UI, printing.

## Windows acceptance tests (mark only after you run them)

- A. Existing Wi-Fi setup still works. [ ] 
- B. Wired only: Admin Wi-Fi off, no internet; User PC `Test-NetConnection <admin> -Port 8765` True; Find Admin works; order -> pay -> verify -> prints. [ ]
- C. Unplug a User cable mid-queue: job retries, never prints elsewhere; reconnect, completes once. [ ]
- D. Restart Admin, User, switch: apps reconnect (User sends heartbeat every 20 s). [ ]
- E. From a PC with a non-LAN address / the internet side, port 8765 is not reachable (rule scoped to LAN). [ ]
