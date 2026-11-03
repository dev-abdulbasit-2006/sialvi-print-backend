"""
Print queue worker.

Runs on a background thread inside the Admin process. Polls the
database (not an in-memory structure) for print_jobs in QUEUED state,
submits them to the real printer, and updates status transactionally.

Because state lives in SQLite rather than memory, an Admin restart
recovers cleanly: any job still QUEUED just gets picked up again;
any job stuck in PRINTING from before a crash is treated as unknown
and flagged for manual admin review rather than silently re-printed
(prevents double-printing per requirement section 30).
"""

import json
import logging
import threading
import time
from datetime import datetime

from admin.database.db import get_conn, transaction
from admin.printer.printer_manager import printer_manager, PrinterError

logger = logging.getLogger("queue_manager")

POLL_INTERVAL_SECONDS = 2
MAX_ATTEMPTS = 3


class QueueManager:
    def __init__(self):
        self._station_wait = {}
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        # On startup, any job left in PRINTING from a previous crash is
        # ambiguous (could have printed, could not have) - never silently
        # retry it. Flag for admin review instead.
        self._recover_ambiguous_jobs()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Queue manager started.")

    def is_running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _recover_ambiguous_jobs(self):
        conn = get_conn()
        stuck = conn.execute(
            "SELECT id FROM print_jobs WHERE status = 'PRINTING'"
        ).fetchall()
        if not stuck:
            return
        with transaction() as conn:
            for row in stuck:
                conn.execute(
                    """UPDATE print_jobs
                       SET status = 'PRINT_ERROR',
                           error_message = 'Interrupted by Admin restart - needs manual verification'
                       WHERE id = ?""",
                    (row["id"],),
                )
                self._sync_order_status(conn, row["id"])
        logger.warning("Recovered %d job(s) left PRINTING after a crash/restart; "
                        "flagged PRINT_ERROR for manual review.", len(stuck))

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._process_next_batch()
            except Exception:
                logger.exception("Unhandled error in queue processing loop.")
            self._stop_event.wait(POLL_INTERVAL_SECONDS)

    def _process_next_batch(self):
        # License gate: without Owner authorization the queue PAUSES. Jobs stay QUEUED untouched
        # (nothing is failed or deleted) and are printed once authorization returns.
        from shared.license_gate import license_ok
        if not license_ok():
            return
        conn = get_conn()
        jobs = conn.execute(
            """SELECT pj.id, pj.order_file_id, pj.printer_id, pj.attempts,
                      of.stored_path,
                      o.station_id, o.station_key, o.station_host, o.station_port,
                      o.printer_name, p.system_name AS legacy_printer_name
               FROM print_jobs pj
               JOIN order_files of ON of.id = pj.order_file_id
               JOIN orders o ON o.id = pj.order_id
               LEFT JOIN printers p ON p.id = pj.printer_id
               WHERE pj.status = 'QUEUED'
               ORDER BY pj.created_at ASC
               LIMIT 5"""
        ).fetchall()

        now = time.monotonic()
        for job in jobs:
            # A User PC whose own license is inactive refuses jobs (LICENSE_REQUIRED). Do not re-upload
            # the file every 2 s: wait a minute before trying that station again.
            if self._station_wait.get(job["station_id"], 0) > now:
                continue
            self._process_job(job)

    def _process_job(self, job):
        job_id = job["id"]

        # Claim the job atomically so a second worker/thread (or a
        # future multi-process deployment) can never double-print it.
        with transaction() as conn:
            claimed = conn.execute(
                "SELECT status FROM print_jobs WHERE id = ? AND status = 'QUEUED'",
                (job_id,),
            ).fetchone()
            if not claimed:
                return  # already claimed elsewhere
            conn.execute(
                """UPDATE print_jobs
                   SET status = 'PRINTING', submitted_at = ?, attempts = attempts + 1
                   WHERE id = ?""",
                (datetime.utcnow().isoformat(), job_id),
            )

        try:
            # Fetch order-level settings needed for submission
            conn = get_conn()
            order_row = conn.execute(
                """SELECT o.copies, o.orientation, o.page_range, o.print_settings_json,
                          pr.paper_size AS profile_paper, pr.color_mode AS profile_color
                   FROM print_jobs pj
                   JOIN orders o ON o.id = pj.order_id
                   LEFT JOIN print_profiles pr ON pr.id = o.profile_id
                   WHERE pj.id = ?""",
                (job_id,),
            ).fetchone()

            print_settings = json.loads(order_row["print_settings_json"]) if order_row and order_row["print_settings_json"] else None
            if job["station_host"] and job["printer_name"]:
                windows_job_id = printer_manager.submit_remote_file(
                    station_key=job["station_key"],
                    station_host=job["station_host"],
                    station_port=job["station_port"] or 8767,
                    station_id=job["station_id"],
                    printer_name=job["printer_name"],
                    file_path=job["stored_path"],
                    job_id=job["id"],
                    copies=order_row["copies"] if order_row else 1,
                    orientation=order_row["orientation"] if order_row else "AUTO",
                    print_settings=print_settings,
                    page_range=(order_row["page_range"] or "") if order_row else "",
                    profile_paper=(order_row["profile_paper"] or "") if order_row else "",
                    color_mode=(order_row["profile_color"] or "") if order_row else "",
                )
            else:
                # Legacy orders created before the User-PC routing migration.
                windows_job_id = printer_manager.submit_file(
                    printer_name=job["legacy_printer_name"],
                    file_path=job["stored_path"],
                    copies=order_row["copies"] if order_row else 1,
                    orientation=order_row["orientation"] if order_row else "AUTO",
                    print_settings=print_settings,
                    page_range=(order_row["page_range"] or "") if order_row else "",
                    profile_paper=(order_row["profile_paper"] or "") if order_row else "",
                    color_mode=(order_row["profile_color"] or "") if order_row else "",
                )

            with transaction() as conn:
                conn.execute(
                    """UPDATE print_jobs
                       SET status = 'PRINTED', windows_job_id = ?, completed_at = ?
                       WHERE id = ?""",
                    (windows_job_id, datetime.utcnow().isoformat(), job_id),
                )
                self._sync_order_status(conn, job_id)

        except PrinterError as e:
            if "LICENSE_REQUIRED" in str(e):
                self._requeue_license_blocked(job, str(e))
            else:
                self._handle_job_failure(job_id, str(e))
        except Exception as e:
            logger.exception("Unexpected error submitting print job %s", job_id)
            self._handle_job_failure(job_id, f"Unexpected error: {e}")

    def _requeue_license_blocked(self, job, error_message):
        """The target User PC is not licensed right now: put the job back WITHOUT using up a retry."""
        job_id = job["id"]
        self._station_wait[job["station_id"]] = time.monotonic() + 60
        logger.warning("Job %s waiting: User PC %s is not licensed.", job_id, job["station_id"])
        with transaction() as conn:
            conn.execute(
                """UPDATE print_jobs
                   SET status = 'QUEUED', attempts = MAX(attempts - 1, 0), error_message = ?
                   WHERE id = ?""",
                (error_message, job_id),
            )
            self._sync_order_status(conn, job_id)

    def _handle_job_failure(self, job_id, error_message):
        conn = get_conn()
        attempts_row = conn.execute(
            "SELECT attempts FROM print_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        attempts = attempts_row["attempts"] if attempts_row else MAX_ATTEMPTS

        with transaction() as conn:
            if attempts >= MAX_ATTEMPTS:
                conn.execute(
                    """UPDATE print_jobs
                       SET status = 'FAILED', error_message = ?
                       WHERE id = ?""",
                    (error_message, job_id),
                )
            else:
                # Return to QUEUED for a bounded number of automatic retries
                # (e.g. transient "printer busy" conditions).
                conn.execute(
                    """UPDATE print_jobs
                       SET status = 'QUEUED', error_message = ?
                       WHERE id = ?""",
                    (error_message, job_id),
                )
            self._sync_order_status(conn, job_id)
        logger.error("Print job %s failed: %s", job_id, error_message)

    def _sync_order_status(self, conn, job_id):
        """
        Keeps orders.status consistent with the state of all its
        print_jobs (requirement 31: database consistency). Called
        inside the same transaction as the job status update.
        """
        order_id_row = conn.execute(
            "SELECT order_id FROM print_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not order_id_row:
            return
        order_id = order_id_row["order_id"]

        job_statuses = [
            r["status"] for r in conn.execute(
                "SELECT status FROM print_jobs WHERE order_id = ?", (order_id,)
            ).fetchall()
        ]
        if not job_statuses:
            return

        if all(s == "PRINTED" for s in job_statuses):
            new_status = "PRINTED"
        elif any(s in ("FAILED", "PRINT_ERROR") for s in job_statuses):
            # PRINT_ERROR is an intentionally non-retryable/manual-review state
            # after an Admin restart. The customer order must not remain stuck
            # showing queued/printing while the job waits for operator review.
            new_status = "FAILED"
        elif any(s == "PRINTING" for s in job_statuses):
            new_status = "PRINTING"
        else:
            new_status = "QUEUED"

        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.utcnow().isoformat(), order_id),
        )


queue_manager = QueueManager()
