"""
Print profile / pricing routes.

The User application only ever reads prices from here - it must never
define or cache prices independently (requirement 3.2). Price changes
are admin-only and take effect immediately for any order not yet
priced (orders already in WAITING_PAYMENT keep their locked-in price).
"""

import json
import logging
import re
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from admin.database.db import get_conn, transaction
from admin.server.helpers import error_response, require_lan_token, require_admin_token
from admin.server.pricing import get_tiers
from admin.server.file_storage import ALLOWED_EXTENSIONS

logger = logging.getLogger("routes_profiles")
bp = Blueprint("profiles", __name__, url_prefix="/api")


def _with_tiers(conn, row) -> dict:
    d = dict(row)
    d["tiers"] = get_tiers(conn, d["id"])
    return d


@bp.route("/profiles", methods=["GET"])
@require_lan_token
def list_profiles():
    conn = get_conn()
    rows = conn.execute(
        """SELECT pp.id, pp.code, pp.display_name, pp.paper_size, pp.media_type, pp.color_mode,
                  pp.quality, pp.supported_formats_json, pp.price_per_page, pp.default_printer_id,
                  pp.default_printer_name, pp.ask_properties,
                  COALESCE(p.display_name, pp.default_printer_name) AS default_printer_display_name
           FROM print_profiles pp
           LEFT JOIN printers p ON p.id = pp.default_printer_id
           WHERE pp.is_enabled = 1 ORDER BY pp.display_name"""
    ).fetchall()
    return jsonify([_with_tiers(conn, r) for r in rows]), 200


@bp.route("/admin/profiles", methods=["GET"])
@require_admin_token
def list_all_profiles():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM print_profiles ORDER BY display_name").fetchall()
    return jsonify([_with_tiers(conn, r) for r in rows]), 200


