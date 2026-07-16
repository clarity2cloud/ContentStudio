"""
CSRF protection middleware.

Validates the Origin (or Referer) header for state-changing requests that
use cookie-based authentication.

Skips validation when:
  - Method is GET, HEAD, OPTIONS, or TRACE  (safe / read-only)
  - Request carries an Authorization: Bearer header  (API client, not browser)
  - No access_token cookie is present  (unauthenticated or header-auth only)
  - Path is an auth endpoint (login/sso/refresh create sessions, not protected actions)
  - No Origin / Referer header present  (edge cases — CLI tools, Swagger with no cookie)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from app.config import settings
from app.utils.logger import logger

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Auth endpoints create sessions — they cannot be CSRF targets and must
# always be reachable. NOTE: this OSS build has no auth router yet (see
# app/core/dependencies.py) — these paths 404 today. They're kept as a
# forward-compatible placeholder for when a real auth router is added, so it
# picks up the correct exemption automatically.
_CSRF_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/sso",
    "/api/v1/auth/external-login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
}


class CSRFMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next) -> Response:
        # Safe methods never mutate state
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        # Auth endpoints are exempt — they establish sessions, not modify
        # authenticated state
        if request.url.path in _CSRF_EXEMPT_PATHS:
            return await call_next(request)

        # API clients use Bearer tokens — no CSRF risk
        if request.headers.get("Authorization", "").startswith("Bearer "):
            return await call_next(request)

        # Only cookie-authenticated requests need the CSRF check
        if not request.cookies.get("access_token"):
            return await call_next(request)

        allowed_origins = settings.get_cors_origins()

        origin = request.headers.get("Origin", "").rstrip("/")
        referer = request.headers.get("Referer", "")

        if origin:
            if not any(origin == o.rstrip("/") for o in allowed_origins):
                logger.warning(
                    f"CSRF: blocked Origin={origin!r} on {request.method} {request.url.path}"
                )
                return JSONResponse(
                    status_code=403, content={
                        "detail": "CSRF validation failed: Origin not allowed."}, )
        elif referer:
            if not any(referer.startswith(o.rstrip("/"))
                       for o in allowed_origins):
                logger.warning(
                    f"CSRF: blocked Referer={referer!r} on {request.method} {request.url.path}"
                )
                return JSONResponse(
                    status_code=403, content={
                        "detail": "CSRF validation failed: Referer not allowed."}, )
        # No Origin/Referer — allow (CLI tools, some Swagger setups)

        return await call_next(request)
