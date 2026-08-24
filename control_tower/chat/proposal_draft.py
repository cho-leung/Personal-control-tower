"""Typed Proposal drafts and their bounded ROOT-inbox submission gateway."""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

import yaml

from ..events import (
    Event,
    EventConflictError,
    EventLedger,
    EventResult,
)
from ..models import Proposal, ProposalState
from ..proposal_factory import ProposalFactory
from ..proposals import write_proposal


class ProposalDraftError(RuntimeError):
    pass


class ProposalDraftCommitError(ProposalDraftError):
    """A Proposal may exist but its governance record is incomplete."""

    def __init__(self, proposal_id, detail):
        self.proposal_id = proposal_id
        super().__init__(
            "Proposal submission needs recovery for "
            f"{proposal_id}: {detail}"
        )


class ProposalDraftType(str, Enum):
    CREATE_TASK = "CREATE_TASK"
    CREATE_PROJECT_REQUEST = "CREATE_PROJECT_REQUEST"
    CREATE_AGENT_REQUEST = "CREATE_AGENT_REQUEST"


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value, label, limit=2000):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or _CONTROL_RE.search(value)
    ):
        raise ProposalDraftError(f"Invalid {label}.")

    return value


def _identifier(value, label):
    _text(value, label, limit=180)

    if Path(value).name != value or value in {".", ".."}:
        raise ProposalDraftError(f"Invalid {label}.")

    return value


def _exact_keys(payload, expected):
    actual = set(payload)

    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        detail = []

        if missing:
            detail.append("missing=" + ",".join(missing))

        if extra:
            detail.append("extra=" + ",".join(extra))

        raise ProposalDraftError(
            "Invalid ProposalDraft payload keys: "
            + "; ".join(detail)
        )


