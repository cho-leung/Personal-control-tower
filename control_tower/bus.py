from dataclasses import replace

from .core.project_engine import ProjectEngine
from .core.decision_engine import DecisionEngine
from .core.execution_engine import ExecutionEngine
from .core.audit_engine import AuditEngine

from .models import (
    Role,
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


    def __init__(
        self,
        vault
    ):

        self.vault = vault

        self.vault.ensure_structure()


        self.agent_registry = AgentRegistry(
            vault.root
        )


        self.event_ledger = EventLedger(
            vault
        )


        # v0.6 architecture injection
        self.project_engine = ProjectEngine(
            self.vault,
            self.agent_registry,
            self.event_ledger
        )

        self.execution_engine = ExecutionEngine(
            self.vault,
            self.agent_registry,
            self.event_ledger
        )

        self.decision_engine = DecisionEngine(
            self.vault,
            self.agent_registry,
            self.event_ledger
        )

        self.audit_engine = AuditEngine(
            self.vault,
            self.agent_registry,
            self.event_ledger
        )



    # ============================
    # Agent Permission
    # ============================

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



    # ============================
    # Event Helper
    # ============================

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
                capability_checked=capability
            )

        )



    # ============================
    # Project Engine Adapter
    # ============================

    def create_research_project(
        self,
        project_id,
        title,
        owner,
        phase
    ):

        return self.project_engine.create_research_project(
            project_id,
            title,
            owner,
            phase
        )



    # ============================
    # Root Authorization
    # ============================

    def root_authorize(
            self,
            state_path,
            authorization_id,
            scope
    ):
        return self.decision_engine.authorize(
            state_path,
            authorization_id,
            scope
        )



    # ============================
    # Execution
    # ============================

    def start_execution(
            self,
            state_path,
            producer_name
    ):
        return self.execution_engine.start_execution(
            state_path,
            producer_name
        )

    # ============================
    # Producer Complete
    # ============================

    def producer_complete(
            self,
            state_path,
            producer_name,
            artifact_text,
            auditor_name
    ):
        return self.execution_engine.producer_complete(
            state_path,
            producer_name,
            artifact_text,
            auditor_name
        )


    # ============================
    # Audit
    # ============================

    def record_audit(
            self,
            state_path,
            auditor_name,
            verdict,
            audit_text
    ):
        return self.audit_engine.record_audit(
            state_path,
            auditor_name,
            verdict,
            audit_text
        )