> **UPDATE (cloud migration):** licensing is now cloud-only (Render). The apps no longer accept a local/LAN or plain-HTTP licensing server; the local-server instructions below describe how the BACKEND code behaves in development and tests only. Current procedure: documentation/CLOUD_LICENSING_RENDER.md.

# Feature 1 — Owner License Manager (serial keys, PC limits, IP monitoring, locking)

Scope: Feature 1 + enforcement. The Admin and User apps ARE now license-gated (see
`OWNER_LICENSE_ENFORCEMENT.md`): without a valid serial key from the Owner License Manager they do nothing.
Pricing, payments, page counting and printer-driver code are untouched
(`tests/test_licensing.py::test_18` guards that only the documented gate points mention licensing).

## Architecture decision (explained before building)
The existing Admin app is a *LAN-only* Flask server inside each shop. A licence authority must be one
central, always-reachable server, so it is a **separate service** (not an extension of the shop Admin):

```
 Owner PC                         Licensing server (yours, HTTPS)              Customer PCs
 owner/  (PySide6 manager) --->   licensing/server (Flask + SQLite)   <---    shared/license_client.py
        Bearer session token       /api/owner/*  owner only                     /api/v1/activate|checkin|deactivate
                                   /api/v1/*     customer installations         (signed Ed25519 token back)
```
Reused: Python 3.12, Flask, SQLite, PySide6 + `shared/theme.py` styling, PyInstaller onedir.
New code only: `licensing/`, `owner/`, `shared/license_token.py`, `shared/license_client.py`, `tests/`.

## Run locally (development, no HTTPS)
```
pip install -r licensing/requirements.txt
set LICENSE_DATA_DIR=C:\SialviLicenseData        (Linux: export ...)
python -m licensing.server create-owner --username owner     # asks for a password (min 10 chars)
python -m licensing.server run                                # http://127.0.0.1:8780 (loopback only)
python -m owner.run_owner                                     # Owner License Manager; sign in
python -m licensing.server show-public-key > server_public.pem
python -m licensing.client_demo --server https://sialvi-print-backend.onrender.com --public-key server_public.pem activate SIALVI-XXXX-XXXX-XXXX-XXXX
```
Use `--state-dir` to simulate several PCs. Tests: `python -m unittest discover -s tests -v`.

## Production deployment
1. Put the server on a host customers can reach, **behind HTTPS**: Caddy/nginx/IIS reverse proxy with a real
   certificate -> `http://127.0.0.1:8780`; run `python -m licensing.server run` (waitress is used if installed).
2. Set `LICENSE_TRUSTED_PROXIES` to the proxy's address so the real client IP is read from `X-Forwarded-For`.
   The header is **ignored** from any other peer. The server refuses to listen on a public address without
   HTTPS/proxy unless `--allow-http`.
3. Back up `LICENSE_DATA_DIR` (licenses.db, `signing_key.pem`, `key_pepper.secret`). Losing the pepper makes
   existing serial keys unverifiable; leaking `signing_key.pem` lets anyone forge tokens. Never commit them.
