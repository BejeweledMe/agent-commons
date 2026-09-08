"""Private catalog organization, separate from immutable instruction identities."""

from __future__ import annotations

from typing import Any

from agent_commons.library import (
    _DIGEST,
    _ID,
    _POLICY,
    LibraryStore,
    _json,
    _refuse,
    _sha,
    _text,
)
from agent_commons.operator_files import replace_operator_file
from agent_commons.storage.opstate import exclusive_lock

MAX_SIDECAR_BYTES = 8_388_608
MAX_OPERATIONS = 4096


def identifier(value: object) -> str:
    if type(value) is not str or not _ID(value):
        _refuse("Library metadata identity is invalid.")
    return value


def translated(value: object, limit: int = 16000) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"en", "ru"}:
        _refuse("Library translated text requires English and Russian.")
    return {locale: _text(value[locale], "translated text", limit) for locale in ("en", "ru")}


def expected(value: object, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or not _DIGEST(value):
        _refuse("Library metadata expected revision is invalid.")


class MetadataFile:
    """Atomic bounded service-owned sidecar; never an operator-selected path."""

    def __init__(self, store: LibraryStore, name: str, schema: str, empty: dict[str, Any]):
        self.store, self.name, self.schema, self.empty = store, name, schema, empty

    def read(self) -> dict[str, Any]:
        path = self.store.root / self.name
        if not path.exists() and not path.is_symlink():
            return {"schema": self.schema, **{key: {} for key in self.empty}, "receipts": {}}
        value = self.store._read(path, MAX_SIDECAR_BYTES)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "receipts", *self.empty}
            or value["schema"] != self.schema
            or any(not isinstance(value[key], dict) for key in (*self.empty, "receipts"))
            or len(value["receipts"]) > MAX_OPERATIONS
        ):
            _refuse("Library metadata storage is invalid.", "library_storage_refused", 409)
        for key, receipt in value["receipts"].items():
            expected(key)
            if not isinstance(receipt, dict) or set(receipt) != {"request", "result"}:
                _refuse("Library metadata receipt is invalid.", "library_storage_refused", 409)
            expected(receipt["request"])
        return value

    def mutate(self, request: dict[str, Any], key: str, operation: Any) -> dict[str, Any]:
        key = _sha(_text(key, "idempotency key", 256).encode())
        digest = _sha(_json(request))
        self.store._check_directory(self.store.root)
        with exclusive_lock(self.store.root / (self.name + ".lock"), policy=_POLICY):
            state = self.read()
            previous = state["receipts"].get(key)
            if previous is not None:
                if previous["request"] != digest:
                    _refuse(
                        "Library idempotency key was used for another edit.",
                        "library_idempotency_conflict",
                        409,
                    )
                return previous["result"]
            if len(state["receipts"]) >= MAX_OPERATIONS:
                _refuse("Library metadata has reached its edit capacity.", "library_capacity", 409)
            result = operation(state)
            state["receipts"][key] = {"request": digest, "result": result}
            encoded = _json(state)
            if len(encoded) > MAX_SIDECAR_BYTES:
                _refuse("Library metadata exceeds its storage limit.", "library_capacity", 409)
            replace_operator_file(self.store.root / self.name, encoded, label="library metadata")
            return result


# Organizational defaults are metadata only, never an ingredient of skill hashes.
_GROUPS = {
    "application": ("Application engineering", "Разработка приложений"),
    "data": ("Data and databases", "Данные и базы данных"),
    "ai": ("AI and machine learning", "ИИ и машинное обучение"),
    "platform": ("Infrastructure and reliability", "Инфраструктура и надёжность"),
    "security": ("Security", "Безопасность"),
    "product": ("Product and design", "Продукт и дизайн"),
    "communication": ("Writing and communication", "Тексты и коммуникация"),
    "ungrouped": ("Ungrouped", "Без группы"),
}
_DEFAULTS = {
    "application": (
        "api-contract-engineering flutter-skill ios-app-development "
        "node-typescript-backend-engineering python-backend-engineering "
        "software-engineering swift-skill system-design telegram-mini-apps "
        "web-frontend-engineering qa-testing"
    ),
    "data": "data-engineering database-engineering",
    "ai": (
        "agent-llm-evals agent-workflows ai-platform-llmops "
        "computer-vision-data-and-labeling computer-vision-evaluation "
        "computer-vision-inference-optimization computer-vision-modeling-and-training "
        "computer-vision-system-design llm-council llm-inference-optimization "
        "llm-system-design ml-system-design neural-training-systems "
        "nlp-modeling-and-adaptation rag-engineering"
    ),
    "platform": "platform-devops-engineering sre-reliability-engineering",
    "security": "application-security-engineering genai-security-testing security-review",
    "product": "business-product-consulting product-design information-decision-support",
    "communication": (
        "audience-adaptation execution-writing information-editing "
        "information-explanation information-presentation "
        "information-research-synthesis information-source-summary information-writing "
        "technical-writing"
    ),
}


