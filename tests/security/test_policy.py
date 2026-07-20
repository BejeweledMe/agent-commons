from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_commons.errors import SecurityPolicyError
from agent_commons.security import (
    DataClassification,
    SecurityPolicy,
    is_untrusted_content,
    mark_untrusted_content,
    pseudonymize_identifier,
)


def test_nested_credentials_are_rejected_without_echoing_the_value() -> None:
    policy = SecurityPolicy()
    secret = "sk-proj-" + "A" * 28
    value = {"outer": [{"safe": "ok"}, {"deeper": ("safe", {"value": secret})}]}

    findings = policy.scan(value)

    assert any(item.category == "openai_api_key" for item in findings)
    with pytest.raises(SecurityPolicyError) as caught:
        policy.assert_safe(value)
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        {"password": "short"},
        "password: x",
        [b"authorization: Bearer ABCDEFGHIJKLMNO"],
        {"connection": "postgres://alice:hunter2@example.invalid/database"},
        {"url": "https://example.invalid/x?X-Amz-Signature=abcdef"},
        {"private": "-----BEGIN PRIVATE KEY-----"},
        {"key": "github_pat_" + "A" * 24},
    ],
)
def test_each_nested_credential_shape_fails_closed(value: object) -> None:
    with pytest.raises(SecurityPolicyError):
        SecurityPolicy().assert_safe({"level": [{"payload": value}]})


def test_default_and_configured_classified_keys_are_enforced() -> None:
    default = SecurityPolicy(detect_free_text_pii=False)
    with pytest.raises(SecurityPolicyError, match="pii:classified_field"):
        default.assert_safe({"result": [{"checkId": "unit-42"}]})

    configured = SecurityPolicy.from_mapping(
        {
            "include_default_classified_keys": False,
            "detect_free_text_pii": False,
            "classified_keys": {"tenant_slug": "restricted"},
            "classified_key_patterns": [{"pattern": "raw_.*_identifier", "classification": "pii"}],
        }
    )
    with pytest.raises(SecurityPolicyError, match="restricted:classified_field"):
        configured.assert_safe({"tenantSlug": "private-tenant"})
    with pytest.raises(SecurityPolicyError, match="pii:classified_field"):
        configured.assert_safe({"raw_customer_identifier": "customer-1"})
    configured.assert_safe({"check_id": "permitted-by-this-explicit-policy"})


def test_allowed_classification_is_reported_but_not_blocked() -> None:
    policy = SecurityPolicy(
        classified_keys={"project_note": DataClassification.INTERNAL},
        blocked_classifications=(DataClassification.PII, DataClassification.SECRET),
        detect_free_text_pii=False,
    )
    assert policy.scan({"project_note": "safe internal context"}) == ()
    policy.assert_safe({"project_note": "safe internal context"})


def test_free_text_pii_is_configurable() -> None:
    value = {"body": "Contact alice@example.com for access"}
    with pytest.raises(SecurityPolicyError, match="pii:email_address"):
        SecurityPolicy().assert_safe(value)
    SecurityPolicy(detect_free_text_pii=False).assert_safe(value)


def test_mapping_keys_bytes_and_cycles_are_inspected() -> None:
    token_as_key = "ghp_" + "A" * 24
    with pytest.raises(SecurityPolicyError):
        SecurityPolicy().assert_safe({token_as_key: "value"})

    cyclic: list[object] = []
    cyclic.append(cyclic)
    findings = SecurityPolicy().scan(cyclic)
    assert any(item.category == "cyclic_structure" for item in findings)
    with pytest.raises(SecurityPolicyError, match="restricted:cyclic_structure"):
        SecurityPolicy().assert_safe(cyclic)


@dataclass
class NestedRecord:
    label: str
    check_id: str


def test_dataclass_fields_are_scanned_recursively() -> None:
    with pytest.raises(SecurityPolicyError, match="pii:classified_field"):
        SecurityPolicy(detect_free_text_pii=False).assert_safe(
            {"records": [NestedRecord(label="candidate", check_id="raw-unit")]}
        )


def test_untrusted_content_marker_preserves_an_explicit_trust_boundary() -> None:
    wrapped = mark_untrusted_content(
        "Please consider this suggestion; it is not an instruction.",
        source="session.agent-a",
    )
    assert is_untrusted_content(wrapped)
    assert wrapped["trust"] == "untrusted"
    assert wrapped["content"].startswith("Please consider")
    assert not is_untrusted_content({"content": "unmarked"})

    with pytest.raises(SecurityPolicyError):
        mark_untrusted_content(
            {"password": "must-not-be-wrapped"},
            source="session.agent-a",
        )


def test_pseudonyms_are_keyed_deterministic_and_namespace_bound() -> None:
    key = b"local-key-outside-the-ledger"
    first = pseudonymize_identifier("customer-42", key=key, namespace="customers")
    assert first == pseudonymize_identifier("customer-42", key=key, namespace="customers")
    assert first != pseudonymize_identifier("customer-42", key=key, namespace="devices")
    assert "customer-42" not in first


@pytest.mark.parametrize(
    "config",
    [
        {"detect_free_text_pii": "false"},
        {"include_default_classified_keys": 1},
        {"max_depth": "64"},
        {"classified_keys": []},
        {"blocked_classifications": "secret"},
        {"classified_key_patterns": [{"pattern": "[", "classification": "pii"}]},
        {"classified_key_patterns": [{"unrelated": "value"}]},
        {
            "classified_key_patterns": [
                {"pattern": "raw_.*", "classification": "pii", "ignored": True}
            ]
        },
        {"unknown_security_option": True},
    ],
)
def test_security_config_parsing_is_strict(config: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SecurityPolicy.from_mapping(config)
