# RELEASE NOTES: recovery pass

Fixed
- Owner packaging: spec now a true onedir (COLLECT); `OwnerSetup.iss` packages the whole folder; `build_all.bat` builds Owner + OwnerSetup and verifies `_internal\python3xx.dll`.
- Admin: `get_queue()` validates its response (dashboard refresh can't crash on odd payloads); `get_dashboard()` already validated.
- Admin: `run_admin.py` overwrote the license gate variable with the password dialog, so the runtime LicenseMonitor watched the wrong object. Fixed (`pw_dialog`).
- Licensing client: network errors during activation return a `NETWORK` state instead of raising.

Added
- Reset License: new `LicenseClient.replace_license` / `LicenseGate.replace_license`; the dialog asks for a new key, validates it on the server, replaces the local license only on success (old state restored on any failure) and releases the old installation slot (best effort). Admin sidebar button "Reset License".
- Tests: `tests/test_admin_api_client.py`, `ResetLicenseTests` in `tests/test_license_gate.py`.
- `BUILD_GUIDE.md`, `ARCHITECTURE.md`, `.gitignore`.

Removed: `.windsurfrules` (identical duplicate of `CLAUDE.md`).

Not verified (needs Windows): PyInstaller/Inno builds, installed-app launches, printing, payments, Firebase.


## Premium User UI (customer app)
- New light/navy premium theme (`user/ui/theme_user.py`), reusable components (`user/ui/components.py`), welcome page, step rail, drag-and-drop upload zone, file rows with real progress, segmented orientation, copies stepper, honest status card (waiting / printing / printed / failed), styled confirmations, motion (fade, animated steps and glyphs, `SIALVI_REDUCE_MOTION=1` disables).
- Business logic untouched; see `documentation/USER_UI_DESIGN.md` for the exact list of small behavior changes.
- Tests: `tests/test_user_ui.py` (25). Preview tool: `tools/ui_preview.py`. Screenshots: `screenshots/`.
- Unverified on Windows: fonts, DPI scaling, touch, real printing.


## Cloud-only licensing (Render)
- Removed every local/LAN/plain-HTTP licensing path from Admin, User, Owner and the build: `allow_insecure_http`, `is_loopback_url`, `SIALVI_LICENSE_*` env override, `--allow-http`, `--insecure-http`, Owner "Allow plain HTTP" box and server-URL field, `run_licensing_server.bat`.
- `shared/license_embedded.py` fixed: it used names the code never read (`LICENSE_SERVER_URL`, `PUBLIC_KEY`) and a placeholder RSA key. Now `SERVER_URL`=Render URL, `PUBLIC_KEY_PEM` filled by `embed_config.py --fetch` (Ed25519). Empty key = apps fail closed.
- `embed_config.py`: cloud-only, rejects http/localhost/IP/private/.local hosts and RSA keys, `--fetch` retries through Render cold starts, `--check --online` compares the embedded key with the live server. Both build scripts use it.
- Backend: root `app.py` now serves the REAL licensing server (the old stub answered ACTIVE for any key). Added `render.yaml`, minimal `requirements.txt`, secrets-from-environment (`LICENSE_SIGNING_KEY_PEM`, `LICENSE_KEY_PEPPER`) and `python -m licensing.server render-secrets`.
- Tests: `tests/test_cloud_licensing.py`; loopback-HTTP helpers moved to `tests/local_test_support.py` (test-only).
- NOT verified: the live Render deployment (no network from the build sandbox), Windows builds. Database persistence on Render free tier is UNSOLVED (see documentation/CLOUD_LICENSING_RENDER.md).


## User app: dark theme + maximize
- The customer app now uses a DARK theme that matches the Admin app (same palette and orange accent); all dialogs (confirm, license, first-run setup, exit password, print properties) use it. Admin/Owner look is unchanged (hash-pinned test).
- The User window starts maximized and has a Maximize/Restore button (the old "no Maximize" kiosk rule and its test were changed on purpose). Minimum size 1200x680; closing still requires the exit password.
- New asset `assets/arrow_down_light.png`; tests: dark palette matches Admin, WCAG AA text pairs recomputed for dark, maximize flags/state.
- Not verified on Windows: real fonts, DPI scaling, multi-monitor maximize.


## Fix: "works today, not recognized tomorrow" (persistent PostgreSQL storage)
- Root cause: the licensing server kept all data in a SQLite file on Render's temporary disk; restarts / sleep wiped licenses, PCs and the owner session.
- Server now supports PostgreSQL (`DATABASE_URL`, psycopg2 pool, sqlite-compatible adapter in `licensing/server/db.py`; writers still serialized, the PC limit stays race-safe). Falls back to SQLite only when `DATABASE_URL` is empty.
- `/api/v1/health` reports `storage` and `persistent`; the Owner app shows a red banner when the server's data is temporary; `app.py` logs an error on Render without `DATABASE_URL`.
- Clearer customer message for an unknown PC. New: `documentation/ACTIVATION_GUIDE.md`, `tests/test_persistence.py` (13 tests), `tests/run_server_tests_on_postgres.py`.
- Verified on a real local PostgreSQL 16: 157/159 existing tests + 13 persistence tests pass (2 tests read the SQLite file directly). NOT verified: your hosted PostgreSQL + Render, Windows builds.


## Remote commands + responsive license dialog
Added
- Remote command channel (Owner → PCs): suspend / reactivate / revoke license, disable / re-enable / remove one PC, force re-check. Server changes the state at once; PCs poll every ~30 s, confirm with a signed check-in and acknowledge (applied / not applied). Owner tab **Remote Commands** with per-PC delivery status. See `documentation/REMOTE_COMMANDS.md`.
- Server: DB migration 2 (`remote_commands`, `remote_command_deliveries`), `/api/v1/poll`, `/api/v1/ack-command`, `/api/owner/commands`, own rate-limit bucket for poll/ack, settings `LICENSE_POLL_INTERVAL_SECONDS`, `LICENSE_COMMAND_TTL_HOURS`, `LICENSE_POLL_RATE_LIMIT`.
- Client: `LicenseClient.poll_cycle()` + gate background poll thread; operations on the local license state are now serialized with a lock.
- License dialog: Activate / Check again / Reset License run the server call in a worker thread, so the window no longer freezes (up to ~45 s) while the free cloud server wakes up.
- Runtime license monitor checks every 5 s (was 15 s) so a suspension shows quickly after the PC learns of it.
Changed
- `NOT_CONFIGURED` message now says the key is not embedded (was "built without a licensing server").
- Check-in decision logic moved into one function (`_denial_code`) shared by check-in and poll (same rules, no behaviour change).
Not done: read-only mode while suspended (needs your decision, see REMOTE_COMMANDS.md). Not verified: PostgreSQL run of the new SQL, PySide6 UI (tab + dialog), Windows builds, live Render.
