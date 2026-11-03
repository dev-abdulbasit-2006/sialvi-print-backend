# Sialvi Printing Solution — UI Integration

The supplied Printing Shop dashboard UI reference has been integrated into the desktop Admin application.

Implemented in the Admin build:
- dark dashboard/sidebar visual language with orange accent
- supplied Sialvi Print logo in the Admin shell and application icon
- orange pending/awaiting states
- Cash / Online / Reject payment actions
- cash and online earnings cards
- receipt history for Today, This Week, This Month and All History, with an Export CSV button (exports the period / payment method / search currently selected; PDF export was removed)
- sequential receipt numbers
- receipt fields: cost, file name, printing, printer, computer
- unified Printing configuration with price, packages, print-properties popup toggle and enabled toggle
- Computer label instead of Station in Admin-facing queue/payment/receipt views
- Admin password on startup with change-password option

The backend remains the existing Flask + SQLite + print-queue architecture; the UI is wired to the real Admin API rather than the static sample data in the supplied React reference.
