from dataclasses import replace
import json
from pathlib import Path

import yaml

from ..agents import (
    AgentRegistry,
    AgentRole,
    AgentStatus,
)
from ..guardrails import GovernanceError
from ..models import Role, State


DIVISION_DIRECTORIES = (
    "01_RESEARCH",
    "02_BUSINESS",
    "03_PERSONAL_GROWTH",
)

TERMINAL_PROJECT_STATES = {
    State.COMPLETE,
    State.ARCHIVED,
}

TERMINAL_TASK_STATUSES = {
    "COMPLETED",
    "CANCELLED",
    "ARCHIVED",
}

ROOT_REQUIRED_CAPABILITIES = {
    "approve",
    "reject",
    "authorize",
}

ROLE_REQUIRED_CAPABILITIES = {
    AgentRole.ROOT: ROOT_REQUIRED_CAPABILITIES,
    AgentRole.PRODUCER: {"produce_artifact"},
    AgentRole.AUDITOR: {"audit"},
}


class AgentLifecycleEngine:
    """Apply Root-approved changes to the canonical agent registry.

    ``agents.yaml`` is the only writable agent source in v1.  The engine is
    intentionally independent of proposal persistence and Root decisions; the
    decision layer is responsible for calling it only after authorization.
    Every operation is safe to retry with the same desired result.
    """

    def __init__(self, vault):
        self.vault = vault
        self.registry = AgentRegistry(vault.root)

    def execute(self, proposal):
        handlers = {
            "ARCHIVE_AGENT": self.archive_agent,
            "UPDATE_AGENT_ROLE": self.update_agent_role,
            "UPDATE_AGENT_CAPABILITY": self.update_agent_capability,
        }

        try:
            handler = handlers[proposal.proposal_type]
        except KeyError as exc:
            raise GovernanceError(
                "Unsupported agent lifecycle proposal: "
                f"{proposal.proposal_type}"
            ) from exc

        return handler(proposal)

    def archive_agent(self, proposal):
        self._assert_proposal_type(proposal, "ARCHIVE_AGENT")
        agent_id = self._proposal_agent_id(proposal)
        agents, index = self._load_unique_agent(agent_id)
        agent = agents[index]

        self._assert_not_root_identity(agent)

        if agent.status == AgentStatus.ARCHIVED:
            return self.registry.path

        self._assert_no_active_project_ownership(agent_id)
        self._assert_no_active_audit_assignment(agent_id)
        self._assert_no_unfinished_tasks(agent_id)

        project_updates = (
            self._plan_archive_binding_updates(agent_id)
        )
        original_agents = list(agents)
        agents[index] = replace(
            agent,
            status=AgentStatus.ARCHIVED,
        )

        try:
            self.registry.save(agents)

            for state_path, _, updated_state in project_updates:
                self.vault.write_state(
                    state_path,
                    updated_state,
                )
        except Exception:
            self.registry.save(original_agents)

            for state_path, original_state, _ in project_updates:
                self.vault.write_state(
                    state_path,
                    original_state,
                )

            raise

        return self.registry.path

    def update_agent_role(self, proposal):
        self._assert_proposal_type(proposal, "UPDATE_AGENT_ROLE")
        agent_id = self._proposal_agent_id(proposal)
        agents, index = self._load_unique_agent(agent_id)
        agent = agents[index]

        requested_role = proposal.payload.get(
            "new_role",
            proposal.payload.get("role"),
        )

        if requested_role is None:
            raise GovernanceError(
                "UPDATE_AGENT_ROLE requires new_role."
            )

        try:
            new_role = AgentRole(str(requested_role).upper())
        except ValueError as exc:
            raise GovernanceError(
                f"Unknown agent role: {requested_role}"
            ) from exc

        if (
            new_role == AgentRole.ROOT
            and agent_id != "personal_root"
        ):
            raise GovernanceError(
                "Only personal_root may hold the ROOT role."
            )

        missing_capabilities = (
            ROLE_REQUIRED_CAPABILITIES.get(
                new_role,
                set(),
            )
            - set(agent.capabilities)
        )

        if missing_capabilities:
            raise GovernanceError(
                f"Role {new_role.value} requires capabilities: "
                + ", ".join(sorted(missing_capabilities))
            )

        expected_role = proposal.payload.get("expected_role")

        if agent.role == new_role:
            if (
                agent.agent_id == "personal_root"
                or agent.role == AgentRole.ROOT
                or agent.status == AgentStatus.ARCHIVED
            ):
                return self.registry.path

            project_updates = self._plan_binding_role_updates(
                agent_id,
                new_role,
                same_role=True,
            )

            for state_path, _, updated_state in project_updates:
                self.vault.write_state(state_path, updated_state)

            return self.registry.path

        self._assert_not_root_identity(agent)
        self._assert_mutable(agent)

        if (
            expected_role is not None
            and agent.role.value != str(expected_role).upper()
        ):
            raise GovernanceError(
                "Agent role idempotency conflict: "
                f"expected {expected_role}, found {agent.role.value}"
            )

        self._assert_no_unfinished_tasks(agent_id)
        project_updates = self._plan_binding_role_updates(
            agent_id,
            new_role,
        )
        original_agents = list(agents)
        agents[index] = replace(agent, role=new_role)

        try:
            self.registry.save(agents)

            for state_path, _, updated_state in project_updates:
                self.vault.write_state(state_path, updated_state)
        except Exception:
            self.registry.save(original_agents)

            for state_path, original_state, _ in project_updates:
                self.vault.write_state(
                    state_path,
                    original_state,
                )

            raise

        return self.registry.path

    def update_agent_capability(self, proposal):
        self._assert_proposal_type(
            proposal,
            "UPDATE_AGENT_CAPABILITY",
        )
        agent_id = self._proposal_agent_id(proposal)
        agents, index = self._load_unique_agent(agent_id)
        agent = agents[index]

        operation = str(
            proposal.payload.get("operation", "")
        ).upper()
        capability = proposal.payload.get("capability")

        if operation not in {"ADD", "REMOVE"}:
            raise GovernanceError(
                "Capability operation must be ADD or REMOVE."
            )

        if (
            not isinstance(capability, str)
            or not capability.strip()
        ):
            raise GovernanceError(
                "UPDATE_AGENT_CAPABILITY requires a capability."
            )

        capability = capability.strip()
        capabilities = set(agent.capabilities)

        if operation == "ADD" and capability in capabilities:
            return self.registry.path

        if operation == "REMOVE" and capability not in capabilities:
            return self.registry.path

        self._assert_mutable(agent)

        if (
            agent.agent_id == "personal_root"
            and operation == "REMOVE"
            and capability in ROOT_REQUIRED_CAPABILITIES
        ):
            raise GovernanceError(
                "Cannot remove a required personal_root capability."
            )

        if operation == "REMOVE":
            self._assert_no_unfinished_tasks(agent_id)
            self._assert_capability_not_required(
                agent_id,
                capability,
            )
            capabilities.remove(capability)
        else:
            capabilities.add(capability)

        agents[index] = replace(
            agent,
            capabilities=sorted(capabilities),
        )
        self.registry.save(agents)
        return self.registry.path

    @staticmethod
    def _assert_proposal_type(proposal, expected):
        if proposal.proposal_type != expected:
            raise GovernanceError(
                f"Expected {expected}, got {proposal.proposal_type}."
            )

    @staticmethod
    def _proposal_agent_id(proposal):
        payload_agent_id = proposal.payload.get("agent_id")
        agent_id = payload_agent_id or proposal.target

        if payload_agent_id and proposal.target != payload_agent_id:
            raise GovernanceError(
                "Agent proposal target does not match agent_id."
            )

        if (
            not isinstance(agent_id, str)
            or not agent_id
            or Path(agent_id).name != agent_id
        ):
            raise GovernanceError(
                f"Invalid agent id: {agent_id}"
            )

        return agent_id

    def _load_unique_agent(self, agent_id):
        agents = self.registry.load()
        matches = [
            index
            for index, agent in enumerate(agents)
            if agent.agent_id == agent_id
        ]

        if not matches:
            raise GovernanceError(
                f"Unknown agent: {agent_id}"
            )

        if len(matches) > 1:
            raise GovernanceError(
                f"Duplicate agent registry entries: {agent_id}"
            )

        return agents, matches[0]

    @staticmethod
    def _assert_not_root_identity(agent):
        if (
            agent.agent_id == "personal_root"
            or agent.role == AgentRole.ROOT
        ):
            raise GovernanceError(
                "The Root identity cannot be archived or re-roled."
            )

    @staticmethod
    def _assert_mutable(agent):
        if agent.status == AgentStatus.ARCHIVED:
            raise GovernanceError(
                f"Archived agent is immutable: {agent.agent_id}"
            )

    def _project_states(self):
        for division_directory in DIVISION_DIRECTORIES:
            pattern = (
                self.vault.root
                / division_directory
            ).glob("*/STATE.md")

            for state_path in sorted(pattern):
                yield (
                    state_path,
                    self.vault.read_state(state_path),
                    division_directory,
                )

    @staticmethod
    def _is_active_project(state):
        return state.state not in TERMINAL_PROJECT_STATES

    def _assert_no_active_project_ownership(self, agent_id):
        owned_projects = [
            state.project_id
            for _, state, _ in self._project_states()
            if (
                state.owner == agent_id
                and self._is_active_project(state)
            )
        ]

        if owned_projects:
            raise GovernanceError(
                "Cannot archive an active project owner: "
                + ", ".join(sorted(owned_projects))
            )

    def _assert_no_active_audit_assignment(self, agent_id):
        assigned_projects = [
            state.project_id
            for _, state, _ in self._project_states()
            if (
                state.auditor == agent_id
                and self._is_active_project(state)
            )
        ]

        if assigned_projects:
            raise GovernanceError(
                "Cannot archive an assigned auditor: "
                + ", ".join(sorted(assigned_projects))
            )

    def _plan_binding_role_updates(
        self,
        agent_id,
        new_role,
        same_role=False,
    ):
        updates = []

        for state_path, state, _ in self._project_states():
            if state.owner == agent_id:
                if same_role:
                    continue

                raise GovernanceError(
                    "Cannot change the role of a project owner: "
                    f"{state.project_id}"
                )

            if (
                state.auditor == agent_id
                and self._is_active_project(state)
            ):
                if same_role:
                    continue

                raise GovernanceError(
                    "Cannot change the role of an assigned auditor: "
                    f"{state.project_id}"
                )

            bindings = {}
            was_bound = False

            for raw_role, raw_members in (
                state.agents or {}
            ).items():
                role = getattr(raw_role, "value", raw_role)
                members = (
                    [raw_members]
                    if isinstance(raw_members, str)
                    else list(raw_members or [])
                )

                if agent_id in members:
                    was_bound = True
                    members = [
                        member
                        for member in members
                        if member != agent_id
                    ]

                if members:
                    bindings[str(role)] = members

            if not was_bound:
                continue

            if new_role == AgentRole.PRODUCER:
                raise GovernanceError(
                    "Only a project owner may hold a PRODUCER binding: "
                    f"{state.project_id}"
                )

            new_members = bindings.setdefault(
                new_role.value,
                [],
            )

            if agent_id not in new_members:
                new_members.append(agent_id)

            updates.append(
                (
                    state_path,
                    state,
                    replace(state, agents=bindings),
                )
            )

        return updates

    def _plan_archive_binding_updates(self, agent_id):
        updates = []

        for state_path, state, _ in self._project_states():
            if not self._is_active_project(state):
                continue

            bindings = {}
            changed = False

            for raw_role, raw_members in (
                state.agents or {}
            ).items():
                role = getattr(raw_role, "value", raw_role)
                members = (
                    [raw_members]
                    if isinstance(raw_members, str)
                    else list(raw_members or [])
                )
                filtered = [
                    member
                    for member in members
                    if member != agent_id
                ]

                if filtered != members:
                    changed = True

                if filtered:
                    bindings[str(role)] = filtered

            if changed:
                updates.append(
                    (
                        state_path,
                        state,
                        replace(state, agents=bindings),
                    )
                )

        return updates

    def _assert_capability_not_required(
        self,
        agent_id,
        capability,
    ):
        required_role = {
            "produce_artifact": Role.PRODUCER.value,
            "audit": Role.AUDITOR.value,
        }.get(capability)

        if required_role is None:
            return

        affected_projects = []

        for _, state, _ in self._project_states():
            if not self._is_active_project(state):
                continue

            members = (state.agents or {}).get(
                required_role,
                [],
            )
            members = (
                [members]
                if isinstance(members, str)
                else list(members or [])
            )

            if (
                agent_id in members
                or (
                    capability == "produce_artifact"
                    and state.owner == agent_id
                )
                or (
                    capability == "audit"
                    and state.auditor == agent_id
                )
            ):
                affected_projects.append(state.project_id)

        if affected_projects:
            raise GovernanceError(
                f"Capability {capability} is required by active projects: "
                + ", ".join(sorted(affected_projects))
            )

    def _assert_no_unfinished_tasks(self, agent_id):
        task_ids = []

        for task_path in self._task_paths():
            for task in self._read_task_records(task_path):
                owner = (
                    task.get("assigned_agent")
                    or task.get("owner_agent")
                    or task.get("owner")
                    or task.get("agent_id")
                    or task.get("assignee")
                    or task.get("assigned_to")
                )

                if owner != agent_id:
                    continue

                status = str(
                    task.get("status", "")
                ).upper()

                if status not in TERMINAL_TASK_STATUSES:
                    task_ids.append(
                        str(
                            task.get(
                                "task_id",
                                task_path.stem,
                            )
                        )
                    )

        if task_ids:
            raise GovernanceError(
                "Agent has unfinished tasks: "
                + ", ".join(sorted(set(task_ids)))
            )

    def _task_paths(self):
        paths = set()

        for division_directory in DIVISION_DIRECTORIES:
            projects_root = self.vault.root / division_directory

            if not projects_root.exists():
                continue

            for task_path in projects_root.glob("*/tasks/*"):
                if task_path.is_file():
                    paths.add(task_path)

        for tasks_root in (
            self.vault.root / "00_ROOT" / "tasks",
            self.vault.machine_dir / "tasks",
        ):
            if tasks_root.exists():
                paths.update(
                    path
                    for path in tasks_root.iterdir()
                    if path.is_file()
                )

        tasks_jsonl = self.vault.machine_dir / "tasks.jsonl"

        if tasks_jsonl.exists():
            paths.add(tasks_jsonl)

        return sorted(paths)

    @staticmethod
    def _read_task_records(path):
        try:
            text = path.read_text(encoding="utf-8")

            if path.suffix.lower() == ".jsonl":
                records = [
                    json.loads(line)
                    for line in text.splitlines()
                    if line.strip()
                ]
            elif path.suffix.lower() == ".json":
                records = json.loads(text)
            else:
                parts = text.split("---", 2)
                source = (
                    parts[1]
                    if len(parts) >= 3
                    else text
                )
                records = yaml.safe_load(source)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise GovernanceError(
                f"Cannot read task record: {path}"
            ) from exc

        if records is None:
            return []

        if isinstance(records, dict):
            return [records]

        if isinstance(records, list):
            return [
                record
                for record in records
                if isinstance(record, dict)
            ]

        return []
