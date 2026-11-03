"""
Shared server helpers: auth, standardized error responses, page-range
parsing/validation, logging configuration.
"""

import logging
import logging.handlers
import re
from functools import wraps
from pathlib import Path

from flask import jsonify, request

from admin.config.config import config


def setup_logging():
    log_path = config.logs_dir / "admin_server.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.get("log_level", "INFO")))
    root.addHandler(handler)
    # Also echo to console for interactive admin runs
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)


class ApiError(Exception):
    """
    Raised by route handlers to produce a clean, human-readable JSON
    error with the correct HTTP status - never a raw traceback
    (requirement 23).
    """
    def __init__(self, message: str, status_code: int = 400, code: str = "ERROR"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def error_response(message: str, status_code: int = 400, code: str = "ERROR"):
    return jsonify({"error": {"code": code, "message": message}}), status_code


def require_lan_token(fn):
    """
    Lightweight shared-secret auth for LAN requests. Not intended as
    internet-grade security, but prevents any device that merely joins
    the LAN from silently issuing admin-equivalent requests
    (requirement 34/21).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-LAN-Token", "")
        if not token or token != config.lan_shared_token:
            return error_response("Unauthorized: invalid or missing LAN token.", 401, "UNAUTHORIZED")
        return fn(*args, **kwargs)
    return wrapper


def require_admin_token(fn):
    """
    Protect Admin-only operations from LAN clients.

    The Admin desktop UI talks to Flask through 127.0.0.1. User PCs only
    need the shared LAN token for customer-facing routes; they must never be
    able to use that token to verify payments or change pricing. Keeping the
    admin-only routes loopback-bound removes the old privilege escalation
    where a kiosk holding the normal LAN token could call an admin endpoint.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        remote = request.remote_addr or ""
        if remote not in ("127.0.0.1", "::1"):
            return error_response("Admin operations are available only on the Admin PC.", 403, "FORBIDDEN")

        token = request.headers.get("X-Admin-Token", "")
        if not token or token != config.lan_shared_token:
            return error_response("Unauthorized: admin privileges required.", 403, "FORBIDDEN")
        return fn(*args, **kwargs)
    return wrapper


_RANGE_TOKEN_RE = re.compile(r"^\s*(\d+)\s*(-\s*(\d+)\s*)?$")


def parse_page_range(range_str: str, total_pages: int) -> list[int]:
    """
    Parses strings like '1-3,5,8,10-12' into a sorted, de-duplicated
    list of valid page numbers within [1, total_pages].
    Raises ApiError on any malformed or out-of-bounds input -
    invalid ranges must never reach the printer (requirement 13).
    """
    if not range_str or not range_str.strip():
        return list(range(1, total_pages + 1))

    pages = set()
    for chunk in range_str.split(","):
        m = _RANGE_TOKEN_RE.match(chunk)
        if not m:
            raise ApiError(f"Invalid page range segment: '{chunk.strip()}'", 400, "INVALID_PAGE_RANGE")
        start = int(m.group(1))
        end = int(m.group(3)) if m.group(3) else start
        if start > end:
            start, end = end, start
        if start < 1 or end > total_pages:
            raise ApiError(
                f"Page range '{chunk.strip()}' is outside document bounds (1-{total_pages}).",
                400, "PAGE_RANGE_OUT_OF_BOUNDS",
            )
        pages.update(range(start, end + 1))

    if not pages:
        raise ApiError("Page range resolved to zero pages.", 400, "INVALID_PAGE_RANGE")
    return sorted(pages)


def safe_extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")
