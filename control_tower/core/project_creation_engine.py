from pathlib import Path

from ..agents import AgentRegistry, AgentRole, AgentStatus
from ..guardrails import GovernanceError
from ..models import (
    Division,
    Lineage,
    ProjectState,
    Role,
    State,
)


DIVISION_PATH = {
    "RESEARCH": "01_RESEARCH",
    "BUSINESS": "02_BUSINESS",
    "PERSONAL_GROWTH": "03_PERSONAL_GROWTH",
}


class ProjectCreationEngine:
    def __init__(self, vault):
        self.vault = vault
        self.agent_registry = AgentRegistry(vault.root)

    def _validate_owner(self, owner):
        agent = self.agent_registry.get(owner)

        if not agent:
            raise GovernanceError(
                f"Unknown project owner: {owner}"
            )

        if agent.status != AgentStatus.ACTIVE:
            raise GovernanceError(
                f"Inactive project owner: {owner}"
            )

        if agent.role != AgentRole.PRODUCER:
            raise GovernanceError(
                "Project owner must be a PRODUCER."
            )

        if "produce_artifact" not in agent.capabilities:
            raise GovernanceError(
                "Project owner lacks produce_artifact capability."
            )

    @staticmethod
    def _assert_existing_matches(state, expected):
        for key, value in expected.items():
            actual = getattr(state, key)

            if actual != value:
                raise GovernanceError(
                    "Project idempotency conflict: "
                    f"{key}"
                )

    def create_project(self, proposal):
        payload = proposal.payload
        project_id = payload["project_id"]
        title = payload["title"]
        division = Division(payload["division"])
        owner = payload["owner"]
        phase = payload.get("phase", "T0")
        lineage = Lineage(
            payload.get("lineage", "CANONICAL")
        )

        if proposal.target != project_id:
            raise GovernanceError(
                "Project proposal target does not match project_id."
            )

        if (
            not project_id
            or Path(project_id).name != project_id
        ):
            raise GovernanceError(
                f"Invalid project id: {project_id}"
            )

        if not phase or Path(phase).name != phase:
            raise GovernanceError(
                f"Invalid project phase: {phase}"
            )

        if division.value not in DIVISION_PATH:
            raise GovernanceError(
                f"Unsupported division: {division.value}"
            )

        self._validate_owner(owner)

        project_dir = (
            self.vault.root
            / DIVISION_PATH[division.value]
            / project_id
        )
        state_path = project_dir / "STATE.md"

        try:
            existing_path = self.vault.find_state_path(
                project_id
            )
        except FileNotFoundError:
            existing_path = None

        if (
            existing_path is not None
            and existing_path != state_path
        ):
            raise GovernanceError(
                "Project id already exists in another division: "
                f"{project_id}"
            )

        if state_path.exists():
            state = self.vault.read_state(state_path)
            self._assert_existing_matches(
                state,
                {
                    "project_id": project_id,
                    "title": title,
                    "division": division,
                    "phase": phase,
                    "owner": owner,
                    "lineage": lineage,
                },
            )
            return state_path

        for subdirectory in (
            "artifacts",
            "audits",
            "tasks",
            "handoffs",
            "claims",
            "failed_routes",
        ):
            (project_dir / subdirectory).mkdir(
                parents=True,
                exist_ok=True,
            )

        state = ProjectState(
            project_id=project_id,
            title=title,
            division=division,
            phase=phase,
            state=State.READY,
            owner=owner,
            owner_role=Role.PRODUCER,
            agents={
                Role.PRODUCER.value: [owner],
            },
            lineage=lineage,
            next_gate="ROOT_AUTHORIZATION",
            notes="Created by Root-approved project proposal.",
        )
        self.vault.write_state(state_path, state)
        return state_path
