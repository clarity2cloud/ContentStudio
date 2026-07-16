# app/utils/ssrf_guard.py
#
# Shared guard against Server-Side Request Forgery (SSRF) for any endpoint
# that fetches a caller-supplied URL (e.g. the image download proxy).
#
# Blocks: non-http(s) schemes, loopback/private/link-local ranges (including
# the 169.254.169.254 cloud metadata endpoint), and re-validates every
# redirect hop so DNS rebinding / redirect chains can't bypass the pre-check.

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

from app.utils.logger import logger

MAX_PROXY_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB cap on proxied downloads
MAX_REDIRECTS = 5

_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24",
    "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96",
)]


def validate_public_url(url: str) -> None:
    """Raise HTTPException(400) if `url` does not point at a public http(s) host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve host.")

    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if any(ip in net for net in _BLOCKED_NETS):
            logger.warning(f"[SSRF-GUARD] Blocked URL resolving to private/blocked address: {url} -> {addr}")
            raise HTTPException(status_code=400, detail="URL resolves to a blocked address.")


async def safe_get(url: str, timeout: float = 60.0) -> httpx.Response:
    """
    Fetch `url` with SSRF protections: validates the initial host, then
    manually follows redirects (up to MAX_REDIRECTS) re-validating each hop
    before it is requested, and enforces a max response size.
    """
    validate_public_url(url)
    current = url

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            resp = await client.get(current)

            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                next_url = urljoin(current, resp.headers["location"])
                validate_public_url(next_url)
                current = next_url
                continue

            resp.raise_for_status()

            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > MAX_PROXY_DOWNLOAD_BYTES:
                raise HTTPException(status_code=400, detail="Remote file exceeds the allowed size limit.")
            if len(resp.content) > MAX_PROXY_DOWNLOAD_BYTES:
                raise HTTPException(status_code=400, detail="Remote file exceeds the allowed size limit.")
            return resp

    raise HTTPException(status_code=400, detail="Too many redirects.")
