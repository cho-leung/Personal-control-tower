import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_tower.agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from control_tower.chief_of_staff import ChiefOfStaff
from control_tower.core.audit_request_engine import (
    AUDIT_REQUEST_COMPLETED,
    AUDIT_REQUEST_PENDING,
    read_frontmatter,
)
from control_tower.decision import approve_proposal
from control_tower.events import EventLedger
from control_tower.guardrails import GovernanceError
from control_tower.handoffs import HandoffStatus, HandoffStore
from control_tower.models import (
    Division,
    Lineage,
    ProjectState,
    Role,
    State,
)
from control_tower.proposals import create_proposal, write_proposal
from control_tower.runtime import MockAgentRuntime
from control_tower.tasks import TaskStatus, TaskStore
from control_tower.vault import Vault


class AuditRequestFlowTests(unittest.TestCase):
    PROJECT_ID = "AUDIT-FLOW"
    PHASE = "T0"
    PRODUCER = "producer_a"
    AUDITOR = "auditor_a"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        self.vault = Vault(self.root)
        self.vault.ensure_structure()
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
        self._install_agents()

    def tearDown(self):
        self.temporary.cleanup()

    def _install_agents(self, self_audit=False):
        owner = self.AUDITOR if self_audit else self.PRODUCER
        agents = [
            AgentState(
                agent_id="personal_root",
                division="ROOT",
                role=AgentRole.ROOT,
                status=AgentStatus.ACTIVE,
                owns=["ALL"],
                capabilities=["approve", "reject", "authorize"],
            ),
            AgentState(
                agent_id=owner,
                division="RESEARCH",
                role=(
                    AgentRole.AUDITOR
                    if self_audit
                    else AgentRole.PRODUCER
                ),
                status=AgentStatus.ACTIVE,
                owns=[self.PROJECT_ID],
                capabilities=(
                    ["produce_artifact", "audit"]
                    if self_audit
                    else ["produce_artifact"]
                ),
            ),
        ]

        if not self_audit:
            agents.append(
                AgentState(
                    agent_id=self.AUDITOR,
                    division="RESEARCH",
                    role=AgentRole.AUDITOR,
                    status=AgentStatus.ACTIVE,
                    owns=[self.PROJECT_ID],
                    capabilities=["audit"],
                )
            )

        AgentRegistry(self.root).save(agents)

    def _write_artifact_state(
        self,
        state=State.PRODUCER_COMPLETE,
        verdict=None,
        claimed_sha=None,
        self_audit=False,
    ):
        owner = self.AUDITOR if self_audit else self.PRODUCER
        artifact_path = (
            self.project_dir
            / "artifacts"
            / f"{self.PHASE}_producer_artifact.txt"
        )
        artifact_path.write_text(
            "frozen producer evidence\n",
            encoding="utf-8",
        )
        actual_sha = self.vault.freeze_artifact(artifact_path)
        artifact_reference = str(
            artifact_path.relative_to(self.root)
        )
        project_state = ProjectState(
            project_id=self.PROJECT_ID,
            title="Audit Flow",
            division=Division.RESEARCH,
            phase=self.PHASE,
            state=state,
            owner=owner,
            owner_role=Role.PRODUCER,
            agents={
                Role.PRODUCER.value: [owner],
                Role.AUDITOR.value: [self.AUDITOR],
            },
            lineage=Lineage.CANONICAL,
            authorization_id="ROOT-T0-001",
            artifact_path=artifact_reference,
            artifact_sha256=(claimed_sha or actual_sha),
            auditor=self.AUDITOR,
            latest_audit_verdict=verdict,
            next_gate=(
                "ROOT_DECISION"
                if state == State.WAITING_ROOT
                else "ROOT_AUDIT_APPROVAL"
            ),
            notes="Fixture state.",
        )
        self.vault.write_state(
            self.state_path,
            project_state,
        )
        return project_state, artifact_path, actual_sha

    def _write_audit_proposal(self, state, reason="Approve audit."):
        proposal = create_proposal(
            proposal_type="CREATE_AUDIT_REQUEST",
            target=self.PROJECT_ID,
            reason=reason,
            created_by="test_fixture",
            payload={
                "phase": state.phase,
                "artifact_path": state.artifact_path,
                "artifact_sha256": state.artifact_sha256,
                "auditor": state.auditor,
                "created_event": "EVT-FIXTURE-PRODUCED",
            },
        )
        path = write_proposal(self.root, proposal)
        return proposal, path

    @staticmethod
    def _request_ids(state):
        request_id = (
            f"AUDIT-{state.project_id}-{state.phase}-"
            f"{state.artifact_sha256[:12]}"
        )
        return (
            request_id,
            f"TASK-{request_id}",
            f"HANDOFF-{request_id}",
        )

    def test_approve_registers_request_task_and_handoff_without_audit(self):
        original, _, _ = self._write_artifact_state()
        proposal, _ = self._write_audit_proposal(original)

        result_path = approve_proposal(
            self.root,
            proposal.proposal_id,
        )
        self.assertEqual(result_path, self.state_path)

        state = self.vault.read_state(self.state_path)
        self.assertEqual(state.state, State.AUDIT_PENDING)
        self.assertEqual(state.next_gate, "INDEPENDENT_AUDIT")
        self.assertIsNone(state.latest_audit_verdict)

        request_id, task_id, handoff_id = self._request_ids(state)
        request_path = (
            self.project_dir
            / "audits"
            / f"{self.PHASE}_audit_request.md"
        )
        request = read_frontmatter(request_path)
        self.assertEqual(request["request_id"], request_id)
        self.assertEqual(request["status"], AUDIT_REQUEST_PENDING)
        self.assertEqual(request["task_id"], task_id)
        self.assertEqual(request["handoff_id"], handoff_id)

        task = TaskStore(self.project_dir).get(task_id)
        self.assertEqual(task.status, TaskStatus.ASSIGNED)
        self.assertEqual(task.assigned_agent, self.AUDITOR)
        self.assertEqual(task.input_artifacts[0].sha256, state.artifact_sha256)

        handoff = HandoffStore(self.project_dir).get(handoff_id)
        self.assertEqual(handoff.status, HandoffStatus.CREATED)
        self.assertEqual(handoff.sender, self.PRODUCER)
        self.assertEqual(handoff.receiver, self.AUDITOR)

        self.assertFalse(
            (self.project_dir / "audits" / "T0_audit.md").exists()
        )
        self.assertFalse(
            (
                self.root
                / "00_ROOT"
                / "inbox"
                / f"{self.PROJECT_ID}_{self.PHASE}_GATE.md"
            ).exists()
        )
        events = EventLedger(self.vault).read_all()
        self.assertEqual(
            [event["action"] for event in events],
            ["CREATE_AUDIT_REQUEST"],
        )

    def test_chief_of_staff_mock_completes_real_audit_engine_flow(self):
        original, _, _ = self._write_artifact_state()
        proposal, _ = self._write_audit_proposal(original)
        approve_proposal(self.root, proposal.proposal_id)
        state = self.vault.read_state(self.state_path)
        _, task_id, handoff_id = self._request_ids(state)

        result = ChiefOfStaff(
            self.vault,
            runtime=MockAgentRuntime(
                auditor_verdict="PASS",
                auditor_notes="Independent evidence is sufficient.",
            ),
        ).tick()
        self.assertIn(task_id, result["tasks_completed"])
        self.assertEqual(result["task_failures"], [])

        completed_state = self.vault.read_state(self.state_path)
        self.assertEqual(completed_state.state, State.WAITING_ROOT)
        self.assertEqual(completed_state.latest_audit_verdict, "PASS")
        self.assertEqual(completed_state.next_gate, "ROOT_DECISION")

        audit_path = self.project_dir / "audits" / "T0_audit.md"
        self.assertTrue(audit_path.exists())
        audit = read_frontmatter(audit_path)
        self.assertEqual(audit["auditor"], self.AUDITOR)
        self.assertEqual(audit["verdict"], "PASS")
        self.assertEqual(
            audit["artifact_sha256"],
            completed_state.artifact_sha256,
        )

        request = read_frontmatter(
            self.project_dir
            / "audits"
            / "T0_audit_request.md"
        )
        self.assertEqual(request["status"], AUDIT_REQUEST_COMPLETED)
        self.assertEqual(request["audit_verdict"], "PASS")

        task = TaskStore(self.project_dir).get(task_id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.result["verdict"], "PASS")
        self.assertEqual(len(task.output_artifacts), 1)

        handoff = HandoffStore(self.project_dir).get(handoff_id)
        self.assertEqual(handoff.status, HandoffStatus.ACKNOWLEDGED)
        self.assertEqual(handoff.acknowledged_by, self.AUDITOR)

        gate_path = (
            self.root
            / "00_ROOT"
            / "inbox"
            / f"{self.PROJECT_ID}_{self.PHASE}_GATE.md"
        )
        self.assertTrue(gate_path.exists())

        events = EventLedger(self.vault).read_all()
        actions = [event["action"] for event in events]
        self.assertIn("AUDIT", actions)
        self.assertIn("TASK_COMPLETED", actions)
        event_count = len(events)
        audit_bytes = audit_path.read_bytes()

        # No assigned work remains; another tick must not duplicate evidence.
        ChiefOfStaff(self.vault).tick()
        self.assertEqual(audit_path.read_bytes(), audit_bytes)
        self.assertEqual(
            len(EventLedger(self.vault).read_all()),
            event_count,
        )

    def test_committed_audit_evidence_reconciles_without_rerun(self):
        original, _, _ = self._write_artifact_state()
        proposal, _ = self._write_audit_proposal(original)
        approve_proposal(self.root, proposal.proposal_id)
        state = self.vault.read_state(self.state_path)
        _, task_id, handoff_id = self._request_ids(state)

        with patch.object(
            TaskStore,
            "complete",
            side_effect=KeyboardInterrupt(
                "interrupted after audit commit"
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                ChiefOfStaff(
                    self.vault,
                    runtime=MockAgentRuntime(
                        auditor_verdict="PASS",
                        auditor_notes=(
                            "Committed independent audit."
                        ),
                    ),
                ).tick()

        partial = self.vault.read_state(
            self.state_path
        )
        self.assertEqual(partial.state, State.WAITING_ROOT)
        self.assertEqual(
            TaskStore(self.project_dir).get(task_id).status,
            TaskStatus.RUNNING,
        )
        gate_path = (
            self.root
            / "00_ROOT"
            / "inbox"
            / f"{self.PROJECT_ID}_{self.PHASE}_GATE.md"
        )
        self.assertFalse(gate_path.exists())

        completed = ChiefOfStaff(
            self.vault
        ).recover_task(
            self.state_path,
            task_id,
            "Recover committed audit evidence.",
        )
        self.assertEqual(
            completed.status,
            TaskStatus.COMPLETED,
        )
        self.assertEqual(completed.attempt, 1)
        self.assertEqual(
            completed.metadata[
                "reconciliation_history"
            ][0]["previous_status"],
            "RUNNING",
        )
        self.assertEqual(
            HandoffStore(self.project_dir).get(
                handoff_id
            ).status,
            HandoffStatus.ACKNOWLEDGED,
        )
        self.assertTrue(gate_path.exists())

        actions = [
            event["action"]
            for event in EventLedger(
                self.vault
            ).read_all()
        ]
        self.assertIn("AUDIT", actions)
        self.assertEqual(
            actions.count("TASK_RECONCILED"),
            1,
        )

        # Exact Root recovery is idempotent.
        ChiefOfStaff(self.vault).recover_task(
            self.state_path,
            task_id,
            "Recover committed audit evidence.",
        )
        self.assertEqual(
            [
                event["action"]
                for event in EventLedger(
                    self.vault
                ).read_all()
            ].count("TASK_RECONCILED"),
            1,
        )

    def test_stale_legacy_proposal_does_not_rewrite_state_or_audit(self):
        legacy_state, _, _ = self._write_artifact_state(
            state=State.WAITING_ROOT,
            verdict="PASS",
        )
        audit_path = self.project_dir / "audits" / "T0_audit.md"
        audit_path.write_text(
            """# Independent Audit

- Auditor: auditor_a
- Artifact SHA-256: {sha}
- Verdict: PASS

Legacy audit passed.
""".format(sha=legacy_state.artifact_sha256),
            encoding="utf-8",
        )
        state_before = self.state_path.read_bytes()
        audit_before = audit_path.read_bytes()
        proposal, _ = self._write_audit_proposal(
            legacy_state,
            reason="Stale replay.",
        )

        approve_proposal(self.root, proposal.proposal_id)

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(audit_path.read_bytes(), audit_before)
        request = read_frontmatter(
            self.project_dir
            / "audits"
            / "T0_audit_request.md"
        )
        self.assertEqual(request["status"], AUDIT_REQUEST_COMPLETED)
        self.assertEqual(request["audit_verdict"], "PASS")
        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(HandoffStore(self.project_dir).list(), [])

    def test_self_audit_rejection_is_atomic(self):
        self._install_agents(self_audit=True)
        invalid_state, artifact_path, _ = self._write_artifact_state(
            self_audit=True,
        )
        proposal, proposal_path = self._write_audit_proposal(invalid_state)
        state_before = self.state_path.read_bytes()
        artifact_before = artifact_path.read_bytes()
        proposal_before = proposal_path.read_bytes()

        with self.assertRaises(GovernanceError):
            approve_proposal(self.root, proposal.proposal_id)

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(artifact_path.read_bytes(), artifact_before)
        self.assertEqual(proposal_path.read_bytes(), proposal_before)
        self.assertFalse(
            (self.project_dir / "audits" / "T0_audit_request.md").exists()
        )
        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(HandoffStore(self.project_dir).list(), [])
        self.assertEqual(EventLedger(self.vault).read_all(), [])

    def test_hash_mismatch_rejection_is_atomic(self):
        invalid_state, artifact_path, _ = self._write_artifact_state(
            claimed_sha="0" * 64,
        )
        proposal, proposal_path = self._write_audit_proposal(invalid_state)
        state_before = self.state_path.read_bytes()
        artifact_before = artifact_path.read_bytes()
        proposal_before = proposal_path.read_bytes()

        with self.assertRaises(GovernanceError):
            approve_proposal(self.root, proposal.proposal_id)

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(artifact_path.read_bytes(), artifact_before)
        self.assertEqual(proposal_path.read_bytes(), proposal_before)
        self.assertFalse(
            (self.project_dir / "audits" / "T0_audit_request.md").exists()
        )
        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(HandoffStore(self.project_dir).list(), [])
        self.assertEqual(EventLedger(self.vault).read_all(), [])


if __name__ == "__main__":
    unittest.main()
