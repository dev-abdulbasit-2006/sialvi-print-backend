# Sialvi Print: premium customer (User) interface

Technology: **PySide6 / Qt Widgets** (no web view). User-only styling; `shared/theme.py` (Admin/Owner) is untouched
(a test pins its hash). **Dark theme matching the Admin app** (same BG/SURFACE/BORDER/TEXT/MUTED and orange accent, checked by a test). The window opens **maximized** and has a Maximize/Restore button; closing still needs the exit password.

## Files
| File | Role |
|---|---|
| `user/ui/theme_user.py` | All tokens (colors, radii, spacing, type, motion timings), the full stylesheet, contrast helper |
| `user/ui/components.py` | `StepIndicator`, `DropZone`, `FileRowWidget`, `StatusCard` + `StateGlyph`, `SegmentedControl`, `CopiesStepper`, `WelcomePage`, `ConfirmDialog`, `fade_in`, `switch_page` |
| `user/ui/main_window.py` | Window layout and the (unchanged) customer-flow handlers |
| `user/ui/styles.py` | Exposes `STYLESHEET` from `theme_user` |
| `tools/ui_preview.py` | Offscreen screenshots with a FAKE api (never shipped in the app) |
| `screenshots/` | After-redesign images at 1366x768, 1920x1080, 1200x680 |

## Screen map (one window, two pages)
1. **Welcome** (page 0): navy logo tile, headline, **Start Printing**, three hint cards. Order is still created on launch; Start retries creation if startup failed.
2. **Workspace** (page 1): step rail (Add files, Choose settings, Price & pay, Print) driven by real state; left: drag-and-drop zone (slim bar once files exist) + file rows with real upload progress + settings grid; right: preview, price, PAY, status card.
3. **Status card** (inside Price & payment): waiting for attendant, printing, printed (animated check), failed (lists failed files). Shown only from real server status; success only on `PRINTED`.
4. **NEW CUSTOMER** (header): styled confirmation (default "Keep order"), same cancel rules as before, then back to Welcome.

## Behaviour preserved
Handlers and attributes are unchanged in name and meaning (`on_select_files`, `_start_upload`, `on_calculate_price`, `on_pay`, `_poll_order_status`, `on_new_customer`, `_show_banner`, ...). Price comes only from the server. The customer still does not pick a payment method (the attendant verifies Cash/Online on the Admin PC). A FAILED order keeps its paid status so NEW CUSTOMER never tries to cancel it. Reset remains manual.
Small, deliberate changes: PAY and NEW CUSTOMER confirmations are styled dialogs (same yes/no semantics); "Calculate Price" is disabled while calculating and the price shows "Calculating…"; a failed calculation resets the price; dropped files with unsupported extensions are refused with a message (the same extension list as the Choose Files dialog); minimum window size 1200x680 (was 1000x680); preview canvas minimum height 190 (was 300).

## Design tokens (see `theme_user.py`)
Dark, same family as the Admin: page `#0F1115`, cards `#1A1D24`, inputs `#15181F`, borders `#2A2F3A`/`#3A4150`, text `#E2E8F0`, muted `#94A3B8`, header gradient `#222630` to `#0B0D12` with an orange rule, accent orange `#F97316` (rings, progress, step rail), buttons deep orange `#C2410C` with white text (5.2:1), link text `#FB923C`, success `#22C55E`, danger `#EF4444`. Document previews stay white "paper" with dark text. Radii 8/12/16. Touch targets >= 44 px (primary 52 px). Fonts: Inter, then Segoe UI Variable, then Segoe UI (no font is bundled). A light dropdown arrow (`assets/arrow_down_light.png`) is used on dark inputs.
Motion: 160 / 240 / 340 ms. Implemented with `QPropertyAnimation`, `QVariantAnimation`, `QGraphicsOpacityEffect` (fade-ins), painted glyphs (spinner arc, drawn check), animated step rail. Set `SIALVI_REDUCE_MOTION=1` to turn motion off. Animations stop when hidden. Not implemented: slide transitions between pages (fade only).

## How to change things
Colors/sizes: edit constants at the top of `theme_user.py`. New step label: `StepIndicator.LABELS`. Preview images: `python tools/ui_preview.py out_dir` (set `W`/`H` env vars for the size).

## Testing
`python -m unittest tests.test_user_ui` (25 tests: WCAG AA contrast of every text pair, Admin theme hash, welcome/start, step logic, upload progress/failure, remove, drag-drop filter, price ok/fail, PAY cancel/confirm, waiting/printing/failed/printed truthfulness, NEW CUSTOMER cancel/confirm and never cancelling paid/failed orders, page-range hint, properties row, orientation and copies widgets, exit password still required).

## Not verified (needs a Windows PC)
Real rendering with Inter/Segoe fonts and display scaling (125/150%), touch input, real printer output, real LAN with the Admin PC, the packaged installer UI. Screenshots were rendered offscreen on Linux with a fallback font; Windows will look slightly different.
