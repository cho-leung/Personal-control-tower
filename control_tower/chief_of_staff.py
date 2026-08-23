import json
from pathlib import Path

from .agents import AgentRegistry, AgentRole, AgentStatus
from .core.audit_engine import AuditEngine
from .core.audit_request_engine import validate_audit_request
from .core.execution_engine import ExecutionEngine
from .events import Event, EventLedger, EventResult
from .guardrails import GovernanceError
from .handoffs import HandoffStatus, HandoffStore
from .models import AuditVerdict, Role, State
from .runner import AutomatonRunner
from .runtime import MockAgentRuntime
from .tasks import TaskStatus, TaskStore


class ChiefOfStaff:
    """Deterministic local coordinator; it never grants authorization."""

    def __init__(self, vault, runtime=None):
        self.vault = vault
        self.vault.ensure_structure()
        self.registry = AgentRegistry(vault.root)
        self.events = EventLedger(vault)
        self.runtime = runtime or MockAgentRuntime()
        self.automaton = AutomatonRunner(vault)
        self.execution_engine = ExecutionEngine(
            vault,
            self.registry,
            self.events,
        )
        self.audit_engine = AuditEngine(
            vault,
            self.registry,
            self.events,
        )

    def _require_active_root(self):
        root = self.registry.get("personal_root")

        if (
            not root
            or root.status != AgentStatus.ACTIVE
            or root.role != AgentRole.ROOT
            or "approve" not in root.capabilities
        ):
            raise GovernanceError(
                "Task recovery requires an ACTIVE personal_root "
                "with approve capability."
            )

    def _record_root_recovery_event(
        self,
        task,
        history_key,
        action,
    ):
        history = task.metadata.get(history_key, [])

        if not history:
            return

        recovery = history[-1]
        number_key = (
            "recovery_number"
            if history_key == "recovery_history"
            else "reconciliation_number"
        )
        number = recovery[number_key]
        self.events.append_once(
            Event(
                event_id=(
                    f"EVT-{task.task_id}-{action}-{number}"
                ),
                actor="personal_root",
                action=action,
                target=task.project_id,
                result=EventResult.SUCCESS,
                capability_checked="approve",
                note=recovery["reason"],
                correlation_id=task.task_id,
                metadata={
                    "task_id": task.task_id,
                    "previous_status": recovery[
                        "previous_status"
                    ],
                    "attempt": recovery["attempt"],
                    number_key: number,
                },
            )
        )

    def _reconcile_producer_task(
        self,
        state_path,
        task,
    ):
        if task.required_role != Role.PRODUCER.value:
            return None

        # A frozen project artifact may only repair the Task that actually
        # entered RUNNING. An unstarted sibling Task cannot claim it.
        if task.attempt < 1:
            return None

        project_dir = state_path.parent
        artifact_path = (
            project_dir
            / "artifacts"
            / f"{task.phase}_producer_artifact.txt"
        )

        if not artifact_path.is_file():
            return None

        artifact_sha256 = self.vault.freeze_artifact(
            artifact_path
        )
        artifact_reference = str(
            artifact_path.relative_to(self.vault.root)
        )
        state = self.vault.read_state(state_path)
        event = Event(
            event_id=(
                f"EVT-{task.project_id}-PRODUCE_COMPLETE-"
                f"{task.phase}-{artifact_sha256[:12]}"
            ),
            actor=task.assigned_agent,
            action="PRODUCE_ARTIFACT",
            target=task.project_id,
            result=EventResult.SUCCESS,
            capability_checked="produce_artifact",
            correlation_id=task.task_id,
            causation_id=task.causation_event_id,
            metadata={"task_id": task.task_id},
        )
        event_committed = self.events.contains(
            event.event_id
        )
        state_committed = (
            state.project_id == task.project_id
            and state.phase == task.phase
            and state.owner == task.assigned_agent
            and state.artifact_path == artifact_reference
            and state.artifact_sha256 == artifact_sha256
            and state.state not in {
                State.PROPOSED,
                State.READY,
                State.AUTHORIZED,
                State.ACTIVE,
            }
        )

        if not state_committed and not event_committed:
            return None

        if (
            state_committed
            and not event_committed
            and not self._is_unambiguous_state_only_task(
                state_path,
                state,
                task,
            )
        ):
            return None

        if (
            state_committed
            and task.authorization_id
            and state.authorization_id
            and task.authorization_id
            != state.authorization_id
        ):
            raise GovernanceError(
                "Committed producer evidence has another authorization."
            )

        expected_auditor = task.metadata.get("auditor")

        if (
            state_committed
            and expected_auditor
            and state.auditor != expected_auditor
        ):
            raise GovernanceError(
                "Committed producer evidence has another auditor."
            )

        # Existing events are validated byte-for-byte (except timestamp);
        # a state commit with a missing event repairs that exact event.
        self.events.append_once(event)
        result = {
            "runtime": {
                "recovered_from": (
                    "frozen_project_evidence"
                ),
            },
            "output_text": artifact_path.read_text(
                encoding="utf-8"
            ),
            "output_artifacts": [
                {
                    "path": artifact_reference,
                    "sha256": artifact_sha256,
                    "metadata": {
                        "kind": "producer_artifact",
                    },
                }
            ],
        }
        return TaskStore(project_dir).reconcile_completion(
            task.task_id,
            result,
            "Recovered from committed frozen artifact evidence.",
        )

    @staticmethod
    def _was_interrupted_running(task):
        history = task.metadata.get(
            "recovery_history",
            [],
        )
        return bool(
            history
            and history[-1].get("previous_status")
            == TaskStatus.RUNNING.value
        )

    def _is_unambiguous_state_only_task(
        self,
        state_path,
        state,
        task,
    ):
        """Resolve ownership when state committed before its event.

        With no correlation event, current RUNNING evidence is strongest. If
        no Task remains RUNNING, only a single prior attempted Task may repair
        the state-only commit. Any ambiguity fails closed.
        """

        candidates = []

        for candidate in TaskStore(
            state_path.parent
        ).list():
            if (
                candidate.project_id != state.project_id
                or candidate.phase != state.phase
                or candidate.required_role
                != Role.PRODUCER.value
                or candidate.required_capability
                != "produce_artifact"
                or candidate.assigned_agent != state.owner
                or candidate.attempt < 1
            ):
                continue

            if (
                candidate.authorization_id
                and state.authorization_id
                and candidate.authorization_id
                != state.authorization_id
            ):
                continue

            expected_auditor = candidate.metadata.get(
                "auditor"
            )

            if (
                expected_auditor
                and state.auditor != expected_auditor
            ):
                continue

            candidates.append(candidate)

        running = [
            candidate
            for candidate in candidates
            if candidate.status == TaskStatus.RUNNING
        ]

        if running:
            return (
                len(running) == 1
                and running[0].task_id == task.task_id
            )

        attempted = [
            candidate
            for candidate in candidates
            if candidate.status in {
                TaskStatus.FAILED,
                TaskStatus.BLOCKED,
            }
            or (
                candidate.status == TaskStatus.ASSIGNED
                and self._was_interrupted_running(
                    candidate
                )
            )
        ]
        return (
            len(attempted) == 1
            and attempted[0].task_id == task.task_id
        )

    def recover_task(
        self,
        state_path,
        task_id,
        reason="Explicit Root retry requested.",
    ):
        """Reconcile committed work, otherwise prepare an explicit retry."""

        self._require_active_root()
        store = TaskStore(state_path.parent)
        task = store.get(task_id)

        if task.status == TaskStatus.COMPLETED:
            if task.required_role == Role.AUDITOR.value:
                self.audit_engine.reconcile_task_from_evidence(
                    state_path,
                    task_id,
                )

            completed = store.get(task_id)
            self._write_run_record(
                completed,
                "SUCCEEDED",
                completed.result,
            )
            self._record_task_event(
                completed,
                "TASK_COMPLETED",
                EventResult.SUCCESS,
                "Task runtime completed.",
            )
            self._record_root_recovery_event(
                completed,
                "reconciliation_history",
                "TASK_RECONCILED",
            )
            return completed

        completed = self._reconcile_producer_task(
            state_path,
            task,
        )

        if (
            completed is None
            and task.required_role == Role.AUDITOR.value
        ):
            completed = (
                self.audit_engine.reconcile_task_from_evidence(
                    state_path,
                    task_id,
                )
            )

        if completed is not None:
            self._write_run_record(
                completed,
                "SUCCEEDED",
                completed.result,
            )
            self._record_task_event(
                completed,
                "TASK_COMPLETED",
                EventResult.SUCCESS,
                "Task runtime completed.",
            )
            self._record_root_recovery_event(
                completed,
                "reconciliation_history",
                "TASK_RECONCILED",
            )
            return completed

        recovered = store.recover_for_retry(
            task_id,
            reason,
        )
        self._record_root_recovery_event(
            recovered,
            "recovery_history",
            "TASK_RETRIED",
        )
        return recovered

    def _state_paths(self):
        paths = []

        for division in (
            "01_RESEARCH",
            "02_BUSINESS",
            "03_PERSONAL_GROWTH",
        ):
            paths.extend(
                sorted(
                    (self.vault.root / division).glob(
                        "*/STATE.md"
                    )
                )
            )

        return paths

    def _validate_agent(self, task, state, state_path):
        if task.project_id != state.project_id:
            raise GovernanceError(
                "Task project does not match project state."
            )

        if task.phase != state.phase:
            raise GovernanceError(
                "Task phase does not match project state."
            )

        agent = self.registry.get(task.assigned_agent)

        if not agent:
            raise GovernanceError(
                f"Unknown task agent: {task.assigned_agent}"
            )

        if agent.status != AgentStatus.ACTIVE:
            raise GovernanceError(
                f"Inactive task agent: {task.assigned_agent}"
            )

        if agent.role.value != task.required_role:
            raise GovernanceError(
                "Task role does not match agent registry."
            )

        if task.required_capability not in agent.capabilities:
            raise GovernanceError(
                "Task agent lacks required capability: "
                f"{task.required_capability}"
            )

        bound = []

        for role, members in (state.agents or {}).items():
            role_value = getattr(role, "value", role)

            if str(role_value).upper() == task.required_role:
                bound.extend(
                    [members]
                    if isinstance(members, str)
                    else (members or [])
                )

        if not bound or task.assigned_agent not in bound:
            raise GovernanceError(
                "Task agent is not bound to the project."
            )

        if task.required_role != Role.AUDITOR.value:
            if state.state not in {
                State.AUTHORIZED,
                State.ACTIVE,
            }:
                raise GovernanceError(
                    "Task requires an AUTHORIZED or ACTIVE project."
                )

            if (
                not task.authorization_id
                or task.authorization_id
                != state.authorization_id
            ):
                raise GovernanceError(
                    "Task authorization does not match project state."
                )
        else:
            if state.state != State.AUDIT_PENDING:
                raise GovernanceError(
                    "Audit Task requires AUDIT_PENDING project state."
                )

            _, request = validate_audit_request(
                state_path,
                state,
            )

            if request.get("task_id") != task.task_id:
                raise GovernanceError(
                    "Audit Task is not linked to the active audit request."
                )

            if (
                task.authorization_id
                != request.get("proposal_id")
            ):
                raise GovernanceError(
                    "Audit Task authorization does not match "
                    "the Root-approved request."
                )

        if (
            task.required_role == Role.PRODUCER.value
            and task.assigned_agent != state.owner
        ):
            raise GovernanceError(
                "Only the project owner may execute producer tasks."
            )

        if (
            task.required_role == Role.AUDITOR.value
            and task.assigned_agent == state.owner
        ):
            raise GovernanceError(
                "PRODUCER / AUDITOR INDEPENDENCE CONFLICT"
            )

        return agent

    def _acknowledge_handoff(self, task, project_dir):
        handoff_id = task.metadata.get("handoff_id")

        if not handoff_id:
            return

        store = HandoffStore(project_dir)
        handoff = store.get(handoff_id)

        if handoff.receiver != task.assigned_agent:
            raise GovernanceError(
                "Task handoff receiver does not match task agent."
            )

        if handoff.status == HandoffStatus.CREATED:
            store.acknowledge(
                handoff_id,
                task.assigned_agent,
            )

    def _write_run_record(
        self,
        task,
        status,
        result=None,
        error=None,
    ):
        run_dir = (
            self.vault.machine_dir
            / "runs"
            / task.task_id
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"attempt-{task.attempt}"
        path = run_dir / f"{run_id}.json"
        data = {
            "run_id": run_id,
            "task_id": task.task_id,
            "agent_id": task.assigned_agent,
            "status": status,
            "result": result or {},
            "error": error,
        }
        path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _record_task_event(
        self,
        task,
        action,
        result,
        note="",
    ):
        self.events.append_once(
            Event(
                event_id=(
                    f"EVT-{task.task_id}-{action}-"
                    f"{task.attempt}"
                ),
                actor=task.assigned_agent,
                action=action,
                target=task.project_id,
                result=result,
                capability_checked=(
                    task.required_capability
                ),
                note=note,
                correlation_id=task.task_id,
                causation_id=task.causation_event_id,
                metadata={
                    "task_id": task.task_id,
                },
            )
        )

    def run_task(self, state_path, task_id):
        project_dir = state_path.parent
        store = TaskStore(project_dir)
        task = store.get(task_id)

        if task.status == TaskStatus.COMPLETED:
            return task

        if task.status != TaskStatus.ASSIGNED:
            raise GovernanceError(
                "Chief of Staff only runs ASSIGNED tasks."
            )

        try:
            state = self.vault.read_state(state_path)
            self._validate_agent(task, state, state_path)
            self._acknowledge_handoff(task, project_dir)

            if (
                task.required_role == Role.PRODUCER.value
                and state.state == State.AUTHORIZED
            ):
                state = self.execution_engine.start_execution(
                    state_path,
                    task.assigned_agent,
                )

            running = store.start(task.task_id)
            self._write_run_record(running, "STARTED")
            result = self.runtime.execute(
                running,
                {
                    "project_state": state.to_dict(),
                    "input_artifacts": [
                        reference.to_dict()
                        for reference in running.input_artifacts
                    ],
                    "context_refs": list(
                        running.context_refs
                    ),
                },
            )

            if result.task_id != running.task_id:
                raise GovernanceError(
                    "Agent result task_id does not match the running Task."
                )

            if result.agent_id != running.assigned_agent:
                raise GovernanceError(
                    "Agent result agent_id does not match the assigned agent."
                )

            if running.required_role == Role.PRODUCER.value:
                if not result.artifact_text:
                    raise GovernanceError(
                        "Producer runtime returned no artifact."
                    )

                auditor = running.metadata.get("auditor")

                if not auditor:
                    auditors = (
                        state.agents or {}
                    ).get(Role.AUDITOR.value, [])
                    auditor = auditors[0] if auditors else None

                if not auditor:
                    raise GovernanceError(
                        "Producer task has no independent auditor route."
                    )

                produced_state = (
                    self.execution_engine.producer_complete(
                        state_path,
                        running.assigned_agent,
                        result.artifact_text,
                        auditor,
                        task_id=running.task_id,
                        causation_event_id=(
                            running.causation_event_id
                        ),
                    )
                )
                artifact_path = (
                    self.vault.root
                    / produced_state.artifact_path
                )
                completed_result = {
                    "runtime": result.metadata,
                    "output_text": result.output_text,
                    "output_artifacts": [
                        {
                            "path": produced_state.artifact_path,
                            "sha256": (
                                produced_state.artifact_sha256
                            ),
                            "metadata": {
                                "kind": "producer_artifact",
                            },
                        }
                    ],
                }
                completed = store.complete(
                    running.task_id,
                    completed_result,
                )
                self._write_run_record(
                    completed,
                    "SUCCEEDED",
                    completed_result,
                )

            elif running.required_role == Role.AUDITOR.value:
                if not result.audit_verdict:
                    raise GovernanceError(
                        "Auditor runtime returned no verdict."
                    )

                self.audit_engine.record_audit(
                    state_path,
                    running.assigned_agent,
                    AuditVerdict(result.audit_verdict),
                    result.audit_text or result.output_text,
                )
                completed = store.get(running.task_id)
                self._write_run_record(
                    completed,
                    "SUCCEEDED",
                    completed.result,
                )

            else:
                completed_result = {
                    "runtime": result.metadata,
                    "output_text": result.output_text,
                    "output_artifacts": [],
                }
                completed = store.complete(
                    running.task_id,
                    completed_result,
                )
                self._write_run_record(
                    completed,
                    "SUCCEEDED",
                    completed_result,
                )

            self._record_task_event(
                completed,
                "TASK_COMPLETED",
                EventResult.SUCCESS,
                "Task runtime completed.",
            )
            return completed

        except Exception as exc:
            current = store.get(task.task_id)

            if current.status == TaskStatus.RUNNING:
                current = store.fail(
                    task.task_id,
                    str(exc),
                )
            elif current.status == TaskStatus.ASSIGNED:
                current = store.block(
                    task.task_id,
                    str(exc),
                )

            self._write_run_record(
                current,
                "FAILED",
                error=str(exc),
            )
            self._record_task_event(
                current,
                "TASK_FAILED",
                EventResult.FAILED,
                str(exc),
            )
            raise

    def _assigned_tasks(self):
        tasks = []

        for state_path in self._state_paths():
            store = TaskStore(state_path.parent)

            for task in store.list(
                status=TaskStatus.ASSIGNED
            ):
                tasks.append((state_path, task))

        return sorted(
            tasks,
            key=lambda item: (
                item[1].created_at,
                item[1].task_id,
            ),
        )

    def tick(self):
        automaton_results = self.automaton.run_pending()
        task_results = []
        failures = []

        for state_path, task in self._assigned_tasks():
            try:
                completed = self.run_task(
                    state_path,
                    task.task_id,
                )
                task_results.append(completed.task_id)
            except Exception as exc:
                failures.append(
                    {
                        "task_id": task.task_id,
                        "error": str(exc),
                    }
                )

        automaton_results.extend(
            self.automaton.run_pending()
        )
        pending_proposals = sorted(
            (
                self.vault.root
                / "00_ROOT"
                / "inbox"
            ).glob("*.md")
        )
        blocked_projects = []

        for state_path in self._state_paths():
            state = self.vault.read_state(state_path)

            if state.state == State.BLOCKED:
                blocked_projects.append(
                    state.project_id
                )

        return {
            "events_processed": len(automaton_results),
            "tasks_completed": task_results,
            "task_failures": failures,
            "pending_root_items": len(pending_proposals),
            "blocked_projects": blocked_projects,
        }
