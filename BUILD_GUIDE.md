# BUILD GUIDE (Windows)

Prerequisites: Windows 10/11, **Python 3.11 or 3.12** (64-bit, on PATH), **Inno Setup 6** (`iscc.exe`).
Do not use Python 3.13+ (pinned PySide6/PyMuPDF/pywin32 have no wheels) and do not mix Python versions between builds.

## Run from source
| App | Command (from project root) |
|---|---|
| Admin | `python -m admin.run_admin` (or `python admin\run_admin.py`) |
| User | `python -m user.run_user` |
| Owner | `python -m owner.run_owner` |
| Licensing | **Cloud only** - https://sialvi-print-backend.onrender.com (no local server). See `documentation/CLOUD_LICENSING_RENDER.md` |

Backend developers only (the cloud service runs `app.py` under gunicorn; these are not used by customers or by builds):
- `python -m licensing.server render-secrets`  prints NEW `LICENSE_KEY_PEPPER` / `LICENSE_SIGNING_KEY_PEM` values for the Render dashboard (writes nothing).
- `python -m licensing.server create-owner --username owner`  creates the owner login (run against the cloud database; never during builds).
- `python -m licensing.server show-public-key`  prints the public key of the data directory it is pointed at.

## One-command release
```
installer\build_all.bat
```
It installs dependencies, checks pywin32, checks cloud licensing (`embed_config.py --check --online`: the embedded key must match the LIVE Render server), builds **Admin, User and Owner** with PyInstaller (all onedir), verifies the Owner `_internal\python3xx.dll` exists, then compiles three installers. It stops at the first failure.

Outputs:
- `installer\Output\AdminSetup.exe`
- `installer\Output\UserSetup.exe`
- `installer\Output\OwnerSetup.exe`  (Owner only. Do NOT give to customers)

Owner only: `installer\build_owner_only.bat` (tests, embed check, PyInstaller, DLL check, `OwnerSetup.exe`).

Intermediate output (`build\`, `dist\`) is generated and safe to delete.

## Embedding licensing (before a release): cloud only
```
python licensing\embed_config.py --fetch          # downloads the public key from https://sialvi-print-backend.onrender.com
python licensing\embed_config.py --check --online # what build_all.bat runs: fails if the key is missing, is RSA, or differs from the live server
```
The server address is fixed to the Render service (`shared/cloud_config.py`); there is no local-server or plain-HTTP option. The Render free tier sleeps when idle: the first request can take up to a minute (the tools retry automatically). Compare the printed fingerprint with the Owner License Manager > App Builds tab.

## Owner: why the "Failed to load Python DLL" happened
Owner is a onedir build: the exe needs the `_internal\` folder (with `python312.dll`) beside it. The old spec had no COLLECT step and the installer copied only the exe. Always ship/install the WHOLE `dist\SialviOwnerLicenseManager\` folder (now done by `OwnerSetup.iss`).

## Tests
```
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests
```
GUI tests are skipped when PySide6 is not installed.

## Configuration supplied separately (never committed)
Licensing server environment: see `licensing\server.env.example`. The Owner signing key and Owner password live only on the licensing server host.

## Verification done for Owner (Linux test build, NOT Windows)
The spec was built with PyInstaller 6.22 on Linux: it produced `SialviOwnerLicenseManager` plus `_internal\` with the Python runtime, and the binary started headless without errors. The Windows `.exe` and `OwnerSetup.exe` must still be built and launched on Windows.

## User UI preview screenshots
`set QT_QPA_PLATFORM=offscreen` then `python tools\ui_preview.py screenshots\mine` (optional `set W=1366` and `set H=768`). Uses a fake API; nothing is sent anywhere.

## Testing the licensing server on PostgreSQL (optional)
`set TEST_DATABASE_URL=postgresql://user:pw@host:5432/scratchdb` (an EMPTY scratch database), then `python tests\run_server_tests_on_postgres.py` and `python -m unittest tests.test_persistence`. Expected: 2 errors in the first run (tests that read the SQLite file directly); the second run must pass completely.
