from dataclasses import replace
import hashlib

import yaml

from ..models import AuditVerdict, Role, State
from ..guardrails import (
    GovernanceError,
    assert_auditable,
    assert_transition,
    assert_valid_auditor,
)
from ..events import Event, EventResult
from ..handoffs import HandoffStatus, HandoffStore
from ..tasks import TaskStatus, TaskStore
from .audit_request_engine import (
    AUDIT_REQUEST_COMPLETED,
    complete_audit_request,
    read_frontmatter,
    validate_audit_request,
    validate_frozen_artifact,
)


class AuditEngine:
    """Record a verdict produced by the assigned independent auditor."""

    def __init__(
        self,
        vault,
        agent_registry,
        event_ledger,
    ):
        self.vault = vault
        self.agent_registry = agent_registry
        self.event_ledger = event_ledger

    def check_agent(self, agent_id, capability):
        agent = self.agent_registry.get(agent_id)

        if not agent:
            raise GovernanceError(
                f"Unknown agent: {agent_id}"
            )

        if capability not in agent.capabilities:
            raise GovernanceError(
                f"Missing capability: {capability}"
            )

        return agent

    @staticmethod
    def _audit_text_sha256(audit_text):
        return hashlib.sha256(
            audit_text.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _audit_path(state_path, state):
        return (
            state_path.parent
            / "audits"
            / f"{state.phase}_audit.md"
        )

    def _audit_metadata(
        self,
        state,
        auditor_name,
        verdict,
        audit_text,
    ):
        return {
            "project_id": state.project_id,
            "phase": state.phase,
            "auditor": auditor_name,
            "artifact_path": state.artifact_path,
            "artifact_sha256": state.artifact_sha256,
            "verdict": verdict.value,
            "audit_text_sha256": self._audit_text_sha256(
                audit_text
            ),
        }

    @staticmethod
    def _render_audit(metadata, audit_text):
        serialized = yaml.safe_dump(
            metadata,
            sort_keys=False,
            allow_unicode=True,
        )
        body = f"""
# Independent Audit

- Project: `{metadata['project_id']}`
- Phase: `{metadata['phase']}`
- Auditor: `{metadata['auditor']}`
- Artifact: `{metadata['artifact_path']}`
- Artifact SHA-256: `{metadata['artifact_sha256']}`
- Verdict: **{metadata['verdict']}**

## Audit Notes

{audit_text}
"""
        return "---\n" + serialized + "---\n" + body.lstrip()

    def _validate_existing_audit(
        self,
        audit_path,
        expected,
        audit_text,
    ):
        text = audit_path.read_text(encoding="utf-8")

        if text.startswith("---"):
            metadata = read_frontmatter(audit_path)

            for key, value in expected.items():
                if metadata.get(key) != value:
                    raise GovernanceError(
                        "Existing audit conflicts with retry: "
                        f"{key}"
                    )

            return

        legacy_values = (
            expected["auditor"],
            expected["artifact_sha256"],
            expected["verdict"],
            audit_text.strip(),
        )

        if any(value not in text for value in legacy_values):
            raise GovernanceError(
                "Existing legacy audit conflicts with retry."
            )

    def _write_or_validate_audit(
        self,
        audit_path,
        metadata,
        audit_text,
    ):
        audit_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if audit_path.exists():
            self._validate_existing_audit(
                audit_path,
                metadata,
                audit_text,
            )
            return

        audit_path.write_text(
            self._render_audit(metadata, audit_text),
            encoding="utf-8",
        )

    def _write_root_gate(self, state, verdict):
        self.vault.write_root_inbox(
            f"{state.project_id}_{state.phase}_GATE.md",
            f"""---
project_id: {state.project_id}
phase: {state.phase}
state: {state.state.value}
audit_verdict: {verdict.value}
artifact_sha256: {state.artifact_sha256}
---
# Root Gate Decision Required

Audit verdict: **{verdict.value}**

No next phase is authorized automatically.

## Root Options

- AUTHORIZE
- MODIFY
- REPAIR
- HOLD
- CLOSE
""",
        )

    def _record_event_once(
        self,
        state,
        auditor_name,
    ):
        self.event_ledger.append_once(
            Event(
                event_id=(
                    f"EVT-{state.project_id}-AUDIT-"
                    f"{state.phase}-"
                    f"{state.artifact_sha256[:12]}"
                ),
                actor=auditor_name,
                action="AUDIT",
                target=state.project_id,
                result=EventResult.SUCCESS,
                capability_checked="audit",
                note=(
                    "Independent audit completed for "
                    f"{state.phase}."
                ),
            )
        )

    def _complete_task_and_handoff(
        self,
        state_path,
        request,
        audit_path,
        verdict,
        auditor_name,
        reconcile_task=False,
    ):
        task_id = request.get("task_id")

        if not task_id:
            return

        task_store = TaskStore(state_path.parent)
        task = task_store.get(task_id)

        if task.assigned_agent != auditor_name:
            raise GovernanceError(
                "Audit task is assigned to a different auditor."
            )

        if not reconcile_task:
            if task.status == TaskStatus.CREATED:
                task = task_store.assign(task_id)

            if task.status == TaskStatus.ASSIGNED:
                task = task_store.start(task_id)

        audit_path_value = str(
            audit_path.relative_to(self.vault.root)
        )
        result = {
            "verdict": verdict.value,
            "audit_path": audit_path_value,
            "output_artifacts": [
                {
                    "path": audit_path_value,
                    "sha256": self.vault.freeze_artifact(
                        audit_path
                    ),
                    "metadata": {
                        "kind": "independent_audit",
                    },
                }
            ],
        }

        if reconcile_task and task.status != TaskStatus.COMPLETED:
            task = task_store.reconcile_completion(
                task_id,
                result,
                "Recovered from committed independent audit evidence.",
            )
        elif task.status == TaskStatus.RUNNING:
            task_store.complete(task_id, result)
        elif task.status == TaskStatus.COMPLETED:
            if task.result != result:
                raise GovernanceError(
                    "Completed audit task conflicts with audit evidence."
                )
        else:
            raise GovernanceError(
                "Audit task is not executable from state: "
                f"{task.status.value}"
            )

        handoff_id = request.get("handoff_id")

        if handoff_id:
            handoff_store = HandoffStore(
                state_path.parent
            )
            handoff = handoff_store.get(handoff_id)

            if handoff.status == HandoffStatus.CREATED:
                handoff_store.acknowledge(
                    handoff_id,
                    auditor_name,
                )

        self.event_ledger.append_once(
            Event(
                event_id=f"EVT-{task_id}-COMPLETED",
                actor=auditor_name,
                action="TASK_COMPLETED",
                target=state_path.parent.name,
                result=EventResult.SUCCESS,
                capability_checked="audit",
                correlation_id=task_id,
                note="Audit task completed.",
                metadata={
                    "task_id": task_id,
                    "audit_path": audit_path_value,
                },
                )
            )

    @staticmethod
    def _read_persisted_audit_text(audit_path):
        text = audit_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)

        if len(parts) < 3:
            raise GovernanceError(
                "Reconciliation requires a v1 audit record."
            )

        marker = "## Audit Notes\n\n"
        _, found, audit_text = parts[2].partition(
            marker
        )

        if not found:
            raise GovernanceError(
                "Audit evidence is missing its notes section."
            )

        # _render_audit adds one document newline after the exact notes.
        if audit_text.endswith("\n"):
            audit_text = audit_text[:-1]

        return audit_text

    def reconcile_task_from_evidence(
        self,
        state_path,
        task_id,
    ):
        """Repair an Audit Task after its audit evidence was committed."""

        state = self.vault.read_state(state_path)

        if state.state not in {
            State.AUDIT_PENDING,
            State.PASS,
            State.PASS_WITH_REPAIRS,
            State.FAIL,
            State.WAITING_ROOT,
        }:
            return None

        audit_path = self._audit_path(
            state_path,
            state,
        )

        if not audit_path.exists():
            return None

        _, request = validate_audit_request(
            state_path,
            state,
        )

        if request.get("task_id") != task_id:
            raise GovernanceError(
                "Committed audit evidence belongs to another Task."
            )

        task = TaskStore(state_path.parent).get(task_id)

        if (
            task.project_id != state.project_id
            or task.phase != state.phase
            or task.required_role != Role.AUDITOR.value
            or task.required_capability != "audit"
        ):
            raise GovernanceError(
                "Committed audit evidence does not match the Audit Task."
            )

        if task.assigned_agent != state.auditor:
            raise GovernanceError(
                "Committed audit evidence belongs to another auditor."
            )

        metadata = read_frontmatter(audit_path)
        verdict_value = metadata.get("verdict")

        try:
            verdict = AuditVerdict(verdict_value)
        except (TypeError, ValueError) as exc:
            raise GovernanceError(
                "Committed audit evidence has an invalid verdict."
            ) from exc

        if (
            state.latest_audit_verdict
            and state.latest_audit_verdict != verdict.value
        ):
            raise GovernanceError(
                "Committed audit verdict conflicts with project state."
            )

        audit_text = self._read_persisted_audit_text(
            audit_path
        )
        self.record_audit(
            state_path,
            task.assigned_agent,
            verdict,
            audit_text,
            _reconcile_task=True,
        )
        return TaskStore(state_path.parent).get(task_id)

    def record_audit(
        self,
        state_path,
        auditor_name,
        verdict: AuditVerdict,
        audit_text,
        _reconcile_task=False,
    ):
        if not isinstance(verdict, AuditVerdict):
            verdict = AuditVerdict(verdict)

        state = self.vault.read_state(state_path)
        agent = self.check_agent(auditor_name, "audit")
        assert_valid_auditor(state, agent)
        validate_frozen_artifact(self.vault, state)

        request_path, request = validate_audit_request(
            state_path,
            state,
        )
        next_state = State(verdict.value)
        audit_path = self._audit_path(state_path, state)
        audit_metadata = self._audit_metadata(
            state,
            auditor_name,
            verdict,
            audit_text,
        )

        already_waiting_root = (
            state.state == State.WAITING_ROOT
            and state.latest_audit_verdict == verdict.value
        )
        recovering_verdict_state = (
            state.state == next_state
            and state.latest_audit_verdict == verdict.value
        )

        if already_waiting_root or recovering_verdict_state:
            if not audit_path.exists():
                raise GovernanceError(
                    "Audit state exists without audit evidence."
                )

            self._validate_existing_audit(
                audit_path,
                audit_metadata,
                audit_text,
            )

            if recovering_verdict_state:
                assert_transition(
                    next_state,
                    State.WAITING_ROOT,
                    Role.CONTROLLER,
                )
                state = replace(
                    state,
                    state=State.WAITING_ROOT,
                    next_gate="ROOT_DECISION",
                )
                self.vault.write_state(state_path, state)

            audit_path_value = str(
                audit_path.relative_to(self.vault.root)
            )
            complete_audit_request(
                request_path,
                verdict.value,
                audit_path_value,
            )
            self._complete_task_and_handoff(
                state_path,
                request,
                audit_path,
                verdict,
                auditor_name,
                reconcile_task=_reconcile_task,
            )
            self._record_event_once(state, auditor_name)
            self._write_root_gate(state, verdict)
            return state

        assert_auditable(state)

        if request.get("status") == AUDIT_REQUEST_COMPLETED:
            raise GovernanceError(
                "Audit request is completed but project is still pending."
            )

        assert_transition(
            State.AUDIT_PENDING,
            next_state,
            Role.AUDITOR,
        )
        assert_transition(
            next_state,
            State.WAITING_ROOT,
            Role.CONTROLLER,
        )

        self._write_or_validate_audit(
            audit_path,
            audit_metadata,
            audit_text,
        )

        state = replace(
            state,
            state=State.WAITING_ROOT,
            latest_audit_verdict=verdict.value,
            next_gate="ROOT_DECISION",
            notes=f"Audit returned {verdict.value}.",
        )
        self.vault.write_state(state_path, state)

        audit_path_value = str(
            audit_path.relative_to(self.vault.root)
        )
        complete_audit_request(
            request_path,
            verdict.value,
            audit_path_value,
        )
        self._complete_task_and_handoff(
            state_path,
            request,
            audit_path,
            verdict,
            auditor_name,
            reconcile_task=_reconcile_task,
        )
        self._record_event_once(state, auditor_name)
        self._write_root_gate(state, verdict)

        return state
