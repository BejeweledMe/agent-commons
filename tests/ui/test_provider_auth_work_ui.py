"""Static Work contracts for honest provider-auth attention and recovery."""

from __future__ import annotations

import json
from pathlib import Path

_WORK = Path(__file__).parents[2] / "frontend" / "work" / "src"


def _source(name: str) -> str:
    return (_WORK / name).read_text(encoding="utf-8")


def test_provider_auth_browser_contract_is_closed_and_secret_free() -> None:
    contracts = _source("contracts.ts")
    api = _source("api.ts")

    for value in (
        "authentication_required",
        "authenticating",
        "credential_store_unavailable",
        "continue_launch",
        "new_run_only",
        "checkedAt",
        "freshness",
    ):
        assert value in contracts
    assert "PROVIDER_AUTH_STATES" in api
    assert "PROVIDER_AUTH_ACTIONS" in api
    assert "parseProviderAuth" in api
    assert "/provider-auth/${encodeURIComponent(profileId)}" in api
    for forbidden in ("oauth_url", "verification_code", "access_token", "raw_output"):
        assert forbidden not in contracts.casefold()
        assert forbidden not in api.casefold()


def test_provider_auth_recovery_is_accessible_explicit_and_bilingual() -> None:
    entry = _source("main.tsx")
    styles = _source("styles.css")
    messages = json.loads(_source("i18n.json"))
    keys = {
        "provider_auth_critical",
        "provider_auth_title",
        "provider_auth_required",
        "provider_auth_authenticating",
        "provider_auth_credential_store_unavailable",
        "provider_auth_status_unavailable",
        "provider_auth_authenticate",
        "provider_auth_cancel",
        "provider_auth_check_again",
        "provider_auth_continue_launch",
        "provider_auth_repair_host_help",
        "provider_auth_secret_boundary",
        "provider_auth_new_run_only",
    }
    assert set(messages["en"]) == set(messages["ru"])
    for locale in ("en", "ru"):
        for key in keys:
            assert messages[locale][key].strip()

    assert 'aria-live="assertive"' in entry
    assert "role={visibleAuth?.blocksLaunch === true" in entry
    assert "tabIndex={-1}" in entry
    assert "authPanelRef.current?.focus()" in entry
    assert "provider-auth-critical" in styles
    assert "border: 2px" in styles
    assert "apiRef.current.providerAuthAction(profileId, action" in entry
    assert "pendingLaunchKey" in entry
    assert "text(providerAuthActionMessage.continue_launch)" in entry
    assert "postStartRecovery" not in entry
    assert "repair_host_credentials" not in entry
    assert "repair_host_credentials" not in _source("contracts.ts")


def test_work_keeps_the_launch_draft_until_auth_is_ready() -> None:
    entry = _source("main.tsx")
    api = _source("api.ts")

    assert "const key = pendingLaunchKey ?? crypto.randomUUID()" in entry
    assert "setPendingLaunchKey(key)" in entry
    assert "authStatus?.blocksLaunch === true" in entry
    assert 'visibleAuth?.state === "ready"' in entry
    assert "setPendingLaunchKey(null)" in entry
    assert "idempotencyKey: string" in api
    assert "idempotency_key: idempotencyKey" in api
