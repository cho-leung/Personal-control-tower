from dataclasses import replace
from pathlib import Path

import yaml

from ..guardrails import (
    GovernanceError,
    assert_frozen_artifact,
    assert_transition,
    assert_valid_auditor,
)
from ..models import Role, State
from ..handoffs import Handoff, HandoffStore
from ..tasks import (
    ArtifactRef,
    Task,
    TaskStatus,
    TaskStore,
)


AUDIT_REQUEST_PENDING = "AUDIT_PENDING"
AUDIT_REQUEST_COMPLETED = "COMPLETED"


def _safe_phase(phase):
    if not phase or Path(phase).name != phase:
        raise GovernanceError(
            f"Invalid project phase: {phase}"
        )

    return phase


def audit_request_path(state_path, phase):
    return (
        state_path.parent
        / "audits"
        / f"{_safe_phase(phase)}_audit_request.md"
    )


def read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)

    if len(parts) < 3:
        raise GovernanceError(
            f"Missing YAML frontmatter: {path}"
        )

    metadata = yaml.safe_load(parts[1])

    if not isinstance(metadata, dict):
        raise GovernanceError(
            f"Invalid YAML frontmatter: {path}"
        )

    return metadata


def _render_request(metadata):
    audit_details = ""

    if metadata.get("status") == AUDIT_REQUEST_COMPLETED:
        audit_details = f"""

## Completion

- Verdict: `{metadata.get('audit_verdict')}`
- Audit: `{metadata.get('audit_path')}`
"""

    body = f"""
# Independent Audit Request

## Assignment

- Project: `{metadata['project_id']}`
- Phase: `{metadata['phase']}`
- Producer: `{metadata['producer']}`
- Auditor: `{metadata['auditor']}`
- Status: `{metadata['status']}`

## Frozen Artifact

- Path: `{metadata['artifact_path']}`
- SHA-256: `{metadata['artifact_sha256']}`

## Root Decision

- Approved by: `{metadata['approved_by']}`
- Proposal: `{metadata['proposal_id']}`
- Reason: {metadata.get('reason') or 'Independent audit required.'}
{audit_details}
"""

    serialized = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
    )

    return "---\n" + serialized + "---\n" + body.lstrip()


