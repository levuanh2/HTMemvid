"""Auth glue: request → user resolution, sanitised user shape, validators.

The @require_auth decorator is provided for FUTURE protected endpoints; it is NOT
applied to any existing app route in this phase (the app APIs stay open).
"""

from __future__ import annotations

import re
from functools import wraps
from typing import Optional

from flask import jsonify, request

from app.domains.auth import tokens, users_store

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MIN_PASSWORD_LEN = 8


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def valid_password(password: str) -> bool:
    return isinstance(password, str) and len(password) >= MIN_PASSWORD_LEN


def public_user(user: dict) -> dict:
    """Sanitised user for API responses — never leaks password_hash/token_version."""
    return {
        "id": user.get("user_id"),
        "email": user.get("email"),
        "display_name": user.get("display_name"),
    }


def _bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def current_user_from_request() -> Optional[dict]:
    """Resolve the authenticated user from the Bearer token, or None.

    Verifies signature + expiry (tokens.read_token) AND that the token's version
    still matches the user's current token_version (revocation check)."""
    token = _bearer_token()
    if not token:
        return None
    payload = tokens.read_token(token)
    if not payload:
        return None
    user = users_store.get_by_id(payload.get("uid"))
    if not user:
        return None
    if int(payload.get("tv", -1)) != int(user.get("token_version", -2)):
        return None
    return user


def require_auth(fn):
    """Decorator for future protected routes (unused in this phase)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user_from_request()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, _user=user, **kwargs)
    return wrapper
