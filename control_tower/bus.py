from dataclasses import replace

from .models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
    AuditVerdict,
)

from .guardrails import (
    assert_transition,
    assert_actor_owns_action,
    assert_auditable,
    GovernanceError,
)

from .agents import AgentRegistry

from .events import (
    Event,
    EventResult,
    EventLedger,
)


class ControlTowerBus:


    def __init__(self, vault):

        self.vault = vault

        self.vault.ensure_structure()

        self.agent_registry = AgentRegistry(
            vault.root
        )

        self.event_ledger = EventLedger(
            vault
        )


    # ----------------------------
    # Agent Permission
    # ----------------------------

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


        if agent.status.value != "ACTIVE":

            raise GovernanceError(
                f"Inactive agent: {agent_id}"
            )


        if capability not in agent.capabilities:

            raise GovernanceError(
                f"Agent {agent_id} lacks capability {capability}"
            )


        return agent



    # ----------------------------
    # Event Helper
    # ----------------------------

    def emit_event(
        self,
        event_id,
        actor,
        action,
        target,
        capability
    ):

        self.event_ledger.append(
            Event(
                event_id=event_id,
                actor=actor,
                action=action,
                target=target,
                result=EventResult.SUCCESS,
                capability_checked=capability,
            )
        )



    # ----------------------------
    # Create Project
    # ----------------------------

    def create_research_project(
        self,
        project_id,
        title,
        owner,
        phase
    ):

        self.check_agent(
            owner,
            "produce_artifact"
        )


        project_dir = (
            self.vault.root
            /
            "01_RESEARCH"
            /
            project_id
        )


        for folder in [
            "handoffs",
            "claims",
            "audits",
            "artifacts",
            "failed_routes",
        ]:

            (
                project_dir / folder
            ).mkdir(
                parents=True,
                exist_ok=True
            )


        state = ProjectState(
            project_id,
            title,
            Division.RESEARCH,
            phase,
            State.READY,
            owner,
            Role.PRODUCER,
            Lineage.CANONICAL,
            next_gate="ROOT_AUTHORIZATION",
            notes="READY only. No execution authorization."
        )


        state_path = (
            project_dir
            /
            "STATE.md"
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.emit_event(
            f"EVT-{project_id}-CREATE",
            owner,
            "CREATE_PROJECT",
            project_id,
            "produce_artifact"
        )


        return state, state_path



    # ----------------------------
    # Root Authorization
    # ----------------------------

    def root_authorize(
        self,
        state_path,
        authorization_id,
        scope
    ):


        self.check_agent(
            "personal_root",
            "authorize"
        )


        state = self.vault.read_state(
            state_path
        )


        assert_transition(
            state.state,
            State.AUTHORIZED,
            Role.ROOT
        )


        state = replace(
            state,
            state=State.AUTHORIZED,
            authorization_id=authorization_id,
            next_gate="PRODUCER_EXECUTION",
            notes=f"Root-authorized scope: {scope}"
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.emit_event(
            f"EVT-{state.project_id}-AUTHORIZE",
            "personal_root",
            "AUTHORIZE",
            state.project_id,
            "authorize"
        )


        self.vault.append_decision(
            f"""
## {authorization_id}

- Project: `{state.project_id}`

- Decision: AUTHORIZED

- Scope: {scope}

"""
        )


        return state

    # ----------------------------
    # Start Execution
    # ----------------------------

    def start_execution(
        self,
        state_path,
        producer_name
    ):

        self.check_agent(
            producer_name,
            "produce_artifact"
        )


        state = self.vault.read_state(
            state_path
        )


        assert_actor_owns_action(
            state,
            producer_name,
            Role.PRODUCER,
            "produce"
        )


        assert_transition(
            state.state,
            State.ACTIVE,
            Role.PRODUCER
        )


        if not state.authorization_id:

            raise GovernanceError(
                "Missing Root authorization."
            )


        state = replace(
            state,
            state=State.ACTIVE,
            next_gate="PRODUCER_COMPLETE",
            notes="Producer execution active."
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.emit_event(
            f"EVT-{state.project_id}-EXECUTE",
            producer_name,
            "START_EXECUTION",
            state.project_id,
            "produce_artifact"
        )


        return state



    # ----------------------------
    # Producer Complete
    # ----------------------------

    def producer_complete(
        self,
        state_path,
        producer_name,
        artifact_text,
        auditor_name
    ):


        self.check_agent(
            producer_name,
            "produce_artifact"
        )


        state = self.vault.read_state(
            state_path
        )


        assert_actor_owns_action(
            state,
            producer_name,
            Role.PRODUCER,
            "produce"
        )


        if state.state != State.ACTIVE:

            raise GovernanceError(
                "Completion requires ACTIVE."
            )


        artifact_path = (
            state_path.parent
            /
            "artifacts"
            /
            f"{state.phase}_producer_artifact.txt"
        )


        artifact_path.write_text(
            artifact_text,
            encoding="utf-8"
        )


        sha = self.vault.freeze_artifact(
            artifact_path
        )


        state = replace(
            state,
            state=State.PRODUCER_COMPLETE,
            artifact_path=str(
                artifact_path.relative_to(
                    self.vault.root
                )
            ),
            artifact_sha256=sha,
            auditor=auditor_name,
            next_gate="INDEPENDENT_AUDIT",
            notes="Producer complete. Artifact frozen."
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.emit_event(
            f"EVT-{state.project_id}-PRODUCE_COMPLETE",
            producer_name,
            "PRODUCE_ARTIFACT",
            state.project_id,
            "produce_artifact"
        )


        assert_transition(
            State.PRODUCER_COMPLETE,
            State.AUDIT_PENDING,
            Role.CONTROLLER
        )


        state = replace(
            state,
            state=State.AUDIT_PENDING,
            next_gate="INDEPENDENT_AUDIT"
        )


        self.vault.write_state(
            state_path,
            state
        )


        return state



    # ----------------------------
    # Independent Audit
    # ----------------------------

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


- Auditor: `{auditor_name}`

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


        self.emit_event(
            f"EVT-{state.project_id}-AUDIT",
            auditor_name,
            "AUDIT",
            state.project_id,
            "audit"
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