"""WSGI entry point for the CLOUD licensing service (Render):   gunicorn app:app

This used to be a stub that answered ACTIVE for any key. It now serves the real licensing server
(licensing/server): serial keys, PC limits, signed Ed25519 tokens, locks, audit log, Owner API.
All secrets come from environment variables (see render.yaml and documentation/CLOUD_LICENSING_RENDER.md).
"""
import logging
import os

from licensing.server.app import create_app
from licensing.server.service import LicenseError

log = logging.getLogger("licensing.bootstrap")


def _bootstrap_owner(app):
    """Render's free tier has no shell, so `create-owner` cannot be run there. If LICENSE_OWNER_USERNAME and
    LICENSE_OWNER_PASSWORD (a dashboard SECRET, min 10 chars) are set, create that owner on start-up when it does
    not exist yet. Idempotent; the password is never logged. Remove both variables once the database is persistent."""
    user, pw = os.environ.get("LICENSE_OWNER_USERNAME", ""), os.environ.get("LICENSE_OWNER_PASSWORD", "")
    if not (user and pw):
        return
    try:
        app.extensions["license_service"].create_owner(user, pw)
        log.warning("Owner account '%s' created from environment settings.", user)
    except LicenseError as exc:
        # "already exists" is the normal case after the first start; anything else is worth knowing.
        if "already exists" not in exc.message:
            log.error("Owner bootstrap skipped: %s", exc.message)


app = create_app()
if os.environ.get("RENDER") and not (os.environ.get("DATABASE_URL") or os.environ.get("LICENSE_DATABASE_URL")):
    log.error("DATABASE_URL is NOT set: licensing data is kept in a temporary SQLite file and will be LOST "
              "whenever this service restarts or sleeps. Set DATABASE_URL to a PostgreSQL database.")
_bootstrap_owner(app)
