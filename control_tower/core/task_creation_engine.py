"""Root-governed creation of ordinary project Tasks."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from ..agents import AgentRegistry, AgentRole, AgentStatus
from ..events import Event, EventLedger, EventResult
from ..guardrails import GovernanceError, assert_valid_auditor
from ..models import Role, State
from ..tasks import Task, TaskStatus, TaskStore


@dataclass(frozen=True)
class TaskCreationCommand:
    project_id: str
    description: str = ""
    task_id: Optional[str] = None
    assigned_agent: Optional[str] = None
    role: str = Role.PRODUCER.value
    task_type: Optional[str] = None
    capability: Optional[str] = None
    auditor: Optional[str] = None
    context_refs: Tuple[str, ...] = ()
    expected_phase: Optional[str] = None
    expected_authorization_id: Optional[str] = None
    proposal_id: Optional[str] = None


@dataclass(frozen=True)
class TaskCreationResult:
    task: Task
    path: Path


class TaskCreationEngine:
    """Validate and assign a Task without executing its runtime."""

    def __init__(self, vault):
        self.vault = vault
        self.registry = AgentRegistry(vault.root)
        self.events = EventLedger(vault)

    def _require_active_root(self):
        root = self.registry.get("personal_root")

        if (
            not root
            or root.status != AgentStatus.ACTIVE
            or root.role != AgentRole.ROOT
            or "approve" not in root.capabilities
        ):
            raise GovernanceError(
                "Task creation requires an ACTIVE personal_root "
                "with approve capability."
            )

        return root

    @staticmethod
    def _bound_members(state, role):
        members = []

        for bound_role, bound_agents in (state.agents or {}).items():
            bound_role_value = getattr(
                bound_role,
                "value",
                bound_role,
            )

            if str(bound_role_value).upper() != role.value:
                continue

            members.extend(
                [bound_agents]
                if isinstance(bound_agents, str)
                else (bound_agents or [])
            )

        return members

    def create(self, command: TaskCreationCommand) -> TaskCreationResult:
        self._require_active_root()
        state_path = self.vault.find_state_path(command.project_id)
        state = self.vault.read_state(state_path)
        role = Role(command.role)

        if role == Role.AUDITOR:
            raise GovernanceError(
                "Audit Tasks are created only by Root approval of "
                "CREATE_AUDIT_REQUEST."
            )

        if command.proposal_id and role != Role.PRODUCER:
            raise GovernanceError(
                "CREATE_TASK proposals initially support PRODUCER Tasks only."
            )

        if command.proposal_id and (
            command.task_type != "PRODUCE_ARTIFACT"
            or command.capability != "produce_artifact"
        ):
            raise GovernanceError(
                "CREATE_TASK proposal type and capability are fixed to "
                "PRODUCE_ARTIFACT / produce_artifact."
            )

        if (
            command.expected_phase is not None
            and command.expected_phase != state.phase
        ):
            raise GovernanceError(
                "Task proposal phase is stale: expected "
                f"{command.expected_phase}, found {state.phase}."
            )

        if state.state not in {State.AUTHORIZED, State.ACTIVE}:
            raise GovernanceError(
                "Task creation requires AUTHORIZED or ACTIVE project."
            )

        if not state.authorization_id:
            raise GovernanceError(
                "Task creation requires explicit Root authorization."
            )

        if (
            command.expected_authorization_id is not None
            and command.expected_authorization_id
            != state.authorization_id
        ):
            raise GovernanceError(
                "Task proposal authorization is stale."
            )

        if command.proposal_id and (
            not command.expected_phase
            or not command.expected_authorization_id
            or not command.task_id
        ):
            raise GovernanceError(
                "CREATE_TASK proposal must pin task, phase, and authorization."
            )

        agent_id = command.assigned_agent or (
            state.owner
            if role == Role.PRODUCER
            else state.auditor
        )

        if not agent_id:
            raise GovernanceError(
                f"No {role.value} assigned to project."
            )

        if role == Role.PRODUCER and agent_id != state.owner:
            raise GovernanceError(
                "Producer Task must be assigned to the project owner."
            )

        task_type = command.task_type or (
            "PRODUCE_ARTIFACT"
            if role == Role.PRODUCER
            else role.value
        )
        capability = command.capability or {
            Role.PRODUCER: "produce_artifact",
            Role.AUDITOR: "audit",
        }.get(role, task_type.lower())
        agent = self.registry.get(agent_id)

        if not agent or agent.status != AgentStatus.ACTIVE:
            raise GovernanceError(
                f"Unknown or inactive task agent: {agent_id}"
            )

        if agent.role.value != role.value:
            raise GovernanceError(
                "Task role does not match agent registry."
            )

        if capability not in agent.capabilities:
            raise GovernanceError(
                f"Task agent lacks capability: {capability}"
            )

        if agent_id not in self._bound_members(state, role):
            raise GovernanceError(
                f"Task agent is not bound as {role.value}: {agent_id}"
            )

        task_auditor = command.auditor

        if role == Role.PRODUCER:
            if not task_auditor:
                auditors = self._bound_members(
                    state,
                    Role.AUDITOR,
                )
                task_auditor = auditors[0] if auditors else None

            if (
                command.proposal_id
                and state.auditor is not None
                and state.auditor != task_auditor
            ):
                raise GovernanceError(
                    "Task proposal auditor is stale."
                )

            if (
                command.proposal_id
                and task_auditor != command.auditor
            ):
                raise GovernanceError(
                    "CREATE_TASK proposal must pin its auditor."
                )

            assert_valid_auditor(
                replace(state, auditor=task_auditor),
                self.registry.get(task_auditor),
            )

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S%f"
        )
        task_id = command.task_id or (
            f"TASK-{state.project_id}-{state.phase}-"
            f"{role.value}-{timestamp}"
        )
        store = TaskStore(state_path.parent)

        if role == Role.PRODUCER:
            for existing_task in store.list():
                if existing_task.task_id == task_id:
                    continue

                if (
                    existing_task.phase == state.phase
                    and existing_task.required_role
                    == Role.PRODUCER.value
                    and existing_task.status
                    != TaskStatus.COMPLETED
                ):
                    raise GovernanceError(
                        "Project phase already has unfinished "
                        "producer Task: "
                        f"{existing_task.task_id}"
                    )

        event_id = f"EVT-{task_id}-CREATED"
        metadata = {
            "auditor": task_auditor,
            "created_by": "personal_root",
        }

        if command.proposal_id:
            metadata["proposal_id"] = command.proposal_id

        task = Task(
            task_id=task_id,
            project_id=state.project_id,
            phase=state.phase,
            task_type=task_type,
            assigned_agent=agent_id,
            required_role=role.value,
            required_capability=capability,
            description=command.description,
            context_refs=list(command.context_refs),
            authorization_id=state.authorization_id,
            causation_event_id=event_id,
            metadata=metadata,
        )
        created = store.ensure(task)

        if created.status == TaskStatus.CREATED:
            created = store.assign(task_id)

        event_metadata = {"task_id": task_id}

        if command.proposal_id:
            event_metadata["proposal_id"] = command.proposal_id

        self.events.append_once(
            Event(
                event_id=event_id,
                actor="personal_root",
                action="TASK_CREATED",
                target=state.project_id,
                result=EventResult.SUCCESS,
                capability_checked="approve",
                note=command.description,
                correlation_id=task_id,
                causation_id=command.proposal_id,
                metadata=event_metadata,
            )
        )

        return TaskCreationResult(
            task=created,
            path=store.path_for(task_id),
        )
