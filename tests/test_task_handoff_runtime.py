import tempfile
import unittest
from pathlib import Path

import yaml

from control_tower.handoffs import (
    Handoff,
    HandoffConflictError,
    HandoffStatus,
    HandoffStore,
)
from control_tower.runtime import (
    AgentRuntimeError,
    MockAgentRuntime,
)
from control_tower.tasks import (
    ArtifactRef,
    Task,
    TaskConflictError,
    TaskStatus,
    TaskStore,
    TaskTransitionError,
)


class V1TaskHandoffRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temporary.name) / "TEST-PROJECT"
        self.project_dir.mkdir(parents=True)
        self.task_store = TaskStore(self.project_dir)
        self.handoff_store = HandoffStore(self.project_dir)
        self.artifact = ArtifactRef(
            path="01_RESEARCH/TEST-PROJECT/artifacts/T0.txt",
            sha256="a" * 64,
            metadata={"kind": "producer_artifact"},
        )

    def tearDown(self):
        self.temporary.cleanup()

    def make_task(self, **overrides):
        values = {
            "task_id": "TASK-AUDIT-T0",
            "project_id": "TEST-PROJECT",
            "phase": "T0",
            "task_type": "AUDIT",
            "assigned_agent": "auditor_a",
            "required_role": "AUDITOR",
            "required_capability": "audit",
            "description": "Audit the frozen T0 artifact.",
            "request_path": (
                "01_RESEARCH/TEST-PROJECT/audits/"
                "T0_audit_request.md"
            ),
            "input_artifacts": [self.artifact],
            "context_refs": ["01_RESEARCH/TEST-PROJECT/STATE.md"],
            "authorization_id": "ROOT-AUDIT-001",
            "causation_event_id": "EVT-T0-PRODUCED",
            "idempotency_key": "AUDIT-TEST-PROJECT-T0-aaaaaaaaaaaa",
            "metadata": {"lane": "research"},
        }
        values.update(overrides)
        return Task(**values)

    def test_task_markdown_round_trip_and_metadata(self):
        task = self.make_task()
        created = self.task_store.create(task)
        loaded = self.task_store.get(task.task_id)

        self.assertEqual(created.evidence_dict(), loaded.evidence_dict())
        self.assertEqual(loaded.metadata, {"lane": "research"})
        self.assertEqual(loaded.input_artifacts[0].sha256, "a" * 64)

        path = self.task_store.path_for(task.task_id)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        metadata = yaml.safe_load(text.split("---", 2)[1])
        self.assertEqual(metadata["status"], "CREATED")

    def test_task_creation_is_idempotent_but_conflicts_on_evidence(self):
        original = self.task_store.create(self.make_task())
        replay = self.task_store.create(self.make_task())
        self.assertEqual(replay.created_at, original.created_at)

        with self.assertRaises(TaskConflictError):
            self.task_store.create(
                self.make_task(required_capability="approve")
            )

    def test_task_strict_transitions_and_idempotent_replay(self):
        self.task_store.create(self.make_task())

        with self.assertRaises(TaskTransitionError):
            self.task_store.start("TASK-AUDIT-T0")

        assigned = self.task_store.assign("TASK-AUDIT-T0")
        self.assertEqual(assigned.status, TaskStatus.ASSIGNED)
        self.assertEqual(
            self.task_store.assign("TASK-AUDIT-T0").status,
            TaskStatus.ASSIGNED,
        )

        running = self.task_store.start("TASK-AUDIT-T0")
        self.assertEqual(running.status, TaskStatus.RUNNING)
        self.assertEqual(running.attempt, 1)
        self.assertEqual(
            self.task_store.start("TASK-AUDIT-T0").attempt,
            1,
        )

        completed = self.task_store.complete(
            "TASK-AUDIT-T0",
            {"verdict": "PASS"},
        )
        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.result["verdict"], "PASS")
        self.assertEqual(
            self.task_store.complete(
                "TASK-AUDIT-T0",
                {"verdict": "PASS"},
            ).status,
            TaskStatus.COMPLETED,
        )

        with self.assertRaises(TaskConflictError):
            self.task_store.complete(
                "TASK-AUDIT-T0",
                {"verdict": "FAIL"},
            )

        with self.assertRaises(TaskTransitionError):
            self.task_store.fail("TASK-AUDIT-T0", "too late")

    def test_failed_task_can_only_retry_through_assigned(self):
        self.task_store.create(self.make_task())
        self.task_store.assign("TASK-AUDIT-T0")
        self.task_store.start("TASK-AUDIT-T0")
        failed = self.task_store.fail(
            "TASK-AUDIT-T0",
            "temporary runtime failure",
        )
        self.assertEqual(failed.status, TaskStatus.FAILED)

        with self.assertRaises(TaskTransitionError):
            self.task_store.start("TASK-AUDIT-T0")

        self.task_store.assign("TASK-AUDIT-T0")
        retried = self.task_store.start("TASK-AUDIT-T0")
        self.assertEqual(retried.attempt, 2)
        self.assertIsNone(retried.error)

    def test_recovery_history_does_not_break_create_replay(self):
        original = self.make_task()
        self.task_store.create(original)
        self.task_store.assign(original.task_id)
        self.task_store.start(original.task_id)
        self.task_store.fail(
            original.task_id,
            "interrupted",
        )
        recovered = self.task_store.recover_for_retry(
            original.task_id,
            "Explicit Root retry.",
        )

        replay = self.task_store.ensure(
            self.make_task()
        )
        self.assertEqual(
            replay.status,
            TaskStatus.ASSIGNED,
        )
        self.assertEqual(
            replay.metadata["recovery_history"],
            recovered.metadata["recovery_history"],
        )

    def make_handoff(self, **overrides):
        values = {
            "handoff_id": "HANDOFF-AUDIT-T0",
            "project_id": "TEST-PROJECT",
            "sender": "producer_a",
            "receiver": "auditor_a",
            "reason": "Independent audit required.",
            "artifact_refs": [self.artifact],
            "context_refs": [
                "01_RESEARCH/TEST-PROJECT/audits/"
                "T0_audit_request.md"
            ],
            "task_id": "TASK-AUDIT-T0",
            "phase": "T0",
            "authorization_id": "ROOT-AUDIT-001",
            "metadata": {
                "may": ["audit frozen artifact"],
                "may_not": ["modify artifact", "authorize next phase"],
            },
        }
        values.update(overrides)
        return Handoff(**values)

    def test_handoff_creation_and_acknowledgement_are_idempotent(self):
        original = self.handoff_store.create(self.make_handoff())
        replay = self.handoff_store.create(self.make_handoff())
        self.assertEqual(replay.timestamp, original.timestamp)

        acknowledged = self.handoff_store.acknowledge(
            original.handoff_id,
            "auditor_a",
            timestamp="2026-08-23T12:00:00+00:00",
        )
        self.assertEqual(
            acknowledged.status,
            HandoffStatus.ACKNOWLEDGED,
        )
        self.assertEqual(acknowledged.acknowledged_by, "auditor_a")

        repeated = self.handoff_store.acknowledge(
            original.handoff_id,
            "auditor_a",
            timestamp="2099-01-01T00:00:00+00:00",
        )
        self.assertEqual(
            repeated.acknowledged_at,
            "2026-08-23T12:00:00+00:00",
        )

        # Replaying creation after acknowledgement returns the current record.
        self.assertEqual(
            self.handoff_store.create(self.make_handoff()).status,
            HandoffStatus.ACKNOWLEDGED,
        )

    def test_handoff_conflicting_evidence_and_wrong_receiver_are_rejected(self):
        self.handoff_store.create(self.make_handoff())

        with self.assertRaises(HandoffConflictError):
            self.handoff_store.create(
                self.make_handoff(receiver="auditor_b")
            )

        with self.assertRaises(HandoffConflictError):
            self.handoff_store.acknowledge(
                "HANDOFF-AUDIT-T0",
                "producer_a",
            )

    def test_mock_runtime_producer_and_auditor_results(self):
        runtime = MockAgentRuntime(
            producer_output="candidate artifact",
            auditor_verdict="PASS_WITH_REPAIRS",
            auditor_notes="Repair citation formatting.",
            metadata={"fixture": "v1"},
        )

        producer = self.make_task(
            task_id="TASK-PRODUCE-T0",
            task_type="PRODUCE_ARTIFACT",
            assigned_agent="producer_a",
            required_role="PRODUCER",
            required_capability="produce_artifact",
            status=TaskStatus.ASSIGNED,
        )
        producer_result = runtime.execute(producer, {"scope": "T0"})
        self.assertEqual(producer_result.artifact_text, "candidate artifact")
        self.assertIsNone(producer_result.audit_verdict)

        auditor = self.make_task(status=TaskStatus.RUNNING)
        audit_result = runtime.execute(auditor, {"artifact": self.artifact})
        self.assertEqual(audit_result.audit_verdict, "PASS_WITH_REPAIRS")
        self.assertEqual(
            audit_result.audit_text,
            "Repair citation formatting.",
        )
        self.assertEqual(audit_result.metadata["fixture"], "v1")

    def test_mock_runtime_rejects_unassigned_task(self):
        with self.assertRaises(AgentRuntimeError):
            MockAgentRuntime().execute(self.make_task(), {})


if __name__ == "__main__":
    unittest.main()
