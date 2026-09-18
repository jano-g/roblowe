"""Jeden používateľ (admin). scrypt hash, podpísaná HttpOnly cookie s session version,
throttling loginu podľa IP z posledného hopu X-Forwarded-For (len za loopback proxy)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Request
from itsdangerous import BadSignature, TimestampSigner

from . import config, db

COOKIE = "roblowe_session"
COOKIE_MAX_AGE = 14 * 24 * 3600
LOGIN_MAX_ATTEMPTS = 6
LOGIN_WINDOW = 15 * 60

_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, h_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(h_b64)
    except (ValueError, TypeError):
        return False
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(h, expected)  # bytes, nie str (diakritika)


def bootstrap_admin() -> None:
    """Pri prázdnej DB vytvorí admina z env. Heslo z env platí len pri prvom štarte."""
    if db.q1("SELECT 1 FROM users LIMIT 1"):
        return
    if config.ALLOW_OPEN and not config.ADMIN_PASSWORD:
        pw = secrets.token_urlsafe(16)
    else:
        pw = config.ADMIN_PASSWORD
    with db.tx():
        db.run(
            "INSERT INTO users(username, password_hash, session_version, created_at) VALUES (?,?,1,?)",
            (config.ADMIN_USERNAME, hash_password(pw), db.now_iso()),
        )
        db.log_history("system", "admin_created", {"username": config.ADMIN_USERNAME})


def set_password(username: str, password: str) -> bool:
    u = db.q1("SELECT id FROM users WHERE username=?", (username,))
    if not u:
        return False
    with db.tx():
        db.run(
            "UPDATE users SET password_hash=?, session_version=session_version+1 WHERE id=?",
            (hash_password(password), u["id"]),
        )
        db.log_history(username, "password_changed")
    return True


def _signer() -> TimestampSigner:
    return TimestampSigner(config.secret_key(), salt="session")


def make_cookie(user_id: int, session_version: int) -> str:
    return _signer().sign(f"{user_id}:{session_version}".encode()).decode()


def user_from_cookie(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        raw = _signer().unsign(value, max_age=COOKIE_MAX_AGE).decode()
        uid_s, ver_s = raw.split(":")
        uid, ver = int(uid_s), int(ver_s)
    except (BadSignature, ValueError):
        return None
    u = db.row("SELECT id, username, session_version FROM users WHERE id=?", (uid,))
    if not u or u["session_version"] != ver:
        return None
    return u


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    xff = request.headers.get("x-forwarded-for")
    if xff and peer in ("127.0.0.1", "::1"):
        return xff.split(",")[-1].strip() or peer  # posledný hop = nginx-om doplnená IP
    return peer


def throttled(ip: str) -> bool:
    now = time.time()
    with _lock:
        hits = [t for t in _attempts.get(ip, []) if now - t < LOGIN_WINDOW]
        _attempts[ip] = hits
        if len(_attempts) > 5000:  # prune, ale nikdy nevymaž aktívne blokovanie
            for k in [k for k, v in _attempts.items() if not v]:
                _attempts.pop(k, None)
        return len(hits) >= LOGIN_MAX_ATTEMPTS


def record_failure(ip: str) -> None:
    with _lock:
        _attempts.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    with _lock:
        _attempts.pop(ip, None)


def login(username: str, password: str) -> dict | None:
    u = db.row("SELECT id, username, password_hash, session_version FROM users WHERE username=?", (username,))
    if not u or not verify_password(password, u["password_hash"]):
        return None
    return u


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"
