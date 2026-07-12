"""Stateless bearer token — itsdangerous signed, no server-side token table.

Payload: {"uid": <user_id>, "tv": <token_version>}. Signature + expiry are
verified here; the token_version match against the current user row is checked in
auth.service (needs a DB lookup). The signing secret comes from AUTH_SECRET;
when absent it falls back to a per-process ephemeral secret in dev (with a
warning), or fails loudly when AUTH_REQUIRE_SECRET=true. The secret is never logged.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

_log = logging.getLogger(__name__)
_SALT = "mv-auth-v1"
_ephemeral_secret: Optional[str] = None
_warned = False


def _truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _get_secret() -> str:
    global _ephemeral_secret, _warned
    secret = (os.getenv("AUTH_SECRET") or "").strip()
    if secret:
        return secret
    if _truthy("AUTH_REQUIRE_SECRET"):
        raise RuntimeError("AUTH_SECRET is required (AUTH_REQUIRE_SECRET=true) but not set.")
    # Dev fallback: ephemeral per-process secret. Tokens do NOT survive a restart.
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_hex(32)
    if not _warned:
        _log.warning("AUTH_SECRET not set — using an ephemeral per-process secret (dev only; "
                     "tokens invalidate on restart). Set AUTH_SECRET for persistence.")
        _warned = True
    return _ephemeral_secret


def token_ttl_sec() -> int:
    try:
        return int((os.getenv("AUTH_TOKEN_TTL_SEC") or "").strip() or 604800)
    except (TypeError, ValueError):
        return 604800


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt=_SALT)


def make_token(user: dict) -> str:
    return _serializer().dumps({"uid": user["user_id"], "tv": int(user.get("token_version", 1))})


def read_token(token: str, max_age: Optional[int] = None) -> Optional[dict]:
    """Return the payload dict, or None if the token is missing/tampered/expired.

    Signature and age are verified here. token_version is validated by the caller
    (auth.service) against the current user row.
    """
    if not token:
        return None
    age = token_ttl_sec() if max_age is None else max_age
    try:
        data = _serializer().loads(token, max_age=age)
    except (SignatureExpired, BadData):
        return None
    except Exception:  # noqa: BLE001 — any decode failure is an auth failure
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    return data
