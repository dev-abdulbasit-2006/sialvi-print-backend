# Clean-PC Installation Test Checklist

Per the original requirement: **this is mandatory, not optional.** A
build that only works on the developer's machine is not considered
done. Run this on a Windows VM (or a spare physical PC) that has never
had Python, VS Code, or any dev tooling installed.

## Setup

- [ ] Fresh Windows 10 or 11 VM/PC
- [ ] Confirm absence of dev tools: `where python` and `where pip`
      should both report "not recognized"
- [ ] No virtual environment, no PyInstaller, nothing dev-related present

## Admin PC test

- [ ] Copy `AdminSetup.exe` to the clean machine (USB drive or network
      share — not via a dev tool)
- [ ] Run it. Confirm:
  - [ ] No error about missing DLLs, missing Python, or missing modules
  - [ ] Firewall prompt/checkbox works, or Windows Firewall dialog
        appears and can be allowed
  - [ ] App launches without a console window flashing errors
  - [ ] Dashboard screen loads and shows zeroed stats (fresh install)
  - [ ] `%PROGRAMDATA%\SialviPrint\` was created with `data\`, `uploads\`,
        `logs\`, `config.json`
  - [ ] Pricing screen shows all 13 print profiles
  - [ ] Printers screen shows the machine's actual installed printers
        (install a test printer via Windows Settings first if the VM
        has none — e.g. "Microsoft Print to PDF")
- [ ] Restart the PC. Confirm the app can be relaunched and the
      dashboard still shows the same data (proves database persistence)
- [ ] Kill the app via Task Manager mid-idle, relaunch — confirm no
      corruption, no "database locked" error

## User PC test (separate clean machine, same LAN)

- [ ] Copy `UserSetup.exe` to a second clean machine
- [ ] Run it. Confirm no missing-dependency errors
- [ ] First-run setup dialog appears; enter the Admin PC's LAN IP and
      the token shown in the Admin PC's `%PROGRAMDATA%\SialviPrint\config.json`
      (a proper "show token" UI screen is a good Phase 2.1 follow-up —
      currently the token must be read from that file manually)
- [ ] "Test Connection" succeeds
- [ ] Select a small test file (e.g. a PDF), confirm it uploads and
      shows a page count
- [ ] Select printer, printing type, copies; click Calculate Price;
      confirm a price appears
- [ ] Click PAY; confirm the payment appears on the Admin PC's
      Pending Payments screen within a few seconds
- [ ] Verify the payment on the Admin PC; confirm the User PC's screen
      updates to "printing" and then to a printed confirmation
      (use "Microsoft Print to PDF" as the target printer if no
      physical printer is available on the test LAN)
- [ ] Click NEW CUSTOMER; confirm the screen fully resets and a fresh
      order can be started immediately

## Multi-user / concurrency test

- [ ] Repeat the User PC test from a third clean machine
      simultaneously with the second — confirm orders don't cross
      contaminate (Order A's files never show up in Order B)
- [ ] Confirm the Admin dashboard's "Active Users" count reflects both
      stations

## Large file test

- [ ] From a User PC, select a file larger than 200MB (a large PDF or
      video-converted-to-PDF works for testing)
- [ ] Confirm the upload progresses (percentage updates) rather than
      freezing the UI
- [ ] Confirm Admin PC memory usage in Task Manager does not spike by
      the full file size during upload (proves streaming, not full
      buffering)

## Failure-mode tests

- [ ] Turn off the Admin PC's Wi-Fi/network mid-session on a User PC —
      confirm a human-readable "Admin server is unavailable" message
      appears, not a crash or raw error
- [ ] Select an unsupported file type (e.g. a `.exe`) — confirm a clear
      rejection message, not a crash
- [ ] Enter an invalid page range (e.g. `99-150` on a 5-page document)
      — confirm it's rejected before reaching the Admin PC's queue

## Sign-off

Only mark Phase 4 complete once every box above is checked on
genuinely clean machines. A single unchecked box is a real gap, not a
formality — treat it as a bug to fix before calling this
production-ready.
