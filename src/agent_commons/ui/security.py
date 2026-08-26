"""Loopback-only access control for the local UI.

Loopback reachability is not proof of identity.  A printed, short-lived,
single-use exchange code therefore establishes an in-memory browser session;
the browser carries only that session in an HTTP-only, same-site cookie after
removing the code from its fragment.  Every data route still requires that
session and a loopback ``Host`` to defeat DNS rebinding.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Final

#: Applied to every response, including errors, so a failure never leaks more
#: than a success does.
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

#: The fragment-held exchange code is accepted only here.  The endpoint creates
#: no canonical record and returns no workspace data; all other API routes
#: require the resulting browser session.
AUTH_EXCHANGE_PATH: Final = "/api/auth/exchange"

#: The one non-GET route that establishes an ephemeral browser session.  It is
#: deliberately separate from the canonical mutation declarations in
#: ``ui.server``: a read-only panel still needs to authenticate without gaining
#: any ledger-writing capability.
AUTH_ROUTES: Final = (("POST", AUTH_EXCHANGE_PATH),)

#: Cookie values are opaque process-memory session credentials.  They are never
#: placed in a URL, browser storage, response body, or canonical event.
SESSION_COOKIE_NAME: Final = "agent_commons_ui_session"
EXCHANGE_CODE_TTL_SECONDS: Final = 60
SESSION_TTL_SECONDS: Final = 8 * 60 * 60
_MAX_EXCHANGE_CODE_LENGTH: Final = 512


def new_token() -> str:
    """Return a 256-bit opaque secret held only in process memory."""

    return secrets.token_urlsafe(32)


def new_api_base() -> str:
    """Return the private, per-process route prefix for browser API calls.

    Cookie ``Domain`` matching deliberately ignores the loopback port.  The
    API base is therefore an additional process-local capability: a cookie
    scoped to this opaque path cannot be attached to another server's ordinary
    ``/api`` routes.
    """

    return f"/api/{new_token()}"


@dataclass
class LocalBrowserSession:
    """One exchange code and its separate finite-lived browser session.

    The lock makes a double-click or two concurrent tabs deterministic: exactly
    one request can consume the code.  The values stay process-local and are
    deliberately absent from the ledger, logs, and HTTP response body.
    """

    exchange_code: str
    session_token: str
    api_base: str = field(default_factory=new_api_base)
    created_at: float = field(default_factory=time.monotonic)
    exchange_ttl_seconds: int = EXCHANGE_CODE_TTL_SECONDS
    session_ttl_seconds: int = SESSION_TTL_SECONDS
    _consumed: bool = field(default=False, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def consume_exchange_code(self, presented: object, *, now: float | None = None) -> str | None:
        """Consume a valid unexpired code once and return its session secret."""

        if (
            not isinstance(presented, str)
            or not presented
            or len(presented) > _MAX_EXCHANGE_CODE_LENGTH
        ):
            return None
        observed_at = time.monotonic() if now is None else now
        with self._lock:
            if self._consumed or observed_at - self.created_at > self.exchange_ttl_seconds:
                return None
            if not token_matches(presented, self.exchange_code):
                return None
            self._consumed = True
            return self.session_token

    def session_matches(self, presented: str | None, *, now: float | None = None) -> bool:
        """Whether a cookie holds this process-local, unexpired session."""

        if not presented:
            return False
        observed_at = time.monotonic() if now is None else now
        if observed_at - self.created_at > self.session_ttl_seconds:
            return False
        return token_matches(presented, self.session_token)


#: Paths that intentionally need no existing browser session.  Static frontend
#: bytes contain no workspace data; the exchange route validates its own
#: single-use code before setting a cookie.
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/favicon.ico",
        "/gallery",
        "/gallery/",
        "/work",
        "/work/",
        AUTH_EXCHANGE_PATH,
    }
)


def is_public_path(path: str) -> bool:
    """Whether a request names static frontend bytes rather than workspace data.

    The Gallery shell and its same-origin assets contain no workspace data.
    Both browser clients exchange a short-lived fragment code for a cookie
    before calling their process-private API base, so only this narrowly
    public route is exempt from normal session middleware.
    """

    return (
        path in PUBLIC_PATHS
        or path.startswith("/gallery/assets/")
        or path.startswith("/work/assets/")
    )


def token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented, expected)


def bearer_token(header: str | None) -> str | None:
    """Parse a legacy bearer header without granting it UI access.

    Kept as a pure compatibility helper while callers migrate; the local UI
    middleware authenticates only the HTTP-only session cookie.
    """

    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset(
        {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
    )


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def content_security_policy(nonce: str) -> str:
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "require-trusted-types-for 'script'"
    )


def gallery_content_security_policy() -> str:
    """CSP for the separately-built, same-origin React Gallery assets."""

    return (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "require-trusted-types-for 'script'"
    )


def work_content_security_policy() -> str:
    """CSP for the separately-built, same-origin React Work application assets."""

    return (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "require-trusted-types-for 'script'"
    )
