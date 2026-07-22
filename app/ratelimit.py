"""
ratelimit.py
============
Small dependency-free per-IP rate limiter (sliding window) for the public API.

Why it exists: one 12 MP analysis peaks near 428 MB RAM / 0.45 s CPU. Exposed
through a tunnel with no limit, a handful of clients can exhaust the Windows
server. This caps how often any single IP can queue expensive work.

Design notes:
  * Sliding window (not fixed buckets) so a client can't burst 2x at the
    boundary between two minutes.
  * In-memory and per-process. That is exactly right for the intended
    deployment (one uvicorn worker on one box). If you ever scale to multiple
    workers, move this to Redis — each worker would otherwise keep its own
    counters and the effective limit would multiply by the worker count.
  * Behind ngrok / Cloudflare the socket IP is the proxy's, so the real client
    is read from X-Forwarded-For (see RATE_LIMIT_TRUST_PROXY_HEADERS).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from . import config

logger = logging.getLogger("haldi.ratelimit")

# ip -> timestamps of recent hits
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()
_last_sweep = 0.0


def client_ip(request) -> str:
    """
    Best-effort real client IP.

    X-Forwarded-For is a comma-separated chain; the ORIGINAL client is the
    first entry. It is spoofable in general, which is why it is gated behind
    RATE_LIMIT_TRUST_PROXY_HEADERS — but with ngrok/Cloudflare in front it is
    the only way to tell users apart.
    """
    if config.RATE_LIMIT_TRUST_PROXY_HEADERS:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            first = fwd.split(",")[0].strip()
            if first:
                return first
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def limit_for_path(path: str) -> int:
    """Requests-per-minute allowed for a given path."""
    if path.startswith("/analyze"):
        return config.RATE_LIMIT_ANALYZE_PER_MIN
    if path.startswith("/calibrate"):
        return config.RATE_LIMIT_CALIBRATE_PER_MIN
    return config.RATE_LIMIT_DEFAULT_PER_MIN


def _sweep(now: float, window: float) -> None:
    """Drop empty/stale buckets so memory can't grow without bound."""
    global _last_sweep
    if now - _last_sweep < 300:          # at most every 5 minutes
        return
    _last_sweep = now
    stale = [ip for ip, dq in _hits.items()
             if not dq or dq[-1] <= now - window]
    for ip in stale:
        _hits.pop(ip, None)
    if stale:
        logger.debug("Rate-limit sweep dropped %d idle buckets.", len(stale))


def check(key: str, limit: int) -> Tuple[bool, int, int]:
    """
    Register a hit for `key`.

    Returns (allowed, remaining, retry_after_seconds).
    When not allowed, retry_after is when the oldest hit leaves the window.
    """
    window = float(config.RATE_LIMIT_WINDOW_SECONDS)
    now = time.monotonic()
    with _lock:
        _sweep(now, window)
        dq = _hits[key]
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(1, int(dq[0] + window - now) + 1)
            return False, 0, retry_after
        dq.append(now)
        return True, max(0, limit - len(dq)), 0
