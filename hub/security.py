"""Password hashing, session tokens and integration tokens.

Passwords use scrypt from the Python standard library with a random salt.
Session and integration tokens are random 256-bit values; only their SHA-256
hashes are stored in the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=len(expected)
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(set(password)) < 4:
        problems.append("Use a less repetitive password.")
    if email and password.lower() == email.lower():
        problems.append("The password must not be the same as the email address.")
    if password.lower() in {"password123", "relayhub123", "welcome1234", "1234567890"}:
        problems.append("That password is too common.")
    return problems


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or ""))


class LoginThrottle:
    """In-memory per-IP throttle on failed logins (in addition to per-account lockout)."""

    def __init__(self, max_failures: int = 20, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._fail: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._fail.get(ip, []) if now - t < self.window]
            self._fail[ip] = hits
            return len(hits) >= self.max_failures

    def record_failure(self, ip: str) -> None:
        with self._lock:
            self._fail.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fail.pop(ip, None)
