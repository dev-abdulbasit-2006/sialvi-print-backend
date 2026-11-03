# Troubleshooting Guide

## Admin startup: `FileExistsError [WinError 183]` followed by `PermissionError [WinError 5]` on `C:\ProgramData\SialviPrint\data`

This means the `data` directory already exists but the normal Windows user running
SialviPrint Admin cannot access it. A common cause is an older installer/app run
creating the ProgramData tree while elevated.

**Fix:**
1. Close SialviPrint Admin completely.
2. Build the current installer and run `AdminSetup.exe` again over the existing
   installation. The installer now explicitly repairs the ACL on
   `%PROGRAMDATA%\SialviPrint` and its runtime folders.
3. Start SialviPrint Admin normally (do not use "Run as administrator").
4. Do **not** delete `printing_system.db` to fix this problem; the database is
   intentionally preserved.

The application also now turns this condition into a clear startup error instead
of exposing the misleading `pathlib` exception chain.

## Admin-only API protection

Admin-only endpoints (payment verification, pricing changes, receipts, earnings,
queue/admin screens, etc.) are now restricted to loopback requests from the
Admin PC. A User PC's normal LAN token can no longer be used to call those
endpoints remotely. Customer/User routes remain LAN-authenticated as before.

## Installation issues

**"Windows protected your PC" (SmartScreen)**
Expected for an unsigned installer. Click "More info" → "Run anyway".
Get a code-signing certificate to remove this permanently (see
`BUILD_REQUIREMENTS.md`).

**Firewall blocks User PCs from connecting**
During AdminSetup, make sure the "Allow this app through Windows
Firewall" checkbox was ticked. To fix after install:
Windows Security → Firewall & network protection → Allow an app through
firewall → find SialviPrint Admin → check Private and Domain.

**Admin app won't start / closes immediately**
Check `%PROGRAMDATA%\SialviPrint\logs\admin_server.log` for the actual
error — the packaged app suppresses the console window, so this log
file is the only place startup errors are visible. Common causes:
- Port 8765 already in use by something else → change `server_port` in
  `%PROGRAMDATA%\SialviPrint\config.json` and restart.
- Database file locked by another running instance → check Task
  Manager for a lingering `SialviPrintAdmin.exe` process.

## User PC connection issues

**"Admin server is unavailable"**
1. Confirm the Admin PC's app is actually running (check its Dashboard
   screen is visible).
2. Confirm both PCs are on the same LAN/subnet.
3. Confirm the IP entered in the User app's setup matches the Admin
   PC's actual current LAN IP (`ipconfig` on the Admin PC) — this
   changes if using DHCP without a reservation. Consider setting a
   static IP or DHCP reservation for the Admin PC.
4. Confirm Windows Firewall on the Admin PC allows the app (see above).

**"Unauthorized: invalid or missing LAN token"**
The connection code entered on the User PC doesn't match the Admin
PC's token. Open `%PROGRAMDATA%\SialviPrint\config.json` on the Admin
PC, copy the exact `lan_shared_token` value, and re-enter it in the
User app's setup dialog.

## Printing issues

**Selected printer shows as unsupported for a print profile**
The printer's real Windows driver was queried and genuinely does not
support the requested paper size or color mode — this is intentional
(requirement 6: never silently send an incorrect print job). Either
choose a different printer/profile combination or check the printer's
driver settings in Windows.

**Files fail to process with "LibreOffice is required..."**
DOCX/XLSX/PPT/TXT/RTF/CSV page counting and printing needs LibreOffice
installed on the Admin PC. Install it from
https://www.libreoffice.org/download/download/ with default settings,
then restart the Admin app.

**PSD / AI / AutoCAD files fail during page counting**
These formats are accepted by the User picker, but the Admin PC must have the
required converter available: ImageMagick for `.psd`/`.ai`, `ezdxf` for `.dxf`
(which is bundled into the Admin build), and ODA File Converter for `.dwg`.
See `DESIGN_FILE_SETUP.md`. The error shown by the User app is the real
conversion dependency/error message; SialviPrint does not guess a page count.

## Data / recovery

**Admin PC crashed or lost power mid-print**
On restart, any job that was mid-print gets flagged `PRINT_ERROR`
rather than silently retried (prevents double-printing). Check the
Admin PC's Print Queue screen for `PRINT_ERROR` entries and decide
manually whether to re-print.

