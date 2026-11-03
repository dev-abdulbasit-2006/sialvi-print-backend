# SIALVIPRINT — MASTER HANDOFF PROMPT
(Paste this whole file into a NEW Claude chat and attach the latest project zip —
 currently **SialviPrint_Final_v3_8.zip** (or whatever number is current when you
 read this — check section 0). Then say: "Read this fully, then continue from
 'CURRENT STAGE'.")

---------------------------------------------------------------------
## 0. WHO / WHAT
I (the user, "bro") am building a commercial Windows print-shop management system for a
client: **SialviPrint Stationers** — tagline "High-quality online printing you can trust."
Customers use a self-service **User (kiosk) app**, pay, and the job prints automatically on
the shop's **Admin PC** after the shop owner verifies the payment. I am not a professional
programmer — practical, step-by-step instructions in simple English, exact Windows
commands. Do not unnecessarily redesign working parts of the system; only change what's
actually asked.

## 1. ZIP LAYOUT — READ THIS BEFORE GIVING ANY BUILD COMMAND
**As of v3.5, the zip extracts FLAT: `admin\`, `user\`, `shared\`, `assets\`, `installer\`,
`documentation\` sit directly inside the extracted folder.** Earlier zips (v3 through v3.4)
had everything nested one level deeper inside a `PrintingSystem\` subfolder — that subfolder
**no longer exists**. If you ever give a `cd ...\PrintingSystem` command against a v3.5+
zip, it will fail with "path not found." `installer\build_all.bat` itself resolves the
project root dynamically (`cd /d "%~dp0.."`) so it doesn't care what the extracted folder is
named — only the very first `cd` into that folder needs to be right.

## 2. ARCHITECTURE
Two Windows desktop apps (Python 3.12, PySide6 UI), talking over the local network:
- **Admin app**: PySide6 UI + embedded Flask API (TCP **8765**), SQLite in
  `C:\ProgramData\SialviPrint\`, pywin32 printing, PyMuPDF page counting, LibreOffice for
  Office-file conversion. Sidebar: Dashboard, Pending Payments, Pricing, Printers, Print
  Queue, Connect User PCs.
- **User app** (kiosk): add files -> preview -> select printer + print type + copies + page
  range + orientation -> Print Properties if required -> Calculate Price -> PAY -> wait ->
  queued -> Admin auto-prints. Server is the only source of truth for pricing — the User app
  never computes a final price itself.
- **Network** (added v3.5): two coexisting connection methods, both must keep working —
  (a) same-router automatic discovery via UDP broadcast on **8766** (`shared/network_discovery.py`:
  `discover_admins()` on the User side, `AdminDiscoveryResponder` started by
  `admin/run_admin.py` on the Admin side, no secret broadcast over UDP), and (b) manual
  Admin IP + port 8765 + connection code, entered in the User app's setup dialog. The Admin
  Flask server binds `0.0.0.0` (`admin/config/config.py: server_host`), not just localhost,
  so it's reachable from other machines on the LAN/router.
- The **printer does not need to be USB-attached to the Admin PC** — a Windows-installed
  network printer (Wi-Fi/Ethernet/print-server) works exactly the same way, since printing
  always goes through the Windows printer driver by name, never a raw port.
- Installers: PyInstaller (onedir) + Inno Setup -> `AdminSetup.exe`, `UserSetup.exe`.

Folders: `admin/` (server/, database/, printer/, queue/, ui/, config/, tools/), `user/`
(ui/, network/, config/), `shared/` (theme.py, resources.py, winprint.py,
network_discovery.py), `assets/`, `installer/`, `documentation/`.

## 3. HOW TO BUILD (Windows only — a chat sandbox is Linux, cannot produce .exe)
```
cd C:\Users\devab\Downloads\<your extracted folder>
py -3.12 -m venv .venv
.venv\Scripts\activate
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Inno Setup 6"
installer\build_all.bat
```
- MUST be Python **3.12** (3.14 fails — pinned wheels for PySide6/PyMuPDF/pywin32). Output:
  `installer\Output\AdminSetup.exe`, `UserSetup.exe`.
- Never double-click the `.iss` files directly — always `installer\build_all.bat`.
- After installing a new build, start the Admin app **once** before testing anything else —
  the database repair and format-widening fixes run automatically at Admin startup, not at
  install time.
- Two separate `requirements.txt` now: `admin\requirements.txt` and `user\requirements.txt`
  (previously one combined file) — `build_all.bat` installs both, nothing to do by hand.
- On failure, ask for the last 30 lines of output.

## 4. BRANDING (done, stable)
Navy `#05203B`, orange `#F4831B`, all in `shared/theme.py`
(`QPushButton[class="primary"]`, never `.primary`). Admin: branded sidebar. User: branded
header, two-column layout, preview panel.

## 5. CHANGE HISTORY (chronological)

**Round 1**: fixed build; UI redesign; User-side preview (images+PDF); "Connect User PCs"
screen (manual IP/port/code).

**Round 2**: Pending Payments ✔/✖ buttons, no reject-reason popup; Pricing "Add Print Type"
+ Packages (quantity price tiers); seeded price sheet (B&W 1-30=Rs10, 31-100=8, 101-150=7,
151-300=6, 301+=5; Color=Rs20); Printers screen manual online/offline status removed (an
auto-disable-if-Windows-no-longer-reports-it mechanism still exists, separate and
intentional); Glossy/Matte/Luster print-properties window before Calculate/PAY, never a
dialog on the Admin PC after verify.

**v3 — print properties, solved**: wanted a Windows "Print Pictures"-wizard-style window,
but that wizard's own Print button fires immediately with no settings returned to the app —
can't be used. Built instead: open the printer's own "Printing Preferences" window via
`DocumentPropertiesW(DM_IN_PROMPT|DM_OUT_BUFFER)` on the User PC, capture the DEVMODE, send
it base64 with the order; Admin prints silently with that exact DEVMODE, no dialog on the
Admin side. Needs the printer installed on the User PC (shared `\\ADMINPC\EPSON...`);
fallback = in-app popup reading options from the Admin's driver. Code: `shared/winprint.py`,
`user/ui/main_window.py::_open_print_properties`, `user/ui/print_properties.py`.

**v3 — printer routing, fixed**: old code switched the Windows DEFAULT printer + async
`ShellExecute "print"` + restored the default immediately -> job raced the restore and came
out of whatever was default at that instant, not the chosen printer. Fixed:
`printer_manager.submit_file` addresses every job BY NAME, never via the Windows default —
PDF/image (+properties) -> silent GDI engine; Office -> LibreOffice-to-PDF -> silent engine;
no LibreOffice/unsupported type -> `ShellExecute "printto"` with the printer name explicit;
true last resort keeps the default switched only until the job provably reaches THAT
printer's queue (tracked via `EnumJobs` diffing, not a blind restore).

**v3.1**: database self-heal for a missing `price_tiers` TABLE only (not yet wrong columns).

**v3.2 — database self-repair**: `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so an old
wrong-shaped table never auto-corrected -> "no such column" errors. `admin/database/repair.py`
now runs at every Admin start (and again if a query trips at runtime), diffs the live DB
against schema.sql, ADDs safe columns, REBUILDS wrong-shaped tables copying every shared
column, old table kept as `<name>__backup_N` (never deleted, never guessed-at). Also
`admin/tools/db_doctor.py` (`python -m admin.tools.db_doctor [--fix]`), banners word-wrap,
error detail 160->300 chars. **Verified for real** from an actual `admin_server.log` — the
repair fired and the error never recurred after.

**v3.3 — every print type accepts every file type**: B&W/Laser/Voucher had a narrower
`supported_formats_json` than Color (no images) — inconsistency, not requested. All 13
built-in types now share one full list (pdf, docx, doc, xlsx, xls, pptx, ppt, txt, rtf, csv,
jpg, jpeg, png, bmp, tiff); one-time startup migration widens an already-installed DB too;
custom (`CUSTOM_%`) types untouched.

**v3.4 (superseded by v3.6's rewrite of the same files — see below)**: first attempt at
clearer `CreateDC` failure text (was a bare, unhelpful "winspool").

**Extensive sandbox test pass on v3.3** (55 automated tests, all passed — see section 6):
jpeg fix, price-tier math at every boundary, printer config, full order lifecycle, database
repair, print-queue retry/permanent-failure/crash-recovery, auth. All at the code level;
real Windows/hardware still needed its own pass.

**v3.5 — LAN + Router Both, and real local DOCX/PPTX preview** (built between sessions,
verified this session by diffing every file against the tested v3.3 baseline):
- **Network discovery, genuinely new and correctly wired**: `shared/network_discovery.py`
  (UDP 8766 broadcast/responder), started by `admin/run_admin.py`, called from
  `user/ui/setup_dialog.py`. Manual IP entry preserved in the same dialog. Confirmed:
  `server_host = "0.0.0.0"` (not localhost-only).
- **DOCX/PPTX preview, genuinely new and well-built** (in `user/ui/preview.py`, runs
  entirely on the User/kiosk PC, no Admin round-trip): tries LibreOffice first if installed
  on that PC (exact PDF-quality preview, real page count), falls back to pure-Python
  `python-docx`/`python-pptx` (paragraph/table HTML for Word, slide title+bullets mock-up
  for PowerPoint) if LibreOffice isn't there — off the GUI thread via `_OfficePreviewWorker`
  (QThread), with stale-request invalidation and temp-file cleanup. Legacy .doc/.ppt only
  get the LibreOffice path (python-docx/pptx can't read the old binary format) — a clear
  message is shown, not a guess, if neither is available. XLSX and PSD/AI still correctly
  show "no preview available."
- **IMPORTANT — verified as claimed vs. NOT verified in the code, this session**: the same
  v3.5 zip's own master prompt claimed extensive printing-pipeline robustness work (exact
  printer validation, avoiding default-printer switching, "more robust GDI printing,"
  keeping GDI/DIB objects alive, spooler job tracking, retry handling, better logging,
  LibreOffice detection without PATH). **Diffing confirmed none of that was actually in the
  code** — `printer_manager.py`, `silent_print.py`, `queue_manager.py` were byte-identical
  to the v3.3 baseline, and LibreOffice detection was still PATH-only. This was flagged
  honestly instead of taken on faith, and is exactly what v3.6 (below) now actually
  implements for real.

**v3.6 — THIS SESSION — the printing-reliability work v3.5 only claimed to have done, now
actually implemented, plus the build-path/documentation fix**:
- `admin/printer/silent_print.py`:
  - **Real GDI/DIB lifetime fix**: `ImageWin.Dib(img)` used to be a bare temporary, drawn
    and immediately eligible for garbage collection — a known real pywin32 footgun, since
    GDI/the printer driver can process the draw asynchronously relative to Python's `draw()`
    call returning; if the Dib's handles get torn down mid-spool the page can come out
    blank/corrupted or silently vanish. Now held in an explicit local variable through the
    matching `EndPage()`.
  - **Every GDI/spooler call's return value is now checked** (`StartDoc`, `StartPage`,
    `EndPage`, `EndDoc`) via a new `_check()` helper — these Win32 calls can return ≤0 on
    failure WITHOUT pywin32 raising a Python exception in every case; previously that meant
    a job could be marked PRINTED when nothing actually reached the printer. Now any ≤0
    result raises `SilentPrintError` immediately, with the real Windows error code/text
    attached via `GetLastError`/`FormatError`.
  - **Real spooler job ID captured and logged**: `StartDoc`'s return value IS the Windows
    spooler job number: `logger.info("Spooler job #%s opened on '%s' for '%s'", ...)` and a
    matching "completed successfully" / "aborted" log line — so a failure can be cross-
    checked against Windows' own "See what's printing" queue on that exact job number, not
    just our own log.
  - **`CreateDC` failures now give a real diagnosis** instead of a bare, often-unhelpful
    exception string (pywin32/the driver sometimes never calls `SetLastError`, so the raw
    exception text can be as useless as literally "winspool") — via `_describe_createdc_failure()`.
- `shared/winprint.py` (the customer's-own-devmode path, used for Glossy/Matte/Luster):
  same `CreateDCW` failure enrichment, plus a logged warning (previously silent) if
  `OpenPrinter` itself fails before even getting to `CreateDC`.
- `admin/printer/printer_manager.py`: new `_validate_printer_exists()`, called at the very
  top of `submit_file()` — fails fast with a clear "not currently installed/visible" message
  (naming which printers Windows DOES currently report) if the target printer name isn't in
  Windows' live list, BEFORE any conversion/rendering time is spent. Skips silently if
  enumeration itself returns nothing (avoids false positives, e.g. on a platform/timing
  hiccup) rather than ever blocking a legitimate print.
- `admin/server/page_counter.py`: `_find_libreoffice()` now falls back to the two default
  Windows install paths (`C:\Program Files\LibreOffice\program\soffice.exe` and the
  `(x86)` variant) when `soffice`/`libreoffice` isn't on PATH — many real shop PCs never get
  LibreOffice added to PATH even though it's genuinely installed. Benefits both page-counting
  AND `printer_manager._convert_to_pdf` (same shared `LIBREOFFICE_BIN` constant).
- `documentation/BUILD_REQUIREMENTS.md`: corrected to the flat (no `PrintingSystem\`) layout
  build steps (see section 1 above) — it was still telling people to `cd PrintingSystem`,
  which doesn't exist in this zip.
- **Verified in the sandbox this session**: full 55-test suite re-run against every change —
  0 regressions. New direct unit test for `_validate_printer_exists` (unknown printer
  correctly rejected with the live list named; known printer accepted; empty enumeration
  doesn't false-positive-block). Everything compiles clean.
- **NOT verified**: real Windows, real GDI failure paths, real spooler behavior, a real
  Epson L3150 actually receiving a job. The GDI-return-value checks and DIB-lifetime fix in
  particular are real, well-documented Windows/pywin32 failure patterns being defended
  against pre-emptively — sandbox mocks cannot prove they fix an intermittent real-world
  failure, only that they don't break anything that was working. **This is exactly why the
  physical test in section 8 is still priority 1.**

**v3.7 — first REAL-Windows failure found from a real admin_server.log, fixed**:
- Symptom: every print job failed 3x with "Printing failed for: <file>. Please see the shop
  attendant." Log showed `FileNotFoundError: Could not find module 'winspool'` ->
  `AttributeError: winspool`, raised from `shared/winprint.py::_winspool()` via
  `silent_print._build_devmode -> dc_strings`. **No job had ever reached StartDoc.**
- Root cause: `ctypes.windll.winspool` loads `winspool.dll`, which does not exist. The real
  file is `winspool.drv`. Fix: `shared/winprint.py::_load_winspool()` uses
  `ctypes.WinDLL("winspool.drv")` (full System32 path fallback, result cached). Also fixes
  the User app's `list_local_printers`/Printing Preferences path (same function).
- Second latent bug fixed pre-emptively: v3.6's `silent_print._check()` treated `None` as
  failure, but pywin32 `StartPage/EndPage/EndDoc` return None on success (they raise on
  failure) -> would have failed every job right after the DLL fix. Now only int <= 0 fails.
- Sandbox-verified: loader logic (mocked WinDLL: normal, fallback, total failure), `_check`.
  NOT verified: real Windows / real Epson. Section 8 physical test order still applies.
- Log also showed jobs sent to 'OneNote for Windows 10' / Fax / XPS / Print-to-PDF virtual
  printers, not only the Epson — operator/User selected them; disable those in Printers.

**v3.8 — second REAL-Windows failure found from a real admin_server.log, fixed**:
- Symptom, after v3.7 fixed the winspool.drv bug: PDFs/images now printed fine, but every
  DOCX order failed 3x with `Print job ... failed: Silent printing does not support '.docx'
  files.`, log line just before it showing `Submitting '<file>.docx' to printer '<n>'`
  straight away (no LibreOffice conversion attempt logged at all).
- Root cause: `printer_manager.py::submit_file()` had
  `if print_settings or suffix in IMAGE_EXTS or suffix in PDF_EXTS: return self._print_silent(...)`
  BEFORE the `if suffix in OFFICE_EXTS: convert to PDF first` branch below it. `print_settings`
  is a non-empty dict (paper size/quality/paper type) on almost every order, not only
  Glossy/Matte/Luster ones - so its mere truthiness sent ANY Office file with settings
  attached straight into the silent-print engine on the RAW .docx file, skipping PDF
  conversion entirely. `silent_print.print_file` correctly refuses raw Office files (it only
  draws images/PDF pages), so this had likely never worked for DOCX/XLSX/PPTX whenever an
  order carried print settings - it looked fine earlier only when test files happened to be
  plain PDFs/images.
- Fix: that early branch now checks specifically for `print_settings.get("devmode_b64")`
  (the actual signal that this is the customer's-own-Windows-DEVMODE path from Printing
  Preferences) instead of the settings dict's truthiness. A DOCX/XLSX/PPTX order without a
  DEVMODE now correctly falls through to the OFFICE_EXTS branch, converts to PDF via
  LibreOffice, then prints that PDF silently - matching the documented design.
- Sandbox-verified: routing-decision truth table (8 cases: DOCX+settings, DOCX+no settings,
  PDF+settings, PNG+devmode, DOCX+devmode, PPTX+settings, etc.) all route correctly. Real
  LibreOffice in the sandbox (the exact `soffice --headless --convert-to pdf` command this
  code runs) confirmed to produce a valid PDF from a real python-docx-generated .docx.
  NOT verified: real Windows / real Epson / the full order-to-paper path end to end.

**v3.9 — Adobe Photoshop/Illustrator + AutoCAD file support added**:
- Root cause in v3.8: the upload layer already listed `.psd`/`.ai`, but `admin/server/page_counter.py`
  deliberately rejected them because no conversion pipeline existed. `.dwg`/`.dxf` were not in the
  upload allow-list at all. The User `QFileDialog` also had no explicit supported-format filter.
- Added `admin/server/design_file_converter.py` as the single conversion path for these design files:
  `.psd`/`.ai` -> ImageMagick PDF; `.dxf` -> ezdxf PDF; `.dwg` -> ODA File Converter DXF -> ezdxf PDF.
- Page counting now counts the converted PDF, and printing uses that same converted PDF through the existing
  silent-print engine. This prevents the page-count path and print path from disagreeing.
- User file picker now explicitly includes PSD, AI, DWG and DXF. Design files show a clear local preview
  placeholder rather than a misleading unsupported-file state.
- Existing built-in print profiles are widened by a one-time `defaults_design_formats_v2` migration;
  custom `CUSTOM_*` profile choices are not overwritten. New custom profiles inherit the full allowed list.
- Admin requirements add `ezdxf` and `matplotlib` for DXF rendering. ImageMagick and ODA File Converter
  remain external Admin-PC runtime dependencies; see `documentation/DESIGN_FILE_SETUP.md`.
- **Sandbox-verified this session**: real ImageMagick converted a real PSD and AI/EPS-compatible sample to PDF;
  real ezdxf rendered a real DXF to PDF; all three produced valid 1-page PDFs and were counted by the real
  PyMuPDF installed in the sandbox. Database migration was also run against a fresh temporary SQLite DB and
  confirmed `psd`, `ai`, `dwg`, `dxf` are present in built-in profile support. Full Windows build and physical
  printer output are NOT verified in this Linux sandbox.

**v3.9.1 — Admin Pending Payments now shows the selected printer**:
- The Pending Payments API now joins each pending order to its selected printer and returns `printer_name` using the printer's configured display name.
- The Admin Pending Payments table now has a dedicated **Printer** column between Station and Pages, so the operator can immediately see where the verified job will be sent (for example, `Epson L3150`).
- Existing payment verification, pricing, queueing, printer routing, and printing behavior are unchanged. This is a display/data-read change only.
- If an old/historical pending order has no printer reference, the UI shows `Not assigned` rather than crashing.
- **Sandbox-verified**: both modified Python files compile cleanly. Real Windows UI rendering and the live printer-name display still require the Windows build/test.

## 6. HOW TO TEST THIS PROJECT IN THE SANDBOX (so a new Claude doesn't rediscover this)
The sandbox (Linux) happens to have **LibreOffice, python-docx, python-pptx, openpyxl**
already installed — use them for REAL conversion tests, not mocks, wherever possible. The
one thing genuinely unavailable is real PyMuPDF/`fitz` (no network access to pip install
it) — a small fake `fitz` stands in, counting real `/Type /Page` markers in actual PDF bytes
(correct on real LibreOffice output, not just hand-crafted fakes). Known sandbox-only
quirks, not bugs:
- `GET /api/printers` triggers a live-printer sync; Linux has no real printers, so it
  auto-disables any test-inserted printer row (re-enable manually after, for later tests
  that need it — this is the auto-disable-when-not-in-Windows'-live-list feature working
  correctly, not a bug).
- Never reuse one process's cached `get_conn()` across a `fresh_db()` (delete+recreate the
  file) — SQLite connections stay bound to the deleted file's descriptor. Isolate any
  "broken/old database" or true-concurrency test in its own subprocess.
- Queue-manager tests need the exact join `_process_next_batch()` uses (id, printer_id,
  attempts, status, stored_path, system_name) — a bare `SELECT * FROM print_jobs` is
  missing columns the function reads and will KeyError.
- A fake PDF's page count must be encoded in its own BYTES (`/Type /Page` markers), never
  the filename — uploads get renamed to an internal UUID on disk immediately.
- `printer_manager._validate_printer_exists` can be tested directly by monkeypatching
  `list_printers` — the print-submission mocks used for queue tests bypass it entirely since
  they replace `submit_file` wholesale.

## 7. CURRENT STAGE — Office-file (docx/doc/xlsx/pptx/ppt) PREVIEW remains DONE (v3.5, see
section 5), Adobe/AutoCAD upload + conversion/printing support is implemented in v3.9, and the
Admin Pending Payments screen now shows the selected printer in v3.9.1.
No further preview work is currently pending unless a new gap is reported. **What remains open**:
code signing; bundling LibreOffice/ImageMagick/other external converters into the Admin installer;
final branding polish on the two installer icons (currently placeholders); and real Windows/physical
printer validation of the new design-file paths.

## 8. CURRENT HIGH-PRIORITY WORK — PHYSICAL PRINT RELIABILITY
This is the top priority, above everything else. Test on REAL WINDOWS, in this exact order,
and do not skip ahead:

1. **Windows itself, no SialviPrint involved**: on the Admin PC, print a Windows Test Page
   to the Epson L3150 (network-connected, no USB needed). If this fails, fix
   Windows/network-printer configuration first — nothing in this app can work around a
   printer Windows itself can't reach.
2. **One PDF, one printer, one copy, normal type (not Glossy/DOCX yet)**: User selects
   Epson L3150 -> upload one PDF -> Calculate -> pay -> Admin verifies -> confirm it reaches
   the L3150 specifically (not the WF-7710 or any other installed printer) and physical paper
   comes out. Check `C:\ProgramData\SialviPrint\logs` for the new `Spooler job #<n> opened /
   completed successfully` log lines from v3.6 — if it says "completed successfully" but no
   paper came out, that's a genuinely new, different symptom worth its own investigation
   (Windows spooler / driver / physical paper-out, not a code issue at this app's level).
3. **DOCX** (only after step 2 works): same flow with a Word file.
4. **PSD / AI**: with ImageMagick installed on the Admin PC, upload each and confirm page count + print.
5. **DXF**: upload, confirm page count + print.
6. **DWG**: with ODA File Converter configured, upload, confirm DWG->DXF conversion + page count + print.
7. **Native Printing Preferences for Glossy** (only after step 2 works): confirm the real
   Windows dialog appears on the User PC (needs the printer installed there too — shared
   `\\ADMINPC\...`), settings honoured on the actual printout, no dialog on the Admin PC.

**Never** conclude a physical test passed without actually running it on Windows with the
real printer — a green sandbox test result is not equivalent to paper coming out.

## 9. LOGGING / HOW TO DEBUG A PRINT FAILURE
Don't guess — read `C:\ProgramData\SialviPrint\logs\admin_server.log`. As of v3.6 it
includes: selected printer name, real Windows spooler job number, conversion result, GDI
step-by-step failures with real Windows error codes/text, retry attempt count, final state.
- **Job stays QUEUED**: check the queue worker is even running (`queue_manager.start()`
  logged at Admin startup) and that `_validate_printer_exists` isn't silently rejecting it
  every attempt (would show as repeated FAILED->QUEUED cycling, 3x, then FAILED with that
  message).
- **Job becomes FAILED**: read the exact `error_message` column / log line — v3.6 makes this
  genuinely diagnostic now (real Windows error code+text), not a guess-generator.
- **Job says PRINTED but no paper**: check Windows' own print queue for that exact spooler
  job number (now logged) — this is now a Windows/driver/physical-printer question, not
  something to keep changing this app's code for blindly.
- **Wrong printer prints**: get the exact `Submitting '<file>' to printer '<name>'` log line
  and the exact Windows printer name it named — the routing fix (section 5, v3) addresses
  this at the code level; if it recurs on v3.6 with real logs, that's a genuine regression
  worth fresh investigation, not an assumption that the old bug is "just back."

## 10. WHAT MUST NOT HAPPEN (unchanged from earlier rounds — still binding)
Never: change the Windows default printer as a routing mechanism; restore the default
printer before a job has verifiably reached the intended queue; show the print-properties
dialog on the Admin PC; make the User app the source of truth for pricing; destroy the
SQLite database to fix a schema problem (repair, never reset); remove manual LAN connection
or same-router discovery; require the Admin PC to have a USB-attached printer; claim a
physical-printer test passed without actually running it on Windows; make unrelated
architecture changes; remove existing requirements without explicit approval.

## 11. HOW I WANT YOU TO WORK
- Inspect the existing implementation and identify the actual root cause before changing
  anything — don't blindly rewrite.
- Change the smallest necessary part; preserve everything else exactly as-is.
- Be honest about sandbox-tested vs. Windows-tested vs. real-printer-tested — always say
  which, explicitly, for every claim.
- Prefer real sandbox tools over mocks when they happen to be available (LibreOffice,
  python-docx/pptx, openpyxl all are — section 6) — only fake what genuinely can't be
  installed (real PyMuPDF/fitz, real pywin32/Windows APIs).
- Test everything possible in the sandbox, then give a complete clean zip (no build/, dist/,
  __pycache__, no leftover `*.db`).
- `str.replace`/`str_replace` on text copied from a view that stripped blank lines FAILS
  SILENTLY — grep to verify edits landed.
- ctypes `c_wchar` is 4 bytes on Linux, 2 on Windows — use explicit uint16 arrays for
  Windows structs.
- Always end with: what changed, what is verified (and how), what to test on Windows, in
  what order.
- Refresh this prompt file whenever the stage changes, in case the chat dies again — that's
  exactly why it keeps getting rewritten from scratch rather than endlessly appended to.


# ARCHITECTURE OVERRIDE — v3.9.1 USER-PC PRINTER ROUTING

This section supersedes the older printer-routing statements in this handoff.

## Required architecture

The User app owns printer discovery and the final Windows printing operation.

```text
USER PC
  |
  | local Windows printer enumeration
  v
USER APP
  |
  | station_id + exact printer_name
  v
ADMIN API / DATABASE
  |
  | payment verification
  | queue
  | Admin-side document conversion
  v
AUTHENTICATED USER PRINT AGENT :8767
  |
  | local Windows spooler
  v
SELECTED PRINTER ON ORIGINAL USER PC
```

### What changed

OLD:
- User calls `/api/printers`.
- Admin enumerates its own Windows printers.
- User receives Admin printer IDs.
- Admin queue prints directly to an Admin Windows printer.

NEW:
- User calls local Windows printer enumeration through `shared.winprint.list_local_printers()`.
- User dropdown contains only printers installed on that User PC.
- User's selected value is the exact local Windows printer name.
- The order stores `station_id`, `printer_name`, `station_host`, and `station_port`.
- Admin no longer validates a new User printer against its own Windows printer list.
- After payment verification, Admin queue sends the prepared document to the originating User PC's authenticated print agent.
- The User print agent verifies the station and exact local printer, then invokes the Windows spooler locally.
- No default-printer switching is used for remote routing.
- Windows printer sharing is NOT required.

## Local printer UI

`user/ui/main_window.py` must use the User machine's local printer list.

`user/network/api_client.py::get_printers()` is now a local discovery function, not an Admin API call.

The printer combo stores the exact Windows printer name as its data value.

Native Printing Preferences must open for that exact local printer.

Built-in Print Properties must also read driver options locally through `shared.silent_print`.

## Remote print agent

`user/network/print_agent.py` is an authenticated HTTP print endpoint running inside the User application on TCP 8767.

A valid print request requires:

- `X-LAN-Token`
- `X-Station-ID`
- `X-Job-ID`
- `X-Printer-Name`
- print options/settings
- a streamed PDF/image document

The agent:
1. authenticates the shared token;
2. verifies the station ID;
3. enumerates local Windows printers;
4. requires the requested printer to exist locally;
5. writes the streamed document to the User temp directory;
6. calls `shared.silent_print.print_file()` against the exact local printer;
7. returns success only after the Windows spool submission path completes;
8. remembers completed job IDs so a lost HTTP response does not normally cause a duplicate retry.

## Document conversion

The Admin remains the conversion authority.

Before remote delivery:
- PDF/images are sent directly.
- Office/text formats are converted to PDF using the existing LibreOffice pipeline.
- PSD/AI/DWG/DXF are converted using the existing design conversion pipeline.

Therefore the User PC does not need LibreOffice, ImageMagick, ODA File Converter, or ezdxf for remote printing.

## Database compatibility

Existing `printers` and `printer_id` fields are retained for historical/legacy orders and existing Admin printer-management UI.

New User-PC orders also keep the existing `printer_id` foreign-key contract by using a disabled internal placeholder printer row:
`__USER_PC_REMOTE_PRINTER__`.

The actual destination is NOT that placeholder. The actual destination is:

- `orders.station_id`
- `orders.station_host`
- `orders.station_port`
- `orders.printer_name`

Queue routing must use those fields for new remote orders.

This avoids destructive changes to the existing `print_jobs.printer_id NOT NULL` schema while preserving historical data.

## Offline behavior

If the originating User PC cannot be reached, the Admin queue must raise a PrinterError and leave the job retryable according to the existing queue logic.

If the selected printer is absent/offline on the User PC, the User agent rejects the job. Never select a different printer.

## Security

Do not expose an unauthenticated print endpoint.

The existing shared LAN token remains the authentication secret.

The station ID is checked separately so a valid User station cannot impersonate another User station in the print-agent route.

## Build/runtime changes

User requirements now include:
- pywin32==306 on Windows
- Pillow>=10.3,<12

User PyInstaller spec must include:
- shared.silent_print
- win32print/win32con/win32gui/win32ui
- Pillow.Image/ImageOps/ImageWin

UserSetup must offer a Windows Firewall rule for TCP 8767.

AdminSetup remains responsible for TCP 8765 and UDP 8766.

## Verification requirements

A complete verification must distinguish:

### SANDBOX VERIFIED
- Python compilation
- import graph
- database migration
- API route registration
- payload construction
- queue routing logic
- print-agent authentication logic
- local printer abstraction on non-Windows where possible

### WINDOWS VERIFIED
- User printer enumeration
- native Printing Preferences
- User print agent starts on port 8767
- Admin reaches the User agent
- Windows printer spooler accepts the job

### REAL PRINTER VERIFIED
- User PC has Printer A
- Admin PC may have Printer B
- User UI shows Printer A, not Printer B
- Payment is verified
- Printer A receives the job
- Printer B does not receive the job
- Repeat with same printer name on two different User PCs
- Repeat with USB, Wi-Fi, and Ethernet/LAN printers

Never claim a real printer test passed unless a real Windows printer actually received the job.

## Files changed by this architecture

Expected core files:
- `shared/silent_print.py`
- `admin/printer/silent_print.py` (compatibility wrapper)
- `admin/printer/printer_manager.py`
- `admin/queue/queue_manager.py`
- `admin/database/schema.sql`
- `admin/database/db.py`
- `admin/server/app.py`
- `admin/server/routes_orders.py`
- `admin/server/routes_payments.py`
- `admin/server/routes_queue.py`
- `admin/server/routes_stations.py`
- `user/network/api_client.py`
- `user/network/print_agent.py`
- `user/run_user.py`
- `user/ui/main_window.py`
- `user/requirements.txt`
- `user/build_user.spec`
- `installer/UserSetup.iss`
- `documentation/NETWORK_SETUP.md`
- `documentation/BUILD_REQUIREMENTS.md`

Do not reintroduce Admin-PC printer enumeration into the User printer picker.

---

## Build script update (Oct 2026): `installer\build_all.bat` v2

Problem fixed: double-clicking `build_all.bat` made the window flash and close ("crash")
because every error did `exit /b` with no `pause`, the file had Unix (LF) line endings and
a non-ASCII dash, and it only looked at whatever `python` was first on PATH.

Now: ASCII + CRLF; the window always stays open (`pause`); it auto-finds Python 3.12/3.11
(`python`, then `py -3.12`, then `py -3.11`); it also looks for Inno Setup in
`%LocalAppData%\Programs`; step 4 no longer calls `EnumPrinters` (could fail if the Print
Spooler is stopped). No application code, specs, or installer scripts were changed.
Keep this file CRLF + ASCII-only if edited again.


## Offline Ethernet-only LAN support (added)

Root cause: installer firewall rules were `profile=private,domain`; an offline wired network is classed Public by Windows.
Fixed with `profile=any` + `remoteip=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16`
(installers + `installer/Fix_LAN_Firewall.bat`). New `shared/netutil.py` (local IPs, `diagnose_admin`, `scan_for_admin`);
`discover_admins` now broadcasts from every adapter. No API/DB/printing changes. Tests: `tests/test_lan_network.py`
(loopback only). Real-Windows acceptance tests are listed in `documentation/OFFLINE_LAN_SETUP.md` and are NOT yet run.

## User-app exit password (added)

The User window now has only Minimize and Close buttons (no Maximize). Close (X, Alt+F4, taskbar > Close) is intercepted in
`MainWindow.closeEvent` and requires the exit password; wrong password/Cancel keeps the app running. Password = salted PBKDF2 hash
in `%LOCALAPPDATA%\SialviPrintUser\config.json` (`exit_password_hash`/`exit_password_salt`), logic in `user/security.py`,
dialogs in `user/ui/exit_dialog.py`, created at first start by `run_user.py`. Windows log-off/shutdown is never blocked
(`commitDataRequest`). 5 wrong tries = 30 s wait. Forgot it? Close `SialviPrintUser.exe` in Task Manager, delete those two keys from
config.json, start the app and create a new one. This is a deterrent, not protection against Task Manager (use standard Windows accounts).

## Feature 1: Owner License Manager (added)
Separate central licensing service `licensing/server` (Flask+SQLite, own DB/owners, Ed25519-signed tokens), owner desktop app `owner/`
(PySide6, tabs Overview/Licenses/Installations/IP Restrictions/Audit), client library `shared/license_client.py` + demo CLI
`python -m licensing.client_demo`. NOT yet integrated into admin/ or user/ (printing unchanged). All rules are in
`licensing/server/service.py` (server-enforced; limit race-safe via BEGIN IMMEDIATE). See `documentation/LICENSING_FEATURE1.md`.
Tests: `tests/test_licensing.py`, `tests/test_owner_ui_smoke.py`. Next features should reuse `service.py`, not bypass it.

## License enforcement in Admin/User (added)
`shared/license_gate.py` (+ `license_ui.py`, `license_embedded.py`) gates `run_admin.py` and `run_user.py` BEFORE any work starts.
Admin Flask `before_request` returns 403 LICENSE_REQUIRED; `queue_manager` pauses (jobs stay QUEUED) and re-queues jobs the User
agent refuses with LICENSE_REQUIRED (no retry consumed); `print_agent` returns 503. `licensing/embed_config.py --check` runs in
`installer/build_all.bat`. Owner tool has an App Builds tab. See `documentation/OWNER_LICENSE_ENFORCEMENT.md`. Do not add licensing
code to pricing/payment/printing modules; only the five gate points are allowed (test_18).


## Owner app UI fixes (v5)
Bug 1 (Create License did nothing): `dlg.Accepted` does not exist on PySide6 instances (AttributeError, invisible in a windowed exe);
always use `QDialog.DialogCode.Accepted` and import QDialog. Bug 2 (unreadable tabs): `shared/theme.py` has no tab/pane/message-box
rules; the Owner app now uses its own `owner/ui/style.py`. Errors are logged to `%LOCALAPPDATA%\SialviOwnerLicenseManager\owner.log`
and shown in a message box. Table rows are identified by a hidden `Qt.UserRole` key, not by visible text. Tests that CLICK the dialogs
and MEASURE rendered contrast: `tests/test_owner_ui_flows.py` (run them on PySide6 6.7.2, the pinned version).
Loopback `http://127.0.0.1` is allowed for the embedded licensing URL (local testing only).


---
# STAGE UPDATE (Nov 2026) — REMOTE COMMANDS; supersedes older "licensing not integrated" notes above
Base zip: `final_version_v5_persistent.zip` + this change. The licensing system is cloud-only (Render + PostgreSQL), HTTPS-only,
and IS integrated: Admin and User apps are gated (`shared/license_gate.py`, `shared/license_ui.py`).
New in this stage (details: `documentation/REMOTE_COMMANDS.md`):
- Owner tab **Remote Commands**; server `/api/v1/poll`, `/api/v1/ack-command`, `/api/owner/commands`; DB migration 2.
- PCs poll every ~30 s (`LicenseClient.poll_cycle`, gate thread `SialviLicensePoll`); a poll answer is only a hint, the signed check-in decides.
- License dialog server calls run in a worker thread (`_run_responsive`); monitor interval 5 s.
Tests: 207 pass on Linux (43 skipped: PySide6 missing, includes 3 new Owner-tab UI tests). NOT verified: PostgreSQL run of migration 2 / new SQL, PySide6 UI, Windows builds, live Render.
Not implemented (needs the owner's decision): read-only mode while suspended (view receipts/logs, block new jobs/payments).
Rule kept: the uploaded `central_licensing_server_implementation.py` / `client_licensing_engine_enforcement.py` / `owner_remote_control_management_ui.py` were intentionally NOT pasted in (they would replace the secure, tested design); do not paste them in later either.
