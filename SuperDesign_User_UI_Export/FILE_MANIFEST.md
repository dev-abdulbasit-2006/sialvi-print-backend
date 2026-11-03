# File Manifest - SuperDesign User UI Export

| Original Relative Path | Exported Relative Path | Purpose | File Type | UI Category | Share with SuperDesign? |
|---|---|---|---|---|---|
| user/ui/__init__.py | ui_files/user/ui/__init__.py | Package marker | Python | Shared UI Support | Yes |
| user/ui/main_window.py | ui_files/user/ui/main_window.py | Main kiosk window, layout, widgets, print flow hooks | Python/PySide6 | Primary UI | Yes (careful: mixes backend logic) |
| user/ui/setup_dialog.py | ui_files/user/ui/setup_dialog.py | First-run admin host/token setup dialog | Python/PySide6 | Primary UI | Yes |
| user/ui/print_properties.py | ui_files/user/ui/print_properties.py | Print properties dialog | Python/PySide6 | Primary UI | Yes |
| user/ui/preview.py | ui_files/user/ui/preview.py | Print preview widget | Python/PySide6 | Primary UI | Yes |
| user/ui/exit_dialog.py | ui_files/user/ui/exit_dialog.py | Exit password dialog | Python/PySide6 | Primary UI | Yes |
| user/ui/styles.py | ui_files/user/ui/styles.py | Kiosk stylesheet wrapper | Python | Styling | Yes |
| user/ui/workers.py | ui_files/user/ui/workers.py | QThread workers for network/print tasks | Python | UI Behavior | Context only |
| shared/theme.py | ui_files/shared/theme.py | Shared QSS theme/palette | Python | Styling | Yes |
| shared/resources.py | ui_files/shared/resources.py | Asset path resolver | Python | Shared UI Support | Yes |
| shared/license_ui.py | ui_files/shared/license_ui.py | License gate dialog UI | Python/PySide6 | Shared UI Support | Yes (context) |
| assets/arrow_down.png | assets/arrow_down.png | ComboBox arrow icon | PNG | Assets | Yes |
| assets/emblem.png | assets/emblem.png | Window/app emblem | PNG | Assets | Yes |
| assets/logo_user_horizontal.png | assets/logo_user_horizontal.png | User app logo | PNG | Assets | Yes |
| user/run_user.py | supporting_context/user/run_user.py | App entry point | Python | Backend Dependency - Context Only | Context only |
| user/config/config.py | supporting_context/user/config/config.py | User config (host/token/station) | Python | Backend Dependency - Context Only | Context only (no secrets) |

## Minimum set
`main_window.py`, `theme.py`, `styles.py`, `resources.py`, `emblem.png`, `logo_user_horizontal.png`, `arrow_down.png`.

## Complete set
All 16 files above.

## Missing/unresolved dependencies
- `shared/license_gate.py`, `shared/netutil.py` not included (backend/licensing internals); UI references them at runtime.
- Admin-side shared theme usage not part of this export.
- `arrow_up.png` exists in assets but unused by user UI; omitted.

## Files not to provide to SuperDesign
- `workers.py` logic, `config.py` runtime secrets, all network/printing code, license internals.
- Admin/owner/monitoring/backend files excluded entirely.
