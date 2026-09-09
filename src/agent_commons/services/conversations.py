"""Project-native conversations backed by canonical threads, never a second chat log."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from agent_commons.domain.conversations import conversation_scope, validate_scope_exists
from agent_commons.domain.states import LIVE_WORKER_DELEGATION_STATES
from agent_commons.errors import (
    IdempotencyConflictError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.runtime.collaboration_storage import collaboration_state_root


def _key(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValidationError("Conversation operation needs a bounded retry identity.")
    return hashlib.sha256(value.encode()).hexdigest()


class ConversationRevisionConflict(LifecycleConflictError):
    """A proven absent message could not append against an old thread revision."""


class ConversationDraftExpired(LifecycleConflictError):
    """An expired, unbound draft has no canonical message or reserved send."""


class ConversationMessageInvalid(ValidationError):
    """The submitted message was rejected before its draft or event was written."""


class ConversationIntentConflict(IdempotencyConflictError):
    """An operation identity already represents a different immutable message."""


class Conversations:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def _operator(self, snapshot: Any) -> None:
        session = self.manager.sessions.require_active(self.manager.session_id)
        if any(
            item.get("child_session_id") == session.session_id
            for item in snapshot.delegations.values()
        ):
            raise LifecycleConflictError("Only the operator may create a conversation.")

    def ensure(self, *, scope: Mapping[str, str], idempotency_key: str) -> dict[str, Any]:
        scope = conversation_scope(scope)
        key = "conversation-" + _key(idempotency_key)
        # An existing conversation is a read. Do not queue it behind an unrelated
        # canonical writer. A send still compares its exact revision at commit.
        snapshot = self.manager.snapshot()
        existing = self._open_conversation(scope, snapshot)
        if existing is not None:
            return existing
        with self.manager._canonical_write_lock():
            # Another operator may have created it while this request waited.
            snapshot = self.manager.snapshot()
            existing = self._open_conversation(scope, snapshot)
            if existing is not None:
                return existing
            refs: list[dict[str, str]] = []
            if scope["kind"] == "project":
                # Deliberate persistent audience, including future project agents.
                recipients = ("operator", "*")
                subject = "Project conversation"
            elif scope["kind"] == "task":
                refs.append(dict(scope))
                recipients = ("operator", "task:" + scope["id"])
                subject = str(snapshot.tasks[scope["id"]].get("title"))
            else:
                refs.append(dict(scope))
                recipients = ("operator", scope["id"])
                subject = str(snapshot.agents[scope["id"]].get("name"))
            result = self.manager.open_thread(
                thread_type="engagement",
                subject=subject[:512],
                desired_outcome="Coordinate the selected project work with the human operator.",
                to=recipients,
                related_refs=refs,
                conversation_scope=scope,
                idempotency_key=key,
            )
            thread_id = result["entity_ref"]["id"]
            if result.get("created") is True and thread_id not in snapshot.threads:
                # A validated, newly appended thread.opened has exactly this
                # initial projection. Existing/repaired histories use replay.
                return self._thread(
                    {
                        "id": thread_id,
                        "revision": result["revision"],
                        "conversation_scope": scope,
                        "subject": subject[:512],
                        "state": "open",
                        "messages": [],
                    },
                    snapshot,
                )
            snapshot = self.manager.snapshot()
            return self._thread(snapshot.threads[thread_id], snapshot)

    def _open_conversation(self, scope: Mapping[str, str], snapshot: Any) -> dict[str, Any] | None:
        self._operator(snapshot)
        validate_scope_exists(scope, snapshot)
        matching = [
            record
            for record in snapshot.threads.values()
            if record.get("conversation_scope") == scope and record.get("state") == "open"
        ]
        if len(matching) > 1:
            raise LifecycleConflictError("Conversation scope has ambiguous open threads.")
        return self._thread(matching[0], snapshot) if matching else None

    def list(self, scope: Mapping[str, str]) -> dict[str, Any]:
        scope = conversation_scope(scope)
        snapshot = self.manager.snapshot()
        validate_scope_exists(scope, snapshot)
        threads = [
            self._thread(record, snapshot)
            for record in snapshot.threads.values()
            if record.get("conversation_scope") == scope
        ]
        return {
            "schema": "agent_commons.conversations.v1",
            "scope": scope,
            "conversations": sorted(threads, key=lambda item: item["thread_id"])[-32:],
        }

    @staticmethod
    def _thread(thread: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
        scope = thread.get("conversation_scope")
        return {
            "thread_id": str(thread["id"]),
            "revision": str(thread["revision"]),
            "scope": conversation_scope(scope),
            "subject": str(thread.get("subject", "")),
            "state": str(thread["state"]),
            "audience": "all_project_agents" if scope["kind"] == "project" else scope["kind"],
            "message_count": len(thread.get("messages", [])),
            "recipient_availability": Conversations._recipient_availability(
                conversation_scope(scope), snapshot, observed_revision=str(thread["revision"])
            ),
        }

    @staticmethod
    def _recipient_availability(
        scope: Mapping[str, str], snapshot: Any, *, observed_revision: str
    ) -> dict[str, str | None]:
        """Return a conservative, snapshot-local view of the conversation recipient.

        A conversation read must not join a second mutable tracker read: both the
        recipient selection and its live delegation state come from ``snapshot``.
        If replay has reported a partial/stale view, absence cannot establish that
        an agent is inactive.
        """

        agent_id = Conversations._recipient_agent_id(scope, snapshot)
        result: dict[str, str | None] = {
            "state": "unknown",
            "agent_id": agent_id,
            "active_delegation_id": None,
            "observed_revision": observed_revision,
        }
        if agent_id is None or not Conversations._snapshot_is_complete(snapshot):
            return result
        for delegation_id, delegation in snapshot.delegations.items():
            if delegation.get("state") not in LIVE_WORKER_DELEGATION_STATES:
                continue
            target = delegation.get("target_ref")
            targets_agent = (
                isinstance(target, Mapping)
                and target.get("kind") == "agent"
                and target.get("id") == agent_id
            )
            if targets_agent or delegation.get("agent_id") == agent_id:
                return {
                    "state": "active",
                    "agent_id": agent_id,
                    "active_delegation_id": str(delegation_id),
                    "observed_revision": observed_revision,
                }
        return {**result, "state": "inactive"}

    @staticmethod
    def _recipient_agent_id(scope: Mapping[str, str], snapshot: Any) -> str | None:
        if scope["kind"] == "agent":
            agent_id = scope["id"]
            return agent_id if agent_id in snapshot.agents else None
        if scope["kind"] != "task":
            return None
        task = snapshot.tasks.get(scope["id"])
        if task is None:
            return None
        extensions = task.get("extensions")
        candidates = {
            value
            for value in (
                task.get("assigned_agent_id"),
                task.get("agent_id"),
                extensions.get("suggested_agent_id") if isinstance(extensions, Mapping) else None,
            )
            if isinstance(value, str) and value in snapshot.agents
        }
        return next(iter(candidates)) if len(candidates) == 1 else None

    @staticmethod
    def _snapshot_is_complete(snapshot: Any) -> bool:
        """Whether absence of a live delegation may be read as "inactive".

        ``ProjectSnapshot`` signals an incomplete replay through ``issues``
        (projection failures) and ``stale_refs`` (references whose target did
        not resolve); it carries no other completeness flag. Either signal makes
        the recipient state ``unknown`` rather than ``inactive``.
        """

        return not bool(getattr(snapshot, "issues", ())) and not bool(
            getattr(snapshot, "stale_refs", ())
        )

    def messages(
        self,
        thread_id: str,
        *,
        after: str | None = None,
        before: str | None = None,
        tail: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValidationError("Conversation page limit must be between 1 and 50.")
        if type(tail) is not bool or sum((after is not None, before is not None, tail)) > 1:
            raise ValidationError("Choose only one conversation cursor direction.")
        snapshot = self.manager.snapshot()
        thread = snapshot.threads.get(thread_id)
        if thread is None or not thread.get("conversation_scope"):
            raise LifecycleConflictError("Project conversation is unavailable.")
        messages = thread.get("messages", [])
        start = 0
        if after is not None:
            indices = [
                i for i, message in enumerate(messages) if message.get("message_id") == after
            ]
            if not indices:
                raise ValidationError("Conversation cursor is unavailable.")
            start = indices[0] + 1
        end = len(messages)
        if before is not None:
            indices = [
                i for i, message in enumerate(messages) if message.get("message_id") == before
            ]
            if not indices:
                raise ValidationError("Conversation cursor is unavailable.")
            end = indices[0]
        if tail or before is not None:
            start = max(0, end - limit)
        chosen = messages[start : min(start + limit, end)]
        items = [
            {
                **self.message_view(message, snapshot),
                "delivery": self.delivery_view(thread, message, snapshot),
            }
            for message in chosen
        ]
        return {
            "schema": "agent_commons.conversation-messages.v1",
            "conversation": self._thread(thread, snapshot),
            "messages": items,
            "previous_cursor": items[0]["message_id"] if items and start > 0 else None,
            "next_cursor": items[-1]["message_id"]
            if items and start + len(chosen) < len(messages)
            else None,
        }

    @staticmethod
    def message_view(message: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
        actor = message.get("actor") or {}
        session_id = actor.get("session_id")
        producing = {
            item.get("agent_id")
            for item in snapshot.delegations.values()
            if item.get("child_session_id") == session_id and item.get("agent_id")
        }
        agent_id = next(iter(producing)) if len(producing) == 1 else None
        agent = snapshot.agents.get(agent_id) if agent_id else None
        return {
            "message_id": message["message_id"],
            "body": message.get("body", ""),
            "attachments": message.get("attachments", []),
            "reply_to_message_id": message.get("reply_to_message_id"),
            "recorded_at": message.get("recorded_at"),
            "author": {
                "kind": "agent" if agent else ("unknown" if producing else "operator"),
                "agent_id": agent_id,
                "name": str(agent.get("name")) if agent else None,
            },
            # Actual read/acknowledgement belongs to the separately verified delivery store.
            "delivery": {"state": "recorded", "read_confirmed": False},
        }

    def reply(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        body: str,
        attachments: list[dict[str, Any]] | None = None,
        reply_to_message_id: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = "conversation-message-" + _key(idempotency_key)
        # This service is an operator entry point; workers use their addressed MCP path.
        snapshot = self.manager.snapshot()
        self._operator(snapshot)
        thread = snapshot.threads.get(thread_id)
        if thread is None or not thread.get("conversation_scope"):
            raise LifecycleConflictError("Project conversation is unavailable.")
        return self.manager.reply_thread(
            thread_id,
            expected_revision,
            body=body,
            attachments=attachments or [],
            reply_to_message_id=reply_to_message_id,
            idempotency_key=key,
        )

    def _attachment_context(
        self, thread_id: str, *, snapshot: Any | None = None
    ) -> tuple[Any, str]:
        from agent_commons.runtime.message_attachments import MessageAttachmentStore

        if snapshot is None:
            snapshot = self.manager.snapshot()
        thread = snapshot.threads.get(thread_id)
        if thread is None or not thread.get("conversation_scope"):
            raise LifecycleConflictError("Project conversation is unavailable.")
        operator = (
            self.manager.sessions.require_active(self.manager.session_id).session_id
            if self.manager.session_id
            else ""
        )
        return MessageAttachmentStore(
            collaboration_state_root(self.manager),
            workspace_root=self.manager.repo_root,
            workspace_id=self.manager.workspace_id,
        ), operator

    @staticmethod
    def _descriptor(item: Any) -> dict[str, Any]:
        return {
            "attachment_id": item.attachment_id,
            "digest": "sha256:" + item.sha256,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "display_name": item.filename,
            "category": item.kind,
        }

    def _draft_view(self, draft: Any) -> dict[str, Any]:
        return {
            "draft_id": draft.draft_id,
            "state": draft.state,
            "expires_at": draft.expires_at,
            "message_id": draft.message_id,
            "attachments": [self._descriptor(item) for item in draft.attachments],
        }

    def reserve_draft(self, thread_id: str) -> dict[str, Any]:
        snapshot = self.manager.snapshot()
        self._operator(snapshot)
        store, operator = self._attachment_context(thread_id, snapshot=snapshot)
        return self._draft_view(store.reserve_draft(thread_id=thread_id, operator_id=operator))

    def show_draft(self, thread_id: str, draft_id: str) -> dict[str, Any]:
        snapshot = self.manager.snapshot()
        self._operator(snapshot)
        store, operator = self._attachment_context(thread_id, snapshot=snapshot)
        return self._draft_view(
            store.show_draft(draft_id, thread_id=thread_id, operator_id=operator)
        )

    def upload(
        self,
        thread_id: str,
        draft_id: str,
        *,
        filename: str,
        media_type: str,
        data: bytes,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.manager.snapshot()
        self._operator(snapshot)
        store, operator = self._attachment_context(thread_id, snapshot=snapshot)
        item = store.upload(
            draft_id,
            thread_id=thread_id,
            operator_id=operator,
            filename=filename,
            declared_media_type=media_type,
            chunks=(data,),
            operation_id=operation_id,
        )
        return self._descriptor(item)

    def remove_attachment(
        self, thread_id: str, draft_id: str, attachment_id: str
    ) -> dict[str, Any]:
        snapshot = self.manager.snapshot()
        self._operator(snapshot)
        store, operator = self._attachment_context(thread_id, snapshot=snapshot)
        draft = store.remove_attachment(
            draft_id, attachment_id, thread_id=thread_id, operator_id=operator
        )
        return self._draft_view(draft)

    def read_attachment(
        self, thread_id: str, message_id: str, attachment_id: str
    ) -> tuple[Any, bytes]:
        snapshot = self.manager.snapshot()
        store, _ = self._attachment_context(thread_id, snapshot=snapshot)
        thread = snapshot.threads.get(thread_id)
        message = next(
            (item for item in thread.get("messages", []) if item.get("message_id") == message_id),
            None,
        )
        if message is None:
            raise LifecycleConflictError("Message attachment is unavailable.")
        expected = next(
            (
                item
                for item in message.get("attachments", [])
                if item.get("attachment_id") == attachment_id
            ),
            None,
        )
        if expected is None:
            raise LifecycleConflictError("Message attachment is unavailable.")
        item, data = store.read_bound(
            thread_id=thread_id, message_id=message_id, attachment_id=attachment_id
        )
        if self._descriptor(item) != expected:
            raise LifecycleConflictError("Message attachment is unavailable.")
        return item, data

    def send(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        body: str,
        idempotency_key: str,
        draft_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        from agent_commons.runtime.message_attachments import BindingReceipt

        key = "conversation-message-" + _key(idempotency_key)
        with self.manager._canonical_write_lock():
            snapshot = self.manager.snapshot()
            self._operator(snapshot)
            store, operator = self._attachment_context(thread_id, snapshot=snapshot)
            draft = (
                store.show_draft(draft_id, thread_id=thread_id, operator_id=operator)
                if draft_id
                else None
            )
            attachments = [self._descriptor(item) for item in draft.attachments] if draft else []
            message_id = self.manager._new_entity_id("message", "thread.replied", key)
            existing = next(
                (
                    item
                    for item in snapshot.threads[thread_id].get("messages", [])
                    if item.get("message_id") == message_id
                ),
                None,
            )
            # Bind retries to content independently of the CAS precondition. A
            # failed append can then recover after another message advances the
            # thread, without changing the frozen body or attachment identity.
            from agent_commons.domain.conversations import validate_conversation_payload

            try:
                validate_conversation_payload(
                    "thread.replied",
                    {
                        "body": body,
                        "attachments": attachments,
                        **(
                            {"reply_to_message_id": reply_to_message_id}
                            if reply_to_message_id is not None
                            else {}
                        ),
                    },
                )
            except ValidationError as exc:
                if existing is None and (draft is None or draft.state == "ready"):
                    raise ConversationMessageInvalid(
                        "Message content is invalid; the draft is editable."
                    ) from exc
                raise
            intent = {
                "key": key,
                "body": body,
                "attachments": attachments,
                "reply_to_message_id": reply_to_message_id,
            }
            binding_key = (
                "conversation-binding-"
                + hashlib.sha256(
                    json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
            prior_binding = draft is not None and draft.state != "ready"
            if prior_binding and draft.operation_id != binding_key:
                raise IdempotencyConflictError(
                    "The frozen draft belongs to different message content."
                )
            namespace = self.manager._namespace(self.manager._active_session())
            if (
                existing is None
                and draft is not None
                and draft.state == "ready"
                and draft.expires_at <= store.clock()
            ):
                # Only release the UI's frozen send when absence is proven under
                # the canonical lock. Bound drafts and ambiguous reservations keep
                # the original identity and recovery path, even after expiry.
                self.manager._guard_integrity(actor=self.manager._actor())
                if (
                    self.manager._event_for_idempotency_identity(namespace, key) is None
                    and self.manager.events.idempotency.lookup(namespace=namespace, key=key) is None
                ):
                    raise ConversationDraftExpired(
                        "Attachments expired before this message was recorded; upload them again."
                    )
                raise LifecycleConflictError("Expired draft has an unresolved send identity.")
            if existing is not None or prior_binding:
                canonical = self.manager._event_for_idempotency_identity(namespace, key)
                if canonical is not None:
                    original = canonical.event["payload"]
                    if original.get("thread_id") == thread_id and all(
                        original.get(field) == intent[field]
                        for field in ("body", "attachments", "reply_to_message_id")
                    ):
                        expected_revision = original["expected_revision"]
                elif (
                    prior_binding
                    and self.manager.events.idempotency.lookup(namespace=namespace, key=key) is None
                ):
                    # Under the canonical lock: no event, no reservation and an
                    # exact frozen-content binding prove this append never won.
                    # Rebase only the concurrency precondition, preserving key.
                    self.manager._guard_integrity(actor=self.manager._actor())
                    expected_revision = snapshot.threads[thread_id]["revision"]
            binding_entered = False

            def before_append() -> None:
                nonlocal binding_entered
                binding_entered = True  # begin_binding itself may fail after a durable replace.
                if draft:
                    store.begin_binding(
                        draft_id,
                        thread_id=thread_id,
                        operator_id=operator,
                        operation_id=binding_key,
                    )

            try:
                result = self.manager.reply_thread(
                    thread_id,
                    expected_revision,
                    body=body,
                    attachments=attachments,
                    reply_to_message_id=reply_to_message_id,
                    idempotency_key=key,
                    _before_append=before_append,
                )
            except IdempotencyConflictError as exc:
                if not binding_entered:
                    raise ConversationIntentConflict(
                        "This operation already identifies different message content."
                    ) from exc
                raise
            except (ValidationError, SecurityPolicyError) as exc:
                if not binding_entered and existing is None and not prior_binding:
                    raise ConversationMessageInvalid(
                        "Message content is invalid; the draft was not frozen."
                    ) from exc
                raise
            except LifecycleConflictError as exc:
                if (
                    not binding_entered
                    and existing is None
                    and not prior_binding
                    and snapshot.threads[thread_id]["revision"] != expected_revision
                ):
                    raise ConversationRevisionConflict(
                        "The conversation changed before this message was recorded."
                    ) from exc
                raise
            snapshot = self.manager.snapshot()
            actual = next(
                item
                for item in snapshot.threads[thread_id].get("messages", [])
                if item["message_id"] == message_id
            )
            if actual.get("attachments", []) != attachments:
                raise LifecycleConflictError(
                    "Canonical attachment binding differs from its frozen draft."
                )
            if draft:
                store.finalize_binding(
                    draft_id,
                    thread_id=thread_id,
                    operator_id=operator,
                    receipt=BindingReceipt(
                        self.manager.workspace_id,
                        thread_id,
                        operator,
                        binding_key,
                        message_id,
                        tuple(item["attachment_id"] for item in attachments),
                    ),
                )
            return {"message_id": message_id, "revision": result["revision"], "state": "recorded"}

    def delivery_view(
        self, thread: Mapping[str, Any], message: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]:
        from agent_commons.runtime.message_delivery import MessageDeliveryStore

        store = MessageDeliveryStore(
            collaboration_state_root(self.manager), self.manager.workspace_id
        )
        addressed = set(thread.get("to", []))
        recipients = []
        for delegation in snapshot.delegations.values():
            author_session = message.get("actor", {}).get("session_id")
            if author_session and delegation.get("child_session_id") == author_session:
                continue
            target = delegation.get("target_ref") or {}
            reachable = {"*", delegation.get("agent_id"), delegation.get("child_session_id")}
            if target.get("kind") == "task":
                reachable.add("task:" + target["id"])
            if not addressed.intersection(reachable) or delegation.get("purpose") not in {
                "implementation",
                "verification",
            }:
                continue
            receipt = store.show(
                str(thread["id"]), str(message["message_id"]), str(delegation["id"])
            )
            active = delegation.get("state") in {"active", "input_needed"}
            if not active and receipt["fetched_at"] is None:
                continue
            state = (
                "acknowledged"
                if receipt["acknowledged_at"]
                else ("fetched" if receipt["fetched_at"] else "queued")
            )
            recipients.append(
                {
                    "agent_id": delegation.get("agent_id"),
                    "state": state,
                    "acknowledged_at": receipt["acknowledged_at"],
                    "fetched_at": receipt["fetched_at"],
                }
            )
        answered = any(
            item.get("reply_to_message_id") == message["message_id"]
            and item.get("actor", {}).get("session_id")
            != message.get("actor", {}).get("session_id")
            for item in thread.get("messages", [])
        )
        acknowledged = any(item["state"] == "acknowledged" for item in recipients)
        state = (
            "answered"
            if answered
            else (
                "acknowledged"
                if acknowledged
                else (
                    "fetched"
                    if any(item["state"] == "fetched" for item in recipients)
                    else ("queued" if recipients else "recorded")
                )
            )
        )
        return {"state": state, "read_confirmed": acknowledged, "recipients": recipients[-128:]}
