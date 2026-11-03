"""
Weekly period tracking and reset.

Uses a persistent 'period_key' (ISO year-week, e.g. '2026-W38') stored
in the database rather than relying on application memory or wall-clock
comparisons at request time. This makes the reset safe against crashes,
restarts, and duplicate triggering (requirement 3.4):

  - current_period_key() always derives the key from the current date -
    it never depends on any in-memory "last reset" flag.
  - close_and_reset_if_needed() is idempotent: if the active period in
    the database already matches the current key, it does nothing. If
    the machine was off when a week boundary passed, the next call
    (whenever it happens) safely archives the old period and opens the
    new one exactly once.
  - Archiving and opening the new period happen in a single
    transaction, so a crash mid-reset cannot leave the system with no
    active period or with two active periods.
"""

import logging
from datetime import datetime, timezone

from admin.database.db import get_conn, transaction

logger = logging.getLogger("period")


def current_period_key(now: datetime = None) -> str:
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def ensure_active_period():
    """Called on startup. Guarantees exactly one active weekly_periods row."""
    conn = get_conn()
    key = current_period_key()
    active = conn.execute(
        "SELECT id, period_key FROM weekly_periods WHERE is_active = 1"
    ).fetchone()

    if active is None:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO weekly_periods (period_key, started_at, is_active) VALUES (?, ?, 1)",
                (key, datetime.utcnow().isoformat()),
            )
        logger.info("No active period found; opened %s", key)
        return

    if active["period_key"] != key:
        close_and_reset(active["period_key"], key)
    # else: already correct, nothing to do (idempotent)


def close_and_reset(old_key: str, new_key: str):
    """
    Archives totals for old_key into weekly_summaries and opens new_key
    as the new active period. Runs as one atomic transaction so a crash
    partway through cannot lose data or double-archive.
    """
    with transaction() as conn:
        # Guard against duplicate reset: if old_key was already archived
        # (e.g. this function got called twice due to a race), skip the
        # archive step but still ensure new_key becomes active.
        already_archived = conn.execute(
            "SELECT 1 FROM weekly_summaries WHERE period_key = ?", (old_key,)
        ).fetchone()

        if not already_archived:
            totals = conn.execute(
                """SELECT
                       COALESCE(SUM(total_price), 0) AS earnings,
                       COUNT(*) AS orders,
                       COALESCE(SUM(total_pages), 0) AS pages
                   FROM orders
                   WHERE period_key = ? AND payment_status = 'PAID'""",
                (old_key,),
            ).fetchone()

            job_count = conn.execute(
                """SELECT COUNT(*) AS c FROM print_jobs pj
                   JOIN orders o ON o.id = pj.order_id
                   WHERE o.period_key = ? AND pj.status = 'PRINTED'""",
                (old_key,),
            ).fetchone()["c"]

            conn.execute(
                """INSERT INTO weekly_summaries
                   (period_key, total_earnings, total_orders, total_pages, total_print_jobs)
                   VALUES (?, ?, ?, ?, ?)""",
                (old_key, totals["earnings"], totals["orders"], totals["pages"], job_count),
            )

        conn.execute(
            "UPDATE weekly_periods SET is_active = 0, closed_at = ? WHERE period_key = ? AND is_active = 1",
            (datetime.utcnow().isoformat(), old_key),
        )

        # Idempotent: only insert new_key if it doesn't already exist
        # (covers the edge case of two weeks passing between checks).
        exists = conn.execute(
            "SELECT 1 FROM weekly_periods WHERE period_key = ?", (new_key,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO weekly_periods (period_key, started_at, is_active) VALUES (?, ?, 1)",
                (new_key, datetime.utcnow().isoformat()),
            )
        else:
            conn.execute(
                "UPDATE weekly_periods SET is_active = 1 WHERE period_key = ?", (new_key,)
            )

        conn.execute(
            """INSERT INTO audit_log (event_type, actor, details_json)
               VALUES ('WEEKLY_RESET', 'system', ?)""",
            (f'{{"old_period":"{old_key}","new_period":"{new_key}"}}',),
        )

    logger.info("Weekly reset: archived %s, opened %s", old_key, new_key)


def get_current_summary() -> dict:
    """Live (unarchived) totals for the currently active period - powers the earnings dashboard."""
    conn = get_conn()
    key = current_period_key()
    row = conn.execute(
        """SELECT
               COALESCE(SUM(CASE WHEN date(datetime(p.verified_at), 'localtime') = date('now','localtime') THEN p.amount ELSE 0 END), 0) AS today_earnings,
               COALESCE(SUM(p.amount), 0) AS week_earnings,
               COUNT(CASE WHEN date(datetime(p.verified_at), 'localtime') = date('now','localtime') THEN 1 END) AS today_orders,
               COUNT(*) AS week_orders
           FROM payments p JOIN orders o ON o.id = p.order_id
           WHERE o.period_key = ? AND p.status = 'VERIFIED'""",
        (key,),
    ).fetchone()
    return dict(row)
