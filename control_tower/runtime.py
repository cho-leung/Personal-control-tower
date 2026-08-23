"""Agent execution boundary and deterministic, credential-free test runtime."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .tasks import Task, TaskStatus, _mapping


AUDIT_VERDICTS = {
    "PASS",
    "PASS_WITH_REPAIRS",
    "FAIL",
}


class AgentRuntimeError(RuntimeError):
    pass


@dataclass
class AgentResult:
    task_id: str
    agent_id: str
    output_text: str = ""
    artifact_text: Optional[str] = None
    audit_verdict: Optional[str] = None
    audit_text: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("Agent result task id is required.")
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValueError("Agent result agent id is required.")
        self.metadata = _mapping(self.metadata, "agent result metadata")
        if self.audit_verdict is not None:
            self.audit_verdict = self.audit_verdict.upper()
            if self.audit_verdict not in AUDIT_VERDICTS:
                raise ValueError(
                    "Unknown audit verdict: {0}".format(self.audit_verdict)
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "output_text": self.output_text,
            "artifact_text": self.artifact_text,
            "audit_verdict": self.audit_verdict,
            "audit_text": self.audit_text,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentResult":
        if not isinstance(data, Mapping):
            raise TypeError("Agent result data must be a mapping.")
        return cls(
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            output_text=data.get("output_text", ""),
            artifact_text=data.get("artifact_text"),
            audit_verdict=data.get("audit_verdict"),
            audit_text=data.get("audit_text"),
            metadata=data.get("metadata", {}),
        )


class AgentRuntime(ABC):
    """Execution adapter.  It returns data and never mutates tower state."""

    @abstractmethod
    def execute(
        self,
        task: Task,
        context: Mapping[str, Any],
    ) -> AgentResult:
        raise NotImplementedError


class MockAgentRuntime(AgentRuntime):
    """Deterministic runtime for local tests and offline development.

    It deliberately has no API key, network client, or filesystem authority.
    The caller remains responsible for validating the result and committing it
    through the control-tower bus.
    """

    def __init__(
        self,
        producer_output: str = "Mock producer artifact.",
        auditor_verdict: str = "PASS",
        auditor_notes: str = "Mock independent audit completed.",
        generic_output: str = "Mock task completed.",
        metadata: Optional[Mapping[str, Any]] = None,
    ):
        if not isinstance(producer_output, str):
            raise TypeError("producer_output must be text.")
        if not isinstance(auditor_notes, str):
            raise TypeError("auditor_notes must be text.")
        if not isinstance(generic_output, str):
            raise TypeError("generic_output must be text.")

        verdict = auditor_verdict.upper()
        if verdict not in AUDIT_VERDICTS:
            raise ValueError(
                "Unknown audit verdict: {0}".format(auditor_verdict)
            )

        self.producer_output = producer_output
        self.auditor_verdict = verdict
        self.auditor_notes = auditor_notes
        self.generic_output = generic_output
        self.metadata = _mapping(metadata, "mock runtime metadata")

    @staticmethod
    def _task_kind(task: Task) -> str:
        role = task.required_role.upper()
        task_type = task.task_type.upper()
        capability = task.required_capability.upper()

        if role == "PRODUCER" or task_type in {
            "PRODUCER",
            "PRODUCE",
            "PRODUCE_ARTIFACT",
        } or capability in {"PRODUCE", "PRODUCE_ARTIFACT"}:
            return "PRODUCER"

        if role == "AUDITOR" or task_type in {
            "AUDITOR",
            "AUDIT",
            "INDEPENDENT_AUDIT",
        } or capability == "AUDIT":
            return "AUDITOR"

        return "GENERIC"

    def execute(
        self,
        task: Task,
        context: Mapping[str, Any],
    ) -> AgentResult:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task.")
        if not isinstance(context, Mapping):
            raise TypeError("context must be a mapping.")
        if task.status not in {TaskStatus.ASSIGNED, TaskStatus.RUNNING}:
            raise AgentRuntimeError(
                "Runtime execution requires ASSIGNED or RUNNING task."
            )

        result_metadata = dict(self.metadata)
        result_metadata.update(
            {
                "runtime": "mock",
                "task_type": task.task_type,
            }
        )

        kind = self._task_kind(task)
        if kind == "PRODUCER":
            return AgentResult(
                task_id=task.task_id,
                agent_id=task.assigned_agent,
                output_text=self.producer_output,
                artifact_text=self.producer_output,
                metadata=result_metadata,
            )

        if kind == "AUDITOR":
            return AgentResult(
                task_id=task.task_id,
                agent_id=task.assigned_agent,
                output_text=self.auditor_notes,
                audit_verdict=self.auditor_verdict,
                audit_text=self.auditor_notes,
                metadata=result_metadata,
            )

        return AgentResult(
            task_id=task.task_id,
            agent_id=task.assigned_agent,
            output_text=self.generic_output,
            metadata=result_metadata,
        )

