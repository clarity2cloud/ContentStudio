# app/services/cache_service.py
"""
Two-Tier Cache Service
═══════════════════════════════════════════════════════════════════════════════

Tier 1: In-process LRU + TTL (always available)
Tier 2: Redis (optional — used if REDIS_URL is set)

Used for:
  • Brand profile fetches  (TTL: 1h)
  • Brand context strings  (TTL: 1h)
  • Generation memory queries (TTL: 5m)
  • Tenant quota counters (TTL: 24h, but mutated atomically)

Designed to degrade gracefully — if Redis is down, app keeps working with in-process.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from app.utils.logger import logger

# Optional Redis import — never crash if not installed
try:
    import redis  # type: ignore
    _REDIS_AVAILABLE = True
except ImportError:
    redis = None
    _REDIS_AVAILABLE = False

# Read from settings (which reads .env) so there's a single source of truth.
# Fall back to os.getenv so the module still works if settings import fails at
# import time (e.g. in test contexts before .env is loaded).
try:
    from app.config import settings as _settings
    REDIS_URL: str = (_settings.REDIS_URL or "").strip()
except Exception:
    REDIS_URL = os.getenv("REDIS_URL", "").strip()

# ── In-process LRU cache ───────────────────────────────────────────────────


class _LRUCache:
    def __init__(self, max_items: int = 5000):
        self._max = max_items
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._data:
                return None
            expires_at, val = self._data[key]
            if expires_at and expires_at < time.time():
                del self._data[key]
                return None
            # bump to most recently used
            self._data.move_to_end(key)
            return val

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            expires_at = time.time() + ttl if ttl > 0 else 0
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear_prefix(self, prefix: str) -> int:
        n = 0
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                del self._data[k]
                n += 1
        return n

    def stats(self) -> dict:
        with self._lock:
            return {"items": len(self._data), "max": self._max}


# ── Redis adapter (best-effort) ────────────────────────────────────────────
class _RedisAdapter:
    def __init__(self, url: str):
        self._client = None
        self._healthy = False
        try:
            self._client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0)
            # ping to verify
            self._client.ping()
            self._healthy = True
            logger.info(f"[CACHE] Redis connected at {url[:40]}...")
        except Exception as e:
            logger.warning(
                f"[CACHE] Redis unavailable, using in-process only: {e}")
            self._healthy = False

    def healthy(self) -> bool:
        return self._healthy

    def get(self, key: str) -> Optional[str]:
        if not self._healthy:
            return None
        try:
            return self._client.get(key)
        except Exception as e:
            logger.debug(f"[CACHE] Redis get failed: {e}")
            self._healthy = False
            return None

    def set(self, key: str, value: str, ttl: int = 300) -> bool:
        if not self._healthy:
            return False
        try:
            return bool(self._client.setex(key, ttl, value))
        except Exception as e:
            logger.debug(f"[CACHE] Redis set failed: {e}")
            self._healthy = False
            return False

    def delete(self, key: str) -> bool:
        if not self._healthy:
            return False
        try:
            return bool(self._client.delete(key))
        except Exception:
            return False

    def incr(self, key: str, amount: int = 1, ttl: int = 86400) -> int:
        """Atomic increment; sets TTL on first creation."""
        if not self._healthy:
            return 0
        try:
            pipe = self._client.pipeline()
            pipe.incrby(key, amount)
            pipe.expire(key, ttl)
            out = pipe.execute()
            return int(out[0])
        except Exception as e:
            logger.debug(f"[CACHE] Redis incr failed: {e}")
            return 0

    def get_int(self, key: str) -> int:
        v = self.get(key)
        try:
            return int(v) if v is not None else 0
        except Exception:
            return 0


# ── Public cache facade ────────────────────────────────────────────────────
class CacheService:
    """Two-tier cache: in-process + Redis (optional)."""

    def __init__(self):
        self._mem = _LRUCache(max_items=5000)
        self._redis: Optional[_RedisAdapter] = None
        if _REDIS_AVAILABLE and REDIS_URL:
            self._redis = _RedisAdapter(REDIS_URL)
            if not self._redis.healthy():
                self._redis = None

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None and self._redis.healthy()

    # ── Generic key/value (auto-JSON) ──────────────────────────────────────
    def get(self, key: str) -> Optional[Any]:
        v = self._mem.get(key)
        if v is not None:
            return v
        if self._redis:
            raw = self._redis.get(key)
            if raw is not None:
                try:
                    decoded = json.loads(raw)
                except Exception:
                    decoded = raw
                # hydrate in-process for hot path
                self._mem.set(key, decoded, ttl=60)
                return decoded
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._mem.set(key, value, ttl=ttl)
        if self._redis:
            try:
                payload = json.dumps(value) if not isinstance(
                    value, (str, int, float)) else str(value)
                self._redis.set(key, payload, ttl=ttl)
            except Exception:
                pass

    def delete(self, key: str) -> None:
        self._mem.delete(key)
        if self._redis:
            self._redis.delete(key)

    def clear_prefix(self, prefix: str) -> int:
        return self._mem.clear_prefix(prefix)

    # ── Atomic counters (for quota tracking) ───────────────────────────────
    def incr_counter(self, key: str, amount: int = 1, ttl: int = 86400) -> int:
        if self._redis:
            n = self._redis.incr(key, amount=amount, ttl=ttl)
            if n > 0:
                return n
        # in-process fallback (not strictly atomic across replicas, but fine
        # for single-pod dev)
        current = int(self._mem.get(key) or 0)
        new = current + amount
        self._mem.set(key, new, ttl=ttl)
        return new

    def get_counter(self, key: str) -> int:
        if self._redis:
            n = self._redis.get_int(key)
            if n:
                return n
        return int(self._mem.get(key) or 0)

    def stats(self) -> dict:
        return {
            "memory": self._mem.stats(),
            "redis": self.redis_enabled,
        }


# ── Singleton ──────────────────────────────────────────────────────────────
cache = CacheService()


# ── Domain-specific helpers ────────────────────────────────────────────────
def brand_key(brand_id: str) -> str:
    return f"brand:{brand_id}"


def brand_context_key(brand_id: str) -> str:
    return f"brand_ctx:{brand_id}"


def default_brand_key(user_id: str) -> str:
    return f"default_brand:{user_id}"


def quota_key(tenant_id: str, day_ymd: str) -> str:
    return f"quota:{tenant_id}:{day_ymd}"


def cost_key(tenant_id: str, day_ymd: str) -> str:
    return f"cost:{tenant_id}:{day_ymd}"


def invalidate_brand(brand_id: str, user_id: Optional[str] = None) -> None:
    """Clear all caches related to a brand. Call after brand update/delete."""
    cache.delete(brand_key(brand_id))
    cache.delete(brand_context_key(brand_id))
    if user_id:
        cache.delete(default_brand_key(user_id))
