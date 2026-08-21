from ..models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State
)

from ..events import Event, EventResult

from ..guardrails import GovernanceError


class ProjectEngine:


    def __init__(
        self,
        vault,
        agent_registry,
        event_ledger
    ):

        self.vault = vault
        self.agent_registry = agent_registry
        self.event_ledger = event_ledger



    def create_research_project(
        self,
        project_id,
        title,
        owner,
        phase
    ):


        agent = self.agent_registry.get(
            owner
        )


        if not agent:

            raise GovernanceError(
                f"Unknown agent: {owner}"
            )


        if "produce_artifact" not in agent.capabilities:

            raise GovernanceError(
                f"{owner} cannot create research project"
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
            "failed_routes"
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

            notes="READY only. Waiting for Root authorization."

        )


        state_path = (
            project_dir /
            "STATE.md"
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.event_ledger.append(

            Event(
                event_id=f"EVT-{project_id}-CREATE",

                actor=owner,

                action="CREATE_PROJECT",

                target=project_id,

                result=EventResult.SUCCESS,

                capability_checked="produce_artifact"
            )

        )


        return state, state_path