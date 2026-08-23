import json
import tempfile
import unittest
from pathlib import Path

import yaml

from control_tower.agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from control_tower.chief_of_staff import ChiefOfStaff
from control_tower.core.audit_request_engine import (
    AUDIT_REQUEST_COMPLETED,
    read_frontmatter,
)
from control_tower.core.decision_engine import DecisionEngine
from control_tower.decision import approve_proposal
from control_tower.events import Event, EventLedger, EventResult
from control_tower.handoffs import HandoffStatus, HandoffStore
from control_tower.models import (
    Division,
    Lineage,
    ProjectState,
    Role,
    State,
)
from control_tower.runtime import MockAgentRuntime
from control_tower.tasks import Task, TaskStatus, TaskStore
from control_tower.vault import Vault


class FullLifecycleTests(unittest.TestCase):
    PROJECT_ID = "FULL-LIFECYCLE"
    PHASE = "T0"
    PRODUCER = "producer_e2e"
    AUDITOR = "auditor_e2e"
    PRODUCER_TASK_ID = "TASK-FULL-LIFECYCLE-T0-PRODUCER"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        self.vault = Vault(self.root)
        self.vault.ensure_structure()
        self.registry = AgentRegistry(self.root)
        self.events = EventLedger(self.vault)
        self.registry.save(
            [
                AgentState(
                    agent_id="personal_root",
                    division="ROOT",
                    role=AgentRole.ROOT,
                    status=AgentStatus.ACTIVE,
                    owns=["ALL"],
                    capabilities=[
                        "approve",
                        "reject",
                        "authorize",
                    ],
                ),
                AgentState(
                    agent_id=self.PRODUCER,
                    division="RESEARCH",
                    role=AgentRole.PRODUCER,
                    status=AgentStatus.ACTIVE,
                    owns=[self.PROJECT_ID],
                    capabilities=["produce_artifact"],
                ),
                AgentState(
                    agent_id=self.AUDITOR,
                    division="RESEARCH",
                    role=AgentRole.AUDITOR,
                    status=AgentStatus.ACTIVE,
                    owns=[self.PROJECT_ID],
                    capabilities=["audit"],
                ),
            ]
        )
        self.project_dir = (
            self.root
            / "01_RESEARCH"
            / self.PROJECT_ID
        )
        for folder in (
            "artifacts",
            "audits",
            "tasks",
            "handoffs",
        ):
            (self.project_dir / folder).mkdir(
                parents=True,
                exist_ok=True,
            )
        self.state_path = self.project_dir / "STATE.md"
        self.vault.write_state(
            self.state_path,
            ProjectState(
                project_id=self.PROJECT_ID,
                title="Full Lifecycle",
                division=Division.RESEARCH,
                phase=self.PHASE,
                state=State.READY,
                owner=self.PRODUCER,
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: [self.PRODUCER],
                    Role.AUDITOR.value: [self.AUDITOR],
                },
                lineage=Lineage.CANONICAL,
                auditor=self.AUDITOR,
                next_gate="ROOT_AUTHORIZATION",
                notes="Ready for the E2E fixture.",
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _proposal_documents(self):
        documents = []
        inbox = self.root / "00_ROOT" / "inbox"

        for path in sorted(inbox.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                continue
            metadata = yaml.safe_load(parts[1])
            if (
                isinstance(metadata, dict)
                and metadata.get("proposal_type")
            ):
                documents.append((path, metadata))

        return documents

    def _authorize_and_assign_producer_task(self):
        state = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        ).authorize(
            self.state_path,
            "ROOT-E2E-T0",
            "Produce and independently audit T0 only.",
        )
        self.assertEqual(state.state, State.AUTHORIZED)

        created_event = (
            f"EVT-{self.PRODUCER_TASK_ID}-CREATED"
        )
        store = TaskStore(self.project_dir)
        task = store.ensure(
            Task(
                task_id=self.PRODUCER_TASK_ID,
                project_id=self.PROJECT_ID,
                phase=self.PHASE,
                task_type="PRODUCE_ARTIFACT",
                assigned_agent=self.PRODUCER,
                required_role=Role.PRODUCER.value,
                required_capability="produce_artifact",
                description="Produce the T0 candidate artifact.",
                authorization_id=state.authorization_id,
                causation_event_id=created_event,
                idempotency_key=self.PRODUCER_TASK_ID,
                metadata={
                    "auditor": self.AUDITOR,
                    "created_by": "personal_root",
                },
            )
        )
        task = store.assign(task.task_id)
        self.events.append_once(
            Event(
                event_id=created_event,
                actor="personal_root",
                action="TASK_CREATED",
                target=self.PROJECT_ID,
                result=EventResult.SUCCESS,
                capability_checked="approve",
                correlation_id=task.task_id,
                metadata={"task_id": task.task_id},
            )
        )
        return task

    def test_producer_tick_proposal_approval_and_audit_tick_e2e(self):
        producer_task = self._authorize_and_assign_producer_task()
        runtime = MockAgentRuntime(
            producer_output="E2E frozen candidate artifact.\n",
            auditor_verdict="PASS_WITH_REPAIRS",
            auditor_notes="Substance passes; normalize one citation.",
            metadata={"fixture": "full_lifecycle"},
        )

        first_tick = ChiefOfStaff(
            self.vault,
            runtime=runtime,
        ).tick()
        self.assertIn(
            producer_task.task_id,
            first_tick["tasks_completed"],
        )
        self.assertEqual(first_tick["task_failures"], [])

        produced_state = self.vault.read_state(self.state_path)
        self.assertEqual(
            produced_state.state,
            State.PRODUCER_COMPLETE,
        )
        self.assertEqual(
            produced_state.next_gate,
            "ROOT_AUDIT_APPROVAL",
        )
        self.assertEqual(produced_state.auditor, self.AUDITOR)
        self.assertTrue(
            (self.root / produced_state.artifact_path).exists()
        )
        self.assertEqual(
            TaskStore(self.project_dir).get(
                self.PRODUCER_TASK_ID
            ).status,
            TaskStatus.COMPLETED,
        )
        self.assertFalse(
            (self.project_dir / "audits" / "T0_audit.md").exists()
        )

        proposals = self._proposal_documents()
        self.assertEqual(len(proposals), 1)
        proposal_path, proposal = proposals[0]
        self.assertEqual(
            proposal["proposal_type"],
            "CREATE_AUDIT_REQUEST",
        )
        self.assertEqual(proposal["target"], self.PROJECT_ID)
        self.assertEqual(
            proposal["payload"]["artifact_sha256"],
            produced_state.artifact_sha256,
        )
        self.assertEqual(first_tick["pending_root_items"], 1)

        approve_proposal(
            self.root,
            proposal["proposal_id"],
        )
        self.assertFalse(proposal_path.exists())

        pending_state = self.vault.read_state(self.state_path)
        self.assertEqual(pending_state.state, State.AUDIT_PENDING)
        request_path = (
            self.project_dir
            / "audits"
            / "T0_audit_request.md"
        )
        request = read_frontmatter(request_path)
        audit_task_id = request["task_id"]
        handoff_id = request["handoff_id"]
        self.assertEqual(
            TaskStore(self.project_dir).get(audit_task_id).status,
            TaskStatus.ASSIGNED,
        )
        self.assertEqual(
            HandoffStore(self.project_dir).get(handoff_id).status,
            HandoffStatus.CREATED,
        )

        second_tick = ChiefOfStaff(
            self.vault,
            runtime=runtime,
        ).tick()
        self.assertIn(
            audit_task_id,
            second_tick["tasks_completed"],
        )
        self.assertEqual(second_tick["task_failures"], [])

        final_state = self.vault.read_state(self.state_path)
        self.assertEqual(final_state.state, State.WAITING_ROOT)
        self.assertEqual(
            final_state.latest_audit_verdict,
            "PASS_WITH_REPAIRS",
        )
        self.assertEqual(final_state.next_gate, "ROOT_DECISION")

        final_request = read_frontmatter(request_path)
        self.assertEqual(
            final_request["status"],
            AUDIT_REQUEST_COMPLETED,
        )
        self.assertEqual(
            final_request["audit_verdict"],
            "PASS_WITH_REPAIRS",
        )
        audit_path = self.project_dir / "audits" / "T0_audit.md"
        audit = read_frontmatter(audit_path)
        self.assertEqual(audit["verdict"], "PASS_WITH_REPAIRS")
        self.assertEqual(audit["auditor"], self.AUDITOR)

        tasks = {
            task.task_id: task
            for task in TaskStore(self.project_dir).list()
        }
        self.assertEqual(
            tasks[self.PRODUCER_TASK_ID].status,
            TaskStatus.COMPLETED,
        )
        self.assertEqual(
            tasks[audit_task_id].status,
            TaskStatus.COMPLETED,
        )
        self.assertEqual(tasks[self.PRODUCER_TASK_ID].attempt, 1)
        self.assertEqual(tasks[audit_task_id].attempt, 1)
        self.assertEqual(
            HandoffStore(self.project_dir).get(handoff_id).status,
            HandoffStatus.ACKNOWLEDGED,
        )

        gate_path = (
            self.root
            / "00_ROOT"
            / "inbox"
            / f"{self.PROJECT_ID}_{self.PHASE}_GATE.md"
        )
        self.assertTrue(gate_path.exists())
        self.assertEqual(self._proposal_documents(), [])
        self.assertEqual(second_tick["pending_root_items"], 1)

        events = self.events.read_all()
        actions = [event["action"] for event in events]
        self.assertEqual(actions.count("PRODUCE_ARTIFACT"), 1)
        self.assertEqual(actions.count("CREATE_AUDIT_REQUEST"), 1)
        self.assertEqual(actions.count("AUDIT"), 1)
        self.assertGreaterEqual(actions.count("TASK_COMPLETED"), 2)
        event_ids = [event["event_id"] for event in events]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        producer_event = next(
            event
            for event in events
            if event["action"] == "PRODUCE_ARTIFACT"
        )
        self.assertEqual(
            producer_event["correlation_id"],
            self.PRODUCER_TASK_ID,
        )
        self.assertEqual(
            producer_event["metadata"]["task_id"],
            self.PRODUCER_TASK_ID,
        )

        cursor = json.loads(
            (
                self.vault.machine_dir
                / "automaton_cursor.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(cursor["next_offset"], len(events))
        self.assertTrue(
            (
                self.vault.machine_dir
                / "runs"
                / self.PRODUCER_TASK_ID
                / "attempt-1.json"
            ).exists()
        )
        self.assertTrue(
            (
                self.vault.machine_dir
                / "runs"
                / audit_task_id
                / "attempt-1.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
