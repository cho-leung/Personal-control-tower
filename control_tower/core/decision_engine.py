from dataclasses import replace
from pathlib import Path

from ..agents import AgentRole, AgentStatus

from ..models import (
    Role,
    State,
)

from ..guardrails import (
    assert_transition,
    GovernanceError,
)

from ..events import (
    Event,
    EventResult,
)


class DecisionEngine:

    ROOT_DECISIONS = {
        "AUTHORIZE",
        "MODIFY",
        "REPAIR",
        "HOLD",
        "CLOSE",
    }

    def __init__(
        self,
        vault,
        agent_registry,
        event_ledger,
    ):
        self.vault = vault
        self.agent_registry = agent_registry
        self.event_ledger = event_ledger

    # =========================================================
    # Root helper
    # =========================================================

    def _get_root(self):

        root = self.agent_registry.get(
            "personal_root"
        )

        if not root:
            raise GovernanceError(
                "Root agent missing."
            )

        if root.status != AgentStatus.ACTIVE:
            raise GovernanceError(
                "Root agent is not ACTIVE."
            )

        if root.role != AgentRole.ROOT:
            raise GovernanceError(
                "personal_root does not have ROOT role."
            )

        return root

    @staticmethod
    def _validate_reference(value, label):
        if (
            not isinstance(value, str)
            or not value.strip()
            or Path(value).name != value
        ):
            raise GovernanceError(
                f"Invalid {label}: {value}"
            )

    def _event_recorded(
        self,
        event_id,
        action,
        target,
        note=None,
        metadata=None,
    ):
        for event in self.event_ledger.read_all():
            if event.get("event_id") != event_id:
                continue

            if (
                event.get("action") != action
                or event.get("target") != target
            ):
                raise GovernanceError(
                    "Decision event idempotency conflict: "
                    f"{event_id}"
                )

            if note is not None and event.get("note") != note:
                raise GovernanceError(
                    "Decision event idempotency conflict: note changed."
                )

            event_metadata = event.get("metadata", {})

            for key, expected in (metadata or {}).items():
                if event_metadata.get(key) != expected:
                    raise GovernanceError(
                        "Decision event idempotency conflict: "
                        f"{key} changed."
                    )

            return event

        return None

    def _archive_gate_once(self, project_id, phase):
        gate_name = f"{project_id}_{phase}_GATE.md"
        gate_path = (
            self.vault.root
            / "00_ROOT"
            / "inbox"
            / gate_name
        )

        if gate_path.exists():
            return self.vault.archive_root_item(gate_path)

        archive = (
            self.vault.root
            / "00_ROOT"
            / "archive"
        )
        matches = sorted(
            archive.glob(f"*_{gate_name}")
        )
        return matches[-1] if matches else None

    def _append_decision_once(
        self,
        project_id,
        decision_id,
        text,
    ):
        path = (
            self.vault.root
            / "00_ROOT"
            / "DECISION_LOG.md"
        )
        marker = (
            "<!-- control-tower-decision:"
            f"{project_id}:{decision_id} -->"
        )

        if (
            path.exists()
            and marker in path.read_text(encoding="utf-8")
        ):
            return False

        self.vault.append_decision(
            marker + "\n" + text
        )
        return True

    # =========================================================
    # Initial / explicit authorization
    # =========================================================

    def authorize(
        self,
        state_path,
        authorization_id,
        scope,
        next_phase=None,
    ):

        root = self._get_root()

        self._validate_reference(
            authorization_id,
            "authorization id",
        )

        if not isinstance(scope, str) or not scope.strip():
            raise GovernanceError(
                "Authorization scope is required."
            )

        if "authorize" not in root.capabilities:
            raise GovernanceError(
                "Root cannot authorize."
            )

        state = self.vault.read_state(
            state_path
        )

        event_id = (
            f"EVT-{state.project_id}-{authorization_id}"
        )

        if self._event_recorded(
            event_id,
            "AUTHORIZE",
            state.project_id,
            note=scope,
            metadata={
                "authorization_id": authorization_id,
                "requested_next_phase": next_phase,
                "scope": scope,
            },
        ):
            return state

        old_phase = state.phase
        recovering = (
            state.state == State.AUTHORIZED
            and state.authorization_id == authorization_id
        )

        if recovering and state.notes != (
            f"Root-authorized scope: {scope}"
        ):
            raise GovernanceError(
                "Authorization idempotency conflict: scope changed."
            )

        if recovering:
            if (
                next_phase is not None
                and state.phase != next_phase
            ):
                raise GovernanceError(
                    "Authorization idempotency conflict: phase changed."
                )

            authorized_phase = state.phase
        else:
            prior_evidence = any(
                (
                    state.artifact_path,
                    state.artifact_sha256,
                    state.latest_audit_verdict,
                )
            )

            if prior_evidence and not next_phase:
                raise GovernanceError(
                    "Reauthorization with prior evidence requires "
                    "a distinct next phase."
                )

            if next_phase:
                self._validate_reference(
                    next_phase,
                    "next phase",
                )

                if next_phase == old_phase:
                    raise GovernanceError(
                        "next phase must differ from the evidence phase."
                    )

            authorized_phase = next_phase or old_phase

        if not recovering:
            assert_transition(
                state.state,
                State.AUTHORIZED,
                Role.ROOT,
            )

            state = replace(
                state,
                phase=authorized_phase,
                state=State.AUTHORIZED,
                authorization_id=authorization_id,
                artifact_path=(
                    None if next_phase else state.artifact_path
                ),
                artifact_sha256=(
                    None if next_phase else state.artifact_sha256
                ),
                auditor=(
                    None if next_phase else state.auditor
                ),
                latest_audit_verdict=(
                    None
                    if next_phase
                    else state.latest_audit_verdict
                ),
                next_gate="PRODUCER_EXECUTION",
                notes=(
                    f"Root-authorized scope: {scope}"
                ),
            )

            self.vault.write_state(
                state_path,
                state,
            )

        self.event_ledger.append_once(
            Event(
                event_id=event_id,
                actor="personal_root",
                action="AUTHORIZE",
                target=state.project_id,
                result=EventResult.SUCCESS,
                capability_checked="authorize",
                note=scope,
                metadata={
                    "authorization_id": authorization_id,
                    "previous_phase": old_phase,
                    "requested_next_phase": next_phase,
                    "scope": scope,
                },
            )
        )

        self._append_decision_once(
            state.project_id,
            authorization_id,
            f"""
## {authorization_id}

- Project:
`{state.project_id}`

- Phase:
`{state.phase}`

- Previous phase:
`{old_phase}`

- Decision:
**AUTHORIZED**

- Scope:
{scope}

"""
        )

        return state

    # =========================================================
    # Root gate decision
    # =========================================================

    def root_decide(
        self,
        state_path,
        decision_id,
        decision,
        note="",
        next_phase=None,
        scope=None,
    ):

        root = self._get_root()

        self._validate_reference(
            decision_id,
            "decision id",
        )

        decision = decision.upper()

        if decision not in self.ROOT_DECISIONS:
            raise GovernanceError(
                f"Unknown Root decision: {decision}"
            )

        # AUTHORIZE needs authorization capability.
        if decision == "AUTHORIZE":

            if "authorize" not in root.capabilities:
                raise GovernanceError(
                    "Root cannot authorize."
                )

        # Other Root decisions use approve capability.
        else:

            if "approve" not in root.capabilities:
                raise GovernanceError(
                    "Root cannot make governance decisions."
                )

        state = self.vault.read_state(
            state_path
        )

        requested_evidence = {
            "decision": decision,
            "note": note,
            "next_phase": next_phase,
            "scope": scope,
        }

        event_id = (
            f"EVT-{state.project_id}-{decision_id}"
        )
        event_action = f"ROOT_{decision}"

        recorded_event = self._event_recorded(
            event_id,
            event_action,
            state.project_id,
            note=note,
            metadata={
                "decision_id": decision_id,
                "requested_next_phase": next_phase,
                "scope": scope,
            },
        )

        if recorded_event:
            event_metadata = recorded_event.get(
                "metadata",
                {},
            )
            previous_phase = (
                event_metadata.get("previous_phase")
                or state.phase
            )
            result_phase = (
                event_metadata.get("result_phase")
                or state.phase
            )
            result_state = (
                event_metadata.get("result_state")
                or state.state.value
            )
            self._append_decision_once(
                state.project_id,
                decision_id,
                f"""
## {decision_id}

- Project:
`{state.project_id}`

- Previous phase:
`{previous_phase}`

- Decision:
**{decision}**

- Current phase:
`{result_phase}`

- New state:
`{result_state}`

- Note:
{note or 'None.'}

- Scope:
{scope or 'None.'}

""",
            )
            self._archive_gate_once(
                state.project_id,
                previous_phase,
            )
            return state

        if state.state != State.WAITING_ROOT:
            stored_evidence = dict(
                state.last_decision_evidence or {}
            )
            stored_requested = {
                key: stored_evidence.get(key)
                for key in requested_evidence
            }

            if (
                state.last_decision_id == decision_id
                and state.last_decision_action == decision
                and stored_requested == requested_evidence
            ):
                previous_phase = stored_evidence.get(
                    "previous_phase",
                    state.phase,
                )
                self.event_ledger.append_once(
                    Event(
                        event_id=event_id,
                        actor="personal_root",
                        action=event_action,
                        target=state.project_id,
                        result=EventResult.SUCCESS,
                        capability_checked=(
                            "authorize"
                            if decision == "AUTHORIZE"
                            else "approve"
                        ),
                        note=note,
                        metadata={
                            "decision_id": decision_id,
                            "previous_phase": previous_phase,
                            "requested_next_phase": next_phase,
                            "scope": scope,
                            "result_phase": state.phase,
                            "result_state": state.state.value,
                        },
                    )
                )
                self._append_decision_once(
                    state.project_id,
                    decision_id,
                    f"""
## {decision_id}

- Project:
`{state.project_id}`

- Previous phase:
`{previous_phase}`

- Decision:
**{decision}**

- Current phase:
`{state.phase}`

- New state:
`{state.state.value}`

- Note:
{note or 'None.'}

- Scope:
{scope or 'None.'}

""",
                )
                self._archive_gate_once(
                    state.project_id,
                    previous_phase,
                )
                return state

            raise GovernanceError(
                "Root decision requires WAITING_ROOT."
            )

        old_phase = state.phase

        # =====================================================
        # AUTHORIZE
        #
        # Audit PASS -> explicitly authorize a NEW phase.
        # No automatic continuation.
        # =====================================================

        if decision == "AUTHORIZE":

            if state.latest_audit_verdict != "PASS":
                raise GovernanceError(
                    "Next phase authorization "
                    "requires audit PASS."
                )

            if not next_phase:
                raise GovernanceError(
                    "AUTHORIZE requires next_phase."
                )

            self._validate_reference(
                next_phase,
                "next phase",
            )

            if next_phase == old_phase:
                raise GovernanceError(
                    "next_phase must differ "
                    "from audited phase."
                )

            assert_transition(
                state.state,
                State.AUTHORIZED,
                Role.ROOT,
            )

            state = replace(
                state,

                phase=next_phase,

                state=State.AUTHORIZED,

                authorization_id=decision_id,

                # Evidence from the old phase must not
                # masquerade as evidence for the new phase.
                artifact_path=None,
                artifact_sha256=None,
                auditor=None,
                latest_audit_verdict=None,

                next_gate="PRODUCER_EXECUTION",

                notes=(
                    "Root authorized next phase. "
                    f"Scope: {scope or note}"
                ),
            )

        # =====================================================
        # MODIFY
        # =====================================================

        elif decision == "MODIFY":

            assert_transition(
                state.state,
                State.WAITING,
                Role.ROOT,
            )

            state = replace(
                state,
                state=State.WAITING,
                authorization_id=None,
                next_gate="MODIFICATION_REQUIRED",
                notes=(
                    "Root requested modification: "
                    f"{note}"
                ),
            )

        # =====================================================
        # REPAIR
        # =====================================================

        elif decision == "REPAIR":

            assert_transition(
                state.state,
                State.REPAIR_REQUIRED,
                Role.ROOT,
            )

            state = replace(
                state,
                state=State.REPAIR_REQUIRED,
                authorization_id=None,
                next_gate="ROOT_AUTHORIZATION",
                notes=(
                    "Root requires repair: "
                    f"{note}"
                ),
            )

        # =====================================================
        # HOLD
        # =====================================================

        elif decision == "HOLD":

            assert_transition(
                state.state,
                State.HOLD,
                Role.ROOT,
            )

            state = replace(
                state,
                state=State.HOLD,
                authorization_id=None,
                next_gate="ROOT_REVIEW",
                notes=(
                    "Root placed project on HOLD: "
                    f"{note}"
                ),
            )

        # =====================================================
        # CLOSE
        # =====================================================

        elif decision == "CLOSE":

            assert_transition(
                state.state,
                State.COMPLETE,
                Role.ROOT,
            )

            state = replace(
                state,
                state=State.COMPLETE,
                authorization_id=None,
                next_gate=None,
                notes=(
                    "Root closed phase/project: "
                    f"{note}"
                ),
            )

        # =====================================================
        # Persist new state
        # =====================================================

        state = replace(
            state,
            last_decision_id=decision_id,
            last_decision_action=decision,
            last_decision_evidence={
                **requested_evidence,
                "previous_phase": old_phase,
            },
        )

        self.vault.write_state(
            state_path,
            state,
        )

        # =====================================================
        # Event ledger
        # =====================================================

        self.event_ledger.append_once(
            Event(
                event_id=event_id,
                actor="personal_root",
                action=f"ROOT_{decision}",
                target=state.project_id,
                result=EventResult.SUCCESS,
                capability_checked=(
                    "authorize"
                    if decision == "AUTHORIZE"
                    else "approve"
                ),
                note=note,
                metadata={
                    "decision_id": decision_id,
                    "previous_phase": old_phase,
                    "requested_next_phase": next_phase,
                    "scope": scope,
                    "result_phase": state.phase,
                    "result_state": state.state.value,
                },
            )
        )

        # =====================================================
        # Human-readable Root decision log
        # =====================================================

        self._append_decision_once(
            state.project_id,
            decision_id,
            f"""
## {decision_id}

- Project:
`{state.project_id}`

- Previous phase:
`{old_phase}`

- Decision:
**{decision}**

- Current phase:
`{state.phase}`

- New state:
`{state.state.value}`

- Note:
{note or 'None.'}

- Scope:
{scope or 'None.'}

"""
        )

        self._archive_gate_once(
            state.project_id,
            old_phase,
        )

        return state
