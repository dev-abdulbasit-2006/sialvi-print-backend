# Build Requirements

These are needed only on the **developer/build machine** that produces
`AdminSetup.exe` and `UserSetup.exe`. They are NOT needed on the shop's
Admin PC or User PCs — the whole point of Phase 4 is that those
machines need nothing but the installer itself.

## Required on the build machine

1. **Windows 10/11 (64-bit)** — pywin32 and the printer integration
   can only be built on Windows.
2. **Python 3.11 or 3.12 (64-bit)**, added to PATH.
3. **Inno Setup 6** — https://jrsoftware.org/isinfo.php
   During install, check "Add to PATH" or add
   `C:\Program Files (x86)\Inno Setup 6` to PATH manually so `iscc`
   is runnable from a terminal.
4. **Git** (optional, only if pulling the source from a repo).

## Optional, but required for full feature coverage on the deployed Admin PC

These aren't needed to *build* the installer, but the **Admin PC**
needs them at runtime for full functionality, and the installer does
not currently bundle them (see "Known gaps" below):

- **LibreOffice** (any recent version, headless mode used) — required
  for accurate page counting and printing of DOCX/XLS/XLSX/PPT/TXT/RTF/CSV.
  Download: https://www.libreoffice.org/download/download/
  Install with default options; the build's `page_counter.py` finds
  `soffice.exe` automatically via PATH.
- **ImageMagick** with its PostScript/Ghostscript delegate — required on the
  Admin PC for `.psd` and `.ai` conversion. See
  `documentation/DESIGN_FILE_SETUP.md`.
- **ODA File Converter** — required on the Admin PC for `.dwg` conversion;
  `.dxf` is rendered by the bundled `ezdxf` renderer. See
  `documentation/DESIGN_FILE_SETUP.md` and check ODA's licensing terms for
  commercial deployment.


## Runtime requirement for User PCs

The User installer now includes the Windows printer integration itself. The User PC does **not** need Python or pip installed separately.

The packaged User application contains:

- pywin32 for Windows printer enumeration and spooler access
- Pillow for PDF/image rendering onto the selected printer
- PyMuPDF for PDF preview/rendering

The User PC must still have the actual printer driver installed in Windows. SialviPrint cannot print to a printer that Windows does not know about.

The User installer also offers a Windows Firewall rule for TCP **8767**, which is required so the Admin PC can deliver an authenticated print job back to that User PC.

## Build steps

This zip (v3.5 onward) extracts flat: `admin\`, `user\`, `shared\` etc. sit directly inside
the extracted folder - there is **no `PrintingSystem\` subfolder** to `cd` into (earlier
zips had one; this one doesn't). From wherever you extracted it:

```
cd C:\Users\devab\Downloads\<your extracted folder>
py -3.12 -m venv .venv
.venv\Scripts\activate
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Inno Setup 6"
installer\build_all.bat
```

`build_all.bat` itself already resolves the project root dynamically
(`cd /d "%~dp0.."`, i.e. "the folder above `installer\`"), so it works correctly
regardless of what the extracted folder is named - the only thing to get right by hand
is the first `cd` to get into that folder in the first place, and where `.venv` lives
(create it directly inside the extracted folder, as above, not one level above it).

This installs both apps' Python dependencies, verifies pywin32 is
actually functional (not just installed), builds both executables with
PyInstaller, then compiles both Inno Setup installers. Output:

```
installer\Output\AdminSetup.exe
installer\Output\UserSetup.exe
```

## Known gaps to close before shipping to a real shop

1. **LibreOffice is a separate, undisclosed-to-the-installer
   dependency.** Either (a) document it as a manual pre-install step
   for the Admin PC (simplest), or (b) bundle LibreOffice's portable
   distribution into the Admin installer (larger download, fully
   self-contained). Recommend (a) for v1, revisit if it causes support
   issues.
2. **Adobe/AutoCAD runtime converters are external dependencies.**
   ImageMagick is used for PSD/AI, `ezdxf` renders DXF, and ODA File
   Converter is used only for DWG -> DXF. They must be present/configured
   on the Admin PC for those formats to process.
3. **Code signing** is not configured. An unsigned installer will
   trigger a Windows SmartScreen warning ("Windows protected your PC").
   This is cosmetic (the "More info → Run anyway" flow still installs
   correctly) but a code-signing certificate removes it entirely if the
   client wants a fully polished install experience.
4. **The two icons in `installer/assets/` are placeholders** generated
   programmatically (a plain letter on a color background) — swap them
   for real branding before shipping.
