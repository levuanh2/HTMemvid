"""
Redis client fail-open cho tầng cache (semantic/retrieval).

Nguyên tắc: cache là tối ưu, KHÔNG phải đường chính — mọi lỗi Redis chỉ được
làm cache miss, không bao giờ raise vào đường trả lời. REDIS_URL rỗng (dev
Windows không chạy Redis) → mọi tầng cache tự tắt, zero cost.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

logger = logging.getLogger(__name__)

_UNAVAILABLE_RETRY_SEC = 60.0

_lock = threading.Lock()
_client: Optional[Any] = None
_injected: bool = False          # test đã inject fake client qua reset_for_tests
_unavailable_until: float = 0.0
_warned_once: bool = False


def _enabled() -> bool:
    if (os.getenv("CACHE_ENABLED", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool((os.getenv("REDIS_URL") or "").strip())


def get_redis() -> Optional[Any]:
    """Trả client Redis sẵn dùng, hoặc None (disabled / đang trong cửa sổ unavailable)."""
    global _client, _unavailable_until, _warned_once
    now = time.time()
    if _injected:
        # client fake của test cũng tôn trọng cửa sổ unavailable (test fail-open)
        return None if now < _unavailable_until else _client
    if not _enabled():
        return None
    if now < _unavailable_until:
        return None
    if _client is not None:
        return _client
    with _lock:
        if _client is not None or time.time() < _unavailable_until:
            return _client
        try:
            import redis  # lazy: dep chỉ cần khi REDIS_URL được set

            c = redis.from_url(
                os.environ["REDIS_URL"],
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            c.ping()
            _client = c
            logger.info("[cache] Redis connected: %s", os.environ["REDIS_URL"])
        except Exception as e:
            _unavailable_until = time.time() + _UNAVAILABLE_RETRY_SEC
            if not _warned_once:
                logger.warning("[cache] Redis unavailable, cache disabled (fail-open): %s", e)
                _warned_once = True
        return _client


def mark_unavailable() -> None:
    """Gọi khi một op Redis lỗi giữa chừng — mở lại cửa sổ retry để không timeout mỗi request."""
    global _client, _unavailable_until
    _unavailable_until = time.time() + _UNAVAILABLE_RETRY_SEC
    if not _injected:
        _client = None


def reset_for_tests(client: Optional[Any] = None) -> None:
    """Inject fake client (test). Truyền None để trả về hành vi thật."""
    global _client, _injected, _unavailable_until, _warned_once
    _client = client
    _injected = client is not None
    _unavailable_until = 0.0
    _warned_once = False
