"""Stable 25-case offline workflow-evaluation catalog.

Only the cases labelled ``implemented`` execute in ordinary CI.  ``planned``
and ``unsupported`` cases are emitted as non-passing outcomes so the catalog
can guide future work without implying that a capability already exists.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import TemporaryDirectory

from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.errors import ConfigurationError
from agent_commons.services import CommonsManager

from .fake_provider import DeterministicFakeProvider
from .model import EvalCase, EvalResult, EvalStatus, MetricsAggregate

CATALOG_VERSION = "2026-08-wave4-v1"


def _case(
    suffix: str,
    title: str,
    scenario: str,
    status: EvalStatus,
    *tags: str,
) -> EvalCase:
    return EvalCase(
        case_id=f"eval.{suffix}",
        title=title,
        scenario=scenario,
        status=status,
        graders=("deterministic_state", "fake_provider_contract"),
        failure_tags=tags,
    )


EVAL_CATALOG: tuple[EvalCase, ...] = (
    _case(
        "p0-state-base-isolation",
        "State-base isolates workspaces",
        "state_isolation",
        EvalStatus.IMPLEMENTED,
        "state",
        "safety",
    ),
    _case(
        "p0-exact-root-mismatch",
        "Exact root rejects foreign workspace",
        "ownership_mismatch",
        EvalStatus.IMPLEMENTED,
        "state",
        "safety",
    ),
    _case(
        "p0-legacy-base-unproven",
        "Legacy base fails closed",
        "legacy_rollback",
        EvalStatus.IMPLEMENTED,
        "migration",
        "safety",
    ),
    _case(
        "p0-read-only-no-mutation",
        "Read-only open leaves state absent",
        "read_only",
        EvalStatus.IMPLEMENTED,
        "safety",
        "rollback",
    ),
    _case(
        "p0-support-path-privacy",
        "Support hides paths by default",
        "support_privacy",
        EvalStatus.IMPLEMENTED,
        "privacy",
        "cli",
    ),
    _case(
        "p0-session-current-selection",
        "Current session needs explicit selection",
        "session_selection",
        EvalStatus.IMPLEMENTED,
        "session",
        "cli",
    ),
    _case(
        "p0-session-shell-export",
        "Shell export is explicit",
        "session_export",
        EvalStatus.IMPLEMENTED,
        "session",
        "cli",
    ),
    _case(
        "p0-typed-reference-errors",
        "Typed reference errors identify their field",
        "typed_references",
        EvalStatus.IMPLEMENTED,
        "cli",
        "safety",
    ),
    _case(
        "claims-overlap",
        "Conflicting path claims",
        "claims",
        EvalStatus.PLANNED,
        "claims",
        "concurrency",
    ),
    _case(
        "dag-cycle-rejection",
        "Dependency cycle rejection",
        "dag",
        EvalStatus.PLANNED,
        "orchestration",
    ),
    _case(
        "task-next-critical-path",
        "Ready task and critical path",
        "dag",
        EvalStatus.PLANNED,
        "orchestration",
    ),
    _case(
        "task-input-safe-request",
        "Bounded task input request",
        "input",
        EvalStatus.PLANNED,
        "privacy",
        "communication",
    ),
    _case(
        "resume-attestation",
        "Reattach only with process attestation",
        "resume",
        EvalStatus.PLANNED,
        "runtime",
        "safety",
    ),
    _case(
        "active-cancel-receipt",
        "Cancellation needs termination receipt",
        "cancel",
        EvalStatus.PLANNED,
        "runtime",
        "safety",
    ),
    _case(
        "crash-reconcile",
        "Crash ambiguity becomes needs operator",
        "crash_recovery",
        EvalStatus.PLANNED,
        "runtime",
        "recovery",
    ),
    _case(
        "builder-path-attestation",
        "Builder path attestation",
        "path_enforcement",
        EvalStatus.PLANNED,
        "paths",
        "safety",
    ),
    _case(
        "compact-orient-budget",
        "Compact orientation budget",
        "compact_read",
        EvalStatus.PLANNED,
        "performance",
        "reads",
    ),
    _case(
        "inbox-cursor",
        "Inbox cursor and bounded projection",
        "compact_read",
        EvalStatus.PLANNED,
        "performance",
        "reads",
    ),
    _case(
        "council-dissent",
        "Council preserves dissent",
        "council",
        EvalStatus.UNSUPPORTED,
        "orchestration",
        "governance",
    ),
    _case(
        "route-dry-run",
        "Route dry-run has no launch",
        "routing",
        EvalStatus.UNSUPPORTED,
        "orchestration",
        "safety",
    ),
    _case(
        "budget-exhaustion",
        "Aggregate budget exhaustion",
        "budget",
        EvalStatus.PLANNED,
        "budget",
        "runtime",
    ),
    _case(
        "stale-state-maintenance",
        "Stale session and claim maintenance",
        "stale_state",
        EvalStatus.PLANNED,
        "maintenance",
        "claims",
    ),
    _case(
        "otel-privacy",
        "Telemetry label privacy",
        "observability",
        EvalStatus.UNSUPPORTED,
        "privacy",
        "observability",
    ),
    _case(
        "secret-rejection",
        "Secrets rejected from canonical metadata",
        "secret_rejection",
        EvalStatus.PLANNED,
        "security",
        "privacy",
    ),
    _case(
        "provider-failure-convergence",
        "Provider failure convergence",
        "provider_failure",
        EvalStatus.UNSUPPORTED,
        "runtime",
        "provider",
    ),
)


def catalog_cases() -> tuple[EvalCase, ...]:
    """Return the ordered catalog; ordering is an API for stable CI reports."""

    return EVAL_CATALOG


def _evidence_digest(case: EvalCase, trace_digest: str, outcome_code: str) -> str:
    payload = json.dumps(
        {
            "catalog_version": CATALOG_VERSION,
            "case_id": case.case_id,
            "outcome_code": outcome_code,
            "trace": trace_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _initialize(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name=name)
    return repo


def _state_base_isolation(root: Path) -> None:
    base = root / "state-base"
    first = CommonsManager(_initialize(root, "alpha"), state_base=base)
    second = CommonsManager(_initialize(root, "beta"), state_base=base)
    opened = first.start_session(
        stable_instance_id="eval-alpha-window-12345678",
        principal="eval",
        client="fake-provider",
        software="offline",
        role="evaluator",
    )
    try:
        second.show_session(opened["session_id"])
    except Exception as exc:  # The public service reports a typed validation error.
        if exc.__class__.__name__ != "ValidationError":
            raise
    else:
        raise AssertionError("a session leaked across workspace namespaces")


def _exact_root_mismatch(root: Path) -> None:
    state = root / "exact-state"
    CommonsManager(_initialize(root, "first"), state_root=state)
    try:
        CommonsManager(_initialize(root, "second"), state_root=state)
    except ConfigurationError as exc:
        if getattr(exc, "code", None) != "state_owner_mismatch":
            raise
    else:
        raise AssertionError("foreign exact state root was accepted")


def _legacy_base_unproven(root: Path) -> None:
    base = root / "legacy-state"
    foreign = base / "sessions" / "foreign.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("{}\n", encoding="utf-8")
    try:
        CommonsManager(_initialize(root, "legacy"), state_base=base)
    except ConfigurationError as exc:
        if getattr(exc, "code", None) != "state_owner_unproven":
            raise
    else:
        raise AssertionError("unproven legacy state was bypassed")
    if foreign.read_text(encoding="utf-8") != "{}\n" or (base / "workspaces").exists():
        raise AssertionError("legacy state was mutated")


def _read_only_no_mutation(root: Path) -> None:
    state = root / "absent-state"
    CommonsManager(_initialize(root, "read-only"), state_root=state, read_only=True)
    if state.exists():
        raise AssertionError("read-only evaluation created operational state")


def _support_path_privacy(root: Path) -> None:
    repo = _initialize(root, "support")
    state = root / "private-state"
    result = CliRunner().invoke(
        cli,
        ["--repo", str(repo), "--state-root", str(state), "--read-only", "--json", "support"],
    )
    if result.exit_code != 0 or str(root) in result.output or state.exists():
        raise AssertionError("support disclosed a path or mutated read-only state")


def _start_session(runner: CliRunner, repo: Path, state_base: Path) -> dict[str, object]:
    started = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(state_base),
            "--json",
            "session",
            "start",
            "--stable-instance-id",
            "eval-session-window-12345678",
            "--principal",
            "eval",
            "--client",
            "fake-provider",
            "--software",
            "offline",
            "--role",
            "evaluator",
        ],
    )
    if started.exit_code != 0:
        raise AssertionError("could not create isolated evaluation session")
    return json.loads(started.output)


def _session_current_selection(root: Path) -> None:
    repo = _initialize(root, "session-current")
    runner = CliRunner()
    _start_session(runner, repo, root / "state")
    current = runner.invoke(cli, ["--repo", str(repo), "--json", "session", "current"])
    if (
        current.exit_code != 1
        or json.loads(current.output)["error"].get("code") != "session_not_selected"
    ):
        raise AssertionError("session current auto-selected an identity")


def _session_shell_export(root: Path) -> None:
    repo = _initialize(root, "session-export")
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(root / "state"),
            "session",
            "start",
            "--stable-instance-id",
            "eval-export-window-12345678",
            "--principal",
            "eval",
            "--client",
            "fake-provider",
            "--software",
            "offline",
            "--role",
            "evaluator",
            "--shell-export",
            "zsh",
        ],
    )
    if result.exit_code != 0 or result.output.count("AGENT_COMMONS_SESSION_ID") != 1:
        raise AssertionError("shell export is not a single explicit selection snippet")


def _typed_reference_error(root: Path) -> None:
    repo = _initialize(root, "typed-ref")
    runner = CliRunner()
    session = _start_session(runner, repo, root / "state")
    result = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(root / "state"),
            "--session-id",
            str(session["session_id"]),
            "--json",
            "review",
            "request",
            "--target-ref",
            "not-a-reference",
            "--target-revision",
            "evt.test",
            "--criterion",
            "typed field diagnostics",
        ],
    )
    body = json.loads(result.output)
    if result.exit_code != 1 or body["error"].get("code") != "invalid_typed_ref":
        raise AssertionError("typed-reference diagnostic was not structured")


IMPLEMENTED_EXECUTORS: dict[str, Callable[[Path], None]] = {
    "eval.p0-state-base-isolation": _state_base_isolation,
    "eval.p0-exact-root-mismatch": _exact_root_mismatch,
    "eval.p0-legacy-base-unproven": _legacy_base_unproven,
    "eval.p0-read-only-no-mutation": _read_only_no_mutation,
    "eval.p0-support-path-privacy": _support_path_privacy,
    "eval.p0-session-current-selection": _session_current_selection,
    "eval.p0-session-shell-export": _session_shell_export,
    "eval.p0-typed-reference-errors": _typed_reference_error,
}


def execute_case(case: EvalCase, root: Path) -> EvalResult:
    """Run one isolated case and return only bounded result metadata."""

    started = time.monotonic_ns()
    if case.status is EvalStatus.PLANNED:
        return EvalResult(case.case_id, EvalStatus.PLANNED, "planned_capability", 0)
    if case.status is EvalStatus.UNSUPPORTED:
        return EvalResult(case.case_id, EvalStatus.UNSUPPORTED, "unsupported_offline_harness", 0)
    trace = DeterministicFakeProvider().run(case)
    try:
        IMPLEMENTED_EXECUTORS[case.case_id](root)
    except AssertionError:
        outcome = "assertion_failed"
        status = EvalStatus.FAILED
    except Exception:
        outcome = "unexpected_exception"
        status = EvalStatus.ERROR
    else:
        outcome = "passed"
        status = EvalStatus.PASSED
    latency_ms = (time.monotonic_ns() - started) // 1_000_000
    return EvalResult(
        case.case_id,
        status,
        outcome,
        latency_ms,
        evidence_digest=_evidence_digest(case, trace.digest, outcome),
    )


def run_catalog(cases: Iterable[EvalCase] = EVAL_CATALOG) -> tuple[EvalResult, ...]:
    """Run each implemented case in a fresh temporary directory."""

    results: list[EvalResult] = []
    for case in cases:
        with TemporaryDirectory(prefix="agent-commons-eval-") as directory:
            results.append(execute_case(case, Path(directory)))
    return tuple(results)


def aggregate_metrics(results: Iterable[EvalResult]) -> MetricsAggregate:
    """Compute documented low-cardinality metrics without raw traces or labels."""

    values = tuple(results)
    executed = tuple(
        result
        for result in values
        if result.status in {EvalStatus.PASSED, EvalStatus.FAILED, EvalStatus.ERROR}
    )
    passed = sum(result.status is EvalStatus.PASSED for result in values)
    failed = sum(result.status is EvalStatus.FAILED for result in values)
    errors = sum(result.status is EvalStatus.ERROR for result in values)
    planned = sum(result.status is EvalStatus.PLANNED for result in values)
    unsupported = sum(result.status is EvalStatus.UNSUPPORTED for result in values)
    denominator = len(executed)
    pass_rate = passed / denominator if denominator else None
    consistency = 1.0 if denominator and passed == denominator else (0.0 if denominator else None)
    return MetricsAggregate(
        catalog_version=CATALOG_VERSION,
        total_cases=len(values),
        executed_cases=denominator,
        passed_cases=passed,
        failed_cases=failed,
        planned_cases=planned,
        unsupported_cases=unsupported,
        error_cases=errors,
        pass_at_1=pass_rate,
        pass_power_k=consistency,
        needs_operator_rate=(
            sum(result.needs_operator for result in executed) / denominator if denominator else None
        ),
        latency_ms_total=sum(result.latency_ms for result in values),
        provider_units_total=sum(result.provider_units for result in values),
        handoff_loops_total=sum(result.handoff_loops for result in values),
    )
