import tempfile
import unittest
from pathlib import Path

from control_tower.models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
)

from control_tower.vault import Vault
from control_tower.agents import AgentRegistry
from control_tower.events import EventLedger
from control_tower.core.decision_engine import DecisionEngine
from control_tower.guardrails import GovernanceError


class DecisionEngineTests(unittest.TestCase):

    def setUp(self):

        self.tmp = tempfile.TemporaryDirectory()

        self.root = Path(self.tmp.name)

        self.vault = Vault(self.root)

        self.vault.ensure_structure()

        self.registry = AgentRegistry(
            self.root
        )

        self.events = EventLedger(
            self.vault
        )

        self.engine = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        )

        self.project_dir = (
            self.root
            / "01_RESEARCH"
            / "TEST-PROJECT"
        )

        self.project_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.state_path = (
            self.project_dir
            / "STATE.md"
        )


    def tearDown(self):

        self.tmp.cleanup()


    def write_waiting_root(
        self,
        verdict="PASS",
        phase="T0",
    ):

        state = ProjectState(
            project_id="TEST-PROJECT",
            title="Test Project",
            division=Division.RESEARCH,
            phase=phase,
            state=State.WAITING_ROOT,
            owner="producer_a",
            owner_role=Role.PRODUCER,
            lineage=Lineage.CANONICAL,
            authorization_id="AUTH-T0",
            artifact_path=(
                "01_RESEARCH/"
                "TEST-PROJECT/"
                "artifacts/"
                "T0.txt"
            ),
            artifact_sha256="abc123",
            auditor="auditor_a",
            latest_audit_verdict=verdict,
            next_gate="ROOT_DECISION",
            notes="Waiting for Root.",
        )

        self.vault.write_state(
            self.state_path,
            state,
        )

        return state


    def test_authorize_next_phase_after_pass(self):

        self.write_waiting_root(
            verdict="PASS"
        )

        result = self.engine.root_decide(
            state_path=self.state_path,
            decision_id="ROOT-T1-001",
            decision="AUTHORIZE",
            next_phase="T1",
            scope="Run T1 only.",
        )

        self.assertEqual(
            result.state,
            State.AUTHORIZED,
        )

        self.assertEqual(
            result.phase,
            "T1",
        )

        self.assertEqual(
            result.authorization_id,
            "ROOT-T1-001",
        )

        self.assertEqual(
            result.next_gate,
            "PRODUCER_EXECUTION",
        )

        self.assertIsNone(
            result.artifact_path
        )

        self.assertIsNone(
            result.artifact_sha256
        )

        self.assertIsNone(
            result.auditor
        )

        self.assertIsNone(
            result.latest_audit_verdict
        )


    def test_authorize_requires_pass(self):

        self.write_waiting_root(
            verdict="FAIL"
        )

        with self.assertRaises(
            GovernanceError
        ):
            self.engine.root_decide(
                state_path=self.state_path,
                decision_id="ROOT-T1-001",
                decision="AUTHORIZE",
                next_phase="T1",
            )


    def test_authorize_requires_new_phase(self):

        self.write_waiting_root(
            verdict="PASS",
            phase="T0",
        )

        with self.assertRaises(
            GovernanceError
        ):
            self.engine.root_decide(
                state_path=self.state_path,
                decision_id="ROOT-T0-AGAIN",
                decision="AUTHORIZE",
                next_phase="T0",
            )


    def test_repair_moves_to_repair_required(self):

        self.write_waiting_root(
            verdict="PASS_WITH_REPAIRS"
        )

        result = self.engine.root_decide(
            state_path=self.state_path,
            decision_id="ROOT-REPAIR-001",
            decision="REPAIR",
            note="Repair the audited defect.",
        )

        self.assertEqual(
            result.state,
            State.REPAIR_REQUIRED,
        )

        self.assertIsNone(
            result.authorization_id
        )

        self.assertEqual(
            result.next_gate,
            "ROOT_AUTHORIZATION",
        )


    def test_hold_moves_to_hold(self):

        self.write_waiting_root()

        result = self.engine.root_decide(
            state_path=self.state_path,
            decision_id="ROOT-HOLD-001",
            decision="HOLD",
            note="Pause this line.",
        )

        self.assertEqual(
            result.state,
            State.HOLD,
        )

        self.assertIsNone(
            result.authorization_id
        )


    def test_close_moves_to_complete(self):

        self.write_waiting_root()

        result = self.engine.root_decide(
            state_path=self.state_path,
            decision_id="ROOT-CLOSE-001",
            decision="CLOSE",
            note="Close this phase.",
        )

        self.assertEqual(
            result.state,
            State.COMPLETE,
        )

        self.assertIsNone(
            result.next_gate
        )


    def test_root_decision_requires_waiting_root(self):

        state = ProjectState(
            project_id="TEST-PROJECT",
            title="Test Project",
            division=Division.RESEARCH,
            phase="T0",
            state=State.READY,
            owner="producer_a",
            owner_role=Role.PRODUCER,
            lineage=Lineage.CANONICAL,
            next_gate="ROOT_AUTHORIZATION",
        )

        self.vault.write_state(
            self.state_path,
            state,
        )

        with self.assertRaises(
            GovernanceError
        ):
            self.engine.root_decide(
                state_path=self.state_path,
                decision_id="ROOT-HOLD-001",
                decision="HOLD",
            )


if __name__ == "__main__":
    unittest.main()