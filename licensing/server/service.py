"""All licensing rules live here (no Flask, no Qt) so they can be unit-tested and
so the server - never the client - makes every authorization decision."""
import ipaddress
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta

from shared.license_token import sign_token
from licensing.server import db, security
from licensing.server.config import Settings

REGISTERED = ("active", "locked")      # installations that occupy a PC slot
ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_LIMIT = 100_000

# Remote commands: (kind, action). The command first changes the authoritative server state (so enforcement never
# depends on a PC being online), then it is queued so each PC is told at its next poll and reports back.
COMMAND_TYPES = {
    "SUSPEND_LICENSE": ("license", "suspend"),
    "REACTIVATE_LICENSE": ("license", "reactivate"),
    "REVOKE_LICENSE": ("license", "revoke"),
    "DISABLE_DEVICE": ("device", "lock"),
    "REENABLE_DEVICE": ("device", "unlock"),
    "REVOKE_DEVICE": ("device", "deactivate"),
    "FORCE_REVALIDATE": (None, None),
}

CLIENT_MESSAGES = {
    "INVALID_KEY": "The serial key is not valid.",
    "LICENSE_SUSPENDED": "This license is suspended. Please contact your software provider.",
    "LICENSE_REVOKED": "This license has been revoked. Please contact your software provider.",
    "LICENSE_EXPIRED": "This license has expired. Please contact your software provider.",
    "LIMIT_REACHED": "The maximum number of PCs for this license has been reached. Contact your software provider or deactivate an unused PC.",
    "INSTALLATION_LOCKED": "This installation is locked. Please contact your software provider.",
    "INSTALLATION_DEACTIVATED": "This installation was deactivated. Please activate it again with your serial key.",
    "IP_BLOCKED": "Authorization is not available from this network address. Please contact your software provider.",
    "INSTALLATION_CONFLICT": "This installation ID is already registered to a different license.",
    "INVALID_CREDENTIALS": "This PC is not registered with the licensing server (it was removed, or the server was reset). Please activate it again with your serial key.",
}


class LicenseError(Exception):
    def __init__(self, code, message=None, http=400, extra=None):
        super().__init__(message or CLIENT_MESSAGES.get(code, code))
        self.code, self.http, self.extra = code, http, extra or {}
        self.message = message or CLIENT_MESSAGES.get(code, code)


def norm_ip(ip) -> str:
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return str(addr)


