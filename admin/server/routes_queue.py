"""
Queue visibility, admin dashboard, and weekly reset routes.
"""

import logging

from flask import Blueprint, jsonify, request

from admin.database.db import get_conn, transaction
from admin.server.helpers import error_response, require_lan_token, require_admin_token
from admin.server.period import current_period_key, ensure_active_period, get_current_summary

logger = logging.getLogger("routes_queue")
bp = Blueprint("queue", __name__, url_prefix="/api")


@bp.route("/orders/<order_id>/queue-status", methods=["GET"])
@require_lan_token
def order_queue_status(order_id):
    """Lets a User PC poll the live status of its own order's print jobs."""
    conn = get_conn()
    order = conn.execute("SELECT id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return error_response("Order not found.", 404, "ORDER_NOT_FOUND")
    jobs = conn.execute(
        """SELECT pj.id, pj.status, pj.error_message, of.original_filename
           FROM print_jobs pj JOIN order_files of ON of.id = pj.order_file_id
           WHERE pj.order_id = ?""",
        (order_id,),
    ).fetchall()
    return jsonify({"order_status": order["status"], "jobs": [dict(j) for j in jobs]}), 200


@bp.route("/admin/queue", methods=["GET"])
@require_admin_token
def admin_queue_view():
    conn = get_conn()
    view = (request.args.get("view") or "active").lower()
    if view not in ("active", "completed", "all"):
        return error_response("view must be active, completed, or all.", 400, "INVALID_QUEUE_VIEW")

    base = """SELECT pj.id, pj.status, pj.attempts, pj.error_message, pj.created_at, pj.completed_at,
                  o.id AS order_id, o.station_id, COALESCE(o.computer_name, o.station_id) AS computer_name,
                  COALESCE(o.printer_name, p.display_name) AS printer_name,
                  pp.display_name AS printing_type, o.total_pages, o.copies,
                  of.original_filename
           FROM print_jobs pj
           JOIN orders o ON o.id = pj.order_id
           LEFT JOIN printers p ON p.id = pj.printer_id
           LEFT JOIN print_profiles pp ON pp.id = o.profile_id
           JOIN order_files of ON of.id = pj.order_file_id"""
    if view == "active":
        where = "WHERE pj.status IN ('QUEUED','PRINTING','FAILED','PRINT_ERROR')"
        order = "ORDER BY CASE pj.status WHEN 'PRINTING' THEN 0 WHEN 'QUEUED' THEN 1 ELSE 2 END, pj.created_at ASC"
    elif view == "completed":
        where = "WHERE pj.status = 'PRINTED'"
        order = "ORDER BY pj.completed_at DESC, pj.created_at DESC LIMIT 50"
    else:
        where = "WHERE pj.status IN ('QUEUED','PRINTING','FAILED','PRINT_ERROR','PRINTED')"
        order = "ORDER BY CASE pj.status WHEN 'PRINTING' THEN 0 WHEN 'QUEUED' THEN 1 WHEN 'FAILED' THEN 2 WHEN 'PRINT_ERROR' THEN 2 ELSE 3 END, COALESCE(pj.completed_at, pj.created_at) DESC LIMIT 100"
    rows = conn.execute(f"{base} {where} {order}").fetchall()
    return jsonify([dict(r) for r in rows]), 200


@bp.route("/admin/dashboard", methods=["GET"])
@require_admin_token
def admin_dashboard():
    conn = get_conn()
    summary = get_current_summary()

    pending_payments = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status = 'PENDING'").fetchone()["c"]
    queue_depth = conn.execute("SELECT COUNT(*) AS c FROM print_jobs WHERE status IN ('QUEUED','PRINTING')").fetchone()["c"]
    failed_jobs = conn.execute("SELECT COUNT(*) AS c FROM print_jobs WHERE status = 'FAILED'").fetchone()["c"]
    active_stations = conn.execute(
        "SELECT COUNT(DISTINCT station_id) AS c FROM orders WHERE datetime(updated_at) > datetime('now', '-10 minutes')"
    ).fetchone()["c"]
    printers_available = conn.execute("SELECT COUNT(*) AS c FROM printers WHERE is_enabled = 1").fetchone()["c"]

    today_breakdown = conn.execute("""SELECT
        COALESCE(SUM(CASE WHEN payment_method='CASH' THEN amount ELSE 0 END),0) AS cash,
        COALESCE(SUM(CASE WHEN payment_method='ONLINE' THEN amount ELSE 0 END),0) AS online
        FROM payments WHERE status='VERIFIED' AND date(datetime(verified_at), 'localtime')=date('now','localtime')""").fetchone()

    yesterday = conn.execute("""SELECT
        COALESCE(SUM(p.amount),0) AS earnings,
        COUNT(*) AS orders
        FROM payments p
        WHERE p.status='VERIFIED'
          AND date(datetime(p.verified_at),'localtime')=date('now','localtime','-1 day')""").fetchone()
    previous_week = conn.execute("""SELECT
        COALESCE(SUM(p.amount),0) AS earnings
        FROM payments p
        WHERE p.status='VERIFIED'
          AND date(datetime(p.verified_at),'localtime') >= date('now','localtime','-14 days')
          AND date(datetime(p.verified_at),'localtime') < date('now','localtime','-7 days')""").fetchone()

    recent_orders = conn.execute("""SELECT
        o.id AS order_id, o.station_id, COALESCE(o.computer_name,o.station_id) AS computer_name,
        pp.display_name AS printing_type, o.total_pages, o.total_quantity, o.total_price,
        o.status AS order_status, o.payment_status, p.payment_method, p.receipt_no,
        p.verified_at, o.printer_name,
        CASE
          WHEN o.status = 'PRINTED' THEN 'done'
          WHEN o.payment_status = 'PENDING' OR o.status = 'WAITING_PAYMENT' THEN 'pending'
          WHEN o.status IN ('QUEUED','PRINTING') THEN 'pending'
          WHEN o.status = 'FAILED' THEN 'failed'
          ELSE 'awaiting'
        END AS badge_status
        FROM orders o
        LEFT JOIN print_profiles pp ON pp.id=o.profile_id
        LEFT JOIN payments p ON p.order_id=o.id AND p.status='VERIFIED'
        WHERE date(datetime(o.created_at), 'localtime')=date('now','localtime')
        ORDER BY o.created_at DESC LIMIT 8""").fetchall()
    week_breakdown = conn.execute("""SELECT
        COALESCE(SUM(CASE WHEN p.payment_method='CASH' THEN p.amount ELSE 0 END),0) AS cash,
        COALESCE(SUM(CASE WHEN p.payment_method='ONLINE' THEN p.amount ELSE 0 END),0) AS online
        FROM payments p JOIN orders o ON o.id=p.order_id
        WHERE p.status='VERIFIED' AND o.period_key=?""", (current_period_key(),)).fetchone()

    return jsonify({
        "period_key": current_period_key(),
        "today_earnings": summary["today_earnings"],
        "week_earnings": summary["week_earnings"],
        "today_cash": today_breakdown["cash"],
        "today_online": today_breakdown["online"],
        "week_cash": week_breakdown["cash"],
        "week_online": week_breakdown["online"],
        "today_orders": summary["today_orders"],
        "week_orders": summary["week_orders"],
        "pending_payments": pending_payments,
        "queue_depth": queue_depth,
        "failed_jobs": failed_jobs,
        "active_stations": active_stations,
        "printers_available": printers_available,
        "today_change_pct": _pct_change(float(yesterday["earnings"]), float(summary["today_earnings"])),
        "today_orders_change_pct": _pct_change(float(yesterday["orders"]), float(summary["today_orders"])),
        "week_change_pct": _pct_change(float(previous_week["earnings"]), float(summary["week_earnings"])),
        "recent_orders": [dict(r) for r in recent_orders],
    }), 200


def _pct_change(previous, current):
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return ((current - previous) / previous) * 100.0



@bp.route("/admin/queue/status", methods=["GET"])
@require_admin_token
def admin_queue_status():
    """Live worker/queue health for the Admin Queue screen."""
    from admin.queue.queue_manager import queue_manager
    conn = get_conn()
    counts = conn.execute(
        """SELECT status, COUNT(*) AS c FROM print_jobs
           WHERE status IN ('QUEUED','PRINTING','FAILED','PRINT_ERROR')
           GROUP BY status"""
    ).fetchall()
    result = {"QUEUED": 0, "PRINTING": 0, "FAILED": 0, "PRINT_ERROR": 0}
    for row in counts:
        result[row["status"]] = int(row["c"])
    return jsonify({"worker_running": queue_manager.is_running(), "counts": result}), 200



@bp.route("/admin/queue/<job_id>/cancel", methods=["POST"])
@require_admin_token
def cancel_queue_job(job_id):
    conn = get_conn()
    job = conn.execute("SELECT id, status, order_id FROM print_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return error_response("Print job not found.", 404, "JOB_NOT_FOUND")
    if job["status"] not in ("QUEUED",):
        return error_response("Only queued jobs can be cancelled.", 409, "JOB_NOT_CANCELLABLE")
    from datetime import datetime
    with transaction() as conn:
        conn.execute("UPDATE print_jobs SET status='CANCELLED', error_message=NULL WHERE id=?", (job_id,))
        # Keep order state consistent with the remaining jobs.
        statuses = [r["status"] for r in conn.execute("SELECT status FROM print_jobs WHERE order_id=?", (job["order_id"],)).fetchall()]
        if statuses and all(st in ("PRINTED", "CANCELLED") for st in statuses):
            new_status = "PRINTED" if any(st == "PRINTED" for st in statuses) else "CANCELLED"
        elif any(st in ("FAILED","PRINT_ERROR") for st in statuses):
            new_status = "FAILED"
        elif any(st == "PRINTING" for st in statuses):
            new_status = "PRINTING"
        else:
            new_status = "QUEUED"
        conn.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?",
                     (new_status, datetime.utcnow().isoformat(), job["order_id"]))
    return jsonify({"status":"CANCELLED","job_id":job_id}), 200


@bp.route("/admin/queue/<job_id>/retry", methods=["POST"])
@require_admin_token
def retry_queue_job(job_id):
    conn = get_conn()
    job = conn.execute("SELECT id, status FROM print_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return error_response("Print job not found.", 404, "JOB_NOT_FOUND")
    if job["status"] not in ("FAILED", "PRINT_ERROR"):
        return error_response("Only failed jobs can be retried.", 409, "JOB_NOT_RETRYABLE")
    from datetime import datetime
    with transaction() as conn:
        conn.execute("""UPDATE print_jobs
                        SET status='QUEUED', attempts=0, error_message=NULL,
                            submitted_at=NULL, completed_at=NULL
                        WHERE id=?""", (job_id,))
        order_id = conn.execute("SELECT order_id FROM print_jobs WHERE id=?", (job_id,)).fetchone()["order_id"]
        conn.execute("UPDATE orders SET status='QUEUED', updated_at=? WHERE id=?",
                     (datetime.utcnow().isoformat(), order_id))
    return jsonify({"status":"QUEUED","job_id":job_id}), 200

@bp.route("/admin/weekly-reset/check", methods=["POST"])
@require_admin_token
def trigger_weekly_reset_check():
    """
    Manually invokes the idempotent weekly-period check (also runs
    automatically on server startup and can be scheduled via Windows
    Task Scheduler for machines left running across a week boundary).
    """
    ensure_active_period()
    return jsonify({"status": "checked", "period_key": current_period_key()}), 200