def write_audit_request(path, metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _render_request(metadata)

    if path.exists():
        existing = read_frontmatter(path)
        evidence_keys = (
            "project_id",
            "phase",
            "artifact_path",
            "artifact_sha256",
            "producer",
            "auditor",
        )

        for key in evidence_keys:
            if existing.get(key) != metadata.get(key):
                raise GovernanceError(
                    "Conflicting audit request evidence: "
                    f"{key}"
                )

        return existing

    path.write_text(content, encoding="utf-8")
    return metadata


def validate_audit_request(state_path, state):
    path = audit_request_path(state_path, state.phase)

    if not path.exists():
        raise GovernanceError(
            "Root-approved audit request is missing."
        )

    metadata = read_frontmatter(path)
    expected = {
        "project_id": state.project_id,
        "phase": state.phase,
        "artifact_path": state.artifact_path,
        "artifact_sha256": state.artifact_sha256,
        "producer": state.owner,
        "auditor": state.auditor,
    }

    for key, value in expected.items():
        if metadata.get(key) != value:
            raise GovernanceError(
                "Audit request does not match project state: "
                f"{key}"
            )

    if metadata.get("status") not in {
        AUDIT_REQUEST_PENDING,
        AUDIT_REQUEST_COMPLETED,
    }:
        raise GovernanceError(
            "Unknown audit request status: "
            f"{metadata.get('status')}"
        )

    return path, metadata


def complete_audit_request(
    path,
    verdict,
    audit_path_value,
):
    metadata = read_frontmatter(path)

    if metadata.get("status") == AUDIT_REQUEST_COMPLETED:
        if (
            metadata.get("audit_verdict") != verdict
            or metadata.get("audit_path") != audit_path_value
        ):
            raise GovernanceError(
                "Completed audit request conflicts with audit result."
            )

        return metadata

    metadata["status"] = AUDIT_REQUEST_COMPLETED
    metadata["audit_verdict"] = verdict
    metadata["audit_path"] = audit_path_value
    path.write_text(
        _render_request(metadata),
        encoding="utf-8",
    )
    return metadata


def validate_frozen_artifact(vault, state):
    assert_frozen_artifact(state)
    artifact = Path(state.artifact_path)

    if not artifact.is_absolute():
        artifact = vault.root / artifact

    root = vault.root.resolve()
    artifact = artifact.resolve()

    try:
        artifact.relative_to(root)
    except ValueError as exc:
        raise GovernanceError(
            "Artifact path escapes the vault."
        ) from exc

    if not artifact.is_file():
        raise GovernanceError(
            f"Frozen artifact not found: {state.artifact_path}"
        )

    actual_sha = vault.freeze_artifact(artifact)

    if actual_sha != state.artifact_sha256:
        raise GovernanceError(
            "Frozen artifact SHA-256 mismatch."
        )

    return artifact


class AuditRequestEngine:
    """Approve entry into audit; never records an audit verdict."""

    def __init__(
        self,
        vault,
        agent_registry,
    ):
        self.vault = vault
        self.agent_registry = agent_registry

    def _validate_snapshot(self, proposal, state):
        snapshot = {
            "phase": state.phase,
            "artifact_path": state.artifact_path,
            "artifact_sha256": state.artifact_sha256,
            "auditor": state.auditor,
        }

        for key, current_value in snapshot.items():
            proposed_value = proposal.payload.get(key)

            if (
                proposed_value not in (None, "")
                and proposed_value != current_value
            ):
                raise GovernanceError(
                    "Stale audit proposal: "
                    f"{key} changed."
                )

    def _validate_artifact(self, state):
        validate_frozen_artifact(self.vault, state)

    def _validate_auditor(self, state):
        agent = self.agent_registry.get(state.auditor)
        assert_valid_auditor(state, agent)

    def _audit_already_completed(self, state_path, state):
        if (
            state.state != State.WAITING_ROOT
            or state.latest_audit_verdict
            not in {"PASS", "PASS_WITH_REPAIRS", "FAIL"}
        ):
            return False

        if not state.auditor:
            raise GovernanceError(
                "Completed audit state has no auditor."
            )

        if state.auditor == state.owner:
            raise GovernanceError(
                "PRODUCER / AUDITOR INDEPENDENCE CONFLICT"
            )

        audit_path = (
            state_path.parent
            / "audits"
            / f"{_safe_phase(state.phase)}_audit.md"
        )

        if not audit_path.exists():
            raise GovernanceError(
                "Project reports an audit verdict but audit evidence is missing."
            )

        text = audit_path.read_text(encoding="utf-8")

        for value in (
            state.auditor,
            state.artifact_sha256,
            state.latest_audit_verdict,
        ):
            if value not in text:
                raise GovernanceError(
                    "Existing audit does not match project state."
                )

        return True

    def _ensure_audit_task_and_handoff(
        self,
        proposal,
        state_path,
        state,
        request_path,
        request_id,
    ):
        project_dir = state_path.parent
        task_id = f"TASK-{request_id}"
        handoff_id = f"HANDOFF-{request_id}"
        request_metadata = read_frontmatter(
            request_path
        )
        authorization_id = request_metadata.get(
            "proposal_id",
            proposal.proposal_id,
        )
        artifact = ArtifactRef(
            path=state.artifact_path,
            sha256=state.artifact_sha256,
            metadata={
                "kind": "producer_artifact",
                "phase": state.phase,
            },
        )
        request_reference = str(
            request_path.relative_to(self.vault.root)
        )
        state_reference = str(
            state_path.relative_to(self.vault.root)
        )
        task_store = TaskStore(project_dir)
        task = task_store.ensure(
            Task(
                task_id=task_id,
                project_id=state.project_id,
                phase=state.phase,
                task_type="INDEPENDENT_AUDIT",
                assigned_agent=state.auditor,
                required_role=Role.AUDITOR.value,
                required_capability="audit",
                description=(
                    "Audit the frozen producer artifact without "
                    "modifying it or authorizing the next phase."
                ),
                request_path=request_reference,
                input_artifacts=[artifact],
                context_refs=[
                    state_reference,
                    request_reference,
                ],
                authorization_id=authorization_id,
                causation_event_id=request_metadata.get(
                    "created_event"
                ),
                idempotency_key=request_id,
                metadata={
                    "proposal_id": authorization_id,
                    "handoff_id": handoff_id,
                },
            )
        )

        if task.status == TaskStatus.CREATED:
            task_store.assign(task_id)

        HandoffStore(project_dir).ensure(
            Handoff(
                handoff_id=handoff_id,
                project_id=state.project_id,
                sender=state.owner,
                receiver=state.auditor,
                reason="Root approved independent audit.",
                artifact_refs=[artifact],
                context_refs=[
                    request_reference,
                    state_reference,
                ],
                task_id=task_id,
                phase=state.phase,
                authorization_id=authorization_id,
                metadata={
                    "request_id": request_id,
                    "may": [
                        "audit the frozen artifact",
                        "record an independent verdict",
                    ],
                    "may_not": [
                        "modify the producer artifact",
                        "authorize the next phase",
                    ],
                },
            )
        )

        return task_id, handoff_id

    def approve(self, proposal):
        state_path = self.vault.find_state_path(proposal.target)
        state = self.vault.read_state(state_path)

        self._validate_snapshot(proposal, state)
        self._validate_artifact(state)

        completed = self._audit_already_completed(
            state_path,
            state,
        )

        if not completed:
            self._validate_auditor(state)

        if not completed and state.state not in {
            State.PRODUCER_COMPLETE,
            State.AUDIT_PENDING,
        }:
            raise GovernanceError(
                "Audit request approval requires "
                "PRODUCER_COMPLETE or AUDIT_PENDING."
            )

        request_path = audit_request_path(
            state_path,
            state.phase,
        )
        request_status = (
            AUDIT_REQUEST_COMPLETED
            if completed
            else AUDIT_REQUEST_PENDING
        )
        request_id = (
            f"AUDIT-{state.project_id}-{state.phase}-"
            f"{state.artifact_sha256[:12]}"
        )
        metadata = {
            "request_id": request_id,
            "proposal_id": proposal.proposal_id,
            "project_id": state.project_id,
            "phase": state.phase,
            "status": request_status,
            "artifact_path": state.artifact_path,
            "artifact_sha256": state.artifact_sha256,
            "producer": state.owner,
            "auditor": state.auditor,
            "approved_by": "personal_root",
            "reason": proposal.reason,
            "created_event": proposal.payload.get(
                "created_event"
            ),
        }

        if completed:
            audit_path = (
                state_path.parent
                / "audits"
                / f"{state.phase}_audit.md"
            )
            metadata["audit_verdict"] = (
                state.latest_audit_verdict
            )
            metadata["audit_path"] = str(
                audit_path.relative_to(self.vault.root)
            )

        existing = write_audit_request(
            request_path,
            metadata,
        )

        if completed:
            if existing.get("status") != AUDIT_REQUEST_COMPLETED:
                complete_audit_request(
                    request_path,
                    state.latest_audit_verdict,
                    metadata["audit_path"],
                )

            return state_path

        if existing.get("status") == AUDIT_REQUEST_COMPLETED:
            raise GovernanceError(
                "Audit request is already completed but project is pending."
            )

        task_id, handoff_id = (
            self._ensure_audit_task_and_handoff(
                proposal,
                state_path,
                state,
                request_path,
                request_id,
            )
        )

        if (
            existing.get("task_id") != task_id
            or existing.get("handoff_id") != handoff_id
        ):
            existing["task_id"] = task_id
            existing["handoff_id"] = handoff_id
            request_path.write_text(
                _render_request(existing),
                encoding="utf-8",
            )

        if state.state == State.PRODUCER_COMPLETE:
            assert_transition(
                state.state,
                State.AUDIT_PENDING,
                Role.ROOT,
            )
            state = replace(
                state,
                state=State.AUDIT_PENDING,
                next_gate="INDEPENDENT_AUDIT",
                notes=(
                    "Root approved independent audit request. "
                    f"Assigned to {state.auditor}."
                ),
            )
            self.vault.write_state(state_path, state)

        return state_path
