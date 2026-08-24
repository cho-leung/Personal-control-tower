"""Pure read model for the conversational Control Tower interface."""

import json
from pathlib import Path
from typing import Dict, List

import yaml

from ..agents import AgentRegistry
from ..models import ProposalState, State
from ..tasks import TaskStatus, TaskStore
from ..vault import Vault
from .models import (
    AgentSummary,
    AttentionSummary,
    EventSummary,
    ProjectSummary,
    ProposalSummary,
    TaskSummary,
    TowerSnapshot,
)


class ChatQueryError(RuntimeError):
    pass


class ChatUnavailableError(ChatQueryError):
    pass


class ChatDataError(ChatQueryError):
    pass


class ControlTowerQueryService:
    """Read the v1 source of truth without creating or mutating files."""

    DIVISION_DIRS = (
        "01_RESEARCH",
        "02_BUSINESS",
        "03_PERSONAL_GROWTH",
    )
    DIVISION_VALUES = {
        "01_RESEARCH": "RESEARCH",
        "02_BUSINESS": "BUSINESS",
        "03_PERSONAL_GROWTH": "PERSONAL_GROWTH",
    }

    def __init__(self, vault_path: Path):
        self.root = Path(vault_path)
        self.vault = Vault(self.root)

    def _assert_confined(self, path: Path) -> None:
        """Reject reads that resolve outside the configured Vault."""

        try:
            root = self.root.resolve(strict=True)
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ChatDataError(
                f"Vault path resolves outside its boundary: {path}"
            ) from exc

    def _validate_initialized_vault(self) -> None:
        required_directories = (
            self.root,
            self.root / "00_ROOT",
            self.root / "00_ROOT" / "inbox",
            self.root / ".control_tower",
            *(
                self.root / division
                for division in self.DIVISION_DIRS
            ),
        )
        required_files = (
            self.root / "00_ROOT" / "agents.yaml",
        )
        required = required_directories + required_files
        missing = [str(path) for path in required if not path.exists()]

        if missing:
            raise ChatUnavailableError(
                "Vault is not initialized or is incomplete: "
                + ", ".join(missing)
            )

        wrong_directories = [
            str(path)
            for path in required_directories
            if not path.is_dir()
        ]
        wrong_files = [
            str(path)
            for path in required_files
            if not path.is_file()
        ]

        if wrong_directories or wrong_files:
            raise ChatDataError(
                "Vault structure has an invalid path type: "
                + ", ".join(wrong_directories + wrong_files)
            )

        for path in required:
            self._assert_confined(path)

    def _state_paths(self):
        paths = []

        for division in self.DIVISION_DIRS:
            division_path = self.root / division

            try:
                project_paths = sorted(division_path.iterdir())
            except OSError as exc:
                raise ChatDataError(
                    f"Cannot enumerate division: {division_path}"
                ) from exc

            for project_path in project_paths:
                self._assert_confined(project_path)

                if not project_path.is_dir():
                    raise ChatDataError(
                        "Unexpected non-project item in division: "
                        f"{project_path}"
                    )

                state_path = project_path / "STATE.md"

                if not state_path.is_file():
                    raise ChatDataError(
                        "Project directory is missing STATE.md: "
                        f"{project_path}"
                    )

                self._assert_confined(state_path)
                paths.append(state_path)

        return paths

    @staticmethod
    def _required_text(data: Dict, key: str, source: Path) -> str:
        value = data.get(key)

        if not isinstance(value, str) or not value:
            raise ChatDataError(
                f"Missing {key} in {source}"
            )

        return value

    def _read_projects_and_tasks(self):
        projects = []
        tasks = []
        seen_projects = set()

        for state_path in self._state_paths():
            try:
                state = self.vault.read_state(state_path)
            except Exception as exc:
                raise ChatDataError(
                    f"Cannot read project state: {state_path}"
                ) from exc

            if state_path.parent.name != state.project_id:
                raise ChatDataError(
                    "Project id does not match its directory: "
                    f"{state_path}"
                )

            expected_division = self.DIVISION_VALUES[
                state_path.parent.parent.name
            ]

            if state.division.value != expected_division:
                raise ChatDataError(
                    "Project division does not match its directory: "
                    f"{state_path}"
                )

            for key, value in {
                "project_id": state.project_id,
                "title": state.title,
                "phase": state.phase,
                "owner": state.owner,
            }.items():
                if not isinstance(value, str) or not value:
                    raise ChatDataError(
                        f"Invalid project {key}: {state_path}"
                    )

            if (
                state.authorization_id is not None
                and (
                    not isinstance(state.authorization_id, str)
                    or not state.authorization_id
                )
            ):
                raise ChatDataError(
                    f"Invalid project authorization: {state_path}"
                )

            if (
                state.auditor is not None
                and (
                    not isinstance(state.auditor, str)
                    or not state.auditor
                )
            ):
                raise ChatDataError(
                    f"Invalid project auditor: {state_path}"
                )

            if not isinstance(state.agents, dict):
                raise ChatDataError(
                    f"Invalid project bindings: {state_path}"
                )

            bound_auditors = state.agents.get("AUDITOR", [])

            if isinstance(bound_auditors, str):
                bound_auditors = [bound_auditors]

            if (
                not isinstance(bound_auditors, list)
                or not all(
                    isinstance(agent_id, str) and agent_id
                    for agent_id in bound_auditors
                )
            ):
                raise ChatDataError(
                    f"Invalid auditor bindings: {state_path}"
                )

            if state.project_id in seen_projects:
                raise ChatDataError(
                    f"Duplicate project id: {state.project_id}"
                )

            seen_projects.add(state.project_id)
            projects.append(
                ProjectSummary(
                    project_id=state.project_id,
                    title=state.title,
                    division=state.division.value,
                    phase=state.phase,
                    state=state.state.value,
                    owner=state.owner,
                    next_gate=state.next_gate,
                    authorization_id=state.authorization_id,
                    auditor=state.auditor,
                    bound_auditors=tuple(bound_auditors),
                )
            )

            try:
                task_store = TaskStore(state_path.parent)

                if task_store.tasks_dir.exists():
                    self._assert_confined(task_store.tasks_dir)

                    if not task_store.tasks_dir.is_dir():
                        raise ChatDataError(
                            "Project tasks path is not a directory: "
                            f"{task_store.tasks_dir}"
                        )

                    for task_path in task_store.tasks_dir.glob("*.md"):
                        self._assert_confined(task_path)

                project_tasks = task_store.list()
            except ChatDataError:
                raise
            except Exception as exc:
                raise ChatDataError(
                    f"Cannot read Tasks for {state.project_id}"
                ) from exc

            tasks.extend(
                TaskSummary(
                    task_id=task.task_id,
                    project_id=task.project_id,
                    phase=task.phase,
                    task_type=task.task_type,
                    status=task.status.value,
                    assigned_agent=task.assigned_agent,
                    required_role=task.required_role,
                )
                for task in project_tasks
            )

        return projects, tasks

    def _read_agents(self):
        try:
            agents = AgentRegistry(self.root).load()
        except Exception as exc:
            raise ChatDataError(
                "Cannot read Agent Registry."
            ) from exc

        root_agents = [
            agent
            for agent in agents
            if agent.agent_id == "personal_root"
            and agent.role.value == "ROOT"
        ]

        if len(root_agents) != 1:
            raise ChatDataError(
                "Agent Registry must contain exactly one personal_root."
            )

        for agent in agents:
            if (
                not isinstance(agent.agent_id, str)
                or not agent.agent_id
                or not isinstance(agent.division, str)
                or not agent.division
                or not isinstance(agent.capabilities, list)
                or not all(
                    isinstance(capability, str)
                    and capability
                    for capability in agent.capabilities
                )
            ):
                raise ChatDataError(
                    "Agent Registry contains invalid data."
                )

        return [
            AgentSummary(
                agent_id=agent.agent_id,
                division=agent.division,
                role=agent.role.value,
                status=agent.status.value,
                capabilities=tuple(agent.capabilities),
            )
            for agent in agents
        ]

    def _read_root_inbox(self):
        proposals = []
        documents = []
        inbox = self.root / "00_ROOT" / "inbox"

        for path in sorted(inbox.glob("*.md")):
            self._assert_confined(path)

            try:
                text = path.read_text(encoding="utf-8")
                parts = text.split("---", 2)

                if len(parts) < 3:
                    documents.append(path.name)
                    continue

                metadata = yaml.safe_load(parts[1])
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ChatDataError(
                    f"Cannot read Root inbox item: {path}"
                ) from exc

            if not isinstance(metadata, dict):
                raise ChatDataError(
                    f"Invalid Root inbox metadata: {path}"
                )

            if not metadata.get("proposal_type"):
                documents.append(path.name)
                continue

            state = self._required_text(metadata, "state", path)

            if state not in {
                ProposalState.CREATED.value,
                ProposalState.WAITING_ROOT.value,
            }:
                continue

            proposals.append(
                ProposalSummary(
                    proposal_id=self._required_text(
                        metadata,
                        "proposal_id",
                        path,
                    ),
                    proposal_type=self._required_text(
                        metadata,
                        "proposal_type",
                        path,
                    ),
                    target=self._required_text(
                        metadata,
                        "target",
                        path,
                    ),
                    state=state,
                    created_by=self._required_text(
                        metadata,
                        "created_by",
                        path,
                    ),
                )
            )

        return proposals, documents

    def _read_recent_events(self):
        path = self.root / ".control_tower" / "events.jsonl"

        if not path.exists():
            return []

        self._assert_confined(path)

        if not path.is_file():
            raise ChatDataError(
                f"Event Ledger is not a file: {path}"
            )

        events = []

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ChatDataError(
                "Cannot read Event Ledger."
            ) from exc

        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ChatDataError(
                    "Invalid Event Ledger JSON at line "
                    f"{line_number}."
                ) from exc

            if not isinstance(event, dict):
                raise ChatDataError(
                    f"Invalid Event Ledger entry at line {line_number}."
                )

            events.append(
                EventSummary(
                    event_id=self._required_text(
                        event,
                        "event_id",
                        path,
                    ),
                    action=self._required_text(
                        event,
                        "action",
                        path,
                    ),
                    result=self._required_text(
                        event,
                        "result",
                        path,
                    ),
                    actor=self._required_text(
                        event,
                        "actor",
                        path,
                    ),
                    target=self._required_text(
                        event,
                        "target",
                        path,
                    ),
                    timestamp_utc=str(
                        event.get("timestamp_utc") or ""
                    ),
                )
            )

        return events[-10:]

    @staticmethod
    def _attention(projects, tasks, proposals):
        attention: List[AttentionSummary] = []

        for project in projects:
            if project.state in {
                State.BLOCKED.value,
                State.WAITING_ROOT.value,
            }:
                attention.append(
                    AttentionSummary(
                        item_type="PROJECT",
                        item_id=project.project_id,
                        status=project.state,
                    )
                )

        for task in tasks:
            if task.status in {
                TaskStatus.BLOCKED.value,
                TaskStatus.FAILED.value,
            }:
                attention.append(
                    AttentionSummary(
                        item_type="TASK",
                        item_id=task.task_id,
                        status=task.status,
                    )
                )

        attention.extend(
            AttentionSummary(
                item_type="PROPOSAL",
                item_id=proposal.proposal_id,
                status=proposal.state,
            )
            for proposal in proposals
        )
        return attention

    def snapshot(self) -> TowerSnapshot:
        self._validate_initialized_vault()
        projects, tasks = self._read_projects_and_tasks()
        agents = self._read_agents()
        proposals, documents = self._read_root_inbox()
        events = self._read_recent_events()
        attention = self._attention(
            projects,
            tasks,
            proposals,
        )

        return TowerSnapshot(
            projects=tuple(
                sorted(projects, key=lambda item: item.project_id)
            ),
            agents=tuple(
                sorted(agents, key=lambda item: item.agent_id)
            ),
            tasks=tuple(
                sorted(tasks, key=lambda item: item.task_id)
            ),
            pending_proposals=tuple(
                sorted(
                    proposals,
                    key=lambda item: item.proposal_id,
                )
            ),
            root_documents=tuple(sorted(documents)),
            attention=tuple(
                sorted(
                    attention,
                    key=lambda item: (
                        item.item_type,
                        item.item_id,
                    ),
                )
            ),
            recent_events=tuple(events),
        )