class SkillOrganization:
    def __init__(self, store: LibraryStore):
        self.store = store
        self.file = MetadataFile(
            store,
            "skill-organization.json",
            "agent_commons.skill-organization-state.v1",
            {"groups": {}, "assignments": {}},
        )

    def _catalog(self, state: dict[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any]:
        if len(state["groups"]) > 128 or len(state["assignments"]) > 600:
            _refuse("Library organization exceeds its limits.")
        groups = [
            {"id": key, "name": {"en": names[0], "ru": names[1]}, "source": "builtin"}
            for key, names in _GROUPS.items()
        ]
        for key, name in sorted(state["groups"].items()):
            identifier(key)
            if key in _GROUPS:
                _refuse("Built-in groups cannot be replaced.")
            groups.append({"id": key, "name": translated(name, 800), "source": "custom"})
        group_ids = {group["id"] for group in groups}
        for key, group_id in state["assignments"].items():
            parts = key.split(":")
            if len(parts) != 2 or parts[0] not in ("builtin", "custom"):
                _refuse("Library skill organization is invalid.")
            identifier(parts[1])
            if type(group_id) is not str or group_id not in group_ids:
                _refuse("Library skill group is unavailable.")
        assignments = []
        defaults = {skill: group for group, names in _DEFAULTS.items() for skill in names.split()}
        for skill in skills:
            ref = skill["ref"]
            key = f"{ref['source']}:{ref['id']}"
            group = state["assignments"].get(
                key,
                defaults.get(ref["id"], "ungrouped") if ref["source"] == "builtin" else "ungrouped",
            )
            assignments.append({"source": ref["source"], "id": ref["id"], "group_id": group})
        assignments.sort(key=lambda row: (row["source"], row["id"]))
        body = {
            "schema": "agent_commons.skill-organization.v1",
            "groups": groups,
            "assignments": assignments,
        }
        return {**body, "revision": _sha(_json(body))}

    def catalog(self, skills: list[dict[str, Any]]) -> dict[str, Any]:
        return self._catalog(self.file.read(), skills)

    def save(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        fields = {"expected_revision", "idempotency_key"} | (
            {"id", "name"} if operation == "groups" else {"skill", "group_id"}
        )
        if operation not in ("groups", "move") or set(body) != fields:
            _refuse("Library organization edit has an invalid shape.")
        expected(body["expected_revision"])
        request = {
            "operation": operation,
            **{key: value for key, value in body.items() if key != "idempotency_key"},
        }

        def change(state: dict[str, Any]) -> dict[str, Any]:
            skills = self.store.catalog()["skills"]
            current = self._catalog(state, skills)
            if current["revision"] != body["expected_revision"]:
                _refuse(
                    "Library organization changed. Reload before saving.",
                    "library_revision_conflict",
                    409,
                )
            if operation == "groups":
                key = identifier(body["id"])
                if key in _GROUPS:
                    _refuse("Built-in groups are immutable.")
                state["groups"][key] = translated(body["name"], 800)
            else:
                skill = body["skill"]
                if not isinstance(skill, dict) or set(skill) != {"source", "id"}:
                    _refuse("Library skill identity is invalid.")
                if not any(
                    {"source": row["ref"]["source"], "id": row["ref"]["id"]} == skill
                    for row in skills
                ):
                    _refuse("Library skill is unavailable.")
                group_id = identifier(body["group_id"])
                if not any(row["id"] == group_id for row in current["groups"]):
                    _refuse("Library group is unavailable.")
                state["assignments"][f"{skill['source']}:{skill['id']}"] = group_id
            return self._catalog(state, skills)

        return self.file.mutate(request, body["idempotency_key"], change)
