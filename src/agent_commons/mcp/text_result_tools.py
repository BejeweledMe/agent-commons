"""Narrow implementation-worker text report and handoff surface."""

from collections.abc import Callable, Mapping
from typing import Any

from agent_commons.errors import CommonsError, IdempotencyConflictError, LifecycleConflictError
from agent_commons.services.text_results import REFUSAL, WorkerTextResults


def register_text_result_tools(
    register: Callable[..., Any], commons: Any, *, worker_binding: Mapping[str, Any] | None
) -> None:
    service = WorkerTextResults(commons, worker_binding)

    def decorate(function):
        return register(
            {
                "readOnlyHint": False,
                "idempotentHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
            worker_only=True,
            worker_purposes=("implementation",),
            worker_terminal_retry=function.__name__ == "commons_finalize_task_result",
        )(function)

    def invoke(method, **kwargs):
        try:
            return method(**kwargs)
        except IdempotencyConflictError:
            raise IdempotencyConflictError(
                "Text result retry differs from its original operation."
            ) from None
        except (CommonsError, OSError, ValueError, TypeError, KeyError):
            raise LifecycleConflictError(REFUSAL) from None

    @decorate
    def commons_publish_text_result(
        title: str,
        summary: str,
        checks: list[str],
        content: str,
        expected_task_revision: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Retain an immutable UTF-8 report for your exact active task revision.

        Inline text only: 64 KiB content, 256-byte title, 4096-byte summary,
        32 checks of 512 bytes each. No credentials or arbitrary file reads.
        Publication leaves task and delegation states unchanged. Reuse the
        identical arguments/key after a lost response; new content needs a new key.
        """
        return invoke(
            service.publish,
            title=title,
            summary=summary,
            checks=checks,
            content=content,
            expected_task_revision=expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @decorate
    def commons_complete_task_result(
        expected_task_revision: str, summary: str, artifact_ids: list[str], idempotency_key: str
    ) -> dict[str, Any]:
        """Complete only your delegated task with 1–64 artifacts you published.

        Pass the original delegated task revision. Retains exact evidence; does
        not submit for review, finalize the run, or accept the task.
        """
        return invoke(
            service.transition,
            phase="complete",
            expected_task_revision=expected_task_revision,
            summary=summary,
            artifact_ids=artifact_ids,
            idempotency_key=idempotency_key,
        )

    @decorate
    def commons_submit_task_result(
        expected_task_revision: str, summary: str, artifact_ids: list[str], idempotency_key: str
    ) -> dict[str, Any]:
        """Submit your completed task revision and unchanged evidence for review.

        Pass the revision returned by commons_complete_task_result. Independent
        review and owner acceptance remain separate; finalize the run afterward.
        """
        return invoke(
            service.transition,
            phase="submit",
            expected_task_revision=expected_task_revision,
            summary=summary,
            artifact_ids=artifact_ids,
            idempotency_key=idempotency_key,
        )

    @decorate
    def commons_finalize_task_result(
        expected_task_revision: str, summary: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Finalize this run after explicit submission of your exact task revision.

        Pass the revision returned by commons_submit_task_result. No approval or
        acceptance is implied. Identical retries recover a lost terminal response.
        """
        return invoke(
            service.finalize,
            expected_task_revision=expected_task_revision,
            summary=summary,
            idempotency_key=idempotency_key,
        )
