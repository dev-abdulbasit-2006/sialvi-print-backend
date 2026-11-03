-- ============================================================
-- SialviPrint Admin Database Schema
-- SQLite, WAL mode, foreign keys enforced
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- system_settings: single-row-per-key config store
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- printers: known Windows printers (discovered + admin-mapped)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS printers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_name     TEXT NOT NULL UNIQUE,   -- exact Windows printer name
    display_name    TEXT NOT NULL,
    is_network      INTEGER NOT NULL DEFAULT 0,
    is_enabled      INTEGER NOT NULL DEFAULT 1,
    last_seen_at    TEXT,
    last_status     TEXT NOT NULL DEFAULT 'UNKNOWN',  -- ONLINE, OFFLINE, ERROR, UNKNOWN
    capabilities_json TEXT,                 -- cached capability snapshot (paper sizes, color, duplex)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- print_profiles: the 13 required printing types
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS print_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,   -- e.g. 'A3_GLOSSY'
    display_name    TEXT NOT NULL,
    paper_size      TEXT NOT NULL,          -- A3, A4, LETTER
    media_type      TEXT NOT NULL,          -- GLOSSY, MATTE, LUSTER, LASER, PLAIN
    color_mode      TEXT NOT NULL,          -- COLOR, BW
    quality         TEXT NOT NULL DEFAULT 'NORMAL',
    supported_formats_json TEXT NOT NULL,   -- json array of extensions
    default_printer_id INTEGER,
    default_printer_name TEXT,
    price_per_page  REAL NOT NULL DEFAULT 0,
    is_enabled      INTEGER NOT NULL DEFAULT 1,
    ask_properties  INTEGER NOT NULL DEFAULT 0,   -- 1 = User must set print properties (paper size/quality/paper type) first
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (default_printer_id) REFERENCES printers(id) ON DELETE SET NULL
);

-- ------------------------------------------------------------
-- price_tiers: optional quantity "packages" per print type.
-- e.g. 1-3 pages = Rs 40/page, 4-10 pages = Rs 30/page.
-- max_qty NULL = "and above". When a profile has tiers, the tier that
-- matches the order's total quantity (pages x copies) sets the
-- per-page price; otherwise print_profiles.price_per_page is used.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_tiers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL,
    min_qty         INTEGER NOT NULL,
    max_qty         INTEGER,
    price_per_page  REAL NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES print_profiles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_tiers_profile ON price_tiers(profile_id);

-- ------------------------------------------------------------
-- profile_printer_map: explicit profile -> printer routing
-- (overrides default_printer_id when present; supports future
--  multi-printer-per-profile load balancing)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profile_printer_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL,
    printer_id  INTEGER NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (profile_id) REFERENCES print_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE CASCADE,
    UNIQUE(profile_id, printer_id)
);

-- ------------------------------------------------------------
-- weekly_periods: persistent identifier for weekly reset safety
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_periods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key      TEXT NOT NULL UNIQUE,   -- ISO week e.g. '2026-W38'
    started_at      TEXT NOT NULL,
    closed_at       TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------------
-- weekly_summaries: archived totals per closed period
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key      TEXT NOT NULL UNIQUE,
    total_earnings  REAL NOT NULL DEFAULT 0,
    total_orders    INTEGER NOT NULL DEFAULT 0,
    total_pages     INTEGER NOT NULL DEFAULT 0,
    total_print_jobs INTEGER NOT NULL DEFAULT 0,
    archived_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- orders: one customer transaction
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,       -- UUID
    station_id      TEXT NOT NULL,          -- unique User PC identity
    computer_name   TEXT,                   -- Windows computer/hostname shown to admin
    station_host    TEXT,                   -- last authenticated LAN address of originating User PC
    station_key     TEXT,                   -- per-station secret for Admin -> User print-agent authentication
    station_port    INTEGER NOT NULL DEFAULT 8767, -- User-side print-agent TCP port
    printer_name    TEXT,                   -- exact Windows printer name on originating User PC
    period_key      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'CREATED',
        -- CREATED, UPLOADING, PROCESSING, WAITING_PAYMENT, PAID,
        -- QUEUED, PRINTING, PRINTED, FAILED, CANCELLED
    printer_id      INTEGER,
    profile_id      INTEGER,
    copies          INTEGER NOT NULL DEFAULT 1,
    orientation     TEXT NOT NULL DEFAULT 'AUTO',   -- PORTRAIT, LANDSCAPE, AUTO
    page_range      TEXT,                   -- raw string e.g. '1-3,5'
    total_pages     INTEGER NOT NULL DEFAULT 0,
    total_quantity  INTEGER NOT NULL DEFAULT 0,     -- total_pages * copies, server-recalculated
    total_price     REAL NOT NULL DEFAULT 0,        -- server-recalculated, never trust client
    payment_status  TEXT NOT NULL DEFAULT 'UNPAID',  -- UNPAID, PENDING, PAID
    verified_by     TEXT,
    verified_at     TEXT,
    error_message   TEXT,
    print_settings_json TEXT,               -- customer-chosen print properties (paper size / quality / paper type)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE SET NULL,
    FOREIGN KEY (profile_id) REFERENCES print_profiles(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_period ON orders(period_key);

-- ------------------------------------------------------------
-- order_files: files within one order, preserving order
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_files (
    id              TEXT PRIMARY KEY,       -- UUID (internal file id, never trust filename)
    order_id        TEXT NOT NULL,
    sequence_index  INTEGER NOT NULL,       -- preserves customer's selection order
    original_filename TEXT NOT NULL,        -- display only, never used as path
    stored_path     TEXT NOT NULL,          -- internal controlled path
    extension       TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    page_count      INTEGER,
    status          TEXT NOT NULL DEFAULT 'PENDING',
        -- PENDING, UPLOADING, UPLOADED, PROCESSING, READY, FAILED
    error_message   TEXT,
    checksum_sha256 TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_order_files_order ON order_files(order_id);

-- ------------------------------------------------------------
-- payments: one row per payment attempt/verification
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,       -- UUID
    order_id        TEXT NOT NULL UNIQUE,
    amount          REAL NOT NULL,
    payment_method  TEXT,                    -- CASH or ONLINE after admin confirmation
    receipt_no      INTEGER UNIQUE,          -- sequential receipt number
    status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING, VERIFIED, REJECTED
    requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
    verified_at     TEXT,
    verified_by     TEXT,
    notes           TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- print_jobs: one row per file that actually gets sent to a printer
-- (an order with 3 files produces 3 print_jobs)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS print_jobs (
    id              TEXT PRIMARY KEY,       -- UUID
    order_id        TEXT NOT NULL,
    order_file_id   TEXT NOT NULL,
    printer_id      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'QUEUED',
        -- QUEUED, PRINTING, PRINTED, FAILED, PRINT_ERROR, CANCELLED
    attempts        INTEGER NOT NULL DEFAULT 0,
    windows_job_id  INTEGER,                -- id returned by win32print
    submitted_at    TEXT,
    completed_at    TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (order_file_id) REFERENCES order_files(id) ON DELETE CASCADE,
    FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_print_jobs_status ON print_jobs(status);

-- ------------------------------------------------------------
-- audit_log: append-only record of sensitive operations
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,          -- PAYMENT_VERIFIED, PRICE_CHANGED, WEEKLY_RESET, etc
    actor           TEXT,                   -- admin username or 'system'
    order_id        TEXT,
    details_json    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
