"""Immutable project-local handoff evidence with explicit acknowledgement."""

from dataclasses import dataclass, field, replace
from enum import Enum
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union
from uuid import uuid4

import yaml

from .tasks import ArtifactRef, _artifact_refs, _mapping, _safe_identifier, utc_now


class HandoffError(RuntimeError):
    """Base error for durable handoff operations."""


class HandoffNotFoundError(HandoffError):
    pass


class HandoffConflictError(HandoffError):
    pass


class HandoffStatus(str, Enum):
    CREATED = "CREATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


@dataclass
class Handoff:
    handoff_id: str
    project_id: str
    sender: str
    receiver: str
    reason: str
    artifact_refs: List[ArtifactRef] = field(default_factory=list)
    context_refs: List[str] = field(default_factory=list)
    status: HandoffStatus = HandoffStatus.CREATED
    task_id: Optional[str] = None
    phase: Optional[str] = None
    authorization_id: Optional[str] = None
    timestamp: str = ""
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.handoff_id = _safe_identifier(self.handoff_id, "handoff id")
        self.project_id = _safe_identifier(self.project_id, "project id")

        for label, value in (
            ("sender", self.sender),
            ("receiver", self.receiver),
            ("reason", self.reason),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Handoff {0} is required.".format(label))

        if self.task_id is not None:
            self.task_id = _safe_identifier(self.task_id, "task id")

        if not isinstance(self.status, HandoffStatus):
            self.status = HandoffStatus(self.status)

        self.artifact_refs = _artifact_refs(self.artifact_refs)
        self.context_refs = list(self.context_refs or [])
        if not all(isinstance(value, str) and value for value in self.context_refs):
            raise ValueError("Handoff context references must be non-empty strings.")

        self.metadata = _mapping(self.metadata, "handoff metadata")
        self.timestamp = self.timestamp or utc_now()

        if self.status == HandoffStatus.ACKNOWLEDGED:
            if not self.acknowledged_at or not self.acknowledged_by:
                raise ValueError(
                    "Acknowledged handoff requires acknowledgement evidence."
                )
            if self.acknowledged_by != self.receiver:
                raise ValueError(
                    "Only the designated receiver may acknowledge a handoff."
                )
        elif self.acknowledged_at is not None or self.acknowledged_by is not None:
            raise ValueError(
                "Unacknowledged handoff cannot contain acknowledgement evidence."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "project_id": self.project_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "reason": self.reason,
            "artifact_refs": [ref.to_dict() for ref in self.artifact_refs],
            "context_refs": list(self.context_refs),
            "status": self.status.value,
            "task_id": self.task_id,
            "phase": self.phase,
            "authorization_id": self.authorization_id,
            "timestamp": self.timestamp,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Handoff":
        if not isinstance(data, Mapping):
            raise TypeError("Handoff data must be a mapping.")
        return cls(
            handoff_id=data["handoff_id"],
            project_id=data["project_id"],
            sender=data["sender"],
            receiver=data["receiver"],
            reason=data["reason"],
            artifact_refs=data.get("artifact_refs", []),
            context_refs=data.get("context_refs", []),
            status=data.get("status", HandoffStatus.CREATED.value),
            task_id=data.get("task_id"),
            phase=data.get("phase"),
            authorization_id=data.get("authorization_id"),
            timestamp=data.get("timestamp", ""),
            acknowledged_at=data.get("acknowledged_at"),
            acknowledged_by=data.get("acknowledged_by"),
            metadata=data.get("metadata", {}),
        )

    def evidence_dict(self) -> Dict[str, Any]:
        """Return immutable routing/evidence fields for idempotency checks."""

        return {
            "handoff_id": self.handoff_id,
            "project_id": self.project_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "reason": self.reason,
            "artifact_refs": [ref.to_dict() for ref in self.artifact_refs],
            "context_refs": list(self.context_refs),
            "task_id": self.task_id,
            "phase": self.phase,
            "authorization_id": self.authorization_id,
            "metadata": dict(self.metadata),
        }


class HandoffStore:
    """Persist handoffs under ``<project>/handoffs``."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.handoffs_dir = self.project_dir / "handoffs"

    def path_for(self, handoff_id: str) -> Path:
        return self.handoffs_dir / (
            _safe_identifier(handoff_id, "handoff id") + ".md"
        )

    def _validate_project(self, handoff: Handoff) -> None:
        if self.project_dir.name != handoff.project_id:
            raise HandoffConflictError(
                "Handoff project does not match its storage directory: "
                "{0} != {1}".format(
                    handoff.project_id,
                    self.project_dir.name,
                )
            )

    @staticmethod
    def _render(handoff: Handoff) -> str:
        metadata = yaml.safe_dump(
            handoff.to_dict(),
            sort_keys=False,
            allow_unicode=True,
        )
        artifacts = "\n".join(
            "- `{0}` (`{1}`)".format(ref.path, ref.sha256)
            for ref in handoff.artifact_refs
        ) or "- None"
        contexts = "\n".join(
            "- `{0}`".format(value) for value in handoff.context_refs
        ) or "- None"
        body = """# Handoff {handoff_id}

- Project: `{project_id}`
- Sender: `{sender}`
- Receiver: `{receiver}`
- Status: `{status}`
- Timestamp: `{timestamp}`

## Reason

{reason}

## Artifact References

{artifacts}

## Context References

{contexts}
""".format(
            handoff_id=handoff.handoff_id,
            project_id=handoff.project_id,
            sender=handoff.sender,
            receiver=handoff.receiver,
            status=handoff.status.value,
            timestamp=handoff.timestamp,
            reason=handoff.reason,
            artifacts=artifacts,
            contexts=contexts,
        )
        return "---\n" + metadata + "---\n" + body

    @staticmethod
    def _read(path: Path) -> Handoff:
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise HandoffError(
                "Missing YAML frontmatter: {0}".format(path)
            )
        data = yaml.safe_load(parts[1])
        if not isinstance(data, Mapping):
            raise HandoffError("Invalid handoff metadata: {0}".format(path))
        return Handoff.from_dict(data)

    @staticmethod
    def _atomic_replace(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            ".{0}.{1}.tmp".format(path.name, uuid4().hex)
        )
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _exclusive_create(path: Path, content: str) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            return True
        except FileExistsError:
            return False

    def create(self, handoff: Handoff) -> Handoff:
        self._validate_project(handoff)
        if handoff.status != HandoffStatus.CREATED:
            raise HandoffConflictError(
                "A new handoff must start in CREATED."
            )

        path = self.path_for(handoff.handoff_id)
        if self._exclusive_create(path, self._render(handoff)):
            return handoff

        existing = self._read(path)
        if existing.evidence_dict() != handoff.evidence_dict():
            raise HandoffConflictError(
                "Handoff id already exists with different evidence: {0}".format(
                    handoff.handoff_id
                )
            )
        return existing

    ensure = create

    def get(self, handoff_id: str) -> Handoff:
        path = self.path_for(handoff_id)
        if not path.exists():
            raise HandoffNotFoundError(
                "Handoff not found: {0}".format(handoff_id)
            )
        handoff = self._read(path)
        self._validate_project(handoff)
        return handoff

    def list(
        self,
        status: Optional[Union[HandoffStatus, str]] = None,
        receiver: Optional[str] = None,
    ) -> List[Handoff]:
        wanted_status = HandoffStatus(status) if status is not None else None
        if not self.handoffs_dir.exists():
            return []

        handoffs = []
        for path in sorted(self.handoffs_dir.glob("*.md")):
            handoff = self._read(path)
            self._validate_project(handoff)
            if wanted_status is not None and handoff.status != wanted_status:
                continue
            if receiver is not None and handoff.receiver != receiver:
                continue
            handoffs.append(handoff)
        return handoffs

    def acknowledge(
        self,
        handoff_id: str,
        receiver: str,
        timestamp: Optional[str] = None,
    ) -> Handoff:
        handoff = self.get(handoff_id)
        if receiver != handoff.receiver:
            raise HandoffConflictError(
                "Only the designated receiver may acknowledge this handoff."
            )

        if handoff.status == HandoffStatus.ACKNOWLEDGED:
            if handoff.acknowledged_by != receiver:
                raise HandoffConflictError(
                    "Handoff was acknowledged by a different receiver."
                )
            return handoff

        acknowledged = replace(
            handoff,
            status=HandoffStatus.ACKNOWLEDGED,
            acknowledged_at=timestamp or utc_now(),
            acknowledged_by=receiver,
        )
        self._atomic_replace(
            self.path_for(handoff_id),
            self._render(acknowledged),
        )
        return acknowledged

