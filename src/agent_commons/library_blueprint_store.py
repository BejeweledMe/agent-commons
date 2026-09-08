"""Immutable custom workflow definitions in bounded private service storage."""

from __future__ import annotations

from typing import Any

from agent_commons.core.canonical import canonical_sha256
from agent_commons.library import LibraryRef, LibraryStore, _refuse, _text
from agent_commons.library_organization import MetadataFile, expected, identifier, translated

MAX_BLUEPRINTS = 128
MAX_VERSIONS = 1024


def _rows(value: object, limit: int, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit or (nonempty and not value):
        _refuse("Blueprint collection is missing or exceeds its limit.")
    return value


def definition(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"name", "description", "slots", "tasks"}:
        _refuse("Blueprint definition has an invalid shape.")
    result: dict[str, Any] = {
        "name": translated(value["name"], 800),
        "description": translated(value["description"]),
        "slots": [],
        "tasks": [],
    }
    for slot in _rows(value["slots"], 32, nonempty=True):
        if not isinstance(slot, dict) or set(slot) != {"id", "name", "role_ref"}:
            _refuse("Blueprint slot has an invalid shape.")
        result["slots"].append(
            {
                "id": identifier(slot["id"]),
                "name": _text(slot["name"], "slot name", 800),
                "role_ref": LibraryRef.parse(slot["role_ref"], kind="role").as_dict(),
            }
        )
    slots = {slot["id"] for slot in result["slots"]}
    if len(slots) != len(result["slots"]):
        _refuse("Blueprint slot identities must be unique.")
    for task in _rows(value["tasks"], 64, nonempty=True):
        if not isinstance(task, dict) or set(task) != {
            "id",
            "title",
            "description",
            "acceptance_criteria",
            "slot_id",
            "depends_on",
        }:
            _refuse("Blueprint task has an invalid shape.")
        criteria = task["acceptance_criteria"]
        if not isinstance(criteria, dict) or set(criteria) != {"en", "ru"}:
            _refuse("Blueprint acceptance criteria require both languages.")
        node = {
            "id": identifier(task["id"]),
            "title": translated(task["title"], 800),
            "description": translated(task["description"]),
            "slot_id": identifier(task["slot_id"]),
            "acceptance_criteria": {
                locale: [
                    _text(item, "acceptance criterion", 16000)
                    for item in _rows(criteria[locale], 32, nonempty=True)
                ]
                for locale in ("en", "ru")
            },
            "depends_on": [identifier(item) for item in _rows(task["depends_on"], 64)],
        }
        if node["slot_id"] not in slots or len(set(node["depends_on"])) != len(node["depends_on"]):
            _refuse("Blueprint task has a missing slot or repeated dependency.")
        result["tasks"].append(node)
    ordered_tasks(result["tasks"])
    return result


def ordered_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable topological traversal; definition order remains part of its hash."""
    nodes = {task["id"]: task for task in tasks}
    if len(nodes) != len(tasks):
        _refuse("Blueprint task identities must be unique.")
    visited: set[str] = set()
    active: set[str] = set()
    result: list[dict[str, Any]] = []

    def visit(key: str) -> None:
        if key not in nodes or key in active:
            _refuse("Blueprint dependencies must form an acyclic graph of existing tasks.")
        if key in visited:
            return
        active.add(key)
        for dependency in nodes[key]["depends_on"]:
            visit(dependency)
        active.remove(key)
        visited.add(key)
        result.append(nodes[key])

    for key in nodes:
        visit(key)
    return result


class BlueprintStore:
    def __init__(self, store: LibraryStore):
        self.store = store
        self.file = MetadataFile(
            store,
            "blueprints.json",
            "agent_commons.blueprint-state.v1",
            {"heads": {}, "versions": {}},
        )

    def _builtins(self) -> list[dict[str, Any]]:
        from agent_commons.ui.library_blueprints import blueprint_catalog

        return blueprint_catalog(self.store, include_custom=False)["blueprints"]

    def _state(self) -> dict[str, Any]:
        state = self.file.read()
        if len(state["heads"]) > MAX_BLUEPRINTS or len(state["versions"]) > MAX_VERSIONS:
            _refuse("Blueprint storage exceeds its limits.")
        for key, plan in state["versions"].items():
            if not isinstance(plan, dict) or set(plan) != {
                "id",
                "source",
                "version",
                "name",
                "description",
                "slots",
                "tasks",
            }:
                _refuse("Stored blueprint has an invalid shape.")
            identifier(plan["id"])
            expected(plan["version"])
            if plan["source"] not in ("builtin", "custom") or key != self._key(plan):
                _refuse("Stored blueprint identity is invalid.")
            content = definition(
                {field: plan[field] for field in ("name", "description", "slots", "tasks")}
            )
            if canonical_sha256({"id": plan["id"], **content}) != plan["version"]:
                _refuse("Stored blueprint content does not match its exact version.")
        for key, head in state["heads"].items():
            identifier(key)
            if (
                not isinstance(head, dict)
                or set(head) != {"version", "archived"}
                or type(head["archived"]) is not bool
            ):
                _refuse("Blueprint head is invalid.")
            expected(head["version"])
            if f"custom:{key}:{head['version']}" not in state["versions"]:
                _refuse("Blueprint current version is unavailable.")
        return state

    @staticmethod
    def _key(ref: dict[str, Any]) -> str:
        return f"{ref['source']}:{ref['id']}:{ref['version']}"

    def catalog(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        state = self._state()
        return [
            {**state["versions"][f"custom:{key}:{head['version']}"], "archived": head["archived"]}
            for key, head in sorted(state["heads"].items())
            if include_archived or not head["archived"]
        ]

    def resolve(self, source: str, id: str, version: str) -> dict[str, Any]:
        identifier(id)
        expected(version)
        if source not in ("builtin", "custom"):
            _refuse("Blueprint source is invalid.")
        state = self._state()
        plan = state["versions"].get(f"{source}:{id}:{version}")
        if plan is None and source == "builtin":
            plan = next(
                (row for row in self._builtins() if row["id"] == id and row["version"] == version),
                None,
            )
        if plan is None:
            _refuse("Exact blueprint version is unavailable.", "blueprint_version_unavailable", 409)
        archived = (
            state["heads"].get(id, {}).get("archived", False) if source == "custom" else False
        )
        return {**plan, "archived": archived}

    def for_apply(self, id: str, version: str, *, retain: bool = False) -> dict[str, Any]:
        identifier(id)
        expected(version)
        # Retained exact identities outlive the current packaged catalog. A
        # removed/renamed built-in must not turn into a custom lookup on retry.
        state = self._state()
        retained_sources = [
            source
            for source in ("builtin", "custom")
            if f"{source}:{id}:{version}" in state["versions"]
        ]
        if len(retained_sources) > 1:
            _refuse("Exact blueprint source is ambiguous.", "blueprint_source_ambiguous", 409)
        if retained_sources:
            source = retained_sources[0]
            plan = {
                **state["versions"][f"{source}:{id}:{version}"],
                "archived": state["heads"].get(id, {}).get("archived", False)
                if source == "custom"
                else False,
            }
        else:
            source = "builtin" if any(row["id"] == id for row in self._builtins()) else "custom"
            plan = self.resolve(source, id, version)
        if source == "builtin" and retain:
            # Freeze every role closure before any partial canonical application.
            for slot in plan["slots"]:
                self.store.retain(slot["role_ref"])
            frozen = {key: value for key, value in plan.items() if key != "archived"}

            def retain(state: dict[str, Any]) -> dict[str, Any]:
                if (
                    len(state["versions"]) >= MAX_VERSIONS
                    and self._key(plan) not in state["versions"]
                ):
                    _refuse("Blueprint version capacity reached.", "library_capacity", 409)
                state["versions"][self._key(plan)] = frozen
                return frozen

            self.file.mutate(
                {"operation": "retain", "ref": {"source": source, "id": id, "version": version}},
                f"retain:{source}:{id}:{version}",
                retain,
            )
        return plan

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        required = {"id", "expected_version", "idempotency_key", "definition"}
        if not required <= set(body) or set(body) - (required | {"fork_ref"}):
            _refuse("Blueprint save has an invalid shape.")
        id = identifier(body["id"])
        expected(body["expected_version"], nullable=True)
        content = definition(body["definition"])
        if any(row["id"] == id for row in self._builtins()):
            _refuse("Built-in blueprint identities are reserved.")
        fork = body.get("fork_ref")
        if fork is not None:
            if (
                body["expected_version"] is not None
                or not isinstance(fork, dict)
                or set(fork) != {"source", "id", "version"}
            ):
                _refuse("Blueprint copy requires a new custom identity and exact source.")
        request = {
            "operation": "save",
            **{key: value for key, value in body.items() if key != "idempotency_key"},
        }

        def change(state: dict[str, Any]) -> dict[str, Any]:
            self._state()
            if fork is not None:
                self.resolve(**fork)
            head = state["heads"].get(id)
            if (head["version"] if head else None) != body["expected_version"]:
                _refuse(
                    "Blueprint changed. Reload before saving.", "library_revision_conflict", 409
                )
            if head is None and len(state["heads"]) >= MAX_BLUEPRINTS:
                _refuse("Blueprint capacity reached.", "library_capacity", 409)
            plan = {
                "id": id,
                **content,
                "source": "custom",
                "version": canonical_sha256({"id": id, **content}),
            }
            if len(state["versions"]) >= MAX_VERSIONS and self._key(plan) not in state["versions"]:
                _refuse("Blueprint version capacity reached.", "library_capacity", 409)
            for slot in content["slots"]:
                self.store.retain(slot["role_ref"])
            state["versions"][self._key(plan)] = plan
            state["heads"][id] = {
                "version": plan["version"],
                "archived": head["archived"] if head else False,
            }
            return {**plan, "archived": state["heads"][id]["archived"]}

        return self.file.mutate(request, body["idempotency_key"], change)

    def archive(self, id: str, body: dict[str, Any]) -> dict[str, Any]:
        identifier(id)
        if (
            set(body) != {"expected_version", "archived", "idempotency_key"}
            or type(body["archived"]) is not bool
        ):
            _refuse("Blueprint archive has an invalid shape.")
        expected(body["expected_version"])

        def change(state: dict[str, Any]) -> dict[str, Any]:
            self._state()
            head = state["heads"].get(id)
            if head is None or head["version"] != body["expected_version"]:
                _refuse("Blueprint changed or is unavailable.", "library_revision_conflict", 409)
            head["archived"] = body["archived"]
            return {
                **state["versions"][f"custom:{id}:{head['version']}"],
                "archived": head["archived"],
            }

        return self.file.mutate(
            {
                "operation": "archive",
                "id": id,
                **{key: value for key, value in body.items() if key != "idempotency_key"},
            },
            body["idempotency_key"],
            change,
        )
