"""Durable project-local task records for the v1 control tower.

Task files are human-readable Markdown documents with YAML frontmatter.  The
project ``STATE.md`` remains the authority for project governance; tasks only
describe bounded units of work performed inside that governed project.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union
from uuid import uuid4

import yaml


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_identifier(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or Path(value).name != value
        or value in {".", ".."}
    ):
        raise ValueError("Invalid {0}: {1!r}".format(label, value))
    return value


def _mapping(value: Optional[Mapping[str, Any]], label: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("{0} must be a mapping.".format(label))
    return dict(value)


class TaskError(RuntimeError):
    """Base error for durable task operations."""


class TaskNotFoundError(TaskError):
    pass


class TaskConflictError(TaskError):
    pass


class TaskTransitionError(TaskError):
    pass


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


ALLOWED_TASK_TRANSITIONS = {
    TaskStatus.CREATED: {
        TaskStatus.ASSIGNED,
        TaskStatus.BLOCKED,
    },
    TaskStatus.ASSIGNED: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
    },
    # A failed or blocked task may be explicitly reassigned for another
    # attempt.  It never resumes RUNNING implicitly.
    TaskStatus.FAILED: {
        TaskStatus.ASSIGNED,
        TaskStatus.BLOCKED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.ASSIGNED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: set(),
}


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("Artifact path is required.")
        if not isinstance(self.sha256, str) or not self.sha256.strip():
            raise ValueError("Artifact SHA-256 is required.")
        object.__setattr__(
            self,
            "metadata",
            _mapping(self.metadata, "artifact metadata"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactRef":
        if not isinstance(data, Mapping):
            raise TypeError("Artifact reference must be a mapping.")
        return cls(
            path=data["path"],
            sha256=data["sha256"],
            metadata=data.get("metadata", {}),
        )


def _artifact_refs(
    values: Optional[Iterable[Union[ArtifactRef, Mapping[str, Any]]]]
) -> List[ArtifactRef]:
    refs: List[ArtifactRef] = []
    for value in values or []:
        if isinstance(value, ArtifactRef):
            refs.append(value)
        else:
            refs.append(ArtifactRef.from_dict(value))
    return refs


@dataclass
class Task:
    task_id: str
    project_id: str
    phase: str
    task_type: str
    assigned_agent: str
    required_role: str
    required_capability: str
    description: str = ""
    status: TaskStatus = TaskStatus.CREATED
    request_path: Optional[str] = None
    input_artifacts: List[ArtifactRef] = field(default_factory=list)
    output_artifacts: List[ArtifactRef] = field(default_factory=list)
    context_refs: List[str] = field(default_factory=list)
    authorization_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    causation_event_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    attempt: int = 0
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.task_id = _safe_identifier(self.task_id, "task id")
        self.project_id = _safe_identifier(self.project_id, "project id")

        for label, value in (
            ("phase", self.phase),
            ("task type", self.task_type),
            ("assigned agent", self.assigned_agent),
            ("required role", self.required_role),
            ("required capability", self.required_capability),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("{0} is required.".format(label.capitalize()))

        if not isinstance(self.status, TaskStatus):
            self.status = TaskStatus(self.status)

        if not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError("Task attempt must be a non-negative integer.")

        self.input_artifacts = _artifact_refs(self.input_artifacts)
        self.output_artifacts = _artifact_refs(self.output_artifacts)
        self.context_refs = list(self.context_refs or [])
        if not all(isinstance(value, str) and value for value in self.context_refs):
            raise ValueError("Task context references must be non-empty strings.")

        self.result = _mapping(self.result, "task result")
        self.metadata = _mapping(self.metadata, "task metadata")
        self.idempotency_key = self.idempotency_key or self.task_id
        self.created_at = self.created_at or utc_now()
        self.updated_at = self.updated_at or self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "phase": self.phase,
            "task_type": self.task_type,
            "assigned_agent": self.assigned_agent,
            "required_role": self.required_role,
            "required_capability": self.required_capability,
            "description": self.description,
            "status": self.status.value,
            "request_path": self.request_path,
            "input_artifacts": [ref.to_dict() for ref in self.input_artifacts],
            "output_artifacts": [ref.to_dict() for ref in self.output_artifacts],
            "context_refs": list(self.context_refs),
            "authorization_id": self.authorization_id,
            "parent_task_id": self.parent_task_id,
            "causation_event_id": self.causation_event_id,
            "idempotency_key": self.idempotency_key,
            "attempt": self.attempt,
            "result": dict(self.result),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        if not isinstance(data, Mapping):
            raise TypeError("Task data must be a mapping.")
        return cls(
            task_id=data["task_id"],
            project_id=data["project_id"],
            phase=data["phase"],
            task_type=data["task_type"],
            assigned_agent=data["assigned_agent"],
            required_role=data["required_role"],
            required_capability=data["required_capability"],
            description=data.get("description", ""),
            status=data.get("status", TaskStatus.CREATED.value),
            request_path=data.get("request_path"),
            input_artifacts=data.get("input_artifacts", []),
            output_artifacts=data.get("output_artifacts", []),
            context_refs=data.get("context_refs", []),
            authorization_id=data.get("authorization_id"),
            parent_task_id=data.get("parent_task_id"),
            causation_event_id=data.get("causation_event_id"),
            idempotency_key=data.get("idempotency_key"),
            attempt=data.get("attempt", 0),
            result=data.get("result", {}),
            error=data.get("error"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            metadata=data.get("metadata", {}),
        )

    def evidence_dict(self) -> Dict[str, Any]:
        """Return immutable creation evidence used for idempotency checks."""

        immutable_metadata = {
            key: value
            for key, value in self.metadata.items()
            if key not in {
                "recovery_history",
                "reconciliation_history",
            }
        }

        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "phase": self.phase,
            "task_type": self.task_type,
            "assigned_agent": self.assigned_agent,
            "required_role": self.required_role,
            "required_capability": self.required_capability,
            "description": self.description,
            "request_path": self.request_path,
            "input_artifacts": [ref.to_dict() for ref in self.input_artifacts],
            "context_refs": list(self.context_refs),
            "authorization_id": self.authorization_id,
            "parent_task_id": self.parent_task_id,
            "causation_event_id": self.causation_event_id,
            "idempotency_key": self.idempotency_key,
            "metadata": immutable_metadata,
        }

    @property
    def created_event(self) -> Optional[str]:
        return self.causation_event_id


class TaskStore:
    """Persist and transition tasks inside one project directory."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.tasks_dir = self.project_dir / "tasks"

    def path_for(self, task_id: str) -> Path:
        return self.tasks_dir / (
            _safe_identifier(task_id, "task id") + ".md"
        )

    def _validate_project(self, task: Task) -> None:
        if self.project_dir.name != task.project_id:
            raise TaskConflictError(
                "Task project does not match its storage directory: "
                "{0} != {1}".format(task.project_id, self.project_dir.name)
            )

    @staticmethod
    def _render(task: Task) -> str:
        metadata = yaml.safe_dump(
            task.to_dict(),
            sort_keys=False,
            allow_unicode=True,
        )
        body = """# Task {task_id}

- Project: `{project_id}`
- Phase: `{phase}`
- Type: `{task_type}`
- Assigned agent: `{assigned_agent}`
- Required role: `{required_role}`
- Required capability: `{required_capability}`
- Status: `{status}`
- Attempt: `{attempt}`

## Description

{description}
""".format(
            task_id=task.task_id,
            project_id=task.project_id,
            phase=task.phase,
            task_type=task.task_type,
            assigned_agent=task.assigned_agent,
            required_role=task.required_role,
            required_capability=task.required_capability,
            status=task.status.value,
            attempt=task.attempt,
            description=task.description or "None.",
        )
        return "---\n" + metadata + "---\n" + body

    @staticmethod
    def _read(path: Path) -> Task:
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise TaskError("Missing YAML frontmatter: {0}".format(path))
        data = yaml.safe_load(parts[1])
        if not isinstance(data, Mapping):
            raise TaskError("Invalid task metadata: {0}".format(path))
        return Task.from_dict(data)

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

    def create(self, task: Task) -> Task:
        self._validate_project(task)
        if task.status != TaskStatus.CREATED:
            raise TaskTransitionError(
                "A new task must start in CREATED."
            )

        path = self.path_for(task.task_id)
        if self._exclusive_create(path, self._render(task)):
            return task

        existing = self._read(path)
        if existing.evidence_dict() != task.evidence_dict():
            raise TaskConflictError(
                "Task id already exists with different evidence: {0}".format(
                    task.task_id
                )
            )
        return existing

    ensure = create

    def get(self, task_id: str) -> Task:
        path = self.path_for(task_id)
        if not path.exists():
            raise TaskNotFoundError("Task not found: {0}".format(task_id))
        task = self._read(path)
        self._validate_project(task)
        return task

    def list(
        self,
        status: Optional[Union[TaskStatus, str]] = None,
        assigned_agent: Optional[str] = None,
    ) -> List[Task]:
        wanted_status = TaskStatus(status) if status is not None else None
        if not self.tasks_dir.exists():
            return []

        tasks = []
        for path in sorted(self.tasks_dir.glob("*.md")):
            task = self._read(path)
            self._validate_project(task)
            if wanted_status is not None and task.status != wanted_status:
                continue
            if assigned_agent is not None and task.assigned_agent != assigned_agent:
                continue
            tasks.append(task)
        return tasks

    def transition(
        self,
        task_id: str,
        new_status: Union[TaskStatus, str],
        expected_status: Optional[Union[TaskStatus, str]] = None,
        result: Optional[Mapping[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Task:
        task = self.get(task_id)
        target = TaskStatus(new_status)

        if expected_status is not None and task.status != TaskStatus(expected_status):
            raise TaskTransitionError(
                "Task status changed: expected {0}, found {1}.".format(
                    TaskStatus(expected_status).value,
                    task.status.value,
                )
            )

        # Replaying the exact terminal/state update is safe.  Different
        # evidence under the same transition is a conflict, not an overwrite.
        if task.status == target:
            if result is not None and task.result != dict(result):
                raise TaskConflictError(
                    "Repeated task transition has a different result."
                )
            if error is not None and task.error != error:
                raise TaskConflictError(
                    "Repeated task transition has a different error."
                )
            return task

        if target not in ALLOWED_TASK_TRANSITIONS[task.status]:
            raise TaskTransitionError(
                "Illegal task transition: {0} -> {1}".format(
                    task.status.value,
                    target.value,
                )
            )

        if result is not None and target != TaskStatus.COMPLETED:
            raise TaskTransitionError(
                "Task results may only be recorded on COMPLETED."
            )
        if error is not None and target not in {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
        }:
            raise TaskTransitionError(
                "Task errors may only be recorded on FAILED or BLOCKED."
            )
        if target == TaskStatus.FAILED and not error:
            raise TaskTransitionError("FAILED requires an error message.")

        updated = replace(
            task,
            status=target,
            attempt=(task.attempt + 1 if target == TaskStatus.RUNNING else task.attempt),
            result=(dict(result) if target == TaskStatus.COMPLETED and result is not None else task.result),
            output_artifacts=(
                _artifact_refs(
                    dict(result).get("output_artifacts", [])
                )
                if target == TaskStatus.COMPLETED
                and result is not None
                else task.output_artifacts
            ),
            error=(
                error
                if target in {TaskStatus.FAILED, TaskStatus.BLOCKED}
                else None
            ),
            updated_at=utc_now(),
        )
        self._atomic_replace(self.path_for(task_id), self._render(updated))
        return updated

    def assign(self, task_id: str) -> Task:
        return self.transition(task_id, TaskStatus.ASSIGNED)

    def start(self, task_id: str) -> Task:
        return self.transition(task_id, TaskStatus.RUNNING)

    def complete(
        self,
        task_id: str,
        result: Optional[Mapping[str, Any]] = None,
    ) -> Task:
        return self.transition(
            task_id,
            TaskStatus.COMPLETED,
            result=result or {},
        )

    def fail(self, task_id: str, error: str) -> Task:
        return self.transition(task_id, TaskStatus.FAILED, error=error)

    def block(self, task_id: str, reason: Optional[str] = None) -> Task:
        return self.transition(task_id, TaskStatus.BLOCKED, error=reason)

    def recover_for_retry(
        self,
        task_id: str,
        reason: str = "Explicit retry requested.",
    ) -> Task:
        """Explicitly recover interrupted or failed work to ASSIGNED.

        RUNNING work is first persisted as FAILED.  Recovery evidence is kept
        in task metadata so reassignment does not erase why the prior attempt
        stopped.  A reconciler never calls this implicitly.
        """

        task = self.get(task_id)

        if task.status == TaskStatus.ASSIGNED:
            return task

        previous_status = task.status
        previous_error = task.error

        if task.status == TaskStatus.RUNNING:
            task = self.fail(task_id, reason)

        if task.status not in {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
        }:
            raise TaskTransitionError(
                "Task retry requires RUNNING, FAILED, BLOCKED, "
                f"or ASSIGNED; found {task.status.value}."
            )

        stored_history = task.metadata.get(
            "recovery_history",
            [],
        )

        if not isinstance(stored_history, list):
            raise TaskConflictError(
                "Task recovery history must be a list."
            )

        history = list(stored_history)
        history.append(
            {
                "recovery_number": len(history) + 1,
                "attempt": task.attempt,
                "previous_status": previous_status.value,
                "reason": previous_error or reason,
                "recovered_at": utc_now(),
            }
        )
        updated = replace(
            task,
            status=TaskStatus.ASSIGNED,
            error=None,
            updated_at=utc_now(),
            metadata={
                **task.metadata,
                "recovery_history": history,
            },
        )
        self._atomic_replace(
            self.path_for(task_id),
            self._render(updated),
        )
        return updated

    def reconcile_completion(
        self,
        task_id: str,
        result: Mapping[str, Any],
        reason: str,
    ) -> Task:
        """Complete a task from already-committed governed evidence.

        This is deliberately separate from ordinary transitions.  A caller
        must first validate immutable project evidence; this method only
        repairs the task record without re-running the side effect.
        """

        task = self.get(task_id)
        completed_result = _mapping(
            result,
            "reconciled task result",
        )

        if task.status == TaskStatus.COMPLETED:
            if task.result != completed_result:
                raise TaskConflictError(
                    "Completed task conflicts with reconciled evidence."
                )

            return task

        if task.status not in {
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
        }:
            raise TaskTransitionError(
                "Task reconciliation requires assigned or attempted "
                f"work; found {task.status.value}."
            )

        stored_history = task.metadata.get(
            "reconciliation_history",
            [],
        )

        if not isinstance(stored_history, list):
            raise TaskConflictError(
                "Task reconciliation history must be a list."
            )

        history = list(stored_history)
        history.append(
            {
                "reconciliation_number": len(history) + 1,
                "attempt": task.attempt,
                "previous_status": task.status.value,
                "reason": reason,
                "reconciled_at": utc_now(),
            }
        )
        updated = replace(
            task,
            status=TaskStatus.COMPLETED,
            result=completed_result,
            output_artifacts=_artifact_refs(
                completed_result.get(
                    "output_artifacts",
                    [],
                )
            ),
            error=None,
            updated_at=utc_now(),
            metadata={
                **task.metadata,
                "reconciliation_history": history,
            },
        )
        self._atomic_replace(
            self.path_for(task_id),
            self._render(updated),
        )
        return updated
