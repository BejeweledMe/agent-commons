from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    AttemptState,
    AttemptStore,
    DesignPackageBindingRequest,
    LaunchPlanner,
    ProviderInitializationState,
    ProviderInitializationStatus,
)
from agent_commons.services.design_gallery import (
    DesignGalleryReads,
    GalleryReadRefusal,
    GalleryRefusalCode,
)
from agent_commons.ui.context import UIContext
from tests.services.test_delegation_launch_plan import (
    _Initialization,
    _service,
    _SuccessWithoutTerminalMcp,
    _workspace,
)
from tests.services.test_design_packages import _draft, _screen_work


@pytest.mark.parametrize("entry", ("first", "cached", "retry"))
@pytest.mark.parametrize(
    "source_change",
    (
        "producer_revised",
        "producer_invalidated",
        "artifact_revised",
        "restricted",
        "artifact_invalidated",
        "missing_manifest",
        "missing_bytes",
        "changed_bytes",
    ),
)
def test_gallery_selection_and_launch_refuse_stale_design_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    source_change: str,
) -> None:
    manager, delegation = _workspace(tmp_path)
    artifact, producer, source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, producer), idempotency_key="freshness-package"
    )
    selection = DesignPackageBindingRequest(package.design_package_id, package.revision)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    delegation_id = str(delegation["entity_ref"]["id"])
    revision = str(delegation["revision"])
    launch_key = "design-freshness-launch"
    context = UIContext(
        manager.repo_root,
        state_root=manager.paths.state_root,
        writer_session_id=manager.session_id,
    )
    assert context.launch_options()["design_packages"][0]["design_package_id"] == (
        package.design_package_id
    )
    if entry == "cached":
        initialization.state = ProviderInitializationState.HOST_SANDBOX_REFUSED
        with pytest.raises(ConfigurationError):
            service.run(
                delegation_id, revision, idempotency_key=launch_key, design_package=selection
            )
        initialization.state = ProviderInitializationState.READY
        assert service.design_package_bindings.get(delegation_id) is not None
        assert service.attempts.list_attempts() == ()
    elif entry == "retry":
        original_transition = AttemptStore.transition

        def crash_before_launch(
            store: AttemptStore, attempt_id: str, state: AttemptState, **values: Any
        ) -> Any:
            if state is AttemptState.LAUNCHING:
                raise RuntimeError("design freshness crash")
            return original_transition(store, attempt_id, state, **values)

        with monkeypatch.context() as patch:
            patch.setattr(AttemptStore, "transition", crash_before_launch)
            with pytest.raises(RuntimeError, match="design freshness crash"):
                service.run(
                    delegation_id, revision, idempotency_key=launch_key, design_package=selection
                )
        assert len(service.attempts.list_attempts()) == 1

    if source_change == "producer_revised":
        manager.submit_task(
            str(producer["entity_ref"]["id"]),
            str(producer["revision"]),
            summary="Submit the same exact screen for review.",
            artifact_refs=(artifact["entity_ref"],),
            idempotency_key="changed-design-producer",
        )
    elif source_change in {"producer_invalidated", "artifact_invalidated"}:
        invalidated = producer if source_change == "producer_invalidated" else artifact
        manager.invalidate_event(
            str(invalidated["revision"]),
            reason="The screen provenance is no longer valid.",
            idempotency_key="invalid-design-source",
        )
    elif source_change in {"artifact_revised", "restricted"}:
        manager.revise_artifact(
            str(artifact["entity_ref"]["id"]),
            str(artifact["revision"]),
            source,
            media_type="image/png",
            classification="restricted" if source_change == "restricted" else "internal",
            idempotency_key="changed-design-artifact",
        )
    elif source_change == "missing_manifest":
        current = manager.snapshot().artifacts[str(artifact["entity_ref"]["id"])]
        manager.manifests.get(current["manifest_ref"]).path.unlink()
    elif source_change == "missing_bytes":
        source.unlink()
    else:
        source.write_bytes(b"invalid changed PNG")

    sources = DesignGalleryReads(manager).inspect_sources(package, snapshot=manager.snapshot())
    assert sources.freshness == "stale"
    if source_change in {"producer_invalidated", "artifact_invalidated", "missing_manifest"}:
        with pytest.raises(GalleryReadRefusal) as gallery_refusal:
            DesignGalleryReads(manager).get(package.design_package_id)
        assert gallery_refusal.value.code is GalleryRefusalCode.PROJECTION_UNAVAILABLE
    else:
        assert DesignGalleryReads(manager).get(package.design_package_id).packages == (sources,)
    assert context.launch_options()["design_packages"] == []
    binding_before = service.design_package_bindings.get(delegation_id)
    context_before = service.context_bindings.get(delegation_id)
    attempts_before = service.attempts.list_attempts()
    sessions_before = manager.sessions.list_sessions()
    events_before = tuple(manager.events.iter_events())
    probes_before = initialization.calls

    def unexpected_build(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Stale design provenance must refuse before invocation construction")

    monkeypatch.setattr(LaunchPlanner, "build", staticmethod(unexpected_build))
    # Removing required canonical provenance also removes the package from the
    # effective projection; byte-only or revision drift leaves a stale package.
    expected_code = (
        "design_package_missing"
        if source_change in {"producer_invalidated", "artifact_invalidated", "missing_manifest"}
        else "design_package_stale"
    )
    with pytest.raises(ConfigurationError) as validation:
        service.validate_design_package_selection(selection)
    assert validation.value.code == expected_code
    with pytest.raises(ConfigurationError) as launch:
        service.run(
            delegation_id,
            revision,
            idempotency_key=launch_key,
            design_package=selection,
            retry=entry == "retry",
        )
    assert launch.value.code == expected_code
    assert initialization.calls == probes_before
    assert runner.invocations == []
    assert service.design_package_bindings.get(delegation_id) == binding_before
    assert service.context_bindings.get(delegation_id) == context_before
    assert service.attempts.list_attempts() == attempts_before
    assert manager.sessions.list_sessions() == sessions_before
    assert tuple(manager.events.iter_events()) == events_before


def test_design_sources_changed_during_probe_refuse_before_child(
    tmp_path: Path,
) -> None:
    manager, delegation = _workspace(tmp_path)
    artifact, producer, source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, producer), idempotency_key="probe-design-package"
    )

    class ChangingInitialization(_Initialization):
        def probe(self, profile: Any, **values: Any) -> ProviderInitializationStatus:
            manager.revise_artifact(
                str(artifact["entity_ref"]["id"]),
                str(artifact["revision"]),
                source,
                media_type="image/png",
                classification="restricted",
                idempotency_key="probe-design-restriction",
            )
            return super().probe(profile, **values)

    runner = _SuccessWithoutTerminalMcp()
    service = _service(
        manager,
        runner=runner,
        initialization=ChangingInitialization(ProviderInitializationState.READY),
    )
    sessions_before = manager.sessions.list_sessions()
    with pytest.raises(ConfigurationError) as refused:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="probe-design-launch",
            design_package=DesignPackageBindingRequest(package.design_package_id, package.revision),
        )
    assert refused.value.code == "design_package_stale"
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert manager.sessions.list_sessions() == sessions_before