4. Owner app: `licensing\build_owner.bat` (Windows) -> `dist\SialviOwnerLicenseManager\`. Owner PC only.
5. Config template: `licensing/server.env.example` (contains no secrets).

## Policies (configurable by environment variable)
| Setting | Default | Meaning |
|---|---|---|
| `LICENSE_CHECKIN_INTERVAL_MINUTES` | 360 | How often a client should renew |
| `LICENSE_OFFLINE_GRACE_HOURS` | 72 | Token lifetime = how long a PC works with no server contact |
| `LICENSE_NOT_RECENT_MINUTES` | 720 | Dashboard shows "Not recently seen" after this |
| `LICENSE_OWNER_SESSION_HOURS` | 8 | Owner session lifetime |
Locking a connected PC takes effect at its next check-in (<= interval). A **disconnected** PC is not locked
instantly: it stops when its grace period ends. A refusal received from the server is remembered and enforced
even offline. "Last seen N ago" is never presented as "online".

## What is implemented (maps to the brief)
- **Create license**: label, PC limit, initial status, notes, optional expiry. Key = `SIALVI-XXXX-XXXX-XXXX-XXXX`
  from `secrets` (~79 bits), UNIQUE in the DB, stored only as HMAC-SHA256(pepper) + last-4 hint, shown **once**
  with a Copy button (not in logs/audit). A forgotten key cannot be recovered - create a new license.
- **PC limit**: enforced in the server inside a `BEGIN IMMEDIATE` transaction (race-tested: 16 threads, limit 3 -> exactly 3).
  Counts distinct installations (`active`+`locked`); repeat check-ins/re-installs of the same ID use no slot.
  Raise/lower any time. Lowering below usage -> HTTP 409 `LIMIT_CONFLICT`, nothing changes, and the owner must
  explicitly pick PCs to deactivate (rows and history are kept). Customers can free a slot (`/api/v1/deactivate`).
- **Monitoring**: per-PC ID, device, license, version, OS, first activation, last check-in, last public IP, status
  (Active / Locked / Deactivated / Suspended / Revoked / Expired / Not recently seen).
  "Pending activation" exists as a label but activation is atomic, so it does not normally appear.
- **IP history**: server-observed peer IP only (body/headers from clients ignored unless from a configured trusted
  proxy). Every activation/check-in/denial appends a row; nothing is overwritten or deleted.
- **Lock/unlock one PC**: confirmation + reason + audit; refused at next check-in with a clear message; history kept;
  other PCs unaffected. Unlock re-enables only if the license is valid (otherwise it warns and is still refused).
  A locked PC cannot clear its lock by self-deactivating/re-activating.
- **IP restrictions**: IPv4/IPv6/CIDR, scope = *block new activations* or *block all authorization*, global or one
  license; global needs explicit confirmation; disable/enable keeps history; independent of PC locks. Too-large
  ranges (/0..) are rejected.
- **License status**: Active / Suspended (reversible) / Revoked (final; create a new license) / Expired (computed).
- **Owner security**: PBKDF2-SHA256 passwords, random session tokens (hashed), 5 failures -> 15 min lockout (per
  user and per IP), every owner endpoint server-side authorized, no default account, no create-license endpoint
  for customers. Client endpoints are rate-limited per IP (default 30/min). Parametrised SQL only.
- **Audit**: license/limit/status edits, activations (accepted + rejected), locks, restrictions, logins - with before/after.

## Honest limits
- **Internet is needed** for activation and at least once per offline-grace period (default 72 h). Offline-only shops should raise `LICENSE_OFFLINE_GRACE_HOURS` (e.g. 720 = 30 days) - see `OWNER_LICENSE_ENFORCEMENT.md`.
- A locked customer who wipes the app can try to activate as a **new** installation ID if a free slot exists. Mitigations:
  slots (locked PCs keep consuming one), IP restrictions, suspending/revoking the license. A hardware-bound ID is a later feature.
- The installation secret is stored in a local JSON file on the customer PC (Windows DPAPI protection is a possible hardening).
- No owner 2-factor login yet. Single SQLite file = one server instance (fine for this scale; migrate to PostgreSQL if you outgrow it).
- A public IP is not a person/device (NAT, VPN, DHCP) - treat IP history as an investigative aid only.
- Rate limiting is per process memory.

## Test report (development environment: Linux, Python 3.12, loopback, reserved doc IPs)
`python -m unittest discover -s tests` -> **82 tests OK** (22 LAN/exit-password + 31 licensing server + 1 owner-UI smoke + 15 app-gate tests).
Covers acceptance items 1-17 (create/unique keys, 5 ok/6th rejected, repeat check-ins, raise to 8, lower conflict,
IP list/history, lock, locked can't renew, unlock vs suspended, scoped IP rules, removing restriction keeps lock,
simultaneous activations, unauthorized access, offline/grace/clock-rollback/tamper) and #18 (printing code does not import licensing).
Also run manually: real `python -m licensing.server run` + `licensing.client_demo` (activate, 2nd PC refused at limit, lock -> refused, offline refusal persists).

**NOT verified (needs you):** the packaged Windows Admin/User exes showing the license screen; real HTTPS + reverse proxy + trusted-proxy IP behaviour on your host; PyInstaller build of the
owner app and how the UI looks on Windows; real customer apps (they are not integrated yet).

## Windows/real-world acceptance checklist (tick only after you do it)
- [ ] Server on a public host behind HTTPS; owner app signs in over HTTPS with a valid certificate.
- [ ] Create a 5-PC license; activate 5 PCs from different networks; 6th refused; raise to 8; 3 more work.
- [ ] Observed public IPs in the list match each PC's real public IP (check the proxy forwards correctly).
- [ ] Lock one PC: refused at next check-in, others fine; unlock restores it.
- [ ] Unplug a PC's internet: still authorized until the grace period ends, then refused.
- [ ] Add a scoped IP restriction; confirm only that scope is affected; removing it leaves PC locks intact.

## Files
Added: `licensing/` (server: config, db, security, service, app, __main__; client_demo; requirements; env example; build_owner.bat),
`owner/` (api, run_owner, ui/main_window, ui/dialogs, build_owner.spec), `shared/license_token.py`, `shared/license_client.py`,
`tests/test_licensing.py`, `tests/test_owner_ui_smoke.py`, this document.
Changed: `documentation/AI_HANDOFF_PROMPT.md`, `README.md` (notes only).
Untouched: all of `admin/`, `user/` (apart from the earlier requested features), installers, printing/pricing/payment code.
