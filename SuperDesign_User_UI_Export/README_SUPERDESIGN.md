# SuperDesign User UI Export - SialviPrint User (Kiosk) App

## What the user-side printing interface does
Customer/kiosk-facing desktop printing UI. Lets a station user pick files, preview, set print properties, and submit print jobs to the admin station. Also includes first-run setup and license/exit dialogs.

## UI technology
PySide6 (Qt Widgets) desktop application. Global stylesheet built from `shared/theme.py` via `user/ui/styles.py` (kiosk variant, base_px=15). No HTML/CSS/JS/Flask/WebView.

## Main UI entry point
`user/run_user.py` creates QApplication, applies `STYLESHEET`, then shows `user/ui/main_window.py::MainWindow` after setup/licensing.

## Visual styles
`shared/theme.py::build_stylesheet` (palette + QSS). `user/ui/styles.py` wraps it as `STYLESHEET`. Widget classes in `user/ui/*.py` define layouts and object names targeted by QSS.

## File relationships
- `run_user.py` -> applies stylesheet -> `MainWindow`.
- `main_window.py` uses `asset_path("logo_user_horizontal.png")`.
- `setup_dialog.py`, `print_properties.py`, `exit_dialog.py` use `asset_path("emblem.png")`.
- `styles.py` -> `shared/theme.py` -> `shared/resources.py` -> `assets/arrow_down.png`.
- `shared/license_ui.py` (license gate dialog) uses `emblem.png`.

## Open first in SuperDesign
1. `ui_files/user/ui/main_window.py`
2. `ui_files/shared/theme.py`
3. `ui_files/user/ui/styles.py`
4. Other dialogs: `setup_dialog.py`, `print_properties.py`, `preview.py`, `exit_dialog.py`.

## Preview
Run `python -m user.run_user` from project root after configuring admin host/token. Requires PySide6 and license. Not safe to preview without valid license/config.

## Unsuitable for direct visual redesign
Files containing business logic/backend integrations: `workers.py` (UI workers wrapping network/printing), `shared/license_ui.py` (license gate), and all network/config code (not included). `main_window.py` mixes UI layout with print-job orchestration; redesign must preserve signals/slots.