def test_current_design_sources_allow_a_different_launching_session(tmp_path: Path) -> None:
    manager, delegation = _workspace(tmp_path)
    artifact, producer, source = _screen_work(manager)
    package = manager.design_packages.publish(
        _draft(manager, artifact, producer), idempotency_key="valid-design-package"
    )
    session = manager.start_session(
        stable_instance_id="design-launch-consumer-12345678",
        principal="operator",
        client="codex",
        software="pytest",
        role="implementer",
    )
    manager.session_id = session["session_id"]
    original = manager.get_delegation(str(delegation["entity_ref"]["id"]))
    delegation = manager.create_delegation(
        target_ref=original["target_ref"],
        target_revision=original["target_revision"],
        target_profile=original["target_profile"],
        purpose=original["purpose"],
        limits=original["limits"],
        idempotency_key="consumer-design-delegation",
    )
    runner = _SuccessWithoutTerminalMcp()
    service = _service(
        manager, runner=runner, initialization=_Initialization(ProviderInitializationState.READY)
    )
    selection = DesignPackageBindingRequest(package.design_package_id, package.revision)
    assert service.validate_design_package_selection(selection).design_package_id == (
        package.design_package_id
    )
    service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="valid-design-launch",
        design_package=selection,
    )
    assert len(runner.invocations) == 1
    assert source.read_bytes() not in runner.invocations[0].stdin
