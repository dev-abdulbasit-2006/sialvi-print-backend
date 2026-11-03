# ARCHITECTURE (verified from source)

| Component | Source | Entry point | Spec / installer |
|---|---|---|---|
| Admin (PySide6 UI + embedded Flask API on TCP 8765, SQLite, print queue) | `admin/` | `admin/run_admin.py` | `admin/build_admin.spec`, `installer/AdminSetup.iss` |
| User / customer app | `user/` | `user/run_user.py` | `user/build_user.spec`, `installer/UserSetup.iss` |
| Owner License Manager (PySide6) | `owner/` | `owner/run_owner.py` | `owner/build_owner.spec`, `installer/OwnerSetup.iss` |
| Licensing service (Flask, Ed25519-signed tokens) on Render | `licensing/server/` served by root `app.py` (`gunicorn app:app`) | cloud only: https://sialvi-print-backend.onrender.com | not installed on PCs |
| Shared | `shared/` | n/a | license client/gate/UI, token verify, printing helpers, LAN discovery |

Communication
- User -> Admin: authenticated HTTP (LAN token) on 8765; UDP discovery to find the Admin.
- Admin UI -> its own Flask server: `admin/ui/api_client.py` (127.0.0.1).
- Admin/User -> licensing server: `shared/license_client.py` (`/api/v1/activate`, `/checkin`, `/deactivate`, plus `/poll` + `/ack-command` every ~30 s for remote commands, see `documentation/REMOTE_COMMANDS.md`); apps embed only the PUBLIC key (`shared/license_embedded.py`).
- Owner -> licensing server: `owner/api.py` (licenses, PCs, IP restrictions, audit, remote commands).

Generated (not source): `build/`, `dist/`, `installer/Output/`, `__pycache__/`.
Tooling/design material (not used by code): `SuperDesign_User_UI_Export/`, `.superdesign/`, `.cursor/`, `SUPERDESIGN_WORKSPACE_ANALYSIS.md`, `CLAUDE.md`.
Other docs: `documentation/`.

Licensing is cloud-only: Admin, User and Owner use the single address in `shared/cloud_config.py`; nothing in the apps reads a local/LAN licensing address. `functions/`, `firebase.json`, `firestore.*` are an UNUSED alternative backend (see documentation/CLOUD_LICENSING_RENDER.md).
