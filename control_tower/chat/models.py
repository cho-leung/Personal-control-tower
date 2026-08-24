"""Typed contracts for governed chat queries and Proposal drafting."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Optional, Tuple, Union


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
    DRAFT_CREATE_TASK = "DRAFT_CREATE_TASK"
    DRAFT_CREATE_PROJECT_REQUEST = (
        "DRAFT_CREATE_PROJECT_REQUEST"
    )
    DRAFT_CREATE_AGENT_REQUEST = (
        "DRAFT_CREATE_AGENT_REQUEST"
    )
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

DRAFT_INTENTS = frozenset(
    {
        IntentKind.DRAFT_CREATE_TASK,
        IntentKind.DRAFT_CREATE_PROJECT_REQUEST,
        IntentKind.DRAFT_CREATE_AGENT_REQUEST,
    }
)


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _required_text(value, label, limit=2000):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or _CONTROL_RE.search(value)
    ):
        raise IntentValidationError(f"Invalid {label}.")

    return value


def _identifier(value, label):
    _required_text(value, label, limit=160)

    if Path(value).name != value or value in {".", ".."}:
        raise IntentValidationError(f"Invalid {label}.")

    return value


@dataclass(frozen=True)
class TaskProposalRequest:
    objective: str
    project_hint: Optional[str] = None

    def __post_init__(self):
        _required_text(self.objective, "task objective")

        if self.project_hint is not None:
            _identifier(self.project_hint, "project hint")


@dataclass(frozen=True)
class ProjectProposalRequest:
    project_id: str
    title: str
    division: str
    owner: str
    phase: str = "T0"
    lineage: str = "CANONICAL"

    def __post_init__(self):
        _identifier(self.project_id, "project id")
        _required_text(self.title, "project title", limit=240)
        _identifier(self.owner, "project owner")
        _identifier(self.phase, "project phase")

        if self.division not in {
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        }:
            raise IntentValidationError(
                "Invalid project division."
            )

        if self.lineage not in {
            "CANONICAL",
            "EXPERIMENTAL_NONCANONICAL",
            "HISTORICAL",
        }:
            raise IntentValidationError(
                "Invalid project lineage."
            )


@dataclass(frozen=True)
class AgentProposalRequest:
    agent_id: str
    division: str
    role: str
    capabilities: Tuple[str, ...]
    status: str = "ACTIVE"

    def __post_init__(self):
        _identifier(self.agent_id, "agent id")

        if self.agent_id == "personal_root":
            raise IntentValidationError(
                "Chat cannot draft changes to personal_root."
            )

        if self.division not in {
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        }:
            raise IntentValidationError(
                "Invalid agent division."
            )

        if self.role not in {
            "CONTROLLER",
            "PRODUCER",
            "AUDITOR",
            "VALIDATOR",
            "BUILDER",
            "SPECIALIST",
        }:
            raise IntentValidationError("Invalid agent role.")

        if self.status != "ACTIVE":
            raise IntentValidationError(
                "New chat-drafted agents must start ACTIVE."
            )

        if (
            not isinstance(self.capabilities, tuple)
            or not self.capabilities
            or len(self.capabilities) > 20
        ):
            raise IntentValidationError(
                "Agent capabilities are required."
            )

        for capability in self.capabilities:
            _identifier(capability, "agent capability")


ProposalRequest = Union[
    TaskProposalRequest,
    ProjectProposalRequest,
    AgentProposalRequest,
]


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    project_id: Optional[str] = None
    confidence: float = 1.0
    proposal_request: Optional[ProposalRequest] = None

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

        expected_request_type = {
            IntentKind.DRAFT_CREATE_TASK: TaskProposalRequest,
            IntentKind.DRAFT_CREATE_PROJECT_REQUEST: (
                ProjectProposalRequest
            ),
            IntentKind.DRAFT_CREATE_AGENT_REQUEST: (
                AgentProposalRequest
            ),
        }.get(self.kind)

        if expected_request_type is None:
            if self.proposal_request is not None:
                raise IntentValidationError(
                    "Only draft intents may carry a proposal request."
                )
        elif not isinstance(
            self.proposal_request,
            expected_request_type,
        ):
            raise IntentValidationError(
                "Draft intent has the wrong typed request."
            )


@dataclass(frozen=True)
class ProjectSummary:
    project_id: str
    title: str
    division: str
    phase: str
    state: str
    owner: str
    next_gate: Optional[str]
    authorization_id: Optional[str]
    auditor: Optional[str]
    bound_auditors: Tuple[str, ...]


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
    required_role: str


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