@bp.route("/admin/profiles/<int:profile_id>/price", methods=["PUT"])
@require_admin_token
def set_price(profile_id):
    body = request.get_json(silent=True) or {}
    try:
        price = float(body.get("price_per_page"))
    except (TypeError, ValueError):
        return error_response("price_per_page must be a number.", 400, "INVALID_PRICE")
    if price < 0:
        return error_response("price_per_page cannot be negative.", 400, "INVALID_PRICE")

    conn = get_conn()
    profile = conn.execute("SELECT id, display_name FROM print_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        return error_response("Profile not found.", 404, "PROFILE_NOT_FOUND")

    with transaction() as conn:
        conn.execute(
            "UPDATE print_profiles SET price_per_page = ?, updated_at = ? WHERE id = ?",
            (price, datetime.utcnow().isoformat(), profile_id),
        )
        conn.execute(
            """INSERT INTO audit_log (event_type, actor, details_json)
               VALUES ('PRICE_CHANGED', 'admin', ?)""",
            (f'{{"profile_id":{profile_id},"new_price":{price}}}',),
        )

    logger.info("Price for profile %s (%s) changed to %.2f", profile_id, profile["display_name"], price)
    return jsonify({"status": "updated", "price_per_page": price}), 200


@bp.route("/admin/profiles/<int:profile_id>/enabled", methods=["PUT"])
@require_admin_token
def set_profile_enabled(profile_id):
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", True))
    conn = get_conn()
    profile = conn.execute("SELECT id FROM print_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        return error_response("Profile not found.", 404, "PROFILE_NOT_FOUND")
    with transaction() as conn:
        conn.execute("UPDATE print_profiles SET is_enabled = ?, updated_at = ? WHERE id = ?",
                     (int(enabled), datetime.utcnow().isoformat(), profile_id))
    return jsonify({"status": "updated", "is_enabled": enabled}), 200


# ------------------------------------------------------------------
# Admin: create / delete print types, packages (quantity tiers),
# and the "ask for print properties" switch
# ------------------------------------------------------------------
@bp.route("/admin/profiles", methods=["POST"])
@require_admin_token
def create_profile():
    """Adds a new print type (e.g. 'Card Printing') with its price."""
    body = request.get_json(silent=True) or {}
    name = (body.get("display_name") or "").strip()
    if not name or len(name) > 60:
        return error_response("Please enter a name (up to 60 characters).", 400, "INVALID_NAME")
    try:
        price = float(body.get("price_per_page"))
    except (TypeError, ValueError):
        return error_response("Price must be a number.", 400, "INVALID_PRICE")
    if price < 0:
        return error_response("Price cannot be negative.", 400, "INVALID_PRICE")
    color_mode = body.get("color_mode", "COLOR")
    if color_mode not in ("COLOR", "BW"):
        return error_response("Color mode must be COLOR or BW.", 400, "INVALID_COLOR_MODE")
    paper_size = (body.get("paper_size") or "A4").strip().upper()[:12] or "A4"
    ask_props = 1 if body.get("ask_properties") else 0
    enabled = 1 if body.get("enabled", True) else 0

    conn = get_conn()
    if conn.execute("SELECT 1 FROM print_profiles WHERE lower(display_name) = lower(?)", (name,)).fetchone():
        return error_response(f"A print type named '{name}' already exists.", 409, "DUPLICATE_NAME")

    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:24] or "TYPE"
    code = f"CUSTOM_{slug}_{uuid.uuid4().hex[:6].upper()}"
    formats = sorted(ALLOWED_EXTENSIONS)   # every file type the system can process

    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO print_profiles
               (code, display_name, paper_size, media_type, color_mode,
                supported_formats_json, price_per_page, ask_properties, is_enabled)
               VALUES (?, ?, ?, 'CUSTOM', ?, ?, ?, ?, ?)""",
            (code, name, paper_size, color_mode, json.dumps(formats), price, ask_props, enabled),
        )
        new_id = cur.lastrowid
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, details_json) VALUES ('PROFILE_CREATED', 'admin', ?)",
            (json.dumps({"profile_id": new_id, "name": name, "price": price}),),
        )
    logger.info("Print type '%s' created (id %s, Rs %.2f)", name, new_id, price)
    return jsonify({"status": "created", "id": new_id}), 201


@bp.route("/admin/profiles/<int:profile_id>", methods=["DELETE"])
@require_admin_token
def delete_profile(profile_id):
    """Only admin-created types can be deleted, and only if no past order used them."""
    conn = get_conn()
    profile = conn.execute("SELECT id, code, display_name FROM print_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        return error_response("Print type not found.", 404, "PROFILE_NOT_FOUND")
    if not profile["code"].startswith("CUSTOM_"):
        return error_response("Built-in print types can't be deleted. Untick 'Enabled' to hide it instead.",
                              409, "BUILTIN_PROFILE")
    if conn.execute("SELECT 1 FROM orders WHERE profile_id = ? LIMIT 1", (profile_id,)).fetchone():
        return error_response("This print type was used in past orders, so it can't be deleted. "
                              "Untick 'Enabled' to hide it instead.", 409, "PROFILE_IN_USE")
    with transaction() as conn:
        conn.execute("DELETE FROM print_profiles WHERE id = ?", (profile_id,))
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, details_json) VALUES ('PROFILE_DELETED', 'admin', ?)",
            (json.dumps({"profile_id": profile_id, "name": profile["display_name"]}),),
        )
    return jsonify({"status": "deleted"}), 200


@bp.route("/admin/profiles/<int:profile_id>/ask-properties", methods=["PUT"])
@require_admin_token
def set_ask_properties(profile_id):
    body = request.get_json(silent=True) or {}
    enabled = 1 if body.get("enabled") else 0
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM print_profiles WHERE id = ?", (profile_id,)).fetchone():
        return error_response("Print type not found.", 404, "PROFILE_NOT_FOUND")
    with transaction() as conn:
        conn.execute("UPDATE print_profiles SET ask_properties = ?, updated_at = ? WHERE id = ?",
                     (enabled, datetime.utcnow().isoformat(), profile_id))
    return jsonify({"status": "updated", "ask_properties": bool(enabled)}), 200


@bp.route("/admin/profiles/<int:profile_id>/tiers", methods=["GET"])
@require_admin_token
def get_profile_tiers(profile_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM print_profiles WHERE id = ?", (profile_id,)).fetchone():
        return error_response("Print type not found.", 404, "PROFILE_NOT_FOUND")
    return jsonify(get_tiers(conn, profile_id)), 200


@bp.route("/admin/profiles/<int:profile_id>/tiers", methods=["PUT"])
@require_admin_token
def replace_profile_tiers(profile_id):
    """Replaces the whole package list for a print type. An empty list = flat price only."""
    body = request.get_json(silent=True) or {}
    raw = body.get("tiers")
    if not isinstance(raw, list) or len(raw) > 30:
        return error_response("tiers must be a list (maximum 30 packages).", 400, "INVALID_TIERS")

    cleaned = []
    for i, t in enumerate(raw, start=1):
        try:
            lo = int(t.get("min_qty"))
            hi = t.get("max_qty")
            hi = None if hi in (None, "") else int(hi)
            price = float(t.get("price_per_page"))
        except (TypeError, ValueError, AttributeError):
            return error_response(f"Package {i}: please enter valid numbers.", 400, "INVALID_TIERS")
        if lo < 1:
            return error_response(f"Package {i}: 'From' must be at least 1 page.", 400, "INVALID_TIERS")
        if hi is not None and hi < lo:
            return error_response(f"Package {i}: 'To' must not be smaller than 'From'.", 400, "INVALID_TIERS")
        if price < 0:
            return error_response(f"Package {i}: price cannot be negative.", 400, "INVALID_TIERS")
        cleaned.append((lo, hi, price))

    # Overlaps are ambiguous for the shop owner, so refuse them clearly.
    ordered = sorted(cleaned, key=lambda x: x[0])
    for a, b in zip(ordered, ordered[1:]):
        if a[1] is None or b[0] <= a[1]:
            return error_response(
                "Packages overlap. Make each 'From' start after the previous package's 'To' "
                "(e.g. 1-3, then 4-10, then 11 and above).", 400, "TIERS_OVERLAP")

    conn = get_conn()
    if not conn.execute("SELECT 1 FROM print_profiles WHERE id = ?", (profile_id,)).fetchone():
        return error_response("Print type not found.", 404, "PROFILE_NOT_FOUND")
    with transaction() as conn:
        conn.execute("DELETE FROM price_tiers WHERE profile_id = ?", (profile_id,))
        for lo, hi, price in ordered:
            conn.execute(
                "INSERT INTO price_tiers (profile_id, min_qty, max_qty, price_per_page) VALUES (?,?,?,?)",
                (profile_id, lo, hi, price),
            )
        conn.execute(
            "INSERT INTO audit_log (event_type, actor, details_json) VALUES ('TIERS_CHANGED', 'admin', ?)",
            (json.dumps({"profile_id": profile_id, "tiers": ordered}),),
        )
    return jsonify({"status": "updated", "count": len(ordered)}), 200