@dataclass(frozen=True)
class ProposalDraft:
    proposal_type: ProposalDraftType
    target: str
    reason: str
    payload: Mapping[str, Any]
    idempotency_context: str
    requires_root_approval: bool = True

    def __post_init__(self):
        if not isinstance(self.proposal_type, ProposalDraftType):
            raise ProposalDraftError(
                "ProposalDraft type is not allowlisted."
            )

        _identifier(self.target, "proposal target")
        _text(self.reason, "proposal reason", limit=1000)

        if self.requires_root_approval is not True:
            raise ProposalDraftError(
                "ProposalDraft must require Root approval."
            )

        if (
            not isinstance(self.idempotency_context, str)
            or not _HEX_RE.fullmatch(self.idempotency_context)
        ):
            raise ProposalDraftError(
                "ProposalDraft idempotency context is invalid."
            )

        if not isinstance(self.payload, Mapping):
            raise ProposalDraftError(
                "ProposalDraft payload must be a mapping."
            )

        try:
            payload = json.loads(
                json.dumps(
                    dict(self.payload),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProposalDraftError(
                "ProposalDraft payload must be JSON-compatible."
            ) from exc

        self._validate_payload(payload)

        for key in ("context_refs", "capabilities"):
            if key in payload:
                payload[key] = tuple(payload[key])

        object.__setattr__(
            self,
            "payload",
            MappingProxyType(payload),
        )

    def _validate_payload(self, payload: Dict[str, Any]):
        if self.proposal_type == ProposalDraftType.CREATE_TASK:
            self._validate_task(payload)
        elif (
            self.proposal_type
            == ProposalDraftType.CREATE_PROJECT_REQUEST
        ):
            self._validate_project(payload)
        elif (
            self.proposal_type
            == ProposalDraftType.CREATE_AGENT_REQUEST
        ):
            self._validate_agent(payload)
        else:
            raise ProposalDraftError(
                "Unsupported ProposalDraft type."
            )

    def _validate_task(self, payload):
        _exact_keys(
            payload,
            {
                "task_id",
                "project_id",
                "phase",
                "task_type",
                "assigned_agent",
                "required_role",
                "required_capability",
                "description",
                "context_refs",
                "authorization_id",
                "auditor",
            },
        )

        for key in (
            "task_id",
            "project_id",
            "phase",
            "assigned_agent",
            "required_capability",
            "authorization_id",
            "auditor",
        ):
            _identifier(payload[key], key)

        _text(payload["description"], "task description")

        if payload["project_id"] != self.target:
            raise ProposalDraftError(
                "Task target does not match project_id."
            )

        if payload["task_type"] != "PRODUCE_ARTIFACT":
            raise ProposalDraftError(
                "Initial CREATE_TASK must produce an artifact."
            )

        if payload["required_role"] != "PRODUCER":
            raise ProposalDraftError(
                "Initial CREATE_TASK must use a PRODUCER."
            )

        if payload["required_capability"] != "produce_artifact":
            raise ProposalDraftError(
                "Initial CREATE_TASK capability is fixed."
            )

        if payload["assigned_agent"] == payload["auditor"]:
            raise ProposalDraftError(
                "Producer and auditor must be independent."
            )

        refs = payload["context_refs"]

        if not isinstance(refs, list) or not all(
            isinstance(ref, str)
            and ref
            and not _CONTROL_RE.search(ref)
            for ref in refs
        ):
            raise ProposalDraftError(
                "Task context_refs must be safe strings."
            )

    def _validate_project(self, payload):
        _exact_keys(
            payload,
            {
                "project_id",
                "title",
                "division",
                "owner",
                "phase",
                "lineage",
            },
        )
        _identifier(payload["project_id"], "project id")
        _identifier(payload["owner"], "project owner")
        _identifier(payload["phase"], "project phase")
        _text(payload["title"], "project title", limit=240)

        if payload["project_id"] != self.target:
            raise ProposalDraftError(
                "Project target does not match project_id."
            )

        if payload["division"] not in {
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        }:
            raise ProposalDraftError("Invalid project division.")

        if payload["lineage"] not in {
            "CANONICAL",
            "EXPERIMENTAL_NONCANONICAL",
            "HISTORICAL",
        }:
            raise ProposalDraftError("Invalid project lineage.")

    def _validate_agent(self, payload):
        _exact_keys(
            payload,
            {
                "agent_id",
                "division",
                "role",
                "capabilities",
                "status",
            },
        )
        _identifier(payload["agent_id"], "agent id")

        if payload["agent_id"] != self.target:
            raise ProposalDraftError(
                "Agent target does not match agent_id."
            )

        if payload["agent_id"] == "personal_root":
            raise ProposalDraftError(
                "Chat cannot draft changes to personal_root."
            )

        if payload["division"] not in {
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        }:
            raise ProposalDraftError("Invalid agent division.")

        if payload["role"] not in {
            "CONTROLLER",
            "PRODUCER",
            "AUDITOR",
            "VALIDATOR",
            "BUILDER",
            "SPECIALIST",
        }:
            raise ProposalDraftError("Invalid agent role.")

        if payload["status"] != "ACTIVE":
            raise ProposalDraftError(
                "New chat-drafted agents must start ACTIVE."
            )

        capabilities = payload["capabilities"]

        if (
            not isinstance(capabilities, list)
            or not capabilities
            or len(capabilities) > 20
        ):
            raise ProposalDraftError(
                "Agent capabilities are required."
            )

        for capability in capabilities:
            _identifier(capability, "agent capability")

        required = {
            "PRODUCER": "produce_artifact",
            "AUDITOR": "audit",
        }.get(payload["role"])

        if required and required not in capabilities:
            raise ProposalDraftError(
                f"Role {payload['role']} requires {required}."
            )

    def payload_dict(self):
        result = dict(self.payload)

        for key in ("context_refs", "capabilities"):
            if key in result:
                result[key] = list(result[key])

        return result

    @property
    def draft_id(self):
        evidence = {
            "proposal_type": self.proposal_type.value,
            "target": self.target,
            "reason": self.reason,
            "payload": self.payload_dict(),
            "idempotency_context": self.idempotency_context,
        }
        digest = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"DRAFT-{digest[:20]}"

    @property
    def proposal_id(self):
        return (
            f"CHAT-{self.proposal_type.value}-"
            f"{self.draft_id.removeprefix('DRAFT-')}"
        )


@dataclass(frozen=True)
class ProposalSubmission:
    proposal: Proposal
    path: Path
    pending_root: bool
    created: bool


class ProposalDraftSubmitter:
    """The only chat capability allowed to mutate pre-approval records."""

    CREATED_BY = "conversational_chief_of_staff"

    def __init__(self, vault):
        self.vault = vault
        self.factory = ProposalFactory(vault)

    @staticmethod
    def _metadata(path):
        try:
            parts = path.read_text(encoding="utf-8").split("---", 2)
            metadata = (
                yaml.safe_load(parts[1])
                if len(parts) >= 3
                else None
            )
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ProposalDraftError(
                f"Cannot verify submitted Proposal: {path}"
            ) from exc

        if not isinstance(metadata, dict):
            raise ProposalDraftError(
                f"Invalid submitted Proposal: {path}"
            )

        return metadata

    def submit(self, draft: ProposalDraft) -> ProposalSubmission:
        if not isinstance(draft, ProposalDraft):
            raise ProposalDraftError(
                "Submitter requires a typed ProposalDraft."
            )

        proposal = self.factory.create(
            {
                "action": draft.proposal_type.value,
                "target": draft.target,
                "reason": draft.reason,
                "payload": draft.payload_dict(),
            }
        )

        if not isinstance(proposal, Proposal):
            raise ProposalDraftError(
                "ProposalFactory rejected the typed draft."
            )

        proposal.proposal_id = draft.proposal_id
        inbox = self.vault.root / "00_ROOT" / "inbox"
        expected_path = inbox / (
            f"{proposal.proposal_id}_{proposal.target}.md"
        )
        existed = expected_path.exists()
        try:
            path = write_proposal(self.vault.root, proposal)
        except (OSError, KeyboardInterrupt) as exc:
            raise ProposalDraftCommitError(
                proposal.proposal_id,
                "the Proposal write was interrupted; inspect ROOT inbox",
            ) from exc

        try:
            metadata = self._metadata(path)
        except KeyboardInterrupt as exc:
            raise ProposalDraftCommitError(
                proposal.proposal_id,
                "the Proposal may exist but verification was interrupted",
            ) from exc

        for key, expected in {
            "proposal_id": proposal.proposal_id,
            "proposal_type": proposal.proposal_type,
            "target": proposal.target,
            "reason": proposal.reason,
            "created_by": self.CREATED_BY,
            "payload": proposal.payload,
        }.items():
            if metadata.get(key) != expected:
                raise ProposalDraftError(
                    "Proposal submission evidence conflict: "
                    f"{key}"
                )

        pending_root = path.parent == inbox
        allowed_states = (
            {
                ProposalState.CREATED.value,
                ProposalState.WAITING_ROOT.value,
            }
            if pending_root
            else {
                ProposalState.EXECUTED.value,
                ProposalState.REJECTED.value,
            }
        )

        if metadata.get("state") not in allowed_states:
            raise ProposalDraftError(
                "Proposal submission has an invalid governance state."
            )

        try:
            EventLedger(self.vault).append_once(
                Event(
                    event_id=(
                        f"EVT-{proposal.proposal_id}-DRAFTED"
                    ),
                    actor=self.CREATED_BY,
                    action="PROPOSAL_DRAFTED",
                    target=proposal.target,
                    result=EventResult.SUCCESS,
                    note=(
                        "Typed Proposal draft registered for Root review."
                    ),
                    correlation_id=proposal.proposal_id,
                    metadata={
                        "proposal_id": proposal.proposal_id,
                        "proposal_type": proposal.proposal_type,
                        "draft_id": draft.draft_id,
                    },
                )
            )
        except (
            EventConflictError,
            OSError,
            ValueError,
            KeyboardInterrupt,
        ) as exc:
            raise ProposalDraftCommitError(
                proposal.proposal_id,
                "the Proposal exists but its draft Event is incomplete",
            ) from exc

        return ProposalSubmission(
            proposal=Proposal.from_dict(metadata),
            path=path,
            pending_root=pending_root,
            created=(pending_root and not existed),
        )
