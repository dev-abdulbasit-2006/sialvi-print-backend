"""Payment confirmation, cash/online tracking and receipt history."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request
from admin.database.db import get_conn, transaction
from admin.server.helpers import error_response, require_lan_token, require_admin_token
from admin.printer.printer_manager import printer_manager

logger = logging.getLogger("routes_payments")
bp = Blueprint("payments", __name__, url_prefix="/api")


def _next_receipt_no(conn):
    row = conn.execute("SELECT COALESCE(MAX(receipt_no), 0) + 1 AS n FROM payments WHERE receipt_no IS NOT NULL").fetchone()
    return int(row["n"])


@bp.route("/orders/<order_id>/pay", methods=["POST"])
@require_lan_token
def request_payment(order_id):
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return error_response("Order not found.", 404, "ORDER_NOT_FOUND")
    if order["status"] != "WAITING_PAYMENT":
        return error_response(f"Order must have printer/profile/options set before payment (current status: {order['status']}).", 409, "INVALID_ORDER_STATE")
    existing = conn.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
    if existing:
        if existing["status"] != "REJECTED":
            return error_response("A payment request already exists for this order.", 409, "PAYMENT_ALREADY_EXISTS")

        # The schema intentionally keeps one payment row per order. Re-open a
        # rejected attempt instead of inserting a second row and hitting the
        # UNIQUE(order_id) constraint. A rejection is not a completed sale.
        payment_id = existing["id"]
        with transaction() as conn:
            conn.execute(
                """UPDATE payments
                   SET amount = ?, payment_method = NULL, receipt_no = NULL,
                       status = 'PENDING', requested_at = datetime('now'),
                       verified_at = NULL, verified_by = NULL, notes = NULL
                   WHERE id = ?""",
                (order["total_price"], payment_id),
            )
            conn.execute(
                "UPDATE orders SET payment_status = 'PENDING', updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), order_id),
            )
        return jsonify({
            "payment_id": payment_id, "order_id": order_id,
            "amount": order["total_price"], "status": "PENDING"
        }), 200

    payment_id = str(uuid.uuid4())
    with transaction() as conn:
        conn.execute(
            "INSERT INTO payments (id, order_id, amount, status) VALUES (?, ?, ?, 'PENDING')",
            (payment_id, order_id, order["total_price"]),
        )
        conn.execute(
            "UPDATE orders SET payment_status = 'PENDING', updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), order_id),
        )
    return jsonify({"payment_id": payment_id, "order_id": order_id, "amount": order["total_price"], "status": "PENDING"}), 201


@bp.route("/admin/payments/pending", methods=["GET"])
@require_admin_token
def list_pending_payments():
    conn = get_conn()
    rows = conn.execute("""SELECT p.id AS payment_id, p.order_id, p.amount, p.requested_at,
                  o.station_id, COALESCE(o.computer_name, o.station_id) AS computer_name,
                  o.total_pages, o.total_quantity, o.copies,
                  COALESCE(o.printer_name, pr.display_name) AS printer_name,
                  pp.display_name AS printing_type,
                  GROUP_CONCAT(of.original_filename, ', ') AS file_names
           FROM payments p
           JOIN orders o ON o.id = p.order_id
           LEFT JOIN printers pr ON pr.id = o.printer_id
           LEFT JOIN print_profiles pp ON pp.id = o.profile_id
           LEFT JOIN order_files of ON of.order_id = o.id
           WHERE p.status = 'PENDING'
           GROUP BY p.id
           ORDER BY p.requested_at ASC""").fetchall()
    return jsonify([dict(r) for r in rows]), 200


@bp.route("/admin/payments/<payment_id>/verify", methods=["POST"])
@require_admin_token
def verify_payment(payment_id):
    body = request.get_json(silent=True) or {}
    admin_user = body.get("admin_username", "admin")
    payment_method = str(body.get("payment_method", "")).upper().strip()
    if payment_method not in ("CASH", "ONLINE"):
        return error_response("Payment method must be CASH or ONLINE.", 400, "INVALID_PAYMENT_METHOD")

    conn = get_conn()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        return error_response("Payment not found.", 404, "PAYMENT_NOT_FOUND")
    if payment["status"] != "PENDING":
        return error_response(f"Payment already {payment['status'].lower()}.", 409, "PAYMENT_NOT_PENDING")
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (payment["order_id"],)).fetchone()
    if not order:
        return error_response("Associated order not found.", 404, "ORDER_NOT_FOUND")

    is_remote = bool(order["printer_name"] and order["station_host"])
    if not is_remote:
        printer = conn.execute("SELECT * FROM printers WHERE id = ?", (order["printer_id"],)).fetchone()
        if not printer:
            return error_response("Order's printer no longer exists.", 409, "PRINTER_MISSING")
        profile = conn.execute("SELECT * FROM print_profiles WHERE id = ?", (order["profile_id"],)).fetchone()
        ok, reason = printer_manager.supports_profile(printer["system_name"], profile["paper_size"], profile["color_mode"])
        if not ok:
            return error_response(f"Cannot release print job: {reason}", 409, "PRINTER_UNAVAILABLE")
    elif not order["station_id"] or not order["station_port"]:
        return error_response("Originating User print station is not registered.", 409, "STATION_UNAVAILABLE")

    files = conn.execute("SELECT id FROM order_files WHERE order_id = ? AND status = 'READY' ORDER BY sequence_index", (order["id"],)).fetchall()
    if not files:
        return error_response("Order has no ready files to print.", 409, "NO_FILES")

    now = datetime.utcnow().isoformat()
    with transaction() as conn:
        receipt_no = _next_receipt_no(conn)
        conn.execute("UPDATE payments SET status = 'VERIFIED', payment_method = ?, receipt_no = ?, verified_at = ?, verified_by = ? WHERE id = ?", (payment_method, receipt_no, now, admin_user, payment_id))
        conn.execute("UPDATE orders SET payment_status = 'PAID', status = 'QUEUED', verified_by = ?, verified_at = ?, updated_at = ? WHERE id = ?", (admin_user, now, now, order["id"]))
        for f in files:
            conn.execute("INSERT INTO print_jobs (id, order_id, order_file_id, printer_id, status) VALUES (?, ?, ?, ?, 'QUEUED')", (str(uuid.uuid4()), order["id"], f["id"], order["printer_id"]))
        conn.execute("INSERT INTO audit_log (event_type, actor, order_id, details_json) VALUES ('PAYMENT_VERIFIED', ?, ?, ?)", (admin_user, order["id"], f'{{"payment_id":"{payment_id}","amount":{order["total_price"]},"payment_method":"{payment_method}","receipt_no":{receipt_no}}}'))
    return jsonify({"status": "PAID", "order_id": order["id"], "print_jobs_created": len(files), "payment_method": payment_method, "receipt_no": receipt_no}), 200


@bp.route("/admin/payments/<payment_id>/reject", methods=["POST"])
@require_admin_token
def reject_payment(payment_id):
    conn = get_conn()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        return error_response("Payment not found.", 404, "PAYMENT_NOT_FOUND")
    if payment["status"] != "PENDING":
        return error_response(f"Payment already {payment['status'].lower()}.", 409, "PAYMENT_NOT_PENDING")
    body = request.get_json(silent=True) or {}
    with transaction() as conn:
        conn.execute("UPDATE payments SET status = 'REJECTED', notes = ? WHERE id = ?", (body.get("reason", ""), payment_id))
        conn.execute("UPDATE orders SET payment_status = 'UNPAID', updated_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), payment["order_id"]))
    return jsonify({"status": "REJECTED"}), 200


def _period_bounds_utc(period: str):
    """Return (utc_start, utc_end) as UTC-naive 'YYYY-MM-DD HH:MM:SS' strings.

    Boundaries are built from NAIVE local datetimes and converted with
    .astimezone(), which lets Python apply the correct UTC offset for each
    boundary (important around daylight-saving changes). Week = Monday 00:00
    to next Monday 00:00 (same ISO-week convention as the rest of the app).
    Month = 1st 00:00 to the 1st of next month 00:00.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        local_start = today
        local_end = today + timedelta(days=1)
    elif period == "week":
        local_start = today - timedelta(days=today.weekday())
        local_end = local_start + timedelta(days=7)
    else:  # month
        local_start = today.replace(day=1)
        if local_start.month == 12:
            local_end = local_start.replace(year=local_start.year + 1, month=1)
        else:
            local_end = local_start.replace(month=local_start.month + 1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        local_start.astimezone(timezone.utc).strftime(fmt),
        local_end.astimezone(timezone.utc).strftime(fmt),
    )


