from pathlib import Path

from ..agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from ..guardrails import GovernanceError
from ..models import Division


ROLE_REQUIRED_CAPABILITIES = {
    AgentRole.ROOT: {
        "approve",
        "reject",
        "authorize",
    },
    AgentRole.PRODUCER: {
        "produce_artifact",
    },
    AgentRole.AUDITOR: {
        "audit",
    },
}


class AgentCreationEngine:
    def __init__(self, vault):
        self.vault = vault
        self.registry = AgentRegistry(vault.root)

    def create_agent(self, proposal):
        payload = proposal.payload
        agent_id = payload["agent_id"]

        if proposal.target != agent_id:
            raise GovernanceError(
                "Agent proposal target does not match agent_id."
            )

        if (
            not agent_id
            or Path(agent_id).name != agent_id
        ):
            raise GovernanceError(
                f"Invalid agent id: {agent_id}"
            )

        division = Division(payload["division"])
        role = AgentRole(payload["role"])
        status = AgentStatus(
            payload.get("status", "ACTIVE")
        )
        capabilities = sorted(
            set(payload.get("capabilities", []))
        )

        if (
            role == AgentRole.ROOT
            and agent_id != "personal_root"
        ):
            raise GovernanceError(
                "Only personal_root may hold the ROOT role."
            )

        if not capabilities:
            raise GovernanceError(
                "Agent must have at least one capability."
            )

        missing_capabilities = (
            ROLE_REQUIRED_CAPABILITIES.get(role, set())
            - set(capabilities)
        )

        if missing_capabilities:
            raise GovernanceError(
                f"Role {role.value} requires capabilities: "
                + ", ".join(sorted(missing_capabilities))
            )

        expected = AgentState(
            agent_id=agent_id,
            division=division.value,
            role=role,
            status=status,
            owns=list(payload.get("owns", [])),
            capabilities=capabilities,
            notes=payload.get(
                "notes",
                "Created by Root-approved agent proposal.",
            ),
        )
        existing = self.registry.get(agent_id)

        if existing:
            comparable_existing = {
                "agent_id": existing.agent_id,
                "division": existing.division,
                "role": existing.role,
                "status": existing.status,
                "capabilities": sorted(
                    existing.capabilities
                ),
            }
            comparable_expected = {
                "agent_id": expected.agent_id,
                "division": expected.division,
                "role": expected.role,
                "status": expected.status,
                "capabilities": sorted(
                    expected.capabilities
                ),
            }

            if comparable_existing != comparable_expected:
                raise GovernanceError(
                    f"Agent idempotency conflict: {agent_id}"
                )

            return self.registry.path

        agents = self.registry.load()
        agents.append(expected)
        self.registry.save(agents)
        return self.registry.path
