"""Loopback is not authentication: every data route needs a token."""

from __future__ import annotations

import pytest

from agent_commons.ui.security import bearer_token, token_matches
from tests.ui.conftest import PORT, authorized

_API_PATHS = ("/api/meta", "/api/graph", "/api/entities/task/task.01K00000000000000000000000")


@pytest.mark.parametrize("path", _API_PATHS)
def test_api_without_a_token_is_unauthorized(client, path: str) -> None:  # type: ignore[no-untyped-def]
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    body = response.text
    assert "test-token" not in body
    assert "workspace." not in body


@pytest.mark.parametrize("path", _API_PATHS)
def test_api_with_a_wrong_token_is_unauthorized(client, path: str) -> None:  # type: ignore[no-untyped-def]
    wrong = client.get(path, headers={"Authorization": "Bearer nope"})
    missing = client.get(path)
    assert wrong.status_code == 401
    # Identical bodies: a wrong token must not reveal that a token exists.
    assert wrong.json() == missing.json()


def test_token_comparison_is_constant_time() -> None:
    assert token_matches("abc", "abc") is True
    assert token_matches("abc", "abd") is False


def test_bearer_parsing_rejects_other_schemes() -> None:
    assert bearer_token("Bearer value") == "value"
    assert bearer_token("Basic value") is None
    assert bearer_token(None) is None
    assert bearer_token("Bearer") is None


@pytest.mark.parametrize(
    "host",
    ["evil.example.com", f"127.0.0.1.nip.io:{PORT}", f"attacker:{PORT}", "127.0.0.1:1"],
)
def test_a_foreign_host_header_is_forbidden(client, host: str) -> None:  # type: ignore[no-untyped-def]
    for path in ("/", "/api/meta"):
        response = client.get(path, headers={**authorized(), "Host": host})
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden_host"


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"])
def test_loopback_hosts_are_accepted(client, host: str) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/meta", headers={**authorized(), "Host": host})
    assert response.status_code == 200


def test_a_cross_origin_request_is_forbidden(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/meta", headers={**authorized(), "Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_origin"


def test_no_response_carries_a_cors_header(client) -> None:  # type: ignore[no-untyped-def]
    cases = [
        ("/", {}),
        ("/api/meta", {}),
        ("/api/meta", authorized()),
        ("/api/entities/task/nope", authorized()),
        ("/api/entities/bogus/bogus.1", authorized()),
        ("/api/meta", {**authorized(), "Host": "evil.example.com"}),
    ]
    for path, headers in cases:
        response = client.get(path, headers=headers)
        leaked = [name for name in response.headers if name.lower().startswith("access-control-")]
        assert leaked == [], f"{path} leaked {leaked}"


def test_options_is_not_served(client) -> None:  # type: ignore[no-untyped-def]
    response = client.options("/api/graph", headers=authorized())
    assert response.status_code == 405
    leaked = [name for name in response.headers if name.lower().startswith("access-control-")]
    assert leaked == []


def test_security_headers_are_present_on_success_and_failure(client) -> None:  # type: ignore[no-untyped-def]
    for headers, expected in ((authorized(), 200), ({}, 401)):
        response = client.get("/api/meta", headers=headers)
        assert response.status_code == expected
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_the_shell_is_unauthenticated_but_carries_no_workspace_data(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "test-token" not in body
    assert "workspace." not in body


def test_the_shell_csp_is_strict_and_the_nonce_changes_per_response(client) -> None:  # type: ignore[no-untyped-def]
    first = client.get("/")
    second = client.get("/")
    policy = first.headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "require-trusted-types-for 'script'" in policy
    nonce = policy.split("script-src 'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in first.text
    assert first.headers["Content-Security-Policy"] != second.headers["Content-Security-Policy"]
    assert "__CSP_NONCE__" not in first.text
