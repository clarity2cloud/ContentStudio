from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.utils.logger import logger
from app.services.cache_service import cache

# Per-path rate rules (limit, window_minutes) to satisfy the security checklist.
#
# The /api/v1/auth/* and /invite rules below are forward-compatible
# placeholders: this OSS build ships with no auth router (see
# app/core/dependencies.py), so these paths 404 today. They're kept so a
# future auth router picks up sane limits automatically instead of falling
# through to the generic rule.
#
# The AI-generation rules are real and active today — these are the calls
# that cost money per request (NVIDIA image gen, Gamma carousel gen), so they
# get a stricter budget than the generic per-IP rate.
_RATE_RULES = {
    "/api/v1/auth/login": (10, 5),
    "/api/v1/auth/register": (5, 10),
    "/api/v1/auth/refresh": (10, 5),
    "/api/v1/auth/callback": (10, 5),
    "/api/v1/auth/password-reset": (5, 5),
    "/api/v1/invite": (20, 60),
    "/api/v1/media/generate/image": (20, 1),
    "/api/v1/media/generate/enhanced-image": (20, 1),
    "/api/v1/media/generate/social": (10, 1),
}

_EXEMPT_PATHS = ("/", "/health", "/docs", "/openapi.json", "/redoc")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP, per-path rate limiting backed by a distributed store.

    PRODUCTION (multi-pod): counters live in Redis (via cache.incr_counter), so a
    limit of N requests/window is enforced ACROSS every API replica — exactly
    what's required under HPA where 3–50 pods share one logical limit.

    DEVELOPMENT (no Redis): falls back to an in-process sliding window. This path
    is correct ONLY for a single replica and logs a one-time warning so it can
    never be mistaken for production-safe enforcement.

    The Redis path uses a fixed-window counter: key = ip:path:floor(now/window).
    incr returns the running count; the key auto-expires one window after creation.
    """

    def __init__(self, app, requests_per_minute: int = 100):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        # in-process fallback store: { (client_ip, path): [datetime, ...] }
        self._buckets = defaultdict(list)
        self._warned_no_redis = False

    def _client_ip(self, request: Request) -> str:
        # X-Forwarded-For is fully client-controlled unless stripped/overwritten
        # by a trusted reverse proxy in front of this app. Only honor it up to
        # TRUSTED_PROXY_COUNT hops from the right (the hops your own ingress
        # appends); a client can freely forge everything to the left of that.
        # Default (0) ignores the header entirely and uses the socket peer,
        # which is safe absent a configured, known proxy chain.
        proxy_count = settings.TRUSTED_PROXY_COUNT
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd and proxy_count > 0:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                idx = max(len(hops) - proxy_count, 0)
                return hops[idx]
        return request.client.host if request.client else "unknown"

    def _allowed_redis(
            self,
            client_ip: str,
            path: str,
            limit: int,
            window_min: int) -> bool:
        window_seconds = window_min * 60
        # Fixed-window bucket id — same across all pods for the same wall-clock
        # window.
        bucket = int(datetime.now(timezone.utc).timestamp() // window_seconds)
        key = f"ratelimit:{client_ip}:{path}:{bucket}"
        # TTL slightly longer than the window so the counter survives until the
        # window naturally rolls over, then disappears.
        count = cache.incr_counter(key, amount=1, ttl=window_seconds + 5)
        return count <= limit

    def _allowed_inmemory(
            self,
            client_ip: str,
            path: str,
            limit: int,
            window_min: int) -> bool:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=window_min)
        key = (client_ip, path)
        self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
        if len(self._buckets[key]) >= limit:
            return False
        self._buckets[key].append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        # allow common health/docs endpoints
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = self._client_ip(request)
        path = request.url.path

        # Determine rule
        if path in _RATE_RULES:
            limit, window_min = _RATE_RULES[path]
        else:
            limit, window_min = self.requests_per_minute, 1

        if cache.redis_enabled:
            allowed = self._allowed_redis(client_ip, path, limit, window_min)
        else:
            if not self._warned_no_redis:
                logger.warning(
                    "[RATE_LIMIT] Redis not configured — using in-process counters. "
                    "This is safe ONLY for single-replica development; under multiple "
                    "API replicas the effective limit becomes (limit × pod count). "
                    "Set REDIS_URL for distributed enforcement.")
                self._warned_no_redis = True
            allowed = self._allowed_inmemory(
                client_ip, path, limit, window_min)

        if not allowed:
            logger.warning(
                f"Rate limit exceeded for IP={client_ip} path={path} limit={limit} window_min={window_min}"
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
            )

        return await call_next(request)
