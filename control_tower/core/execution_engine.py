from dataclasses import replace

from ..models import (
    Role,
    State,
)

from ..guardrails import (
    assert_actor_owns_action,
    GovernanceError,
)

from ..events import (
    Event,
    EventResult,
)



class ExecutionEngine:


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


        self.event_ledger.append(

            Event(

                event_id=
                f"EVT-{state.project_id}-EXECUTE",

                actor=producer_name,

                action="START_EXECUTION",

                target=state.project_id,

                result=EventResult.SUCCESS,

                capability_checked="produce_artifact"

            )

        )


        return state



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


        state = replace(
            state,
            state=State.AUDIT_PENDING
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.event_ledger.append(

            Event(

                event_id=
                f"EVT-{state.project_id}-PRODUCE_COMPLETE",

                actor=producer_name,

                action="PRODUCE_ARTIFACT",

                target=state.project_id,

                result=EventResult.SUCCESS,

                capability_checked="produce_artifact"

            )

        )


        return state