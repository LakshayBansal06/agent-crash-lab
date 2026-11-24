"""Authentication, authorization, rate limiting and HTTP hardening."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request

from crashlab.store import Principal

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
MAX_BODY_BYTES = int(os.environ.get("CRASHLAB_MAX_BODY_BYTES", str(5 * 1024 * 1024)))

LOCAL_PRINCIPAL = Principal("local-dev", "default", frozenset({"admin"}))
SESSION_COOKIE = "acl_session"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def public_url() -> str:
    """The site's own https address when deployed (Render sets RENDER_EXTERNAL_URL automatically)."""
    return (os.environ.get("CRASHLAB_PUBLIC_URL", "").strip() or os.environ.get("RENDER_EXTERNAL_URL", "").strip()).rstrip("/")


def same_origin(request: Request) -> bool:
    """CSRF guard for cookie-authenticated writes: the browser says where the request came from."""
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") in {public_url(), str(request.base_url).rstrip("/")} - {""}
    return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")


def user_principal(user: dict) -> Principal:
    scopes = {"read", "write"} | ({"admin"} if user["role"] == "admin" else set())
    return Principal("user:" + user["id"], user["project_id"], frozenset(scopes))


def auth_mode() -> str:
    """required: accounts and sign-in. public: no sign-in pages; every browser gets its own private workspace
    with read/write (never admin) access. off: one local admin workspace, loopback only."""
    mode = os.environ.get("CRASHLAB_AUTH", "required").lower()
    if mode not in ("required", "public", "off"):
        raise RuntimeError("CRASHLAB_AUTH must be 'required', 'public' or 'off'")
    return mode


VISITOR_COOKIE = "acl_visitor"
_VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def visitor_principal(token: str | None) -> Principal | None:
    """Public mode: a browser's random cookie maps to its own workspace. Read and write only, so a visitor can
    never reach admin features (real-model runs that spend API credit, the admin console, contact messages)."""
    if not token or not _VISITOR_RE.match(token):
        return None
    vid = hashlib.sha256(token.encode()).hexdigest()[:20]
    return Principal(f"visitor:{vid}", f"v-{vid}", frozenset({"read", "write"}))


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def requires(scope: str):
    """FastAPI dependency: authenticate the caller and check one scope.
    401 = who are you?  403 = you can't do that."""

    def dep(request: Request) -> Principal:
        mode = auth_mode()
        if mode == "off":
            request.state.principal = LOCAL_PRINCIPAL
            return LOCAL_PRINCIPAL
        store = request.app.state.store
        token = bearer(request)
        p = store.authenticate(token) if token else None
        if mode == "public" and p is None and not token:
            p = visitor_principal(request.cookies.get(VISITOR_COOKIE) or getattr(request.state, "visitor", None))
            if p and request.method not in SAFE_METHODS and not same_origin(request):
                raise HTTPException(403, "cross-site request blocked")
        if p is None and not token:
            user = store.session_user(request.cookies.get(SESSION_COOKIE))
            if user:
                if request.method not in SAFE_METHODS and not same_origin(request):
                    raise HTTPException(403, "cross-site request blocked")
                p = user_principal(user)
        if p is None:
            request.app.state.metrics.inc("auth_failures_total")
            raise HTTPException(401, "sign in or send a valid API key", headers={"WWW-Authenticate": "Bearer"})
        if not p.can(scope):
            raise HTTPException(403, f"this key lacks the '{scope}' scope")
        request.state.principal = p
        return p

    return Depends(dep)


# ------------------------------------------------------------------ rate limit
@dataclass
class Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token bucket per (client, class). In-process: fine for one API node;
    a multi-node deployment moves the buckets to Redis."""

    def __init__(self, limits: dict[str, tuple[int, float]]) -> None:
        self.limits = limits  # class -> (capacity, refill tokens per second)
        self.buckets: dict[tuple[str, str], Bucket] = {}
        self.lock = threading.Lock()

    def check(self, client: str, klass: str) -> float:
        """Returns 0 if allowed, else seconds to wait."""
        cap, rate = self.limits[klass]
        now = time.monotonic()
        with self.lock:
            b = self.buckets.get((client, klass))
            if b is None:
                b = self.buckets[(client, klass)] = Bucket(float(cap), now)
            b.tokens = min(cap, b.tokens + (now - b.updated) * rate)
            b.updated = now
            if b.tokens >= 1:
                b.tokens -= 1
                return 0.0
            if len(self.buckets) > 50_000:  # bound memory under a flood of clients
                self.buckets.clear()
            return (1 - b.tokens) / rate


def per_minute(env: str, default: int) -> tuple[int, float]:
    n = int(os.environ.get(env, str(default)))
    return (n, n / 60.0)


def rate_limiter() -> RateLimiter:
    return RateLimiter({
        "read": per_minute("CRASHLAB_RATE_READ_PER_MIN", 300),
        "write": per_minute("CRASHLAB_RATE_WRITE_PER_MIN", 60),
        "expensive": per_minute("CRASHLAB_RATE_EXPENSIVE_PER_MIN", 10),
        "contact": per_minute("CRASHLAB_RATE_CONTACT_PER_MIN", 5),
        "auth": per_minute("CRASHLAB_RATE_AUTH_PER_MIN", 10),
    })


EXPENSIVE = ("/experiments", "/minimize", "/regression-suites", "/regression")


def classify(method: str, path: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    return "expensive" if any(path.endswith(s) or s + "/" in path for s in EXPENSIVE) else "write"


def client_ip(request: Request) -> str:
    """The visitor's IP for rate limits and logs. Behind a proxy (Render), CRASHLAB_PROXY_HOPS=1 trusts only the
    address the proxy itself appended: the right-most X-Forwarded-For entry. The left-most entries are whatever
    the client sent and must never be trusted, or a forged header resets every per-IP limit."""
    hops = int(os.environ.get("CRASHLAB_PROXY_HOPS", "0") or 0)
    if hops > 0:
        chain = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if chain:
            return chain[-min(hops, len(chain))]
    return request.client.host if request.client else "unknown"


def client_id(request: Request) -> str:
    token = bearer(request)
    if token:
        return "k:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + client_ip(request)


# --------------------------------------------------------------------- headers
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

# The interactive API reference (Swagger UI) is the only page allowed to load scripts from a CDN.
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def cors_origins() -> list[str]:
    raw = os.environ.get("CRASHLAB_CORS", "").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise RuntimeError("CRASHLAB_CORS='*' is not allowed: list the exact origins that may call the API")
    return origins
