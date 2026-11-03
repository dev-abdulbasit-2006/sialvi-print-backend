"""
Printer routes: live discovery for the User app's printer picker, and
admin-side sync/mapping. (No online/offline status is tracked - by design.)
"""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from admin.database.db import get_conn, transaction
from admin.server.helpers import error_response, require_lan_token, require_admin_token
from admin.printer.printer_manager import printer_manager, PrinterError

logger = logging.getLogger("routes_printers")
bp = Blueprint("printers", __name__, url_prefix="/api")


@bp.route("/printers", methods=["GET"])
@require_lan_token
def list_printers():
    """
    Returns printers the User app can choose from. Always backed by a
    fresh sync against the real Windows printer list, never a stale or
    fabricated cache (requirement 5).
    """
    _sync_printers()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, system_name, display_name, is_network, is_enabled FROM printers WHERE is_enabled = 1"
    ).fetchall()
    return jsonify([dict(r) for r in rows]), 200


@bp.route("/printers/<int:printer_id>/capabilities", methods=["GET"])
@require_lan_token
def printer_capabilities(printer_id):
    conn = get_conn()
    printer = conn.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
    if not printer:
        return error_response("Printer not found.", 404, "PRINTER_NOT_FOUND")
    try:
        caps = printer_manager.get_capabilities(printer["system_name"])
    except PrinterError as e:
        return error_response(str(e), 409, "PRINTER_UNAVAILABLE")
    return jsonify(caps), 200


@bp.route("/printers/<int:printer_id>/print-options", methods=["GET"])
@require_lan_token
def printer_print_options(printer_id):
    """
    Options for the User app's "Print Properties" popup (paper size, quality,
    paper type), taken from the real printer driver on this Admin PC.
    """
    conn = get_conn()
    printer = conn.execute("SELECT * FROM printers WHERE id = ? AND is_enabled = 1", (printer_id,)).fetchone()
    if not printer:
        return error_response("Printer not found.", 404, "PRINTER_NOT_FOUND")
    profile = None
    profile_id = request.args.get("profile_id", type=int)
    if profile_id:
        profile = conn.execute("SELECT paper_size, media_type FROM print_profiles WHERE id = ?", (profile_id,)).fetchone()
    try:
        options = printer_manager.get_print_options(
            printer["system_name"],
            profile["paper_size"] if profile else "",
            profile["media_type"] if profile else "",
        )
    except Exception:
        logger.exception("Could not read print options for printer %s", printer_id)
        return error_response("Could not read this printer's print options. Please try again.", 409, "PRINTER_UNAVAILABLE")
    options["printer_name"] = printer["display_name"]
    return jsonify(options), 200


def _sync_printers():
    """
    Pulls the real Windows printer list and upserts into the database,
    marking anything no longer present as disabled rather than deleting
    it (preserves printer_id references from historical orders).
    """
    try:
        live = printer_manager.list_printers()
    except PrinterError as e:
        logger.error("Printer sync failed: %s", e)
        return

    conn = get_conn()
    now = datetime.utcnow().isoformat()
    live_names = {p["system_name"] for p in live}

    with transaction() as conn:
        for p in live:
            existing = conn.execute(
                "SELECT id FROM printers WHERE system_name = ?", (p["system_name"],)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE printers SET display_name = ?, is_network = ?,
                                            last_seen_at = ?, is_enabled = 1
                       WHERE id = ?""",
                    (p["display_name"], int(p["is_network"]), now, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO printers (system_name, display_name, is_network, last_seen_at, is_enabled)
                       VALUES (?, ?, ?, ?, 1)""",
                    (p["system_name"], p["display_name"], int(p["is_network"]), now),
                )

        known = conn.execute("SELECT id, system_name FROM printers").fetchall()
        for row in known:
            if row["system_name"] not in live_names:
                conn.execute("UPDATE printers SET is_enabled = 0 WHERE id = ?", (row["id"],))


@bp.route("/admin/printers/<int:printer_id>/profile-mapping", methods=["PUT"])
@require_admin_token
def set_profile_mapping(printer_id):
    """Admin maps which printer handles which print profile (requirement 8)."""
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profile_id")
    if not profile_id:
        return error_response("profile_id is required.", 400, "MISSING_PROFILE_ID")

    conn = get_conn()
    printer = conn.execute("SELECT id, system_name FROM printers WHERE id = ?", (printer_id,)).fetchone()
    if not printer:
        return error_response("Printer not found.", 404, "PRINTER_NOT_FOUND")
    profile = conn.execute("SELECT id FROM print_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        return error_response("Print profile not found.", 404, "PROFILE_NOT_FOUND")

    with transaction() as conn:
        conn.execute("""UPDATE print_profiles
                              SET default_printer_id = ?, default_printer_name = ?, updated_at = ?
                              WHERE id = ?""",
                     (printer_id, printer["system_name"], datetime.utcnow().isoformat(), profile_id))
        conn.execute(
            "INSERT OR IGNORE INTO profile_printer_map (profile_id, printer_id) VALUES (?, ?)",
            (profile_id, printer_id),
        )

    return jsonify({"status": "mapped"}), 200