**Need to check what happened with a specific order**
Every payment verification, price change, and weekly reset is recorded
in the `audit_log` table inside
`%PROGRAMDATA%\SialviPrint\data\printing_system.db` (a standard SQLite
file — DB Browser for SQLite can open it for inspection).

## Uninstalling

Uninstalling either app does **not** delete
`%PROGRAMDATA%\SialviPrint\` (Admin) or `%LOCALAPPDATA%\SialviPrintUser\`
(User) — order history, earnings records, and station configuration
are preserved intentionally. Delete these folders manually only if a
genuinely full wipe is wanted.


## Glossy / photo prints: the Windows "Printing Preferences" window on User PCs
When a customer picks a print type that needs printer settings (Glossy, Matte, Luster, or any type with
"Print Properties" ticked in Admin > Pricing), the User app opens the printer's own Windows
**Printing Preferences** window (paper size, quality, paper type). Their choices are saved with the order and
applied automatically on the Admin PC after payment is verified - no window appears on the Admin PC.

**One-time setup on each User PC:** install the shop printer(s) so Windows knows them:
Settings > Bluetooth & devices > Printers & scanners > Add device > "The printer that I want isn't listed" >
"Select a shared printer by name" > `\\ADMIN-PC-NAME\EPSON WF-7710 Series` (the Admin PC must share the printer).
If a printer is not installed on the User PC, the app shows its built-in "Print Properties" popup instead
(paper size / quality / paper type read from the Admin PC's driver).

## Every job prints on the printer the customer selected
Jobs are addressed to the chosen printer BY NAME (PDF, images and - via LibreOffice - Word/Excel/PowerPoint).
The Windows default printer is no longer used. Install LibreOffice on the Admin PC so Office files use this route.
The Admin log line  `Submitting '<file>' to printer '<name>'`  shows exactly which printer each job was sent to.

---

## "An unexpected error occurred on the Admin server ... [OperationalError: no such column: X]"

**What it means.** The database file on that PC was created by an older version of
SialviPrint, so one of its tables is missing a column (or has it under an old name)
that the current code asks for. It is not a printer or network problem, and no
customer data is wrong - the app simply cannot read that table.

**The fix (from v3.2 on) is automatic.** Every time the Admin app starts it compares
the database with the current schema and repairs any difference:

* a missing column is added in place, or
* the table is rebuilt from the schema and the data from the columns both versions
  share is copied across.

The old table is always kept as `<name>__backup_1` - nothing is deleted. Every repair
is written to `C:\ProgramData\SialviPrint\logs\admin_server.log`, e.g.

```
[WARNING] db_repair: Database repaired: added column 'print_profiles.ask_properties';
          rebuilt table 'price_tiers' (was missing: min_qty, max_qty, price_per_page)
```

**So: install the new build and start the Admin app once.** The Pricing screen should
open normally after that.

**If a table had to be rebuilt**, open Admin > Pricing and check the **Packages** of
each print type. When the old table's columns could not be matched safely, the packages
are left empty rather than guessed (a guessed price would be a wrong price), and the
original rows stay in the backup table. Re-enter them with the Packages button.

### Checking the database by hand

From the `PrintingSystem` folder with the venv active:

```
python -m admin.tools.db_doctor          (report only)
python -m admin.tools.db_doctor --fix    (report, then repair)
```

It prints every table with its columns and lists anything that does not match.

### Still failing?

The full technical cause is now shown in brackets in the Admin screen banner (the
banner wraps onto several lines, so the whole message is readable) and logged. Send
the last 60 lines of `C:\ProgramData\SialviPrint\logs\admin_server.log`.


## User PC cannot connect over Ethernet-only LAN (no internet / no Wi-Fi)

See `OFFLINE_LAN_SETUP.md`. Short version: run `Fix_LAN_Firewall.bat` as Administrator on the Admin PC (and User PCs),
make sure all PCs share the same first three IP numbers, and enter the Admin IP (not a PC name).

## User app asks for a password when closing

That is the exit password (set the first time the User app starts). Minimize works without it. If forgotten: end `SialviPrintUser.exe`
in Task Manager, delete the lines `exit_password_hash` and `exit_password_salt` in `%LOCALAPPDATA%\SialviPrintUser\config.json`,
start the app and create a new password.
