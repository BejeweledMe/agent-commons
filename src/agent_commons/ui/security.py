"""Loopback-only access control for the local UI.

The threat model is explicit that reaching a port on loopback is not proof of
identity, so every data route requires a bearer token, and every request must
carry a loopback ``Host`` to defeat DNS rebinding.
"""

from __future__ import annotations

import hmac
import secrets

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

#: Paths that intentionally need no token: they contain no workspace data.
PUBLIC_PATHS = frozenset({"/", "/favicon.ico", "/gallery", "/gallery/"})


def is_public_path(path: str) -> bool:
    """Whether a request names static frontend bytes rather than workspace data.

    The Gallery shell uses the same fragment-held bearer token as the legacy
    panel. Browser subresource requests cannot attach that token, so only its
    prebuilt code and CSS are public; every Gallery data request remains behind
    the usual bearer-token middleware.
    """

    return path in PUBLIC_PATHS or path.startswith("/gallery/assets/")


def new_token() -> str:
    """A 256-bit token held only in memory and in the printed URL."""

    return secrets.token_urlsafe(32)


def token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented, expected)


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


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()
