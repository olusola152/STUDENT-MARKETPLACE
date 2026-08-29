"""Brute-force throttling for the sign-in forms.

In-memory, per process. Good enough for a single web dyno; if the app is ever
run on several processes, move this to the database or Redis.
"""

import time
from collections import defaultdict

from flask import current_app, request

_attempts = defaultdict(list)


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def login_blocked():
    """True when this address has failed too often recently."""
    window = current_app.config["LOGIN_LOCKOUT_SECONDS"]
    limit = current_app.config["LOGIN_MAX_ATTEMPTS"]
    now = time.time()

    recent = [t for t in _attempts[_client_ip()] if now - t < window]
    _attempts[_client_ip()] = recent
    return len(recent) >= limit


def record_failure():
    _attempts[_client_ip()].append(time.time())


def clear_failures():
    _attempts.pop(_client_ip(), None)
