from dataclasses import replace

from ..models import (
    Role,
    State,
    AuditVerdict,
)

from ..guardrails import (
    assert_transition,
    assert_auditable,
    GovernanceError,
)

from ..events import (
    Event,
    EventResult,
)



class AuditEngine:


    def __init__(
        self,
        vault,
        agent_registry,
        event_ledger
    ):

        self.vault = vault
        self.agent_registry = agent_registry
        self.event_ledger = event_ledger



    def check_agent(
        self,
        agent_id,
        capability
    ):

        agent = self.agent_registry.get(
            agent_id
        )


        if not agent:

            raise GovernanceError(
                f"Unknown agent: {agent_id}"
            )


        if capability not in agent.capabilities:

            raise GovernanceError(
                f"Missing capability: {capability}"
            )


        return agent



    def record_audit(
        self,
        state_path,
        auditor_name,
        verdict: AuditVerdict,
        audit_text
    ):


        self.check_agent(
            auditor_name,
            "audit"
        )


        state = self.vault.read_state(
            state_path
        )


        assert_auditable(
            state
        )


        audit_path = (

            state_path.parent
            /
            "audits"
            /
            f"{state.phase}_audit.md"

        )


        audit_path.write_text(

            f"""
# Independent Audit


- Auditor:
`{auditor_name}`


- Artifact SHA-256:
`{state.artifact_sha256}`


- Verdict:

**{verdict.value}**


## Audit Notes

{audit_text}

""",

            encoding="utf-8"

        )


        next_state = State(
            verdict.value
        )


        assert_transition(
            State.AUDIT_PENDING,
            next_state,
            Role.AUDITOR
        )


        state = replace(
            state,
            state=next_state,
            latest_audit_verdict=verdict.value,
            next_gate="ROOT_REVIEW",
            notes=f"Audit returned {verdict.value}."
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.event_ledger.append(

            Event(

                event_id=
                f"EVT-{state.project_id}-AUDIT",

                actor=auditor_name,

                action="AUDIT",

                target=state.project_id,

                result=EventResult.SUCCESS,

                capability_checked="audit"

            )

        )


        assert_transition(
            next_state,
            State.WAITING_ROOT,
            Role.CONTROLLER
        )


        state = replace(
            state,
            state=State.WAITING_ROOT,
            next_gate="ROOT_DECISION"
        )


        self.vault.write_state(
            state_path,
            state
        )


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


Audit verdict:

**{verdict.value}**


No next phase authorized automatically.


## Root Options

- AUTHORIZE

- MODIFY

- REPAIR

- HOLD

- CLOSE

"""

        )


        return state