"""Authenticated User-station registration used for remote printer routing."""
from datetime import datetime
from flask import Blueprint, jsonify, request

from admin.database.db import transaction
from admin.server.helpers import error_response, require_lan_token

bp = Blueprint("stations", __name__, url_prefix="/api")


@bp.route("/stations/register", methods=["POST"])
@require_lan_token
def register_station():
    body = request.get_json(silent=True) or {}
    station_id = (body.get("station_id") or "").strip()
    station_key = (body.get("station_key") or "").strip()
    try:
        port = int(body.get("print_agent_port", 8767))
    except (TypeError, ValueError):
        return error_response("Invalid print-agent port.", 400, "INVALID_AGENT_PORT")
    if not station_id:
        return error_response("station_id is required.", 400, "MISSING_STATION_ID")
    if len(station_key) < 32:
        return error_response("station_key is required.", 400, "MISSING_STATION_KEY")
    if not (1024 <= port <= 65535):
        return error_response("Invalid print-agent port.", 400, "INVALID_AGENT_PORT")
    with transaction() as conn:
        known = conn.execute(
            """SELECT 1 FROM orders
               WHERE station_id = ? AND station_key = ?
               ORDER BY created_at DESC LIMIT 1""",
            (station_id, station_key),
        ).fetchone()
        if not known:
            return error_response("Station authentication failed.", 403, "STATION_AUTH_FAILED")
        conn.execute(
            """UPDATE orders
               SET station_host = ?, station_port = ?, updated_at = ?
               WHERE station_id = ? AND station_key = ?
                 AND status NOT IN ('PRINTED','CANCELLED','FAILED')""",
            (request.remote_addr, port, datetime.utcnow().isoformat(), station_id, station_key),
        )
    return jsonify({
        "status": "registered",
        "station_id": station_id,
        "station_host": request.remote_addr,
        "station_port": port,
    }), 200
