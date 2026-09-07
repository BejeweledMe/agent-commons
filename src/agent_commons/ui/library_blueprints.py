"""Five small, explicit workflow plans over service-owned specializations."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from agent_commons.core.canonical import canonical_sha256
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.library import LibraryStore
from agent_commons.runtime.model import BuiltinProfileId, validate_model_name


def _text(en: str, ru: str) -> dict[str, str]:
    return {"en": en, "ru": ru}


def _node(identifier: str, en: str, ru: str, role: str, *dependencies: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": _text(en, ru),
        "slot_id": role,
        "description": _text(
            f"Task: {en}. Use the project brief below; clarify material uncertainty "
            "before implementation and record evidence for this outcome.",
            f"Задача: {ru}. Используйте описание проекта ниже, уточните существенные "
            "неизвестные до реализации и сохраните доказательства результата.",
        ),
        "acceptance_criteria": {
            "en": [
                "The stated outcome is implemented or documented for the agreed scope.",
                "Relevant verification and remaining limitations are recorded.",
            ],
            "ru": [
                "Заявленный результат реализован или описан в согласованном объёме.",
                "Сохранены результаты подходящих проверок и оставшиеся ограничения.",
            ],
        },
        "depends_on": list(dependencies),
    }


def _definitions() -> list[dict[str, Any]]:
    return [
        {
            "id": "web-app",
            "name": _text("Web application", "Веб-приложение"),
            "description": _text(
                "From the first user journey to an integrated browser app.",
                "От первого пользовательского сценария до работающего веб-приложения.",
            ),
            "tasks": [
                _node(
                    "experience",
                    "Define the user journey",
                    "Спроектировать сценарий",
                    "product-designer",
                ),
                _node(
                    "api",
                    "Build the application API",
                    "Реализовать API",
                    "python-backend-engineer",
                    "experience",
                ),
                _node(
                    "interface",
                    "Build the browser interface",
                    "Создать интерфейс",
                    "frontend-engineer",
                    "experience",
                ),
                _node(
                    "integration",
                    "Connect the complete journey",
                    "Соединить полный сценарий",
                    "delivery-tech-lead",
                    "api",
                    "interface",
                ),
                _node(
                    "quality",
                    "Verify the usable application",
                    "Проверить приложение",
                    "qa-engineer",
                    "integration",
                ),
            ],
        },
        {
            "id": "mobile-app",
            "name": _text("Mobile application", "Мобильное приложение"),
            "description": _text(
                "Choose a platform, shape the experience and deliver a tested client.",
                "Выбрать платформу, спроектировать опыт и собрать проверенный клиент.",
            ),
            "tasks": [
                _node(
                    "platform",
                    "Choose the mobile platform",
                    "Выбрать мобильную платформу",
                    "delivery-tech-lead",
                ),
                _node(
                    "experience",
                    "Design the mobile journey",
                    "Спроектировать мобильный сценарий",
                    "product-designer",
                    "platform",
                ),
                _node(
                    "client",
                    "Implement the mobile client",
                    "Создать мобильный клиент",
                    "delivery-tech-lead",
                    "experience",
                ),
                _node(
                    "quality",
                    "Verify the device experience",
                    "Проверить на устройствах",
                    "qa-engineer",
                    "client",
                ),
            ],
        },
        {
            "id": "telegram-mini-app",
            "name": _text("Telegram Mini App", "Telegram Mini App"),
            "description": _text(
                "Build a Mini App around its host, authentication and browser contract.",
                "Собрать Mini App с учётом платформы, авторизации и браузера.",
            ),
            "tasks": [
                _node(
                    "contract",
                    "Define host and authentication",
                    "Определить интеграцию и авторизацию",
                    "node-backend-engineer",
                ),
                _node(
                    "interface",
                    "Build the Mini App interface",
                    "Создать интерфейс Mini App",
                    "frontend-engineer",
                    "contract",
                ),
                _node(
                    "api",
                    "Implement the Mini App backend",
                    "Реализовать сервер Mini App",
                    "node-backend-engineer",
                    "contract",
                ),
                _node(
                    "integration",
                    "Verify the Telegram host journey",
                    "Проверить сценарий в Telegram",
                    "delivery-tech-lead",
                    "interface",
                    "api",
                ),
                _node(
                    "quality",
                    "Verify failure and recovery paths",
                    "Проверить ошибки и восстановление",
                    "qa-engineer",
                    "integration",
                ),
            ],
        },
        {
            "id": "grounded-ai-assistant",
            "name": _text("Grounded AI assistant", "AI-помощник с поиском по данным"),
            "description": _text(
                "Connect a useful task, trusted retrieval, an interface and evaluation.",
                "Связать полезную задачу, поиск по данным, интерфейс и оценку качества.",
            ),
            "tasks": [
                _node(
                    "contract",
                    "Define task and data boundaries",
                    "Определить задачу и границы данных",
                    "llm-engineer",
                ),
                _node(
                    "retrieval",
                    "Build grounded retrieval",
                    "Реализовать поиск по источникам",
                    "rag-engineer",
                    "contract",
                ),
                _node(
                    "interface",
                    "Build the assistant interface",
                    "Создать интерфейс помощника",
                    "frontend-engineer",
                    "contract",
                ),
                _node(
                    "integration",
                    "Integrate the assistant journey",
                    "Собрать сценарий помощника",
                    "llm-engineer",
                    "retrieval",
                    "interface",
                ),
                _node(
                    "evaluation",
                    "Evaluate useful and failed cases",
                    "Оценить полезность и ошибки",
                    "ai-evaluation-engineer",
                    "integration",
                ),
            ],
        },
        {
            "id": "improve-service",
            "name": _text("Improve an existing service", "Улучшить существующий сервис"),
            "description": _text(
                "Diagnose a concrete problem, change its cause and verify the result.",
                "Разобрать конкретную проблему, устранить причину и проверить результат.",
            ),
            "tasks": [
                _node(
                    "diagnosis",
                    "Locate the observed problem",
                    "Найти причину проблемы",
                    "sre-engineer",
                ),
                _node(
                    "change",
                    "Implement the bounded correction",
                    "Внести целевое исправление",
                    "delivery-tech-lead",
                    "diagnosis",
                ),
                _node(
                    "regression",
                    "Verify the regression boundary",
                    "Проверить регрессии",
                    "qa-engineer",
                    "change",
                ),
                _node(
                    "security",
                    "Review affected security claims",
                    "Проверить затронутую безопасность",
                    "security-reviewer",
                    "change",
                ),
                _node(
                    "outcome",
                    "Record the verified outcome",
                    "Зафиксировать проверенный результат",
                    "sre-engineer",
                    "regression",
                    "security",
                ),
            ],
        },
    ]


def blueprint_catalog(store: LibraryStore) -> dict[str, Any]:
    roles = {
        item["ref"]["id"]: item
        for item in store.catalog()["roles"]
        if item["ref"]["source"] == "builtin"
    }
    plans = _definitions()
    for plan in plans:
        slot_ids = list(dict.fromkeys(node["slot_id"] for node in plan["tasks"]))
        plan["slots"] = [
            {"id": item, "name": roles[item]["name"], "role_ref": roles[item]["ref"]}
            for item in slot_ids
        ]
        plan["version"] = canonical_sha256(plan)
    return {"schema": "agent_commons.library-blueprints.v1", "blueprints": plans}


def apply_blueprint(context: Any, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
    fields = {"expected_version", "idempotency_key", "title", "brief", "locale", "bindings"}
    if set(body) != fields:
        raise ValidationError("blueprint application has unsupported or missing fields")
    title, key, locale = body["title"], body["idempotency_key"], body["locale"]
    brief = body["brief"]
    if type(brief) is not str or not 1 <= len(brief.strip()) <= 4000:
        raise ValidationError(
            "describe the project outcome and constraints in 1 to 4000 characters"
        )
    if (
        not isinstance(title, str)
        or not 1 <= len(title.strip()) <= 128
        or type(locale) is not str
        or locale not in {"en", "ru"}
    ):
        raise ValidationError("blueprint title or language is invalid")
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValidationError("blueprint application needs a bounded idempotency key")
    store = context.library_store()
    plan = next((p for p in blueprint_catalog(store)["blueprints"] if p["id"] == identifier), None)
    if plan is None or body["expected_version"] != plan["version"]:
        raise ValidationError("blueprint changed; refresh its exact version before applying")
    bindings = body["bindings"]
    slots = {slot["id"]: slot for slot in plan["slots"]}
    if not isinstance(bindings, list) or len(bindings) != len(slots):
        raise ValidationError("select a provider and name for every blueprint role")
    selected: dict[str, dict[str, Any]] = {}
    for value in bindings:
        if not isinstance(value, dict) or set(value) != {"slot_id", "profile_id", "model", "name"}:
            raise ValidationError("blueprint role selection is invalid")
        slot = value["slot_id"]
        if not isinstance(slot, str) or slot not in slots or slot in selected:
            raise ValidationError("blueprint role selection is missing or repeated")
        try:
            BuiltinProfileId(value["profile_id"])
        except (ValueError, TypeError) as exc:
            raise ValidationError("blueprint provider profile is invalid") from exc
        name = value["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
            raise ValidationError("blueprint role name is invalid")
        if value["model"] is not None:
            validate_model_name(value["model"])
        store.compose_role(slots[slot]["role_ref"])
        selected[slot] = value
    manager = context.writer()
    manager.policy.assert_safe(body, context="blueprint application metadata")
    prefix = "blueprint-" + hashlib.sha256(key.encode()).hexdigest()
    intent = canonical_sha256({**body, "bindings": [selected[slot] for slot in slots]})
    roles: dict[str, str] = {}
    tasks: dict[str, str] = {}
    with manager._canonical_write_lock():
        for ordinal, slot_id in enumerate(slots):
            value = selected[slot_id]
            # Same operation keys and exact inputs replay existing steps. A
            # changed request cannot silently reuse the first role's identity.
            result = manager.create_agent(
                name=value["name"].strip(),
                profile_id=value["profile_id"],
                model=value["model"],
                specialization_ref=slots[slot_id]["role_ref"],
                context_mode="fresh",
                library_store=store,
                rationale=(
                    f"Workflow {title.strip()} ({identifier}, {plan['version']}); intent {intent}."
                ),
                # Ordinal zero is invariant across blueprint identities. Its
                # payload binds the whole intent before another slot can write.
                idempotency_key=prefix + ":role:" + str(ordinal),
            )
            roles[slot_id] = str(result["entity_ref"]["id"])
        for node in plan["tasks"]:
            result = manager.create_task(
                title=f"{title.strip()} · {node['title'][locale]}",
                description=node["description"][locale] + "\n\n" + brief.strip(),
                acceptance_criteria=tuple(node["acceptance_criteria"][locale]),
                dependencies=tuple(tasks[key] for key in node["depends_on"]),
                suggested_agent_id=roles[node["slot_id"]],
                idempotency_key=prefix + ":task:" + node["id"],
            )
            tasks[node["id"]] = str(result["entity_ref"]["id"])
    context.invalidate()
    return {
        "schema": "agent_commons.blueprint-application.v1",
        "state": "created",
        "blueprint_id": identifier,
        "roles": [{"slot_id": slot, "agent_id": agent} for slot, agent in roles.items()],
        "tasks": [
            {
                "node_id": node["id"],
                "task_id": tasks[node["id"]],
                "agent_id": roles[node["slot_id"]],
            }
            for node in plan["tasks"]
        ],
    }


def register_blueprint_reads(routes: Any, *, store_factory: Any, dependencies: list[Any]) -> None:
    @routes.get("/api/library/blueprints", dependencies=dependencies)
    async def blueprints() -> JSONResponse:
        try:
            result = await asyncio.to_thread(lambda: blueprint_catalog(store_factory()))
        except CommonsError:
            return JSONResponse(
                {
                    "error": {
                        "code": "blueprints_unavailable",
                        "message": "Workflow blueprints are unavailable.",
                    }
                },
                status_code=409,
            )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})


def register_blueprint_writes(routes: Any, *, context: Any, read_body: Any, record: Any) -> None:
    @routes.post("/api/library/blueprints/{blueprint_id}/apply")
    async def apply(blueprint_id: str, request: Request) -> Any:
        # Bounded parsing happens before any canonical step or private retain.
        from agent_commons.core.canonical import loads_json_strict

        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32768:
                return JSONResponse(
                    {
                        "error": {
                            "code": "blueprint_too_large",
                            "message": "Workflow selection exceeds its request limit.",
                        }
                    },
                    status_code=413,
                )
        try:
            body = loads_json_strict(bytes(raw))
            if not isinstance(body, dict):
                raise ValidationError("invalid workflow selection")
        except (ValueError, CommonsError):
            return JSONResponse(
                {
                    "error": {
                        "code": "blueprint_invalid",
                        "message": "Workflow selection must be valid JSON.",
                    }
                },
                status_code=422,
            )
        return await record(lambda: apply_blueprint(context, blueprint_id, body))