@bp.route("/admin/receipts", methods=["GET"])
@require_admin_token
def receipt_history():
    period = (request.args.get("period") or "today").lower()
    method = (request.args.get("method") or "").upper().strip()
    if period not in ("today", "week", "month", "all"):
        return error_response("period must be today, week, month, or all.", 400, "INVALID_PERIOD")
    conn = get_conn()
    where = ["p.status = 'VERIFIED'"]
    params = []

    # Receipt History must be independent of the weekly reset/period_key.
    # verified_at is stored by the server as a UTC-naive ISO timestamp, so the
    # local-day/week/month boundaries are converted to UTC-naive values before
    # querying SQLite. This keeps Today / This Week / This Month reliable even
    # after the weekly accounting period has been rotated.
    if period in ("today", "week", "month"):
        utc_start, utc_end = _period_bounds_utc(period)
        # datetime() normalizes both ISO 'T' timestamps and SQLite's
        # 'YYYY-MM-DD HH:MM:SS' timestamps before comparison.
        where.append("datetime(p.verified_at) >= datetime(?)")
        where.append("datetime(p.verified_at) < datetime(?)")
        params.extend((utc_start, utc_end))

    # Empty/ALL means every verified payment method.
    if method in ("CASH", "ONLINE"):
        where.append("UPPER(TRIM(p.payment_method)) = ?")
        params.append(method)
    rows = conn.execute(f"""SELECT p.receipt_no, p.id AS payment_id, p.verified_at, p.amount,
                  p.payment_method, o.id AS order_id,
                  o.total_pages, o.total_quantity, o.copies, o.total_price,
                  GROUP_CONCAT(of.original_filename, ' | ') AS file_name,
                  pp.display_name AS printing_type,
                  COALESCE(o.printer_name, pr.display_name, '—') AS printer_name,
                  COALESCE(o.computer_name, o.station_id, '—') AS computer_name,
                  GROUP_CONCAT(DISTINCT pj.status) AS print_status
           FROM payments p
           JOIN orders o ON o.id = p.order_id
           LEFT JOIN order_files of ON of.order_id = o.id
           LEFT JOIN print_profiles pp ON pp.id = o.profile_id
           LEFT JOIN printers pr ON pr.id = o.printer_id
           LEFT JOIN print_jobs pj ON pj.order_file_id = of.id
           WHERE {' AND '.join(where)}
           GROUP BY p.id
           ORDER BY p.verified_at DESC, p.receipt_no DESC""", params).fetchall()
    return jsonify([dict(r) for r in rows]), 200


