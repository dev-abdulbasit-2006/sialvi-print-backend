# Sialvi Printing Solution — Admin Backend (Phase 1)

This is the **foundation layer** of the LAN printing management system:
the Admin PC's local database, printer integration, print queue worker,
and the full LAN HTTP API that User PCs will talk to. It is real,
runnable code — not a mockup — but it is the backend only. The Admin
and User **desktop UIs**, and the **Windows installers**, are Phase 2–4
(see "What's next" below).

## What's included

```
PrintingSystem/
├── admin/
│   ├── config/config.py        # Windows-appropriate paths, LAN token, settings
│   ├── database/
│   │   ├── schema.sql           # Full schema: orders, files, payments, jobs, etc.
│   │   └── db.py                 # SQLite connection + atomic transaction helper
│   ├── printer/printer_manager.py   # Real Windows printer discovery/capability/print via pywin32
│   ├── queue/queue_manager.py       # Background thread; crash-safe, no double-printing
│   └── server/
│       ├── app.py                # Flask app, startup sequence, global error handling
│       ├── helpers.py            # LAN auth, page-range parsing, logging setup
│       ├── file_storage.py       # Streamed chunked upload, safe internal file IDs
│       ├── page_counter.py       # Real page counting (PDF/PPTX exact, Office via LibreOffice, design via conversion)
       ├── design_file_converter.py # PSD/AI/CAD -> PDF conversion pipeline
│       ├── period.py             # Crash-safe weekly reset via persistent period key
│       ├── routes_orders.py      # Create order, upload files, set options, price calc
│       ├── routes_payments.py    # PAY + admin verification (only path to release printing)
│       ├── routes_printers.py    # Live printer discovery + admin profile mapping
│       ├── routes_profiles.py    # Print profiles + admin-only pricing
│       └── routes_queue.py       # Queue status, admin dashboard, weekly reset trigger
│   └── requirements.txt
├── user/                           # Customer kiosk + authenticated local print agent

└── shared/                        # reserved for code shared with the User app (Phase 3)
```

## Running it (development machine, before packaging)

This runs on Windows only for full functionality (printer features
require `pywin32`). On Linux/Mac the API layer runs but `printer_manager`
returns an empty list — useful only for early UI development against
the API.

```bash
cd PrintingSystem
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r admin/requirements.txt
python -m admin.server.app
```

On first run this creates `%PROGRAMDATA%\SialviPrint\` containing:
- `data\printing_system.db` — the SQLite database
- `uploads\` — customer files, one subfolder per order
- `logs\admin_server.log` — rotating log file
- `config.json` — settings, including the auto-generated LAN token

**The LAN token** in `config.json` (`lan_shared_token`) must be shared
with every User app's config (Phase 3) and sent as the `X-LAN-Token`
header on every request. Admin-only routes additionally require
`X-Admin-Token` with the same value (the Admin UI, running on the same
machine, sends this automatically once built in Phase 2).

## API reference (LAN contract)

All requests need header `X-LAN-Token: <token>`. Admin-only routes also
need `X-Admin-Token: <token>`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/orders` | Create a new order for a station (`station_id` in body) |
| POST | `/api/orders/<id>/files` | Stream-upload one file (headers: `X-Filename`, `X-Sequence-Index`) |
| DELETE | `/api/orders/<id>/files/<file_id>` | Remove a file from the order |
| PUT | `/api/orders/<id>/options` | Set printer/profile/copies/orientation/page range → returns price |
| GET | `/api/orders/<id>` | Get order state |
| DELETE | `/api/orders/<id>` | NEW CUSTOMER reset (only before payment) |
| POST | `/api/orders/<id>/pay` | Customer presses PAY → creates pending payment |
| GET | `/api/orders/<id>/queue-status` | Poll print job status for this order |
| GET | `/api/printers` | List enabled, live-synced Windows printers |
| GET | `/api/printers/<id>/capabilities` | Real capability detection (color/duplex/paper sizes) |
| GET | `/api/profiles` | List the 13 print profiles with current prices |
| GET | `/api/admin/payments/pending` | Admin: list orders awaiting verification |
| POST | `/api/admin/payments/<id>/verify` | Admin: verify → releases print jobs |
| POST | `/api/admin/payments/<id>/reject` | Admin: reject a payment |
| GET | `/api/admin/queue` | Admin: full non-completed queue view |
| GET | `/api/admin/dashboard` | Admin: earnings/orders/queue/printer stats |
| PUT | `/api/admin/profiles/<id>/price` | Admin: change price per page |
| PUT | `/api/admin/printers/<id>/profile-mapping` | Admin: map printer → profile |
| POST | `/api/admin/weekly-reset/check` | Admin/manual: idempotent weekly reset trigger |

Every error returns `{"error": {"code": "...", "message": "..."}}` with
an appropriate HTTP status — never a raw traceback.

## Design decisions worth knowing about

- **Page counting for Office formats** (DOCX/XLSX/PPTX-via-text/etc.)
  converts through LibreOffice headless to get an exact count that
  matches what will actually print, rather than guessing. **LibreOffice
  must be installed on the Admin PC** for these formats — this is a
  real dependency, documented rather than hidden.
- **PSD/AI/AutoCAD files use a dedicated conversion pipeline** before page counting/printing.
  PSD/AI use ImageMagick; DXF uses ezdxf; DWG uses ODA File Converter -> DXF -> ezdxf.
  External Admin-PC dependencies are documented rather than hidden.

- **Printing itself uses the existing silent PDF path for design files**, after conversion, so the Admin
  never opens an interactive design application just to print a paid job.
- **Concurrency**: Flask runs `threaded=True` so large uploads from one
  User PC never block others; the print queue runs on its own thread
  polling the database (not memory), so an Admin crash/restart recovers
  safely without double-printing.

## What's next (not yet built)

1. **Phase 2 — Admin desktop UI**: dashboard, pending-payments screen,
   price editor, printer mapping screen, queue monitor — talking to
   this API over `localhost`.
2. **Phase 3 — User desktop UI**: the customer-facing kiosk screen
   (file picker, printer/profile/copies/page-range/orientation, PAY,
   NEW CUSTOMER) talking to this API over the LAN, plus its own config
   screen for entering the Admin PC's address/port/token.
3. **Phase 4 — Packaging**: PyInstaller `--onedir` specs for both apps,
   an Inno Setup installer script producing `AdminSetup.exe` and
   `UserSetup.exe`, tested on a clean Windows VM with no dev tools.

Say the word and I'll start Phase 2.


## Current printer architecture (v3.9.1+)

The User PC is now the owner of printer discovery and final printer routing.

- The User app enumerates printers installed on the **User PC**.
- The selected exact Windows printer name is stored with the order together with the persistent station ID and current station address.
- The Admin PC does not need that printer installed.
- After payment verification, the Admin converts Office/design files as required and streams the prepared PDF/image to the authenticated User print agent on TCP 8767.
- The User print agent validates the exact local printer and submits it to that PC's Windows spooler.
- Windows printer sharing is not required.
- The Windows default printer is not used as the routing mechanism.
- If the User PC or selected printer is unavailable, the queue retries rather than redirecting to another printer.

The Admin PC remains the conversion authority, so LibreOffice/ImageMagick/ODA dependencies stay on the Admin PC. The User installer now includes pywin32 and Pillow because the final Windows printing operation happens on the User PC.

See `documentation/NETWORK_SETUP.md` for the deployment topology.

## Licensing (Feature 1)
Owner License Manager + licensing server: see `documentation/LICENSING_FEATURE1.md`.
