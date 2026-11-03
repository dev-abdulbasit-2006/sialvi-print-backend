# Owner application + license enforcement in the Admin and User apps

## The three pieces
| Piece | Who runs it | Role |
|---|---|---|
| **Licensing server** (`python -m licensing.server run`, behind HTTPS) | You, on one reachable host | Stores licenses, decides every authorization, signs tokens |
| **Owner License Manager** (`owner/`, exe `SialviOwnerLicenseManager`) | Only you | Create keys, set PC limits, see PCs + public IPs, lock PCs/IPs, suspend/revoke, audit, **App Builds** tab |
| **Admin + User apps** (`SialviPrintAdmin.exe`, `SialviPrintUser.exe`) | Customers | Refuse to work without a valid license |

## What the customer apps now do
- **Start-up:** a "License Required" screen appears *before anything else* (before the database, print queue,
  server, print agent or UI). The only options are: enter a valid serial key, or Exit.
- **Activation** needs internet once. The server records the PC (name, app+version, OS, public IP) and returns a
  signed token valid for the offline-grace period (default 72 h). The app renews it automatically in the background.
- **Admin PC:** every API call (except `/api/health`) returns `403 LICENSE_REQUIRED` while unlicensed, so User PCs cannot
  create orders; the **print queue pauses** - jobs stay `QUEUED`, nothing is failed or deleted.
- **User PC:** its print agent refuses jobs while unlicensed (`LICENSE_REQUIRED`); the Admin then **re-queues the job
  without using up a retry** and waits 60 s before trying that PC again. When the PC is licensed again, jobs print.
- **Lost authorization while running** (you lock/suspend/revoke, limit exceeded, grace over): a blocking license screen
  covers the app until it is restored or the user exits (checked every 15 s locally, server contact per the policy).
- **One PC = one slot.** Admin and User apps on the *same* PC share one license identity; each *other* PC (the Admin PC and
  every User PC) uses one slot. A 5-PC key = e.g. 1 Admin + 4 User PCs.

## Anti-tampering design
- Server address + **public** key are compiled into the apps (`shared/license_embedded.py`) - customers cannot point them at a
  fake server, and cannot forge/extend a token (needs your private key, which never leaves the server).
- The "dev bypass" environment variables work **only when running from source**, never in a packaged `.exe`.
- A packaged build with no licensing embedded refuses to run (fail closed), and `installer/build_all.bat` **refuses to build**
  until you embed it.
- Honest limit: a skilled attacker can patch an exe on their own PC. Code signing/obfuscation are possible later hardening;
  the server-side limits, IP monitoring and locks remain your real control.

## Owner workflow (do once, then per customer)
1. Deploy the server behind HTTPS; `python -m licensing.server create-owner --username <you>`.
2. Open the Owner License Manager -> **App Builds** tab -> *Fetch server key* (check the fingerprint) -> *Save settings file*
   and place it as `shared/license_embedded.py` (or run `python licensing\embed_config.py --server https://YOUR-SERVER --fetch`).
3. Build: `installer\build_all.bat` (it verifies licensing is embedded). Give the customer the Admin + User installers.
4. **Licenses** tab -> *Create New License* (name, PC limit) -> copy the key -> send it to the customer.
5. Customer enters the key in each PC's license screen. You see each PC (app + version, OS, last check-in, public IP) in
   **Installations**; lock a PC, block an IP, change the limit, suspend or revoke whenever needed.

## Offline shops (important)
The apps must reach your licensing server over the internet to activate and to renew within the grace period. A shop whose
network never has internet can't activate. For shops that only have internet occasionally, raise the grace period on the
server, e.g. `LICENSE_OFFLINE_GRACE_HOURS=720` (30 days). A longer grace also means a **lock takes up to that long to take
effect on a PC that stays offline**. (A future option: let User PCs renew through the Admin PC.)

## Where license data is stored on a customer PC
`%PROGRAMDATA%\SialviPrint\license\license_state.json` (falls back to `%LOCALAPPDATA%` if not writable): installation ID,
installation secret, signed token. Deleting it makes the PC "not activated" and it must activate again (a new slot is used
only if the old one wasn't deactivated - ask the customer to use *Deactivate* via you, or deactivate the old row in the Owner app).

## Tests (development environment) vs. what still needs Windows
Run: `python -m unittest discover -s tests -v` -> 82 tests OK. They cover: unconfigured/frozen builds refuse; activation, lock,
unlock, persisted refusal after restart; Admin API 403 until licensed; queue pauses and touches nothing; job re-queued without a
lost retry; print agent 503; same-PC apps share a slot; embed tool; build script ordering; entry points gate before any work;
the license dialog (headless).
**Needs real Windows (not yet done):** build the exes, see the license screen on a clean PC, activate against your real HTTPS
server, lock a PC and watch it block, pull the network cable and confirm the grace behaviour, print a job after re-licensing.


## Owner License Manager: troubleshooting (v5)
- **Rebuild after updating:** `licensing\build_owner.bat` (or `pyinstaller owner\build_owner.spec --clean`). Old `dist\` exes keep the old bugs.
- **Errors are never silent any more.** Any unexpected error shows a message box and is written to
  `%LOCALAPPDATA%\SialviOwnerLicenseManager\owner.log` - send me that file if something still misbehaves.
- **Look:** the Owner app now has its own self-contained high-contrast dark theme (`owner/ui/style.py`), independent of
  `shared/theme.py`. Change colours only there; `tests/test_owner_ui_flows.py` measures the rendered contrast.
- **Buttons greyed out?** Select a row first (License / PC / restriction); the action buttons enable when a row is selected.
- **Local testing:** removed. The apps accept ONLY the cloud server address (https). Unit tests use test-only helpers under `tests/`.
  to customers, re-embed your real `https://` address (`python licensing\embed_config.py --server https://... --fetch`).