@bp.route("/admin/earnings", methods=["GET"])
@require_admin_token
def earnings():
    period = (request.args.get("period") or "today").lower()
    if period not in ("today", "week"):
        return error_response("period must be today or week.", 400, "INVALID_PERIOD")
    conn = get_conn()
    if period == "today":
        row = conn.execute("""SELECT
            COALESCE(SUM(CASE WHEN p.payment_method='CASH' THEN p.amount ELSE 0 END),0) cash,
            COALESCE(SUM(CASE WHEN p.payment_method='ONLINE' THEN p.amount ELSE 0 END),0) online,
            COALESCE(SUM(p.amount),0) total
            FROM payments p WHERE p.status='VERIFIED' AND date(datetime(p.verified_at), 'localtime')=date('now','localtime')""").fetchone()
    else:
        from admin.server.period import current_period_key
        row = conn.execute("""SELECT
            COALESCE(SUM(CASE WHEN p.payment_method='CASH' THEN p.amount ELSE 0 END),0) cash,
            COALESCE(SUM(CASE WHEN p.payment_method='ONLINE' THEN p.amount ELSE 0 END),0) online,
            COALESCE(SUM(p.amount),0) total
            FROM payments p JOIN orders o ON o.id=p.order_id
            WHERE p.status='VERIFIED' AND o.period_key=?""", (current_period_key(),)).fetchone()
    return jsonify(dict(row)), 200