def parse_expiry(value):
    """None/'' -> None. 'YYYY-MM-DD' -> end of that day (UTC). Full UTC timestamps accepted."""
    if value in (None, ""):
        return None
    value = str(value).strip()
    for fmt, fix in (("%Y-%m-%d", lambda d: d.replace(hour=23, minute=59, second=59)),
                     ("%Y-%m-%dT%H:%M:%SZ", lambda d: d)):
        try:
            return fix(datetime.strptime(value, fmt)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise LicenseError("VALIDATION", "Expiration must be YYYY-MM-DD.", 400)


def _row(r):
    return dict(r) if r is not None else None


def _clean(text, limit):
    return str(text or "").strip()[:limit]


class LicenseService:
    def __init__(self, settings: Settings):
        self.s = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = settings.data_dir / "licenses.db"
        # PostgreSQL when DATABASE_URL is set (persistent), otherwise the local SQLite file.
        self.db_target = (getattr(settings, "database_url", "") or "").strip() or self.db_path
        self.persistent = db.is_postgres(self.db_target)
        db.migrate(self.db_target)
        self.pepper, self.signing_key = security.load_or_create_server_secrets(
            settings.data_dir, settings.key_pepper, getattr(settings, "signing_key_pem", ""))

    # ---------------------------------------------------------------- plumbing
    def conn(self):
        return db.connect(self.db_target)

    def _audit(self, conn, actor, action, target_type, target_id, license_id=None,
               before=None, after=None, reason="", ip=None):
        conn.execute(
            "INSERT INTO audit_events(ts,actor,action,target_type,target_id,license_id,before_json,after_json,reason,ip)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (db.utcnow(), actor, action, target_type, str(target_id), license_id,
             json.dumps(before, sort_keys=True) if before is not None else None,
             json.dumps(after, sort_keys=True) if after is not None else None,
             _clean(reason, 500), ip))

    @staticmethod
    def _effective_license_status(lic) -> str:
        if lic["status"] == "active" and lic["expires_at"] and lic["expires_at"] < db.utcnow():
            return "expired"
        return lic["status"]

    def _usage(self, conn, license_id) -> int:
        return conn.execute("SELECT COUNT(*) c FROM installations WHERE license_id=? AND status IN ('active','locked')",
                            (license_id,)).fetchone()["c"]

    def _license_dict(self, conn, lic):
        d = _row(lic)
        d.pop("key_hash", None)
        d["effective_status"] = self._effective_license_status(lic)
        d["registered_pcs"] = self._usage(conn, lic["id"])
        d["usage_text"] = f"{d['registered_pcs']} / {lic['max_installations']} PCs"
        d["display_id"] = f"LIC-{lic['id']:05d}"
        d["key_display"] = "SIALVI-••••-••••-••••-" + lic["key_hint"]
        return d

    def _ip_restriction(self, conn, ip, scope, license_id):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        rows = conn.execute("SELECT * FROM ip_restrictions WHERE active=1 AND scope=? AND (license_id IS NULL OR license_id=?)",
                            (scope, license_id)).fetchall()
        for r in rows:
            if addr in ipaddress.ip_network(r["network"], strict=False):
                return r
        return None

    def _make_token(self, lic_id, inst_id):
        now = int(time.time())
        exp = now + self.s.offline_grace_hours * 3600
        payload = {"v": 1, "lic": lic_id, "inst": inst_id, "iat": now, "exp": exp,
                   "interval": self.s.checkin_interval_minutes * 60}
        return sign_token(payload, self.signing_key), datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _auth_response(self, token, expires_at):
        return {"authorized": True, "token": token, "expires_at": expires_at,
                "checkin_interval_minutes": self.s.checkin_interval_minutes,
                "offline_grace_hours": self.s.offline_grace_hours, "server_time": db.utcnow()}

    # ------------------------------------------------------------ owner accounts
    def create_owner(self, username, password):
        username = _clean(username, 64)
        if not re.match(r"^[A-Za-z0-9._-]{3,64}$", username):
            raise LicenseError("VALIDATION", "Username must be 3-64 letters, digits, . _ -")
        if len(password) < 10:
            raise LicenseError("VALIDATION", "Owner password must be at least 10 characters.")
        h, salt, it = security.hash_password(password)
        conn = self.conn()
        try:
            with db.transaction(conn):
                try:
                    conn.execute("INSERT INTO owners(username,password_hash,password_salt,iterations,created_at) VALUES (?,?,?,?,?)",
                                 (username, h, salt, it, db.utcnow()))
                except Exception:
                    raise LicenseError("VALIDATION", "That owner username already exists.")
                self._audit(conn, "system", "owner_created", "owner", username)
        finally:
            conn.close()

    def login(self, username, password, ip):
        username, ip = _clean(username, 64), norm_ip(ip)
        since = (datetime.now(timezone.utc) - timedelta(minutes=self.s.lockout_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self.conn()
        try:
            fails_user = conn.execute("SELECT COUNT(*) c FROM login_attempts WHERE username=? AND success=0 AND ts>=?",
                                      (username, since)).fetchone()["c"]
            fails_ip = conn.execute("SELECT COUNT(*) c FROM login_attempts WHERE ip=? AND success=0 AND ts>=?",
                                    (ip, since)).fetchone()["c"]
            if fails_user >= self.s.max_failed_logins or fails_ip >= self.s.max_failed_logins * 4:
                raise LicenseError("LOGIN_LOCKED", f"Too many failed logins. Try again in {self.s.lockout_minutes} minutes.", 429)
            owner = conn.execute("SELECT * FROM owners WHERE username=? AND disabled=0", (username,)).fetchone()
            if owner:
                ok = security.verify_password(password, owner["password_hash"], owner["password_salt"], owner["iterations"])
            else:  # same work for unknown users: no username enumeration by timing
                security.hash_password(password)
                ok = False
            now = db.utcnow()
            conn.execute("INSERT INTO login_attempts(username,ip,ts,success) VALUES (?,?,?,?)", (username, ip, now, 1 if ok else 0))
            if not ok:
                raise LicenseError("BAD_LOGIN", "Wrong username or password.", 401)
            token = security.new_secret()
            exp = (datetime.now(timezone.utc) + timedelta(hours=self.s.owner_session_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            with db.transaction(conn):
                conn.execute("DELETE FROM owner_sessions WHERE expires_at < ?", (now,))
                conn.execute("INSERT INTO owner_sessions(owner_id,token_hash,created_at,expires_at,ip) VALUES (?,?,?,?,?)",
                             (owner["id"], security.hash_secret(token), now, exp, ip))
                self._audit(conn, username, "owner_login", "owner", username, ip=ip)
            return {"token": token, "expires_at": exp, "username": username}
        finally:
            conn.close()

    def authenticate_owner(self, token):
        if not token:
            return None
        conn = self.conn()
        try:
            r = conn.execute("SELECT o.username FROM owner_sessions s JOIN owners o ON o.id=s.owner_id "
                             "WHERE s.token_hash=? AND s.expires_at>? AND o.disabled=0",
                             (security.hash_secret(token), db.utcnow())).fetchone()
            return r["username"] if r else None
        finally:
            conn.close()

    def logout(self, token):
        conn = self.conn()
        try:
            conn.execute("DELETE FROM owner_sessions WHERE token_hash=?", (security.hash_secret(token or ""),))
        finally:
            conn.close()

    # ------------------------------------------------------------------ licenses
    def create_license(self, actor, label, max_installations, status="active", notes="", expires_at=None):
        label = _clean(label, 120)
        if not label:
            raise LicenseError("VALIDATION", "Please enter a customer / license name.")
        max_installations = self._valid_limit(max_installations)
        if status not in ("active", "suspended"):
            raise LicenseError("VALIDATION", "Initial status must be active or suspended.")
        expires = parse_expiry(expires_at)
        conn = self.conn()
        try:
            for _ in range(10):
                key = security.generate_serial_key()
                now = db.utcnow()
                try:
                    with db.transaction(conn):
                        cur = conn.execute(
                            "INSERT INTO licenses(key_hash,key_hint,label,status,max_installations,created_at,updated_at,expires_at,notes)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (security.hash_key(key, self.pepper), security.key_hint(key), label, status,
                             max_installations, now, now, expires, _clean(notes, 2000)))
                        lid = cur.lastrowid
                        self._audit(conn, actor, "license_created", "license", lid, lid, None,
                                    {"label": label, "max_installations": max_installations, "status": status, "expires_at": expires})
                    lic = conn.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
                    return self._license_dict(conn, lic), key   # key is returned ONCE; only its hash is stored
                except LicenseError:
                    raise
                except Exception as exc:  # UNIQUE(key_hash) collision: astronomically unlikely, just retry
                    if "UNIQUE" not in str(exc):
                        raise
            raise LicenseError("SERVER_ERROR", "Could not generate a unique key.", 500)
        finally:
            conn.close()

    @staticmethod
    def _valid_limit(value):
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise LicenseError("VALIDATION", "PC limit must be a whole number.")
        if not 1 <= n <= MAX_LIMIT:
            raise LicenseError("VALIDATION", f"PC limit must be between 1 and {MAX_LIMIT}.")
        return n

    def list_licenses(self, q=None):
        conn = self.conn()
        try:
            sql, args = "SELECT * FROM licenses", ()
            if q:
                like = f"%{q.strip()}%"
                sql += " WHERE label LIKE ? OR notes LIKE ? OR key_hint LIKE ? OR ('LIC-' || printf('%05d', id)) LIKE ?"
                args = (like, like, like, like)
            rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
            return [self._license_dict(conn, r) for r in rows]
        finally:
            conn.close()

    def get_license(self, license_id):
        conn = self.conn()
        try:
            lic = conn.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
            if not lic:
                raise LicenseError("NOT_FOUND", "License not found.", 404)
            return self._license_dict(conn, lic)
        finally:
            conn.close()

    def update_license(self, actor, license_id, label=None, notes=None, expires_at="__keep__"):
        conn = self.conn()
        try:
            with db.transaction(conn):
                lic = conn.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
                if not lic:
                    raise LicenseError("NOT_FOUND", "License not found.", 404)
                new = {"label": _clean(label, 120) if label is not None else lic["label"],
                       "notes": _clean(notes, 2000) if notes is not None else lic["notes"],
                       "expires_at": lic["expires_at"] if expires_at == "__keep__" else parse_expiry(expires_at)}
                if not new["label"]:
                    raise LicenseError("VALIDATION", "Name cannot be empty.")
                conn.execute("UPDATE licenses SET label=?, notes=?, expires_at=?, updated_at=? WHERE id=?",
                             (new["label"], new["notes"], new["expires_at"], db.utcnow(), license_id))
                self._audit(conn, actor, "license_edited", "license", license_id, license_id,
                            {k: lic[k] for k in new}, new)
        finally:
            conn.close()
        return self.get_license(license_id)

    def set_limit(self, actor, license_id, new_limit, deactivate_installation_ids=None, reason=""):
        new_limit = self._valid_limit(new_limit)
        conn = self.conn()
        try:
            with db.transaction(conn):
                lic = conn.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
                if not lic:
                    raise LicenseError("NOT_FOUND", "License not found.", 404)
                regs = conn.execute("SELECT id,device_name,status,last_checkin_at,last_ip FROM installations "
                                    "WHERE license_id=? AND status IN ('active','locked') ORDER BY activated_at", (license_id,)).fetchall()
                usage = len(regs)
                freed = []
                if new_limit < usage:
                    need = usage - new_limit
                    ids = list(dict.fromkeys(deactivate_installation_ids or []))
                    valid = {r["id"] for r in regs}
                    if len(ids) < need or any(i not in valid for i in ids):
                        raise LicenseError(
                            "LIMIT_CONFLICT",
                            f"{usage} PCs are registered but the new limit is {new_limit}. Nothing was changed. "
                            f"Choose at least {need} installation(s) to deactivate, or keep a higher limit.",
                            409, {"usage": usage, "new_limit": new_limit, "must_free": need,
                                  "installations": [dict(r) for r in regs]})
                    now = db.utcnow()
                    for i in ids:
                        conn.execute("UPDATE installations SET status='deactivated', deactivated_at=?, deactivated_reason=? WHERE id=?",
                                     (now, _clean(reason, 500) or "Deactivated to fit the new PC limit", i))
                        self._audit(conn, actor, "installation_deactivated", "installation", i, license_id,
                                    {"status": "registered"}, {"status": "deactivated"}, "Deactivated to fit new PC limit")
                        freed.append(i)
                conn.execute("UPDATE licenses SET max_installations=?, updated_at=? WHERE id=?", (new_limit, db.utcnow(), license_id))
                self._audit(conn, actor, "pc_limit_changed", "license", license_id, license_id,
                            {"max_installations": lic["max_installations"]},
                            {"max_installations": new_limit, "deactivated": freed}, reason)
        finally:
            conn.close()
        return self.get_license(license_id)

    def set_license_status(self, actor, license_id, action, reason=""):
        transitions = {"suspend": ("active", "suspended"), "reactivate": ("suspended", "active"), "revoke": (("active", "suspended"), "revoked")}
        if action not in transitions:
            raise LicenseError("VALIDATION", "Action must be suspend, reactivate or revoke.")
        allowed_from, to = transitions[action]
        allowed_from = (allowed_from,) if isinstance(allowed_from, str) else allowed_from
        conn = self.conn()
        try:
            with db.transaction(conn):
                lic = conn.execute("SELECT * FROM licenses WHERE id=?", (license_id,)).fetchone()
                if not lic:
                    raise LicenseError("NOT_FOUND", "License not found.", 404)
                if lic["status"] == "revoked":
                    raise LicenseError("INVALID_STATE", "A revoked license cannot be changed. Create a new license instead.", 409)
                if lic["status"] not in allowed_from:
                    raise LicenseError("INVALID_STATE", f"Cannot {action} a license that is {lic['status']}.", 409)
                conn.execute("UPDATE licenses SET status=?, updated_at=? WHERE id=?", (to, db.utcnow(), license_id))
                self._audit(conn, actor, f"license_{to}" if action != "reactivate" else "license_reactivated",
                            "license", license_id, license_id, {"status": lic["status"]}, {"status": to}, reason)
        finally:
            conn.close()
        return self.get_license(license_id)

    # ------------------------------------------------------------- installations
    def _display_status(self, inst, lic):
        if inst["status"] == "deactivated":
            return "Deactivated"
        if inst["status"] == "locked":
            return "Locked"
        eff = self._effective_license_status(lic)
        if eff == "revoked":
            return "Revoked"
        if eff == "suspended":
            return "Suspended"
        if eff == "expired":
            return "Expired"
        last = db.parse_ts(inst["last_checkin_at"])
        if last is None:
            return "Pending activation"
        if (datetime.now(timezone.utc) - last).total_seconds() > self.s.not_recent_minutes * 60:
            return "Not recently seen"
        return "Active"

    @staticmethod
    def _ago(ts):
        last = db.parse_ts(ts)
        if not last:
            return "never"
        sec = int((datetime.now(timezone.utc) - last).total_seconds())
        if sec < 90:
            return "just now" if sec < 10 else f"{sec} seconds ago"
        if sec < 5400:
            return f"{round(sec / 60)} minutes ago"
        if sec < 172800:
            return f"{round(sec / 3600)} hours ago"
        return f"{sec // 86400} days ago"

    def _inst_dict(self, inst, lic):
        d = _row(inst)
        d.pop("secret_hash", None)
        d["display_status"] = self._display_status(inst, lic)
        d["last_seen"] = self._ago(inst["last_checkin_at"])
        d["license_label"] = lic["label"]
        return d

    def list_installations(self, license_id=None):
        conn = self.conn()
        try:
            sql = "SELECT i.*, l.id AS _lid FROM installations i JOIN licenses l ON l.id=i.license_id"
            args = ()
            if license_id is not None:
                sql += " WHERE i.license_id=?"
                args = (license_id,)
            out = []
            for r in conn.execute(sql + " ORDER BY i.activated_at DESC", args).fetchall():
                lic = conn.execute("SELECT * FROM licenses WHERE id=?", (r["license_id"],)).fetchone()
                out.append(self._inst_dict(r, lic))
            return out
        finally:
            conn.close()

    def _load_inst(self, conn, inst_id):
        inst = conn.execute("SELECT * FROM installations WHERE id=?", (inst_id,)).fetchone()
        if not inst:
            raise LicenseError("NOT_FOUND", "Installation not found.", 404)
        return inst

    def lock_installation(self, actor, inst_id, reason=""):
        conn = self.conn()
        try:
            with db.transaction(conn):
                inst = self._load_inst(conn, inst_id)
                if inst["status"] != "active":
                    raise LicenseError("INVALID_STATE", f"Only an active installation can be locked (this one is {inst['status']}).", 409)
                conn.execute("UPDATE installations SET status='locked', lock_reason=?, locked_at=? WHERE id=?",
                             (_clean(reason, 500), db.utcnow(), inst_id))
                self._audit(conn, actor, "installation_locked", "installation", inst_id, inst["license_id"],
                            {"status": "active"}, {"status": "locked"}, reason)
        finally:
            conn.close()
        return self._inst_by_id(inst_id)

    def unlock_installation(self, actor, inst_id, reason=""):
        conn = self.conn()
        try:
            with db.transaction(conn):
                inst = self._load_inst(conn, inst_id)
                if inst["status"] != "locked":
                    raise LicenseError("INVALID_STATE", "This installation is not locked.", 409)
                conn.execute("UPDATE installations SET status='active', lock_reason='', locked_at=NULL WHERE id=?", (inst_id,))
                self._audit(conn, actor, "installation_unlocked", "installation", inst_id, inst["license_id"],
                            {"status": "locked"}, {"status": "active"}, reason)
        finally:
            conn.close()
        out = self._inst_by_id(inst_id)
        lic = self.get_license(out["license_id"])
        if lic["effective_status"] != "active":   # unlock never bypasses the license state
            out["warning"] = f"Unlocked, but the license is {lic['effective_status']}, so it will still be refused."
        return out

    def deactivate_installation(self, actor, inst_id, reason=""):
        conn = self.conn()
        try:
            with db.transaction(conn):
                inst = self._load_inst(conn, inst_id)
                if inst["status"] == "deactivated":
                    raise LicenseError("INVALID_STATE", "Already deactivated.", 409)
                conn.execute("UPDATE installations SET status='deactivated', deactivated_at=?, deactivated_reason=? WHERE id=?",
                             (db.utcnow(), _clean(reason, 500), inst_id))
                self._audit(conn, actor, "installation_deactivated", "installation", inst_id, inst["license_id"],
                            {"status": inst["status"]}, {"status": "deactivated"}, reason)
        finally:
            conn.close()
        return self._inst_by_id(inst_id)

    def _inst_by_id(self, inst_id):
        conn = self.conn()
        try:
            inst = self._load_inst(conn, inst_id)
            lic = conn.execute("SELECT * FROM licenses WHERE id=?", (inst["license_id"],)).fetchone()
            return self._inst_dict(inst, lic)
        finally:
            conn.close()

    def get_installation(self, inst_id):
        return self._inst_by_id(inst_id)

    def ip_history(self, inst_id, limit=500):
        conn = self.conn()
        try:
            self._load_inst(conn, inst_id)
            return [dict(r) for r in conn.execute(
                "SELECT * FROM ip_history WHERE installation_id=? ORDER BY id DESC LIMIT ?", (inst_id, int(limit))).fetchall()]
        finally:
            conn.close()

    def installation_events(self, inst_id, limit=500):
        conn = self.conn()
        try:
            self._load_inst(conn, inst_id)
            return [dict(r) for r in conn.execute(
                "SELECT * FROM audit_events WHERE target_type='installation' AND target_id=? ORDER BY id DESC LIMIT ?",
                (inst_id, int(limit))).fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------ IP restrictions
    def add_restriction(self, actor, network, scope, license_id=None, reason="", confirm_global=False):
        if scope not in ("block_activation", "block_authorization"):
            raise LicenseError("VALIDATION", "Scope must be block_activation or block_authorization.")
        try:
            net = ipaddress.ip_network(str(network).strip(), strict=False)
        except ValueError:
            raise LicenseError("VALIDATION", "Enter a valid IPv4/IPv6 address or CIDR range.")
        if isinstance(net, ipaddress.IPv6Network) and net.network_address.ipv4_mapped:
            net = ipaddress.ip_network(f"{net.network_address.ipv4_mapped}/{max(net.prefixlen - 96, 0)}")
        if net.prefixlen < (8 if net.version == 4 else 32):
            raise LicenseError("VALIDATION", "That range is too large to block safely (minimum /8 for IPv4, /32 for IPv6).")
        if license_id in (None, "", 0):
            license_id = None
            if not confirm_global:
                raise LicenseError(
                    "GLOBAL_CONFIRMATION_REQUIRED",
                    "This restriction applies to ALL licenses. Customers sharing this public IP (same office, ISP or "
                    "carrier NAT) may be affected. Confirm to continue.", 409)
        conn = self.conn()
        try:
            with db.transaction(conn):
                if license_id is not None and not conn.execute("SELECT 1 FROM licenses WHERE id=?", (license_id,)).fetchone():
                    raise LicenseError("NOT_FOUND", "License not found.", 404)
                cur = conn.execute("INSERT INTO ip_restrictions(network,scope,license_id,reason,active,created_at,created_by) VALUES (?,?,?,?,1,?,?)",
                                   (str(net), scope, license_id, _clean(reason, 500), db.utcnow(), actor))
                rid = cur.lastrowid
                self._audit(conn, actor, "ip_restriction_added", "ip_restriction", rid, license_id, None,
                            {"network": str(net), "scope": scope, "license_id": license_id}, reason)
            return self._restriction_by_id(conn, rid)
        finally:
            conn.close()

    def _restriction_by_id(self, conn, rid):
        return dict(conn.execute("SELECT * FROM ip_restrictions WHERE id=?", (rid,)).fetchone())

    def set_restriction_active(self, actor, rid, active, reason=""):
        conn = self.conn()
        try:
            with db.transaction(conn):
                r = conn.execute("SELECT * FROM ip_restrictions WHERE id=?", (rid,)).fetchone()
                if not r:
                    raise LicenseError("NOT_FOUND", "Restriction not found.", 404)
                conn.execute("UPDATE ip_restrictions SET active=?, changed_at=?, changed_by=? WHERE id=?",
                             (1 if active else 0, db.utcnow(), actor, rid))
                self._audit(conn, actor, "ip_restriction_enabled" if active else "ip_restriction_disabled", "ip_restriction",
                            rid, r["license_id"], {"active": r["active"]}, {"active": 1 if active else 0}, reason)
            return self._restriction_by_id(conn, rid)
        finally:
            conn.close()

    def list_restrictions(self):
        conn = self.conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT r.*, l.label AS license_label FROM ip_restrictions r LEFT JOIN licenses l ON l.id=r.license_id ORDER BY r.id DESC").fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------- audit/overview
    def audit_list(self, limit=500, license_id=None, target_type=None):
        conn = self.conn()
        try:
            sql, args, where = "SELECT * FROM audit_events", [], []
            if license_id is not None:
                where.append("license_id=?"); args.append(license_id)
            if target_type:
                where.append("target_type=?"); args.append(target_type)
            if where:
                sql += " WHERE " + " AND ".join(where)
            return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, min(int(limit), 5000))).fetchall()]
        finally:
            conn.close()

    def overview(self):
        conn = self.conn()
        try:
            c = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
            now = db.utcnow()
            recent = (datetime.now(timezone.utc) - timedelta(minutes=self.s.not_recent_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "total_licenses": c("SELECT COUNT(*) FROM licenses"),
                "active_licenses": c("SELECT COUNT(*) FROM licenses WHERE status='active' AND (expires_at IS NULL OR expires_at>=?)", now),
                "suspended_licenses": c("SELECT COUNT(*) FROM licenses WHERE status='suspended'"),
                "revoked_licenses": c("SELECT COUNT(*) FROM licenses WHERE status='revoked'"),
                "expired_licenses": c("SELECT COUNT(*) FROM licenses WHERE status='active' AND expires_at IS NOT NULL AND expires_at<?", now),
                "registered_installations": c("SELECT COUNT(*) FROM installations WHERE status IN ('active','locked')"),
                "locked_installations": c("SELECT COUNT(*) FROM installations WHERE status='locked'"),
                "recently_seen_installations": c("SELECT COUNT(*) FROM installations WHERE status='active' AND last_checkin_at>=?", recent),
                "not_recent_minutes": self.s.not_recent_minutes,
            }
        finally:
            conn.close()

    # ================================================================ CLIENT SIDE
    def _validate_client_fields(self, installation_id, device_name, app_version, os_info):
        if not ID_RE.match(str(installation_id or "")):
            raise LicenseError("VALIDATION", "installation_id must be 8-64 letters, digits, . _ -")
        return _clean(device_name, 120), _clean(app_version, 40), _clean(os_info, 160)

    def _reject(self, conn, code, inst_id, license_id, ip, event, app_version="", write_history=False):
        self._audit(conn, "client", f"{event}_rejected", "installation" if inst_id else "license",
                    inst_id or (license_id or "unknown"), license_id, None, {"code": code}, code, ip)

    def activate(self, key, installation_id, device_name, app_version, os_info, ip):
        installation_id_ = installation_id
        device_name, app_version, os_info = self._validate_client_fields(installation_id, device_name, app_version, os_info)
        ip = norm_ip(ip)
        if not key or len(str(key)) > 64:
            raise LicenseError("INVALID_KEY", http=403)
        key_hash = security.hash_key(key, self.pepper)
        conn = self.conn()
        rejection = None
        result = None
        try:
            with db.transaction(conn):       # serializes concurrent activations: the limit cannot be exceeded
                lic = conn.execute("SELECT * FROM licenses WHERE key_hash=?", (key_hash,)).fetchone()
                inst = conn.execute("SELECT * FROM installations WHERE id=?", (installation_id_,)).fetchone()
                now = db.utcnow()
                if not lic:
                    rejection = "INVALID_KEY"
                    self._reject(conn, rejection, None, None, ip, "activation")
                else:
                    eff = self._effective_license_status(lic)
                    consumes_slot = inst is None or inst["status"] == "deactivated"
                    if eff != "active":
                        rejection = {"suspended": "LICENSE_SUSPENDED", "revoked": "LICENSE_REVOKED", "expired": "LICENSE_EXPIRED"}[eff]
                    elif inst is not None and inst["license_id"] != lic["id"]:
                        rejection = "INSTALLATION_CONFLICT"
                    elif self._ip_restriction(conn, ip, "block_authorization", lic["id"]) or \
                            (consumes_slot and self._ip_restriction(conn, ip, "block_activation", lic["id"])):
                        rejection = "IP_BLOCKED"
                    elif inst is not None and inst["status"] == "locked":
                        rejection = "INSTALLATION_LOCKED"
                    elif consumes_slot and self._usage(conn, lic["id"]) >= lic["max_installations"]:
                        rejection = "LIMIT_REACHED"
                    if rejection:
                        self._reject(conn, rejection, installation_id_ if inst else None, lic["id"], ip, "activation")
                        if inst is not None and inst["license_id"] == lic["id"]:
                            conn.execute("INSERT INTO ip_history(installation_id,license_id,ip,ts,app_version,event,result) VALUES (?,?,?,?,?,?,?)",
                                         (installation_id_, lic["id"], ip, now, app_version, "activation", f"denied:{rejection}"))
                            conn.execute("UPDATE installations SET last_ip=? WHERE id=?", (ip, installation_id_))
                    else:
                        secret = security.new_secret()
                        if inst is None:
                            conn.execute("INSERT INTO installations(id,license_id,device_name,app_version,os_info,secret_hash,status,activated_at,last_checkin_at,last_ip)"
                                         " VALUES (?,?,?,?,?,?,'active',?,?,?)",
                                         (installation_id_, lic["id"], device_name, app_version, os_info, security.hash_secret(secret), now, now, ip))
                            action = "installation_activated"
                        else:   # known PC re-activating (reinstall) or a deactivated PC coming back
                            conn.execute("UPDATE installations SET device_name=?,app_version=?,os_info=?,secret_hash=?,status='active',"
                                         "last_checkin_at=?,last_ip=?,deactivated_at=NULL,deactivated_reason='' WHERE id=?",
                                         (device_name, app_version, os_info, security.hash_secret(secret), now, ip, installation_id_))
                            action = "installation_reactivated" if consumes_slot else "installation_reactivated_same_slot"
                        conn.execute("INSERT INTO ip_history(installation_id,license_id,ip,ts,app_version,event,result) VALUES (?,?,?,?,?,?,?)",
                                     (installation_id_, lic["id"], ip, now, app_version, "activation", "ok"))
                        self._audit(conn, f"client:{installation_id_}", action, "installation", installation_id_, lic["id"],
                                    None, {"device_name": device_name, "ip": ip}, "", ip)
                        token, exp = self._make_token(lic["id"], installation_id_)
                        result = self._auth_response(token, exp)
                        result.update({"installation_id": installation_id_, "installation_secret": secret})
        finally:
            conn.close()
        if rejection:
            raise LicenseError(rejection, http=403)
        return result

    def _authenticate_installation(self, conn, installation_id, secret):
        inst = conn.execute("SELECT * FROM installations WHERE id=?", (str(installation_id or ""),)).fetchone()
        if inst is None:
            security.hash_secret(secret or "")
            raise LicenseError("INVALID_CREDENTIALS", http=401)
        if not security.secret_matches(secret or "", inst["secret_hash"]):
            raise LicenseError("INVALID_CREDENTIALS", http=401)
        return inst

    def _denial_code(self, conn, inst, lic, ip):
        """The single place that decides whether an installation may be authorized right now (None = allowed).
        Used by check-in (which then issues a token) and by poll (which only reports the decision)."""
        eff = self._effective_license_status(lic)
        if inst["status"] == "deactivated":
            return "INSTALLATION_DEACTIVATED"
        if inst["status"] == "locked":
            return "INSTALLATION_LOCKED"
        if eff != "active":
            return {"suspended": "LICENSE_SUSPENDED", "revoked": "LICENSE_REVOKED", "expired": "LICENSE_EXPIRED"}[eff]
        if self._ip_restriction(conn, ip, "block_authorization", lic["id"]):
            return "IP_BLOCKED"
        return None

    def checkin(self, installation_id, secret, app_version, ip):
        ip, app_version = norm_ip(ip), _clean(app_version, 40)
        conn = self.conn()
        rejection, result = None, None
        try:
            with db.transaction(conn):
                inst = self._authenticate_installation(conn, installation_id, secret)
                lic = conn.execute("SELECT * FROM licenses WHERE id=?", (inst["license_id"],)).fetchone()
                now = db.utcnow()
                rejection = self._denial_code(conn, inst, lic, ip)
                conn.execute("INSERT INTO ip_history(installation_id,license_id,ip,ts,app_version,event,result) VALUES (?,?,?,?,?,?,?)",
                             (inst["id"], lic["id"], ip, now, app_version, "checkin", f"denied:{rejection}" if rejection else "ok"))
                if rejection:
                    conn.execute("UPDATE installations SET last_ip=? WHERE id=?", (ip, inst["id"]))
                else:
                    conn.execute("UPDATE installations SET last_ip=?, last_checkin_at=?, app_version=COALESCE(NULLIF(?,''),app_version) WHERE id=?",
                                 (ip, now, app_version, inst["id"]))
                    token, exp = self._make_token(lic["id"], inst["id"])
                    result = self._auth_response(token, exp)
        finally:
            conn.close()
        if rejection:
            raise LicenseError(rejection, http=403)
        return result

    def deactivate_self(self, installation_id, secret, ip):
        ip = norm_ip(ip)
        conn = self.conn()
        try:
            with db.transaction(conn):
                inst = self._authenticate_installation(conn, installation_id, secret)
                if inst["status"] == "locked":
                    # a locked PC must not be able to clear its own lock by deactivating + re-activating
                    raise LicenseError("INSTALLATION_LOCKED", http=403)
                if inst["status"] != "deactivated":
                    conn.execute("UPDATE installations SET status='deactivated', deactivated_at=?, deactivated_reason=? WHERE id=?",
                                 (db.utcnow(), "Deactivated by the customer (PC replaced)", inst["id"]))
                    conn.execute("INSERT INTO ip_history(installation_id,license_id,ip,ts,app_version,event,result) VALUES (?,?,?,?,?,?,?)",
                                 (inst["id"], inst["license_id"], ip, db.utcnow(), "", "deactivation", "ok"))
                    self._audit(conn, f"client:{inst['id']}", "installation_deactivated", "installation", inst["id"],
                                inst["license_id"], {"status": inst["status"]}, {"status": "deactivated"}, "Customer deactivated this PC", ip)
        finally:
            conn.close()
        return {"deactivated": True}

    # ------------------------------------------------------------------ remote commands
    def poll(self, installation_id, secret, ip):
        """Light, frequent call from a PC (default every 30 s): reports the server's CURRENT decision for this
        installation and hands over any undelivered commands. It never issues a token and writes history only
        when something changed (new public IP, command delivered/expired), so frequent polling stays cheap."""
        ip = norm_ip(ip)
        conn = self.conn()
        try:
            inst = self._authenticate_installation(conn, installation_id, secret)
            lic = conn.execute("SELECT * FROM licenses WHERE id=?", (inst["license_id"],)).fetchone()
            code = self._denial_code(conn, inst, lic, ip)
            now = db.utcnow()
            rows = conn.execute(
                "SELECT c.id AS cid, c.command_type, c.issued_at, c.expires_at, d.status AS dstatus "
                "FROM remote_command_deliveries d JOIN remote_commands c ON c.id=d.command_id "
                "WHERE d.installation_id=? AND d.status IN ('pending','delivered') ORDER BY c.issued_at, c.id",
                (inst["id"],)).fetchall()
            expired = [r for r in rows if r["expires_at"] < now]
            live = [r for r in rows if r["expires_at"] >= now]
            ip_changed = ip != "unknown" and ip != (inst["last_ip"] or "")
            if expired or ip_changed or any(r["dstatus"] == "pending" for r in live):
                with db.transaction(conn):
                    for r in expired:
                        conn.execute("UPDATE remote_command_deliveries SET status='expired' "
                                     "WHERE command_id=? AND installation_id=? AND status IN ('pending','delivered')",
                                     (r["cid"], inst["id"]))
                    for r in live:
                        conn.execute("UPDATE remote_command_deliveries SET status='delivered', delivered_at=COALESCE(delivered_at, ?) "
                                     "WHERE command_id=? AND installation_id=? AND status IN ('pending','delivered')",
                                     (now, r["cid"], inst["id"]))
                    if ip_changed:
                        conn.execute("INSERT INTO ip_history(installation_id,license_id,ip,ts,app_version,event,result) VALUES (?,?,?,?,?,?,?)",
                                     (inst["id"], lic["id"], ip, now, inst["app_version"] or "", "poll", f"denied:{code}" if code else "ok"))
                        conn.execute("UPDATE installations SET last_ip=? WHERE id=?", (ip, inst["id"]))
        finally:
            conn.close()
        return {"server_time": now, "poll_interval_seconds": self.s.poll_interval_seconds,
                "authorized": code is None, "code": code or "OK",
                # the owner's internal reason is deliberately NOT sent to the customer's PC
                "commands": [{"command_id": r["cid"], "command_type": r["command_type"],
                              "issued_at": r["issued_at"], "expires_at": r["expires_at"]} for r in live]}

    def ack_command(self, installation_id, secret, command_id, status, result, ip):
        """A PC reports what happened after it received a command. Authenticated with the installation secret,
        so one PC can never acknowledge (or forge) another PC's command. Idempotent."""
        if status not in ("applied", "rejected"):
            raise LicenseError("VALIDATION", "status must be 'applied' or 'rejected'.")
        ip = norm_ip(ip)
        conn = self.conn()
        try:
            with db.transaction(conn):
                inst = self._authenticate_installation(conn, installation_id, secret)
                d = conn.execute("SELECT * FROM remote_command_deliveries WHERE command_id=? AND installation_id=?",
                                 (str(command_id or ""), inst["id"])).fetchone()
                if d is None:
                    raise LicenseError("NOT_FOUND", "Unknown command for this installation.", 404)
                if d["status"] in ("applied", "rejected"):
                    return {"acknowledged": True, "status": d["status"]}
                if d["status"] == "expired":
                    return {"acknowledged": False, "status": "expired"}
                conn.execute("UPDATE remote_command_deliveries SET status=?, acknowledged_at=?, result=? "
                             "WHERE command_id=? AND installation_id=?",
                             (status, db.utcnow(), _clean(result, 300), d["command_id"], inst["id"]))
                self._audit(conn, f"client:{inst['id']}", f"command_{status}", "command", d["command_id"],
                            inst["license_id"], None, {"status": status}, _clean(result, 300), ip)
                return {"acknowledged": True, "status": status}
        finally:
            conn.close()

    def issue_command(self, actor, license_id, command_type, installation_id=None, reason=""):
        spec = COMMAND_TYPES.get(str(command_type or "").upper())
        if spec is None:
            raise LicenseError("VALIDATION", "Unknown command. Allowed: " + ", ".join(COMMAND_TYPES))
        command_type = str(command_type).upper()
        kind, action = spec
        reason = _clean(reason, 300)
        if command_type != "FORCE_REVALIDATE" and len(reason) < 3:
            raise LicenseError("VALIDATION", "A reason is required for this command (it is saved in the audit log).")
        self.get_license(license_id)                                   # 404 if unknown
        if kind == "license" and installation_id:
            raise LicenseError("VALIDATION", "This command applies to the whole license, not one PC.")
        if kind == "device" or installation_id:
            if not installation_id:
                raise LicenseError("VALIDATION", "Choose the PC this command is for.")
            target = self._inst_by_id(installation_id)
            if int(target["license_id"]) != int(license_id):
                raise LicenseError("VALIDATION", "That PC does not belong to this license.", 400)
        warning = None
        # 1) authoritative change first: an invalid transition raises here and nothing is queued
        if kind == "license":
            self.set_license_status(actor, license_id, action, reason)
        elif kind == "device":
            out = getattr(self, f"{action}_installation")(actor, installation_id, reason)
            warning = (out or {}).get("warning")
        # 2) queue delivery to the affected PCs
        cmd_id = uuid.uuid4().hex
        now = db.utcnow()
        expires = (datetime.now(timezone.utc) + timedelta(hours=self.s.command_ttl_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self.conn()
        try:
            with db.transaction(conn):
                if installation_id:
                    targets = [str(installation_id)]
                else:
                    targets = [r["id"] for r in conn.execute(
                        "SELECT id FROM installations WHERE license_id=? AND status IN ('active','locked') ORDER BY id", (license_id,)).fetchall()]
                conn.execute("INSERT INTO remote_commands(id,license_id,installation_id,command_type,reason,issued_by,issued_at,expires_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (cmd_id, license_id, installation_id, command_type, reason, actor, now, expires))
                for t in targets:
                    conn.execute("INSERT INTO remote_command_deliveries(command_id,installation_id,status) VALUES (?,?,'pending')", (cmd_id, t))
                self._audit(conn, actor, "command_issued", "command", cmd_id, license_id, None,
                            {"type": command_type, "installation_id": installation_id, "pcs": len(targets)}, reason)
        finally:
            conn.close()
        out = self.command_detail(cmd_id)
        if warning:
            out["warning"] = warning
        return out

    @staticmethod
    def _delivery_summary(cmd, deliveries, now):
        counts = {"pending": 0, "delivered": 0, "applied": 0, "rejected": 0, "expired": 0}
        for d in deliveries:
            st = d["status"]
            if st in ("pending", "delivered") and cmd["expires_at"] < now:
                st = "expired"                                     # shown as expired even before a poll marks it
            counts[st] += 1
        total = len(deliveries)
        if total == 0:
            overall = "no_pcs"
        elif counts["applied"] == total:
            overall = "complete"
        elif counts["rejected"]:
            overall = "problem"
        elif counts["pending"] + counts["delivered"]:
            overall = "waiting"
        else:
            overall = "expired"
        return {"total": total, **counts, "overall": overall}

    def list_commands(self, license_id=None, limit=200):
        conn = self.conn()
        try:
            q = ("SELECT c.*, l.label AS license_label FROM remote_commands c JOIN licenses l ON l.id=c.license_id")
            args = []
            if license_id is not None:
                q += " WHERE c.license_id=?"; args.append(int(license_id))
            q += " ORDER BY c.issued_at DESC, c.id DESC LIMIT ?"
            args.append(max(1, min(int(limit), 500)))
            cmds = [dict(r) for r in conn.execute(q, args).fetchall()]
            if not cmds:
                return []
            marks = ",".join("?" for _ in cmds)
            by_cmd = {}
            for r in conn.execute(f"SELECT command_id, status FROM remote_command_deliveries WHERE command_id IN ({marks})",
                                  [c["id"] for c in cmds]).fetchall():
                by_cmd.setdefault(r["command_id"], []).append(r)
            now = db.utcnow()
            for c in cmds:
                c["summary"] = self._delivery_summary(c, by_cmd.get(c["id"], []), now)
            return cmds
        finally:
            conn.close()

    def command_detail(self, command_id):
        conn = self.conn()
        try:
            c = conn.execute("SELECT c.*, l.label AS license_label FROM remote_commands c JOIN licenses l ON l.id=c.license_id "
                             "WHERE c.id=?", (str(command_id),)).fetchone()
            if c is None:
                raise LicenseError("NOT_FOUND", "Command not found.", 404)
            c = dict(c)
            rows = conn.execute("SELECT d.*, i.device_name FROM remote_command_deliveries d JOIN installations i ON i.id=d.installation_id "
                                "WHERE d.command_id=? ORDER BY i.device_name, d.installation_id", (c["id"],)).fetchall()
            now = db.utcnow()
            c["summary"] = self._delivery_summary(c, rows, now)
            c["deliveries"] = []
            for r in rows:
                r = dict(r)
                if r["status"] in ("pending", "delivered") and c["expires_at"] < now:
                    r["status"] = "expired"
                c["deliveries"].append(r)
            return c
        finally:
            conn.close()
