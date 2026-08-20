from dataclasses import replace

from .models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
    AuditVerdict
)

from .guardrails import (
    assert_transition,
    assert_actor_owns_action,
    assert_auditable,
    GovernanceError
)

from .agents import AgentRegistry



class ControlTowerBus:


    def __init__(self, vault):

        self.vault = vault

        self.vault.ensure_structure()

        self.agent_registry = AgentRegistry(
            vault.root
        )



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


        d = (
            self.vault.root
            /
            "01_RESEARCH"
            /
            project_id
        )


        for sub in [
            "handoffs",
            "claims",
            "audits",
            "artifacts",
            "failed_routes"
        ]:

            (
                d / sub
            ).mkdir(
                parents=True,
                exist_ok=True
            )


        s = ProjectState(

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


        p = d / "STATE.md"


        self.vault.write_state(
            p,
            s
        )


        self.vault.append_event(
            {
                "type":"PROJECT_CREATED",
                "project_id":project_id,
                "owner":owner
            }
        )


        return s, p





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


        s = self.vault.read_state(
            state_path
        )


        assert_transition(
            s.state,
            State.AUTHORIZED,
            Role.ROOT
        )


        s = replace(
            s,
            state=State.AUTHORIZED,
            authorization_id=authorization_id,
            next_gate="PRODUCER_EXECUTION",
            notes=f"Root-authorized scope: {scope}"
        )


        self.vault.write_state(
            state_path,
            s
        )


        self.vault.append_decision(
            f"""
## {authorization_id}

- Project: `{s.project_id}`

- Phase: `{s.phase}`

- Decision: **AUTHORIZED**

- Scope: {scope}

"""
        )


        return s





    def start_execution(
        self,
        state_path,
        producer_name
    ):


        self.check_agent(
            producer_name,
            "produce_artifact"
        )


        s = self.vault.read_state(
            state_path
        )


        assert_actor_owns_action(
            s,
            producer_name,
            Role.PRODUCER,
            "produce"
        )


        assert_transition(
            s.state,
            State.ACTIVE,
            Role.PRODUCER
        )


        if not s.authorization_id:

            raise GovernanceError(
                "Missing Root authorization."
            )


        s = replace(
            s,
            state=State.ACTIVE,
            next_gate="PRODUCER_COMPLETE"
        )


        self.vault.write_state(
            state_path,
            s
        )


        return s





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


        s = self.vault.read_state(
            state_path
        )


        assert_actor_owns_action(
            s,
            producer_name,
            Role.PRODUCER,
            "produce"
        )


        if s.state != State.ACTIVE:

            raise GovernanceError(
                "Completion requires ACTIVE."
            )


        d = state_path.parent


        artifact = (
            d
            /
            "artifacts"
            /
            f"{s.phase}_producer_artifact.txt"
        )


        artifact.write_text(
            artifact_text,
            encoding="utf-8"
        )


        sha = self.vault.freeze_artifact(
            artifact
        )


        s = replace(
            s,
            state=State.PRODUCER_COMPLETE,
            artifact_path=str(
                artifact.relative_to(
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
            s
        )


        assert_transition(
            State.PRODUCER_COMPLETE,
            State.AUDIT_PENDING,
            Role.CONTROLLER
        )


        s = replace(
            s,
            state=State.AUDIT_PENDING
        )


        self.vault.write_state(
            state_path,
            s
        )


        return s

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


        s = self.vault.read_state(
            state_path
        )


        assert_auditable(
            s
        )


        d = state_path.parent


        audit_file = (
            d
            /
            "audits"
            /
            f"{s.phase}_audit.md"
        )


        audit_file.write_text(

            f"""
# Independent Audit


- Auditor: `{auditor_name}`

- Artifact SHA-256:
`{s.artifact_sha256}`

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


        s = replace(
            s,
            state=next_state,
            latest_audit_verdict=verdict.value,
            next_gate="ROOT_REVIEW",
            notes=f"Audit returned {verdict.value}. Waiting for Root decision."
        )


        self.vault.write_state(
            state_path,
            s
        )


        assert_transition(
            next_state,
            State.WAITING_ROOT,
            Role.CONTROLLER
        )


        s = replace(
            s,
            state=State.WAITING_ROOT,
            next_gate="ROOT_DECISION"
        )


        self.vault.write_state(
            state_path,
            s
        )


        self.vault.write_root_inbox(

            f"{s.project_id}_{s.phase}_GATE.md",

            f"""---

project_id: {s.project_id}

phase: {s.phase}

state: {s.state.value}

audit_verdict: {verdict.value}

artifact_sha256: {s.artifact_sha256}

---


# Root Gate Decision Required


Audit verdict:

**{verdict.value}**


No next phase has been authorized automatically.


## Root Options


- AUTHORIZE

- MODIFY

- REPAIR

- HOLD

- CLOSE

"""

        )


        self.vault.append_event(

            {

                "type": "AUDIT_RECORDED",

                "project_id": s.project_id,

                "auditor": auditor_name,

                "verdict": verdict.value

            }

        )


        return s