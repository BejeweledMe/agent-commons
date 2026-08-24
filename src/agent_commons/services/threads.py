"""Thread commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.errors import ValidationError

from ._validation import _nonempty_list


class ThreadCommands:
    """Commands for coordination threads and the main chat."""

    def list_threads(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("thread", state=state)

    def open_thread(
        self,
        *,
        thread_type: str,
        subject: str,
        desired_outcome: str,
        to: Sequence[str],
        related_refs: Sequence[Mapping[str, str]] = (),
        extensions: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.opened", idempotency_key)
        thread_id = self._new_entity_id("thread", "thread.opened", key)
        refs = self._assert_refs_exist(related_refs)
        payload: dict[str, Any] = {
            "thread_id": thread_id,
            "thread_type": thread_type,
            "subject": subject,
            "desired_outcome": desired_outcome,
            "to": sorted(set(_nonempty_list(to, "to"))),
            "related_refs": refs,
        }
        if extensions:
            payload["extensions"] = dict(extensions)
        return self.record_event(
            "thread.opened",
            payload,
            idempotency_key=key,
            tags=("thread", thread_type),
        )

    # -- the main chat --------------------------------------------------------

    def open_engagement(
        self,
        *,
        subject: str,
        body: str,
        objective_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Open the standing conversation between a person and the top of the org.

        Addressed to every role that answers to the operator, as one thread with
        several recipients rather than one thread each.  Merging separate
        threads in the view would invent an ordering no record has; a thread's
        recipient list already means "all of these", so the merged case is the
        canonical one.
        """

        # Validate the message before opening the thread, and open-then-reply as
        # one action.  The thread.opened used to land first and the reply
        # validate second, so a bad body left an empty engagement thread in the
        # immutable ledger forever (round 2, product).
        if not body.strip():
            raise ValidationError("an engagement needs a non-empty message")
        with self._canonical_write_lock():
            snapshot = self.snapshot()
            top = sorted(
                identifier
                for identifier, record in snapshot.agents.items()
                if record.get("state") == "active"
                and not record.get("template")
                and not record.get("created_by_agent_id")
            )
            related: list[dict[str, str]] = [{"kind": "agent", "id": item} for item in top]
            if objective_id:
                related.append({"kind": "objective", "id": objective_id})
            opened = self.open_thread(
                thread_type="engagement",
                subject=subject,
                desired_outcome="the work moves, and a person stays in the loop",
                # The operator is a recipient too, so replies land in their inbox.
                to=("operator", *top),
                related_refs=related,
                idempotency_key=self._idempotency_key("thread.opened", idempotency_key),
            )
            return self.reply_thread(
                opened["entity_ref"]["id"],
                opened["revision"],
                body=body,
                idempotency_key=f"{opened['idempotency_key']}:body",
            )

    def list_engagements(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        """Main chats, newest last, with the roles each one actually addresses.

        Recipients are fixed when the thread is opened, so a role created later
        is reported as unaddressed rather than quietly folded in: rewriting
        canonical recipients from a projection is precisely what a view must
        never do.
        """

        snapshot = self.snapshot()
        current_top = {
            identifier
            for identifier, record in snapshot.agents.items()
            if record.get("state") == "active"
            and not record.get("template")
            and not record.get("created_by_agent_id")
        }
        found = []
        for identifier, thread in sorted(snapshot.threads.items()):
            if thread.get("thread_type") != "engagement":
                continue
            if not include_resolved and thread.get("state") != "open":
                continue
            addressed = {str(item) for item in thread.get("to") or ()}
            found.append(
                {
                    "thread_id": identifier,
                    "revision": thread.get("revision"),
                    "state": thread.get("state"),
                    "subject": thread.get("subject"),
                    "addressed_roles": sorted(addressed & current_top),
                    "unaddressed_roles": sorted(current_top - addressed),
                    "messages": [dict(item) for item in thread.get("messages") or ()],
                    "recorded_at": thread.get("recorded_at"),
                }
            )
        return found

    def reply_thread(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        body: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.replied", idempotency_key)
        message_id = self._new_entity_id("message", "thread.replied", key)
        return self.record_event(
            "thread.replied",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "body": body,
                "expected_revision": expected_revision,
            },
            idempotency_key=key,
            tags=("thread",),
        )

    def resolve_thread(
        self,
        thread_id: str,
        expected_revision: str,
        *,
        resolution: str,
        summary: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("thread.resolved", idempotency_key)
        return self.record_event(
            "thread.resolved",
            {
                "thread_id": thread_id,
                "expected_revision": expected_revision,
                "resolution": resolution,
                "summary": summary,
            },
            idempotency_key=key,
            tags=("thread",),
        )
