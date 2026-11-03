"""
Order lifecycle routes: create order, upload files, set options,
compute price. Called by User PCs.
"""

import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from admin.database.db import get_conn, transaction
from admin.server.helpers import ApiError, error_response, parse_page_range, require_lan_token
from admin.server.file_storage import save_stream, ALLOWED_EXTENSIONS
from admin.server.page_counter import count_pages, PageCountError
from admin.server.period import current_period_key
from admin.server.pricing import get_tiers, compute_total

import json as _json_mod


def _json_dumps(obj):
    return _json_mod.dumps(obj, separators=(",", ":"))


logger = logging.getLogger("routes_orders")
bp = Blueprint("orders", __name__, url_prefix="/api/orders")


@bp.route("", methods=["POST"])
@require_lan_token
def create_order():
    body = request.get_json(silent=True) or {}
    station_id = body.get("station_id")
    station_key = (body.get("station_key") or "").strip()
    computer_name = (body.get("computer_name") or "").strip()[:255]
    if not station_id:
        return error_response("station_id is required to isolate sessions.", 400, "MISSING_STATION_ID")
    if len(station_key) < 32:
        return error_response("station_key is required for secure print routing.", 400, "MISSING_STATION_KEY")

    order_id = str(uuid.uuid4())
    with transaction() as conn:
        conn.execute(
            """INSERT INTO orders (id, station_id, computer_name, station_host, station_key, station_port, period_key, status)
               VALUES (?, ?, ?, ?, ?, 8767, ?, 'CREATED')""",
            (order_id, station_id, computer_name or station_id.rsplit("-", 1)[0], request.remote_addr, station_key, current_period_key()),
        )
    logger.info("Order %s created by station %s", order_id, station_id)
    return jsonify({"order_id": order_id}), 201


