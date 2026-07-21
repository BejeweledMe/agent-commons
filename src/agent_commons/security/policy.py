"""Security classification and trust-boundary helpers.

The scanner is intentionally conservative and never returns the matched value.
It is suitable for caller-controlled JSON-like values before they are written to
the collaboration ledger, operational state, receipts, or audit records.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_commons.errors import SecurityPolicyError


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    PII = "pii"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """A non-sensitive description of a rejected value."""

    category: str
    classification: DataClassification
    location: str


_DEFAULT_CLASSIFIED_KEYS: dict[str, DataClassification] = {
    "account_number": DataClassification.PII,
    "check_id": DataClassification.PII,
    "customer_email": DataClassification.PII,
    "customer_id": DataClassification.PII,
    "device_id": DataClassification.PII,
    "email": DataClassification.PII,
    "passport": DataClassification.PII,
    "phone": DataClassification.PII,
    "social_security_number": DataClassification.PII,
    "ssn": DataClassification.PII,
    "user_id": DataClassification.PII,
}

_CREDENTIAL_MARKERS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "passwd",
        "password",
        "pwd",
        "secret",
        "token",
    }
)

_QUOTED_ASSIGNMENT = re.compile(
    r"""
    (?P<key>
        "(?:\\.|[^"\\\r\n])+"
        |
        '(?:\\.|[^'\\\r\n])+'
        |
        [A-Za-z_][A-Za-z0-9_.-]*
    )
    \s*[:=]\s*
    (?P<value>
        "(?:\\.|[^"\\\r\n])*"
        |
        '(?:\\.|[^'\\\r\n])*'
    )
    """,
    re.VERBOSE,
)

_UNQUOTED_ASSIGNMENT = re.compile(
    r"""
    (?P<key>[A-Za-z_][A-Za-z0-9_.-]*)
    \s*[:=]\s*
    (?P<value>[^\s"'`#,}\]]+)
    """,
    re.VERBOSE,
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:ENCRYPTED |RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"
            r"(?: BLOCK)?-----",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(
            r"(?<![A-Z0-9])(?:AKIA|ASIA|AIDA|AIPA|ANPA|ANVA|AGPA|APKA|AROA)"
            r"[A-Z0-9]{16}(?![A-Z0-9])"
        ),
    ),
    ("github_token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}")),
    (
        "github_fine_grained_token",
        re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}"),
    ),
    (
        "openai_api_key",
        re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"),
    ),
    ("google_api_key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}")),
    ("google_oauth_token", re.compile(r"(?<![A-Za-z0-9])ya29\.[0-9A-Za-z_-]{20,}")),
    ("gitlab_token", re.compile(r"(?<![A-Za-z0-9])glpat-[0-9A-Za-z_-]{20,}")),
    ("huggingface_token", re.compile(r"(?<![A-Za-z0-9])hf_[0-9A-Za-z]{20,}")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[0-9A-Za-z-]{20,}")),
    (
        "authorization_header",
        re.compile(
            r"\bauthorization\s*:\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    (
        "credentialed_uri",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
    (
        "cloud_signed_url",
        re.compile(
            r"(?:X-Amz-(?:Credential|Signature|Security-Token)|"
            r"X-Goog-(?:Credential|Signature))="
            r"|[?&](?:sig|Signature|AWSAccessKeyId|GoogleAccessId)=[^&\s]+",
            re.IGNORECASE,
        ),
    ),
)

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email_address",
        re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I),
    ),
    ("us_social_security_number", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
)


def _normalize_key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return "_".join(part for part in re.split(r"[^A-Za-z0-9]+", value.lower()) if part)


def _credential_key(value: str) -> bool:
    normalized = _normalize_key(value)
    parts = tuple(part for part in normalized.split("_") if part)
    if any(part in _CREDENTIAL_MARKERS for part in parts):
        return True
    compact = "".join(parts)
    adjacent = set(zip(parts, parts[1:], strict=False))
    return "apikey" in compact or bool(
        adjacent
        & {
            ("api", "key"),
            ("access", "key"),
            ("account", "key"),
            ("private", "key"),
        }
    )


def _nonempty(value: Any) -> bool:
    return value not in (None, "", b"", (), [], {}, set(), frozenset())


def _text_variants(value: str | bytes) -> tuple[str, ...]:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    without_nuls = text.replace("\x00", "")
    return (text,) if without_nuls == text else (text, without_nuls)


class SecurityPolicy:
    """Recursively inspect JSON-like values using a configurable data policy."""

    def __init__(
        self,
        *,
        classified_keys: Mapping[str, str | DataClassification] | None = None,
        classified_key_patterns: Sequence[
            tuple[str | re.Pattern[str], str | DataClassification]
        ] = (),
        blocked_classifications: Sequence[str | DataClassification] = (
            DataClassification.RESTRICTED,
            DataClassification.PII,
            DataClassification.SECRET,
        ),
        include_default_classified_keys: bool = True,
        detect_free_text_pii: bool = True,
        max_depth: int = 64,
        max_items: int = 100_000,
        max_findings: int = 32,
    ) -> None:
        keys = dict(_DEFAULT_CLASSIFIED_KEYS) if include_default_classified_keys else {}
        for key, classification in (classified_keys or {}).items():
            keys[_normalize_key(str(key))] = DataClassification(classification)
        self.classified_keys = keys
        self.classified_key_patterns = tuple(
            (
                re.compile(pattern) if isinstance(pattern, str) else pattern,
                DataClassification(classification),
            )
            for pattern, classification in classified_key_patterns
        )
        self.blocked_classifications = frozenset(
            DataClassification(item) for item in blocked_classifications
        )
        self.detect_free_text_pii = bool(detect_free_text_pii)
        if max_depth < 1 or max_items < 1 or max_findings < 1:
            raise ValueError("security scan limits must be positive")
        self.max_depth = int(max_depth)
        self.max_items = int(max_items)
        self.max_findings = int(max_findings)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> SecurityPolicy:
        """Build a policy from a YAML/JSON-compatible mapping."""

        supported = {
            "classified_keys",
            "classified_key_patterns",
            "blocked_classifications",
            "include_default_classified_keys",
            "detect_free_text_pii",
            "max_depth",
            "max_items",
            "max_findings",
        }
        unknown = sorted(set(config) - supported)
        if unknown:
            raise ValueError("unsupported security configuration keys: " + ", ".join(unknown))

        def boolean(name: str, default: bool) -> bool:
            value = config.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            return value

        def positive_integer(name: str, default: int) -> int:
            value = config.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
            return value

        raw_keys = config.get("classified_keys")
        if raw_keys is not None and not isinstance(raw_keys, Mapping):
            raise ValueError("classified_keys must be a mapping")

        raw_patterns = config.get("classified_key_patterns", ())
        patterns: list[tuple[str, str]] = []
        if isinstance(raw_patterns, Mapping):
            patterns.extend((str(pattern), str(value)) for pattern, value in raw_patterns.items())
        else:
            if isinstance(raw_patterns, (str, bytes)) or not isinstance(raw_patterns, Sequence):
                raise ValueError("classified_key_patterns must be a mapping or sequence")
            for item in raw_patterns:
                if not isinstance(item, Mapping) or set(item) != {
                    "pattern",
                    "classification",
                }:
                    raise ValueError(
                        "classified_key_patterns entries require exactly pattern/classification"
                    )
                patterns.append((str(item["pattern"]), str(item["classification"])))
        blocked = config.get(
            "blocked_classifications",
            ("restricted", "pii", "secret"),
        )
        if isinstance(blocked, (str, bytes)) or not isinstance(blocked, Sequence):
            raise ValueError("blocked_classifications must be a sequence")
        try:
            return cls(
                classified_keys=raw_keys,
                classified_key_patterns=patterns,
                blocked_classifications=blocked,
                include_default_classified_keys=boolean("include_default_classified_keys", True),
                detect_free_text_pii=boolean("detect_free_text_pii", True),
                max_depth=positive_integer("max_depth", 64),
                max_items=positive_integer("max_items", 100_000),
                max_findings=positive_integer("max_findings", 32),
            )
        except re.error as exc:
            raise ValueError("classified_key_patterns contains an invalid regex") from exc

    def classify_key(self, key: str) -> DataClassification | None:
        normalized = _normalize_key(key)
        if _credential_key(normalized):
            return DataClassification.SECRET
        classification = self.classified_keys.get(normalized)
        if classification is not None:
            return classification
        for pattern, candidate in self.classified_key_patterns:
            if pattern.fullmatch(normalized):
                return candidate
        return None

    def _scan_text(self, value: str | bytes, location: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for text in _text_variants(value):
            for match in _QUOTED_ASSIGNMENT.finditer(text):
                key = match.group("key")
                if key[:1] in {'"', "'"} and key[-1:] == key[:1]:
                    key = key[1:-1]
                if _credential_key(key) and match.group("value")[1:-1].strip():
                    findings.append(
                        SecurityFinding(
                            "credential_assignment", DataClassification.SECRET, location
                        )
                    )
                    return findings
            for match in _UNQUOTED_ASSIGNMENT.finditer(text):
                if _credential_key(match.group("key")):
                    findings.append(
                        SecurityFinding(
                            "credential_assignment", DataClassification.SECRET, location
                        )
                    )
                    return findings
            for category, pattern in _SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(SecurityFinding(category, DataClassification.SECRET, location))
                    return findings
            if self.detect_free_text_pii:
                for category, pattern in _PII_PATTERNS:
                    if pattern.search(text):
                        findings.append(SecurityFinding(category, DataClassification.PII, location))
                        return findings
        return findings

    def scan_text_lines(self, value: str | bytes) -> tuple[tuple[int, int, SecurityFinding], ...]:
        """Return safe line ranges for every free-text finding without its value."""

        values: set[tuple[int, int, str, DataClassification]] = set()

        def add(
            text: str,
            match: re.Match[str],
            category: str,
            classification: DataClassification,
        ) -> None:
            start_line = text.count("\n", 0, match.start()) + 1
            final_offset = max(match.start(), match.end() - 1)
            end_line = text.count("\n", 0, final_offset) + 1
            values.add((start_line, end_line, category, classification))

        for text in _text_variants(value):
            for match in _QUOTED_ASSIGNMENT.finditer(text):
                key = match.group("key")
                if key[:1] in {'"', "'"} and key[-1:] == key[:1]:
                    key = key[1:-1]
                if _credential_key(key) and match.group("value")[1:-1].strip():
                    add(
                        text,
                        match,
                        "credential_assignment",
                        DataClassification.SECRET,
                    )
            for match in _UNQUOTED_ASSIGNMENT.finditer(text):
                if _credential_key(match.group("key")):
                    add(
                        text,
                        match,
                        "credential_assignment",
                        DataClassification.SECRET,
                    )
            for category, pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    add(text, match, category, DataClassification.SECRET)
            if self.detect_free_text_pii:
                for category, pattern in _PII_PATTERNS:
                    for match in pattern.finditer(text):
                        add(text, match, category, DataClassification.PII)
        return tuple(
            (
                start_line,
                end_line,
                SecurityFinding(category, classification, "$"),
            )
            for start_line, end_line, category, classification in sorted(
                values,
                key=lambda item: (item[0], item[1], item[2], item[3].value),
            )
        )

    def scan(self, value: Any) -> tuple[SecurityFinding, ...]:
        findings: list[SecurityFinding] = []
        seen: set[int] = set()
        visited = 0

        def add(category: str, classification: DataClassification, location: str) -> None:
            if len(findings) < self.max_findings:
                findings.append(SecurityFinding(category, classification, location))

        def walk(item: Any, location: str, depth: int) -> None:
            nonlocal visited
            if len(findings) >= self.max_findings:
                return
            visited += 1
            if visited > self.max_items:
                add("scan_item_limit_exceeded", DataClassification.RESTRICTED, location)
                return
            if depth > self.max_depth:
                add("scan_depth_limit_exceeded", DataClassification.RESTRICTED, location)
                return
            if item is None or isinstance(item, (bool, int, float)):
                return
            if isinstance(item, (str, bytes)):
                findings.extend(self._scan_text(item, location))
                return
            if isinstance(item, Path):
                findings.extend(self._scan_text(str(item), location))
                return

            track_identity = isinstance(
                item, (Mapping, list, tuple, set, frozenset)
            ) or is_dataclass(item)
            if track_identity:
                identity = id(item)
                if identity in seen:
                    add("cyclic_structure", DataClassification.RESTRICTED, location)
                    return
                seen.add(identity)

            try:
                if is_dataclass(item) and not isinstance(item, type):
                    for field in fields(item):
                        child = getattr(item, field.name)
                        classification = self.classify_key(field.name)
                        child_location = f"{location}.{field.name}"
                        if classification in self.blocked_classifications and _nonempty(child):
                            add("classified_field", classification, child_location)
                        walk(child, child_location, depth + 1)
                    return
                if isinstance(item, Mapping):
                    for index, (raw_key, child) in enumerate(item.items()):
                        key = str(raw_key)
                        key_location = f"{location}.<key:{index}>"
                        findings.extend(self._scan_text(key, key_location))
                        classification = self.classify_key(key)
                        child_location = f"{location}.{_normalize_key(key) or '<field>'}"
                        if classification in self.blocked_classifications and _nonempty(child):
                            add("classified_field", classification, child_location)
                        walk(child, child_location, depth + 1)
                    return
                if isinstance(item, (list, tuple, set, frozenset)):
                    for index, child in enumerate(item):
                        walk(child, f"{location}[{index}]", depth + 1)
                    return
                add("unsupported_value_type", DataClassification.RESTRICTED, location)
            finally:
                if track_identity:
                    seen.discard(id(item))

        walk(value, "$", 0)
        return tuple(findings[: self.max_findings])

    def assert_safe(self, value: Any, *, context: str = "content") -> None:
        blocked = [
            finding
            for finding in self.scan(value)
            if finding.classification in self.blocked_classifications
        ]
        if blocked:
            first = blocked[0]
            raise SecurityPolicyError(
                f"{context} rejected by security policy "
                f"({first.classification.value}:{first.category})"
            )


UNTRUSTED_CONTENT_SCHEMA = "agent_commons.untrusted_content.v1"


def mark_untrusted_content(
    content: Any,
    *,
    source: str,
    media_type: str = "text/plain",
    policy: SecurityPolicy | None = None,
) -> dict[str, Any]:
    """Wrap agent/external content as data that must never gain instruction authority."""

    if not source.strip() or not media_type.strip():
        raise ValueError("untrusted content source and media_type are required")
    wrapper = {
        "schema": UNTRUSTED_CONTENT_SCHEMA,
        "trust": "untrusted",
        "source": source,
        "media_type": media_type,
        "content": content,
    }
    (policy or SecurityPolicy()).assert_safe(wrapper, context="untrusted content")
    return wrapper


def is_untrusted_content(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema") == UNTRUSTED_CONTENT_SCHEMA
        and value.get("trust") == "untrusted"
        and "content" in value
    )


def pseudonymize_identifier(value: str, *, key: bytes, namespace: str) -> str:
    """Return a non-reversible, namespace-bound identifier for allowed correlation."""

    if not value or not key or not namespace:
        raise ValueError("value, key, and namespace must be non-empty")
    digest = hmac.new(key, (namespace + "\0" + value).encode("utf-8"), hashlib.sha256)
    return f"hmac-sha256:{digest.hexdigest()}"
