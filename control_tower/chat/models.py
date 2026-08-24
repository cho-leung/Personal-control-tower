"""Typed contracts shared by chat adapters and the read-only presenter."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple


class IntentValidationError(ValueError):
    """An adapter returned an unsafe or malformed intent."""


class IntentKind(str, Enum):
    ORGANIZATION_OVERVIEW = "ORGANIZATION_OVERVIEW"
    PROJECT_LIST = "PROJECT_LIST"
    PROJECT_DETAIL = "PROJECT_DETAIL"
    AGENT_LIST = "AGENT_LIST"
    TASK_LIST = "TASK_LIST"
    ROOT_INBOX = "ROOT_INBOX"
    ATTENTION_ITEMS = "ATTENTION_ITEMS"
    RECENT_EVENTS = "RECENT_EVENTS"
    HELP = "HELP"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    UNKNOWN = "UNKNOWN"


READ_ONLY_INTENTS = frozenset(
    {
        IntentKind.ORGANIZATION_OVERVIEW,
        IntentKind.PROJECT_LIST,
        IntentKind.PROJECT_DETAIL,
        IntentKind.AGENT_LIST,
        IntentKind.TASK_LIST,
        IntentKind.ROOT_INBOX,
        IntentKind.ATTENTION_ITEMS,
        IntentKind.RECENT_EVENTS,
        IntentKind.HELP,
    }
)


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    project_id: Optional[str] = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IntentKind):
            raise IntentValidationError(
                "Intent kind must be an allowlisted IntentKind."
            )

        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise IntentValidationError(
                "Intent confidence must be between 0 and 1."
            )

        if self.project_id is not None:
            if (
                not isinstance(self.project_id, str)
                or not self.project_id.strip()
                or self.project_id != self.project_id.strip()
                or Path(self.project_id).name != self.project_id
                or self.project_id in {".", ".."}
                or "\n" in self.project_id
                or "\r" in self.project_id
            ):
                raise IntentValidationError(
                    "Intent project_id is invalid."
                )

        if (
            self.kind == IntentKind.PROJECT_DETAIL
            and self.project_id is None
        ):
            raise IntentValidationError(
                "PROJECT_DETAIL requires project_id."
            )

        if (
            self.kind != IntentKind.PROJECT_DETAIL
            and self.project_id is not None
        ):
            raise IntentValidationError(
                "Only PROJECT_DETAIL may carry project_id."
            )


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    division: str
    phase: str
    state: str
    owner: str
    next_gate: Optional[str]


@dataclass(frozen=True)
class AgentSummary:
    agent_id: str
    division: str
    role: str
    status: str
    capabilities: Tuple[str, ...]


@dataclass(frozen=True)
class TaskSummary:
    task_id: str
    project_id: str
    phase: str
    task_type: str
    status: str
    assigned_agent: str


@dataclass(frozen=True)
class ProposalSummary:
    proposal_id: str
    proposal_type: str
    target: str
    state: str
    created_by: str


@dataclass(frozen=True)
class AttentionSummary:
    item_type: str
    item_id: str
    status: str


@dataclass(frozen=True)
class EventSummary:
    event_id: str
    action: str
    result: str
    actor: str
    target: str
    timestamp_utc: str


@dataclass(frozen=True)
class TowerSnapshot:
    projects: Tuple[ProjectSummary, ...]
    agents: Tuple[AgentSummary, ...]
    tasks: Tuple[TaskSummary, ...]
    pending_proposals: Tuple[ProposalSummary, ...]
    root_documents: Tuple[str, ...]
    attention: Tuple[AttentionSummary, ...]
    recent_events: Tuple[EventSummary, ...]
