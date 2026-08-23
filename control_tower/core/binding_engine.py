from dataclasses import replace

from ..agents import AgentRegistry, AgentRole, AgentStatus
from ..guardrails import GovernanceError
from ..models import Role


ROLE_CAPABILITY = {
    Role.PRODUCER: "produce_artifact",
    Role.AUDITOR: "audit",
}


class BindingEngine:
    def __init__(self, vault):
        self.vault = vault
        self.agent_registry = AgentRegistry(vault.root)

    def bind(self, proposal):
        project_id = proposal.payload["project_id"]
        agent_id = proposal.payload["agent_id"]
        role = Role(proposal.payload["role"])

        if proposal.target != project_id:
            raise GovernanceError(
                "Binding proposal target does not match project_id."
            )

        state_path = self.vault.find_state_path(project_id)
        state = self.vault.read_state(state_path)
        agent = self.agent_registry.get(agent_id)

        if not agent:
            raise GovernanceError(
                f"Unknown agent: {agent_id}"
            )

        if agent.status != AgentStatus.ACTIVE:
            raise GovernanceError(
                f"Inactive agent: {agent_id}"
            )

        if agent.role != AgentRole(role.value):
            raise GovernanceError(
                "Binding role must match registered agent role."
            )

        required_capability = ROLE_CAPABILITY.get(role)

        if (
            required_capability
            and required_capability not in agent.capabilities
        ):
            raise GovernanceError(
                f"Agent lacks {required_capability}: {agent_id}"
            )

        if role == Role.PRODUCER and agent_id != state.owner:
            raise GovernanceError(
                "Only the project owner may hold the PRODUCER binding."
            )

        if role == Role.AUDITOR and agent_id == state.owner:
            raise GovernanceError(
                "PRODUCER / AUDITOR INDEPENDENCE CONFLICT"
            )

        agents = {
            key: list(value)
            for key, value in (state.agents or {}).items()
        }
        members = agents.setdefault(role.value, [])

        if agent_id in members:
            return state_path

        members.append(agent_id)
        state = replace(state, agents=agents)
        self.vault.write_state(state_path, state)
        return state_path
