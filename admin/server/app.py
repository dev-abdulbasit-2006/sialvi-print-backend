"""
Admin server entry point.

Startup sequence (order matters):
  1. Configure logging
  2. Initialize database (creates tables if missing, seeds profiles)
  3. Ensure a weekly period is active (safe/idempotent)
  4. Start the background print queue worker thread
  5. Start Flask serving on the configured LAN interface/port

Run directly with `python -m admin.server.app` during development.
In production this is invoked by the Admin desktop application (Phase 2)
or run as a Windows service via the packaged executable.
"""

import logging
import traceback

from flask import Flask, jsonify, request

from admin.config.config import config
from admin.database.db import init_db
from admin.server.helpers import setup_logging, ApiError
from admin.server.period import ensure_active_period
from admin.queue.queue_manager import queue_manager

logger = logging.getLogger("app")


import uuid as _uuid
INSTANCE_ID = _uuid.uuid4().hex   # unique per Admin process; lets run_admin tell its own server from a stale one


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = None  # streaming upload enforces its own limit; do not double-buffer

    from admin.server.routes_orders import bp as orders_bp
    from admin.server.routes_payments import bp as payments_bp
    from admin.server.routes_printers import bp as printers_bp
    from admin.server.routes_profiles import bp as profiles_bp
    from admin.server.routes_queue import bp as queue_bp
    from admin.server.routes_stations import bp as stations_bp

    app.register_blueprint(orders_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(printers_bp)
    app.register_blueprint(profiles_bp)
    app.register_blueprint(queue_bp)
    app.register_blueprint(stations_bp)

    # ------------------------------------------------------------
    # License enforcement (server side of the Admin PC): while this Admin is not authorized by the
    # Owner's licensing service, every API call except /api/health is refused. Nothing is deleted.
    # ------------------------------------------------------------
    from shared.license_gate import license_ok, license_message

    @app.before_request
    def _require_license():
        path = request.path
        if path == "/api/health" or not path.startswith("/api/"):
            return None
        if not license_ok():
            return jsonify({"error": {"code": "LICENSE_REQUIRED", "message": license_message()}}), 403
        return None

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "app": "sialviprint-admin", "instance": INSTANCE_ID}), 200

    # ------------------------------------------------------------
    # Global error handling (requirement 23): no raw traceback ever
    # reaches a client. Full detail goes to the log file only.
    # ------------------------------------------------------------
    @app.errorhandler(ApiError)
    def handle_api_error(e: ApiError):
        return jsonify({"error": {"code": e.code, "message": e.message}}), e.status_code

    @app.errorhandler(404)
    def handle_404(e):
        return jsonify({"error": {"code": "NOT_FOUND", "message": "The requested resource was not found."}}), 404

    @app.errorhandler(413)
    def handle_413(e):
        return jsonify({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Uploaded file exceeds the allowed size."}}), 413

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        logger.error("Unhandled exception: %s\n%s", e, traceback.format_exc())
        detail = ""
        if request.path.startswith("/api/admin/"):
            # Admin screens only (never customers): show the technical cause to speed up support
            detail = f" [{type(e).__name__}: {str(e)[:300]}]"
        return jsonify({
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred on the Admin server. "
                            "This has been logged; please try again or contact support." + detail,
            }
        }), 500

    return app


def main():
    setup_logging()
    logger.info("Starting SialviPrint Admin server...")

    init_db()
    logger.info("Database ready at %s", config.db_path)

    ensure_active_period()

    queue_manager.start()

    app = create_app()
    logger.info("Listening on %s:%s", config.server_host, config.server_port)
    # threaded=True: required so a large file upload from one User PC
    # does not block requests from other User PCs (requirement 17/18).
    app.run(host=config.server_host, port=config.server_port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
