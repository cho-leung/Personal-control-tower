from dataclasses import replace

from ..models import (
    Role,
    State,
)

from ..guardrails import (
    assert_transition,
    assert_actor_owns_action,
    assert_valid_auditor,
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
        capability,
        required_role=None,
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
                f"Missing capability: {capability}"
            )


        if (
            required_role is not None
            and agent.role.value != required_role.value
        ):

            raise GovernanceError(
                f"Role mismatch: {agent.role.value} "
                f"!= {required_role.value}"
            )


        return agent



    def start_execution(
        self,
        state_path,
        producer_name
    ):

        agent = self.check_agent(
            producer_name,
            "produce_artifact",
            Role.PRODUCER,
        )


        state = self.vault.read_state(
            state_path
        )


        assert_actor_owns_action(
            state,
            producer_name,
            Role(agent.role.value),
            "produce"
        )


        if not state.authorization_id:

            raise GovernanceError(
                "Missing Root authorization."
            )

        assert_transition(
            state.state,
            State.ACTIVE,
            Role(agent.role.value),
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


        self.event_ledger.append_once(

            Event(

                event_id=
                f"EVT-{state.project_id}-EXECUTE-"
                f"{state.phase}-{state.authorization_id}",

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
        auditor_name,
        task_id=None,
        causation_event_id=None,
    ):


        agent = self.check_agent(
            producer_name,
            "produce_artifact",
            Role.PRODUCER,
        )


        state = self.vault.read_state(
            state_path
        )


        auditor = self.agent_registry.get(
            auditor_name
        )


        assert_valid_auditor(
            replace(state, auditor=auditor_name),
            auditor,
        )


        assert_actor_owns_action(
            state,
            producer_name,
            Role(agent.role.value),
            "produce"
        )


        if state.state != State.ACTIVE:

            raise GovernanceError(
                "Completion requires ACTIVE."
            )


        assert_transition(
            state.state,
            State.PRODUCER_COMPLETE,
            Role(agent.role.value),
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

            next_gate="ROOT_AUDIT_APPROVAL",

            notes="Producer complete. Artifact frozen."

        )


        self.vault.write_state(
            state_path,
            state
        )


        self.event_ledger.append_once(

            Event(

                event_id=
                f"EVT-{state.project_id}-PRODUCE_COMPLETE-"
                f"{state.phase}-{sha[:12]}",

                actor=producer_name,

                action="PRODUCE_ARTIFACT",

                target=state.project_id,

                result=EventResult.SUCCESS,

                capability_checked="produce_artifact",

                correlation_id=task_id,

                causation_id=causation_event_id,

                metadata=(
                    {"task_id": task_id}
                    if task_id
                    else {}
                )

            )

        )


        return state
