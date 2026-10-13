"""Local user accounts, sessions and read-only integration tokens."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .. import audit
from ..db import get_setting, jdump, jload, tx, utcnow
from ..errors import Conflict, Forbidden, HubError, NotFound, Unauthorized, ValidationFailed
from ..fields import registry
from ..permissions import ACTIONS, ADMIN_ONLY, ROLES, CurrentUser
from ..security import hash_password, new_token, password_problems, token_hash, valid_email, verify_password

LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def setup_required(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def _check_password(pw: str, email: str) -> None:
    probs = password_problems(pw or "", email)
    if probs:
        raise ValidationFailed(" ".join(probs), details={"fields": {"password": " ".join(probs)}})


def create_first_admin(conn, email: str, name: str, password: str, ip: str) -> int:
    email = (email or "").strip().lower()
    if not valid_email(email):
        raise ValidationFailed("Enter a valid email address as the username.")
    if not (name or "").strip():
        raise ValidationFailed("Enter a display name.")
    _check_password(password, email)
    pw_hash = hash_password(password)
    with tx(conn):
        if not setup_required(conn):
            raise Forbidden("Setup has already been completed.")
        now = utcnow()
        uid = conn.execute(
            "INSERT INTO users(email, display_name, password_hash, role, password_changed_at, created_at, created_by, updated_at, updated_by) "
            "VALUES (?,?,?, 'admin', ?, ?, 'first-run setup', ?, 'first-run setup')",
            (email, name.strip(), pw_hash, now, now, now)).lastrowid
        audit.log(conn, email, "user.first_admin_created", "user", uid, {"email": email}, ip)
    return uid


def login(conn, email: str, password: str, ip: str, user_agent: str, throttle) -> tuple[str, dict]:
    email = (email or "").strip().lower()
    if throttle.blocked(ip):
        raise HubError("Too many failed sign-in attempts from this computer. Wait 15 minutes and try again.", status=429)
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    now = datetime.now(timezone.utc)
    if row and row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
        raise HubError("This account is temporarily locked after repeated failed sign-ins. Try again later or ask an "
                       "administrator to reset the password.", status=429)
    ok = row is not None and verify_password(password or "", row["password_hash"])
    if not ok:
        throttle.record_failure(ip)
        if row is not None:
            with tx(conn):
                attempts = row["failed_attempts"] + 1
                locked = (now + timedelta(minutes=LOCKOUT_MINUTES)).replace(microsecond=0).isoformat() if attempts >= LOCKOUT_ATTEMPTS else None
                conn.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                             (0 if locked else attempts, locked, row["id"]))
                audit.log(conn, email, "auth.login_failed", "user", row["id"], {"locked": bool(locked)}, ip)
        raise Unauthorized("The email or password is not correct.")
    if not row["is_active"]:
        raise Unauthorized("This account is disabled. Ask an administrator.")
    throttle.reset(ip)
    token = new_token()
    csrf = new_token(24)
    absolute = int(get_setting(conn, "session_absolute_hours", 12))
    with tx(conn):
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))
        conn.execute("INSERT INTO sessions(id, user_id, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?,?,?)",
                     (token_hash(token), row["id"], csrf, utcnow(), utcnow(),
                      (now + timedelta(hours=absolute)).replace(microsecond=0).isoformat(), ip, (user_agent or "")[:200]))
        conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (utcnow(), row["id"]))
        audit.log(conn, email, "auth.login", "user", row["id"], None, ip)
    return token, {"csrf_token": csrf}


def logout(conn, user: CurrentUser) -> None:
    with tx(conn):
        conn.execute("DELETE FROM sessions WHERE id = ?", (user.session_id,))
        audit.log(conn, user.email, "auth.logout", "user", user.id, None, user.ip)


def change_password(conn, user: CurrentUser, current: str, new: str) -> None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,)).fetchone()
    if not verify_password(current or "", row["password_hash"]):
        raise ValidationFailed("The current password is not correct.")
    if current == new:
        raise ValidationFailed("Choose a new password that is different from the current one.")
    _check_password(new, user.email)
    with tx(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, password_changed_at = ?, updated_at = ?, updated_by = ? "
                     "WHERE id = ?", (hash_password(new), utcnow(), utcnow(), user.email, user.id))
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?", (user.id, user.session_id))
        audit.log(conn, user.email, "auth.password_changed", "user", user.id, None, user.ip)


# ---------------------------------------------------------------- administration

def user_public(r) -> dict:
    return {"id": r["id"], "email": r["email"], "display_name": r["display_name"], "role": r["role"],
            "role_label": ROLES[r["role"]]["label"], "is_active": bool(r["is_active"]),
            "must_change_password": bool(r["must_change_password"]), "location_scope": jload(r["location_scope"], []),
            "extra_permissions": jload(r["extra_permissions"], []), "revoked_permissions": jload(r["revoked_permissions"], []),
            "restricted_fields": jload(r["restricted_fields"], []), "dob_access": r["dob_access"],
            "last_login_at": r["last_login_at"], "locked": bool(r["locked_until"] and r["locked_until"] > utcnow()),
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_users(conn) -> list[dict]:
    return [user_public(r) for r in conn.execute("SELECT * FROM users ORDER BY email").fetchall()]


def _validate_profile(conn, data: dict) -> dict:
    out = {}
    if "role" in data:
        if data["role"] not in ROLES:
            raise ValidationFailed("Choose a valid role.")
        out["role"] = data["role"]
    if "display_name" in data:
        if not str(data["display_name"]).strip():
            raise ValidationFailed("Display name is required.")
        out["display_name"] = str(data["display_name"]).strip()
    locations = get_setting(conn, "locations", [])
    if "location_scope" in data:
        ls = data["location_scope"] or []
        if any(l not in locations for l in ls):
            raise ValidationFailed("Unknown location in the location scope.")
        out["location_scope"] = jdump(ls)
    for key in ("extra_permissions", "revoked_permissions"):
        if key in data:
            vals = data[key] or []
            if any(v not in ACTIONS for v in vals):
                raise ValidationFailed("Unknown permission.")
            if key == "extra_permissions" and any(v in ADMIN_ONLY for v in vals):
                raise ValidationFailed("Administrator-only permissions can only be given through the Administrator role.")
            out[key] = jdump(vals)
    if "restricted_fields" in data:
        keys = {f.key for f in registry(conn)}
        vals = data["restricted_fields"] or []
        if any(v not in keys or v in ("id", "rhc_id", "full_name") for v in vals):
            raise ValidationFailed("Unknown or unrestrictable field.")
        out["restricted_fields"] = jdump(vals)
    if "dob_access" in data:
        if data["dob_access"] not in ("full", "day_month", "none"):
            raise ValidationFailed("Choose a date of birth access level.")
        out["dob_access"] = data["dob_access"]
    if "is_active" in data:
        out["is_active"] = 1 if data["is_active"] else 0
    return out


def temp_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


def create_user(conn, admin: CurrentUser, data: dict) -> tuple[dict, str]:
    email = (data.get("email") or "").strip().lower()
    if not valid_email(email):
        raise ValidationFailed("Enter a valid email address as the username.")
    fields = _validate_profile(conn, {"role": data.get("role", "viewer"), "display_name": data.get("display_name") or email,
                                      **{k: v for k, v in data.items() if k in ("location_scope", "extra_permissions",
                                                                                "revoked_permissions", "restricted_fields", "dob_access")}})
    pw = temp_password()
    now = utcnow()
    with tx(conn):
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise Conflict("A user with this email already exists.")
        cols = ["email", "password_hash", "must_change_password", "created_at", "created_by", "updated_at", "updated_by", *fields]
        vals = [email, hash_password(pw), 1, now, admin.email, now, admin.email, *fields.values()]
        uid = conn.execute(f"INSERT INTO users({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals).lastrowid
        audit.log(conn, admin.email, "user.created", "user", uid, {"email": email, "role": fields["role"]}, admin.ip)
    return user_public(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()), pw


def _admin_count(conn, exclude: int | None = None) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id IS NOT ?", (exclude,)).fetchone()[0]


def update_user(conn, admin: CurrentUser, user_id: int, data: dict) -> dict:
    fields = _validate_profile(conn, data)
    with tx(conn):
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise NotFound("User not found.")
        demoting = row["role"] == "admin" and (fields.get("role", "admin") != "admin" or fields.get("is_active", 1) == 0)
        if demoting and _admin_count(conn, exclude=user_id) == 0:
            raise ValidationFailed("There must always be at least one active administrator.")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE users SET {sets}, updated_at = ?, updated_by = ? WHERE id = ?",
                         [*fields.values(), utcnow(), admin.email, user_id])
        if fields.get("is_active") == 0 or "role" in fields:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit.log(conn, admin.email, "user.updated", "user", user_id, {"email": row["email"], "changes": data}, admin.ip)
    return user_public(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def reset_password(conn, admin: CurrentUser, user_id: int) -> str:
    pw = temp_password()
    with tx(conn):
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise NotFound("User not found.")
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL, "
                     "updated_at = ?, updated_by = ? WHERE id = ?", (hash_password(pw), utcnow(), admin.email, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit.log(conn, admin.email, "user.password_reset", "user", user_id, {"email": row["email"]}, admin.ip)
    return pw


# ---------------------------------------------------------------- integration tokens

SENSITIVE_FOR_TOKENS = {"date_of_birth", "notes"}


def token_public(r) -> dict:
    return {"id": r["id"], "name": r["name"], "prefix": r["token_prefix"], "location_scope": jload(r["location_scope"], []),
            "allowed_fields": jload(r["allowed_fields"], []), "include_photos": bool(r["include_photos"]),
            "created_by": r["created_by"], "created_at": r["created_at"], "revoked_at": r["revoked_at"],
            "last_used_at": r["last_used_at"]}


def create_token(conn, admin: CurrentUser, data: dict) -> tuple[dict, str]:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationFailed("Give the token a name that says which tool uses it.")
    fields = {f.key: f for f in registry(conn)}
    allowed = data.get("allowed_fields") or ["rhc_id", "full_name", "designation", "department", "official_email", "location"]
    bad = [k for k in allowed if k not in fields or k in SENSITIVE_FOR_TOKENS or fields[k].sensitive or k == "photo"]
    if bad:
        raise ValidationFailed("These fields cannot be shared with integration tokens: " + ", ".join(bad))
    locations = get_setting(conn, "locations", [])
    scope = data.get("location_scope") or []
    if any(l not in locations for l in scope):
        raise ValidationFailed("Unknown location.")
    raw = "rhub_" + new_token()
    with tx(conn):
        tid = conn.execute("INSERT INTO api_tokens(name, token_hash, token_prefix, location_scope, allowed_fields, include_photos, "
                           "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (name, token_hash(raw), raw[:10], jdump(scope), jdump(sorted(set(allowed) | {"rhc_id"})),
                            1 if data.get("include_photos") else 0, admin.email, utcnow())).lastrowid
        audit.log(conn, admin.email, "token.created", "api_token", tid, {"name": name, "scope": scope}, admin.ip)
    return token_public(conn.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()), raw


def revoke_token(conn, admin: CurrentUser, token_id: int) -> None:
    with tx(conn):
        n = conn.execute("UPDATE api_tokens SET revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked_at IS NULL",
                         (utcnow(), admin.email, token_id)).rowcount
        if not n:
            raise NotFound("Token not found or already revoked.")
        audit.log(conn, admin.email, "token.revoked", "api_token", token_id, None, admin.ip)


def list_tokens(conn) -> list[dict]:
    return [token_public(r) for r in conn.execute("SELECT * FROM api_tokens ORDER BY id DESC").fetchall()]


def token_from_header(conn, header: str | None) -> dict:
    if not header or not header.lower().startswith("bearer "):
        raise Unauthorized("Provide an integration token as 'Authorization: Bearer <token>'.")
    raw = header.split(" ", 1)[1].strip()
    r = conn.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash(raw),)).fetchone()
    if r is None or r["revoked_at"]:
        raise Unauthorized("The integration token is not valid or has been revoked.")
    conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (utcnow(), r["id"]))
    return dict(r)
