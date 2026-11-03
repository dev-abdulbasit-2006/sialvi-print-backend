"""HTTP API of the Sialvi licensing service.

Two separate surfaces:
  /api/v1/*     used by customer installations (no owner power whatsoever)
  /api/owner/*  used by the Owner License Manager (Bearer session token required)
The server decides every authorization; clients only receive signed tokens.
"""
import ipaddress
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import Flask, g, jsonify, request

from licensing.server import db
from licensing.server.config import Settings
from licensing.server.service import LicenseError, LicenseService, norm_ip
from shared.license_token import public_key_pem


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    svc = LicenseService(settings)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.extensions["license_service"] = svc

    trusted = []
    for entry in settings.trusted_proxies:
        trusted.append(ipaddress.ip_network(entry, strict=False))

    def _is_trusted(ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in trusted)

    def observed_ip() -> str:
        """The authoritative source IP: the TCP peer, or - only when that peer is a configured
        trusted proxy - the right-most X-Forwarded-For entry that is not itself a trusted proxy.
        Anything the client puts in its JSON body is ignored."""
        peer = norm_ip(request.remote_addr or "")
        if trusted and _is_trusted(peer):
            hops = [h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
            for hop in reversed(hops):
                hop = norm_ip(hop)
                if hop != "unknown" and not _is_trusted(hop):
                    return hop
        return peer

    # ---- simple per-IP sliding-window rate limit for the public client endpoints
    hits, hits_lock = defaultdict(deque), threading.Lock()

    def rate_limited(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            ip, now = observed_ip(), time.monotonic()
            with hits_lock:
                q = hits[ip]
                while q and now - q[0] > settings.rate_limit_window_seconds:
                    q.popleft()
                if len(q) >= settings.rate_limit_requests:
                    return jsonify(error={"code": "RATE_LIMITED", "message": "Too many requests. Slow down."}), 429
                q.append(now)
            return fn(*a, **kw)
        return wrapper

    # Poll/ack are tiny and frequent (every PC, every ~30 s) and a whole shop shares one public IP, so they get their
    # own, higher per-IP budget instead of competing with activation/check-in attempts.
    poll_hits, poll_lock = defaultdict(deque), threading.Lock()

    def poll_limited(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            ip, now = observed_ip(), time.monotonic()
            with poll_lock:
                q = poll_hits[ip]
                while q and now - q[0] > settings.rate_limit_window_seconds:
                    q.popleft()
                if len(q) >= settings.poll_rate_limit_requests:
                    return jsonify(error={"code": "RATE_LIMITED", "message": "Too many requests. Slow down."}), 429
                q.append(now)
            return fn(*a, **kw)
        return wrapper

    def owner_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            auth = request.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            actor = svc.authenticate_owner(token)
            if not actor:
                return jsonify(error={"code": "UNAUTHORIZED", "message": "Owner login required."}), 401
            g.actor, g.token = actor, token
            return fn(*a, **kw)
        return wrapper

    def body() -> dict:
        data = request.get_json(silent=True)
        if data is None and request.data:
            raise LicenseError("VALIDATION", "Request body must be valid JSON.")
        if data is not None and not isinstance(data, dict):
            raise LicenseError("VALIDATION", "Request body must be a JSON object.")
        return data or {}

    @app.errorhandler(LicenseError)
    def _license_error(e):
        return jsonify(error={"code": e.code, "message": e.message, **e.extra}), e.http

    @app.errorhandler(413)
    def _too_big(_):
        return jsonify(error={"code": "VALIDATION", "message": "Request too large."}), 413

    @app.errorhandler(404)
    def _nf(_):
        return jsonify(error={"code": "NOT_FOUND", "message": "Not found."}), 404

    @app.errorhandler(405)
    def _mna(_):
        return jsonify(error={"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed."}), 405

    @app.errorhandler(Exception)
    def _unexpected(e):
        app.logger.exception("Unhandled error")           # message only; request bodies/secrets are never logged
        return jsonify(error={"code": "SERVER_ERROR", "message": "Internal server error."}), 500

    # ============================== client endpoints ==============================
    @app.get("/")
    def root():
        return jsonify(status="ok", service="SialviPrint Licensing Server", version="1.0",
                       endpoints=["/api/v1/health", "/api/v1/public-key", "/api/v1/activate",
                                  "/api/v1/checkin", "/api/v1/deactivate", "/api/owner/login"])

    @app.get("/api/v1/health")
    def health():
        # "storage" tells the owner at a glance whether licenses survive a restart (postgresql) or not (sqlite).
        return jsonify(status="ok", server_time=db.utcnow(),
                       storage="postgresql" if svc.persistent else "sqlite", persistent=bool(svc.persistent))

    @app.get("/api/v1/public-key")
    def pubkey():
        return jsonify(public_key=public_key_pem(svc.signing_key))

    @app.post("/api/v1/activate")
    @rate_limited
    def activate():
        b = body()
        return jsonify(svc.activate(b.get("serial_key"), b.get("installation_id"), b.get("device_name"),
                                    b.get("app_version"), b.get("os_info"), observed_ip()))

    @app.post("/api/v1/checkin")
    @rate_limited
    def checkin():
        b = body()
        return jsonify(svc.checkin(b.get("installation_id"), b.get("installation_secret"), b.get("app_version"), observed_ip()))

    @app.post("/api/v1/poll")
    @poll_limited
    def poll():
        b = body()
        return jsonify(svc.poll(b.get("installation_id"), b.get("installation_secret"), observed_ip()))

    @app.post("/api/v1/ack-command")
    @poll_limited
    def ack_command():
        b = body()
        return jsonify(svc.ack_command(b.get("installation_id"), b.get("installation_secret"), b.get("command_id"),
                                       b.get("status"), b.get("result", ""), observed_ip()))

    @app.post("/api/v1/deactivate")
    @rate_limited
    def deactivate():
        b = body()
        return jsonify(svc.deactivate_self(b.get("installation_id"), b.get("installation_secret"), observed_ip()))

    # ============================== owner endpoints ===============================
    @app.post("/api/owner/login")
    def login():
        b = body()
        return jsonify(svc.login(b.get("username"), b.get("password") or "", observed_ip()))

    @app.post("/api/owner/logout")
    @owner_required
    def logout():
        svc.logout(g.token)
        return jsonify(ok=True)

    @app.get("/api/owner/me")
    @owner_required
    def me():
        return jsonify(username=g.actor, policy={
            "checkin_interval_minutes": settings.checkin_interval_minutes,
            "offline_grace_hours": settings.offline_grace_hours,
            "not_recent_minutes": settings.not_recent_minutes})

    @app.get("/api/owner/overview")
    @owner_required
    def overview():
        return jsonify(svc.overview())

    @app.get("/api/owner/licenses")
    @owner_required
    def licenses():
        return jsonify(licenses=svc.list_licenses(request.args.get("q")))

    @app.post("/api/owner/licenses")
    @owner_required
    def create_license():
        b = body()
        lic, key = svc.create_license(g.actor, b.get("label"), b.get("max_installations"), b.get("status", "active"),
                                      b.get("notes", ""), b.get("expires_at"))
        return jsonify(license=lic, serial_key=key), 201   # the only time the plain key exists

    @app.get("/api/owner/licenses/<int:lid>")
    @owner_required
    def one_license(lid):
        return jsonify(svc.get_license(lid))

    @app.patch("/api/owner/licenses/<int:lid>")
    @owner_required
    def edit_license(lid):
        b = body()
        return jsonify(svc.update_license(g.actor, lid, b.get("label"), b.get("notes"),
                                          b["expires_at"] if "expires_at" in b else "__keep__"))

    @app.post("/api/owner/licenses/<int:lid>/limit")
    @owner_required
    def limit(lid):
        b = body()
        return jsonify(svc.set_limit(g.actor, lid, b.get("max_installations"),
                                     b.get("deactivate_installation_ids"), b.get("reason", "")))

    @app.post("/api/owner/licenses/<int:lid>/status")
    @owner_required
    def lic_status(lid):
        b = body()
        return jsonify(svc.set_license_status(g.actor, lid, b.get("action"), b.get("reason", "")))

    @app.get("/api/owner/licenses/<int:lid>/installations")
    @owner_required
    def lic_installs(lid):
        svc.get_license(lid)
        return jsonify(installations=svc.list_installations(lid))

    @app.get("/api/owner/installations")
    @owner_required
    def all_installs():
        return jsonify(installations=svc.list_installations())

    @app.get("/api/owner/installations/<iid>")
    @owner_required
    def one_install(iid):
        return jsonify(svc.get_installation(iid))

    @app.get("/api/owner/installations/<iid>/ip-history")
    @owner_required
    def ip_hist(iid):
        return jsonify(history=svc.ip_history(iid))

    @app.get("/api/owner/installations/<iid>/events")
    @owner_required
    def inst_events(iid):
        return jsonify(events=svc.installation_events(iid))

    @app.post("/api/owner/installations/<iid>/lock")
    @owner_required
    def lock(iid):
        return jsonify(svc.lock_installation(g.actor, iid, body().get("reason", "")))

    @app.post("/api/owner/installations/<iid>/unlock")
    @owner_required
    def unlock(iid):
        return jsonify(svc.unlock_installation(g.actor, iid, body().get("reason", "")))

    @app.post("/api/owner/installations/<iid>/deactivate")
    @owner_required
    def owner_deactivate(iid):
        return jsonify(svc.deactivate_installation(g.actor, iid, body().get("reason", "")))

    @app.get("/api/owner/ip-restrictions")
    @owner_required
    def restrictions():
        return jsonify(restrictions=svc.list_restrictions())

    @app.post("/api/owner/ip-restrictions")
    @owner_required
    def add_restriction():
        b = body()
        return jsonify(svc.add_restriction(g.actor, b.get("network"), b.get("scope"), b.get("license_id"),
                                           b.get("reason", ""), bool(b.get("confirm_global")))), 201

    @app.post("/api/owner/ip-restrictions/<int:rid>/<state>")
    @owner_required
    def restriction_state(rid, state):
        if state not in ("enable", "disable"):
            raise LicenseError("NOT_FOUND", "Not found.", 404)
        return jsonify(svc.set_restriction_active(g.actor, rid, state == "enable", body().get("reason", "")))

    @app.get("/api/owner/commands")
    @owner_required
    def commands_list():
        return jsonify(commands=svc.list_commands(request.args.get("license_id", type=int), request.args.get("limit", 200, type=int)))

    @app.post("/api/owner/commands")
    @owner_required
    def commands_issue():
        b = body()
        return jsonify(svc.issue_command(g.actor, b.get("license_id"), b.get("command_type"), b.get("installation_id"),
                                         b.get("reason", ""))), 201

    @app.get("/api/owner/commands/<cid>")
    @owner_required
    def commands_detail(cid):
        return jsonify(svc.command_detail(cid))

    @app.get("/api/owner/audit")
    @owner_required
    def audit():
        lid = request.args.get("license_id", type=int)
        return jsonify(events=svc.audit_list(request.args.get("limit", 500, type=int), lid, request.args.get("target_type")))

    return app