@bp.route("/<order_id>/files", methods=["POST"])
@require_lan_token
def upload_file(order_id):
    """
    Streams a single file upload directly to disk (never fully
    buffered in memory), counts its pages using the real rendering
    path, and records it against the order preserving sequence order.
    """
    conn = get_conn()
    order = conn.execute("SELECT id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return error_response("Order not found.", 404, "ORDER_NOT_FOUND")
    if order["status"] not in ("CREATED", "UPLOADING", "PROCESSING"):
        return error_response(f"Cannot add files to an order in status {order['status']}.", 409, "INVALID_ORDER_STATE")

    original_filename = request.headers.get("X-Filename")
    if not original_filename:
        return error_response("X-Filename header is required.", 400, "MISSING_FILENAME")

    try:
        sequence_index = int(request.headers.get("X-Sequence-Index", "0"))
    except ValueError:
        return error_response("X-Sequence-Index must be an integer.", 400, "INVALID_SEQUENCE_INDEX")

    with transaction() as conn:
        conn.execute("UPDATE orders SET status = 'UPLOADING', updated_at = ? WHERE id = ?",
                     (datetime.utcnow().isoformat(), order_id))

    try:
        saved = save_stream(order_id, original_filename, request.stream)
    except ApiError:
        raise
    except Exception as e:
        logger.exception("Upload failed for order %s", order_id)
        return error_response("File upload was interrupted. Please try again.", 400, "UPLOAD_INTERRUPTED")

    file_id = saved["file_id"]
    with transaction() as conn:
        conn.execute(
            """INSERT INTO order_files
               (id, order_id, sequence_index, original_filename, stored_path,
                extension, size_bytes, status, checksum_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'UPLOADED', ?)""",
            (file_id, order_id, sequence_index, original_filename, saved["stored_path"],
             saved["extension"], saved["size_bytes"], saved["checksum_sha256"]),
        )

    # Count pages now, synchronously - keeps the client informed
    # immediately and keeps this phase's implementation simple. Under
    # very high concurrent load this is the first candidate to move to
    # a background worker (see documentation/SCALING_NOTES.md).
    try:
        pages = count_pages(saved["stored_path"], saved["extension"])
        with transaction() as conn:
            conn.execute(
                "UPDATE order_files SET page_count = ?, status = 'READY' WHERE id = ?",
                (pages, file_id),
            )
    except PageCountError as e:
        with transaction() as conn:
            conn.execute(
                "UPDATE order_files SET status = 'FAILED', error_message = ? WHERE id = ?",
                (str(e), file_id),
            )
        return error_response(str(e), 422, "PAGE_COUNT_FAILED")

    return jsonify({"file_id": file_id, "page_count": pages, "status": "READY"}), 201


@bp.route("/<order_id>/files/<file_id>", methods=["DELETE"])
@require_lan_token
def remove_file(order_id, file_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT stored_path FROM order_files WHERE id = ? AND order_id = ?",
        (file_id, order_id),
    ).fetchone()
    if not row:
        return error_response("File not found on this order.", 404, "FILE_NOT_FOUND")

    with transaction() as conn:
        conn.execute("DELETE FROM order_files WHERE id = ?", (file_id,))

    import os
    try:
        os.remove(row["stored_path"])
    except OSError:
        pass  # best-effort cleanup

    return jsonify({"status": "removed"}), 200


@bp.route("/<order_id>/options", methods=["PUT"])
@require_lan_token
def set_options(order_id):
    """
    Sets printer, profile, copies, orientation, and page range, then
    server-side-recalculates total pages/quantity/price. The client's
    own displayed numbers are never trusted for billing.
    """
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return error_response("Order not found.", 404, "ORDER_NOT_FOUND")

    body = request.get_json(silent=True) or {}
    station_id = body.get("station_id")
    computer_name = (body.get("computer_name") or "").strip()[:255]
    printer_id = body.get("printer_id")  # legacy Admin-printer route
    printer_name = (body.get("printer_name") or "").strip()
    profile_id = body.get("profile_id")
    if station_id and station_id != order["station_id"]:
        return error_response("Order belongs to a different User station.", 403, "STATION_MISMATCH")
    if not printer_name and printer_id is None:
        return error_response("A local User-PC printer is required.", 400, "MISSING_PRINTER")
    copies = int(body.get("copies", 1))
    orientation = body.get("orientation", "AUTO")
    page_range = body.get("page_range", "")
    print_settings = body.get("print_settings")

    if copies < 1:
        return error_response("Copies must be at least 1.", 400, "INVALID_COPIES")
    if orientation not in ("PORTRAIT", "LANDSCAPE", "AUTO"):
        return error_response("Orientation must be PORTRAIT, LANDSCAPE, or AUTO.", 400, "INVALID_ORIENTATION")

    profile = conn.execute("SELECT * FROM print_profiles WHERE id = ? AND is_enabled = 1", (profile_id,)).fetchone()
    if not profile:
        return error_response("Selected print profile is invalid or disabled.", 400, "INVALID_PROFILE")

    # Print types flagged "ask_properties" (e.g. glossy photo paper) must carry the
    # customer's print properties BEFORE the price/PAY step - that is what lets the
    # Admin PC print automatically (with no dialog) once payment is verified.
    settings_json = None
    if print_settings is not None:
        if not isinstance(print_settings, dict):
            return error_response("print_settings must be an object.", 400, "INVALID_PRINT_SETTINGS")
        clean = {}
        for key in ("paper_size", "quality", "paper_type"):
            val = print_settings.get(key)
            if val is not None:
                val = str(val).strip()
                if len(val) > 80:
                    return error_response("A print property value is too long.", 400, "INVALID_PRINT_SETTINGS")
                clean[key] = val
        devmode = print_settings.get("devmode_b64")
        if devmode:
            from shared import winprint
            try:
                winprint.decode(str(devmode))          # size + structure sanity check
            except winprint.WinPrintError as e:
                return error_response(str(e), 400, "INVALID_PRINT_SETTINGS")
            clean["devmode_b64"] = str(devmode)
        if clean:
            settings_json = _json_dumps(clean)
    if profile["ask_properties"] and not settings_json:
        return error_response(
            f"Please set the print properties for '{profile['display_name']}' before calculating the price.",
            400, "PRINT_PROPERTIES_REQUIRED",
        )

    # New architecture: the selected printer belongs to the originating User PC.
    # We intentionally do not look it up in the Admin PC's printer table.
    remote_printer_id = conn.execute(
        "SELECT id FROM printers WHERE system_name = '__USER_PC_REMOTE_PRINTER__'"
    ).fetchone()
    if printer_name:
        if len(printer_name) > 255:
            return error_response("Printer name is too long.", 400, "INVALID_PRINTER")
        if remote_printer_id is None:
            return error_response("Remote printer routing is not initialized.", 500, "REMOTE_ROUTING_NOT_READY")
        printer_id = remote_printer_id["id"]
    else:
        printer = conn.execute("SELECT * FROM printers WHERE id = ? AND is_enabled = 1", (printer_id,)).fetchone()
        if not printer:
            return error_response("Selected printer is invalid or disabled.", 400, "INVALID_PRINTER")

    files = conn.execute(
        "SELECT id, extension, page_count FROM order_files WHERE order_id = ? AND status = 'READY' ORDER BY sequence_index",
        (order_id,),
    ).fetchall()
    if not files:
        return error_response("Order has no ready files.", 400, "NO_FILES")

    import json as _json
    supported_formats = set(_json.loads(profile["supported_formats_json"]))
    for f in files:
        if f["extension"] not in supported_formats:
            return error_response(
                f"File type .{f['extension']} is not supported by profile '{profile['display_name']}'.",
                422, "UNSUPPORTED_FORMAT_FOR_PROFILE",
            )

    # Page range only makes sense against a single-document total; when
    # multiple files are present it's applied per-file identically if
    # provided, otherwise all pages of all files are used.
    total_pages = 0
    for f in files:
        if page_range:
            try:
                selected = parse_page_range(page_range, f["page_count"])
            except ApiError as e:
                return error_response(e.message, e.status_code, e.code)
            total_pages += len(selected)
        else:
            total_pages += f["page_count"]

    total_quantity = total_pages * copies
    unit, total_price = compute_total(profile["price_per_page"], get_tiers(conn, profile["id"]), total_quantity)

    with transaction() as conn:
        conn.execute(
            """UPDATE orders
               SET printer_id = ?, printer_name = ?, computer_name = COALESCE(?, computer_name), station_host = ?, station_port = 8767,
                   profile_id = ?, copies = ?, orientation = ?,
                   page_range = ?, total_pages = ?, total_quantity = ?,
                   total_price = ?, print_settings_json = ?,
                   status = 'WAITING_PAYMENT', updated_at = ?
               WHERE id = ?""",
            (printer_id, printer_name or None, computer_name or None, request.remote_addr, profile_id, copies, orientation, page_range,
             total_pages, total_quantity, total_price, settings_json,
             datetime.utcnow().isoformat(), order_id),
        )

    return jsonify({
        "total_pages": total_pages,
        "total_quantity": total_quantity,
        "total_price": total_price,
        "unit_price": unit,
        "status": "WAITING_PAYMENT",
    }), 200


@bp.route("/<order_id>", methods=["GET"])
@require_lan_token
def get_order(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return error_response("Order not found.", 404, "ORDER_NOT_FOUND")
    return jsonify(dict(order)), 200


@bp.route("/<order_id>", methods=["DELETE"])
@require_lan_token
def cancel_order(order_id):
    """
    Backs the 'NEW CUSTOMER' reset for the current unsubmitted session.
    Only allowed while the order has not yet been paid, so a customer
    can never cancel an order that's already verified/printing
    (requirement 16).
    """
    conn = get_conn()
    order = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return jsonify({"status": "ok"}), 200  # already gone; NEW CUSTOMER must not error
    if order["status"] in ("PAID", "QUEUED", "PRINTING", "PRINTED"):
        return error_response("Cannot cancel an order that has already been paid or printed.", 409, "CANNOT_CANCEL")

    from admin.server.file_storage import delete_order_files
    with transaction() as conn:
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    delete_order_files(order_id)
    return jsonify({"status": "cancelled"}), 200
