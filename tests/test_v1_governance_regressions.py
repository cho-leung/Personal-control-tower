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
from control_tower.bus import ControlTowerBus
from control_tower.chief_of_staff import ChiefOfStaff
from control_tower.core.agent_creation_engine import (
    AgentCreationEngine,
)
from control_tower.core.agent_lifecycle_engine import (
    AgentLifecycleEngine,
)
from control_tower.core.decision_engine import DecisionEngine
from control_tower.core.execution_engine import ExecutionEngine
from control_tower.core.project_creation_engine import (
    ProjectCreationEngine,
)
from control_tower.decision import approve_proposal
from control_tower.events import (
    Event,
    EventConflictError,
    EventLedger,
    EventResult,
)
from control_tower.guardrails import GovernanceError
from control_tower.models import (
    Division,
    Lineage,
    ProjectState,
    Proposal,
    ProposalState,
    Role,
    State,
)
from control_tower.proposals import (
    create_sync_proposal,
    write_proposal,
)
from control_tower.runtime import AgentResult, MockAgentRuntime
from control_tower.tasks import Task, TaskStatus, TaskStore
from control_tower.vault import Vault


class V1GovernanceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        self.vault = Vault(self.root)
        self.vault.ensure_structure()
        self.registry = AgentRegistry(self.root)
        agents = self.registry.load()
        agents.extend(
            [
                AgentState(
                    agent_id="producer_a",
                    division="RESEARCH",
                    role=AgentRole.PRODUCER,
                    status=AgentStatus.ACTIVE,
                    owns=[],
                    capabilities=["produce_artifact"],
                ),
                AgentState(
                    agent_id="auditor_a",
                    division="RESEARCH",
                    role=AgentRole.AUDITOR,
                    status=AgentStatus.ACTIVE,
                    owns=[],
                    capabilities=["audit"],
                ),
                AgentState(
                    agent_id="worker_a",
                    division="RESEARCH",
                    role=AgentRole.SPECIALIST,
                    status=AgentStatus.ACTIVE,
                    owns=[],
                    capabilities=["research"],
                ),
            ]
        )
        self.registry.save(agents)
        self.events = EventLedger(self.vault)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def proposal(proposal_type, target, payload):
        return Proposal(
            proposal_id=f"{proposal_type}-REGRESSION",
            proposal_type=proposal_type,
            target=target,
            reason="Governance regression test.",
            state=ProposalState.WAITING_ROOT,
            created_by="TEST",
            payload=payload,
        )

    def project_proposal(
        self,
        project_id="PROJECT-A",
        division="RESEARCH",
        target=None,
    ):
        return self.proposal(
            "CREATE_PROJECT",
            target or project_id,
            {
                "project_id": project_id,
                "title": project_id,
                "division": division,
                "owner": "producer_a",
                "phase": "T0",
                "lineage": "CANONICAL",
            },
        )

    def test_bus_project_creation_is_a_root_proposal(self):
        proposal, proposal_path = (
            ControlTowerBus(self.vault).create_research_project(
                "BUS-PROJECT",
                "Bus Project",
                "producer_a",
                "T0",
            )
        )

        self.assertTrue(proposal_path.exists())

        with self.assertRaises(FileNotFoundError):
            self.vault.find_state_path("BUS-PROJECT")

        approve_proposal(self.root, proposal.proposal_id)
        state = self.vault.read_state(
            self.vault.find_state_path("BUS-PROJECT")
        )
        self.assertEqual(state.state, State.READY)

    def test_project_identity_cannot_split_across_divisions(self):
        engine = ProjectCreationEngine(self.vault)
        engine.create_project(self.project_proposal())

        with self.assertRaisesRegex(
            GovernanceError,
            "another division",
        ):
            engine.create_project(
                self.project_proposal(
                    division="BUSINESS",
                )
            )

        with self.assertRaisesRegex(
            GovernanceError,
            "target",
        ):
            engine.create_project(
                self.project_proposal(
                    project_id="PAYLOAD-ID",
                    target="OTHER-ID",
                )
            )

        self.assertEqual(
            self.vault.find_state_path("PROJECT-A").parent.parent.name,
            "01_RESEARCH",
        )

    def test_unbound_auditor_is_rejected_before_artifact_write(self):
        project_dir = self.root / "01_RESEARCH" / "UNBOUND-AUDIT"
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="UNBOUND-AUDIT",
                title="Unbound Audit",
                division=Division.RESEARCH,
                phase="T0",
                state=State.ACTIVE,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-T0",
            ),
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "AUDITOR binding",
        ):
            ExecutionEngine(
                self.vault,
                self.registry,
                self.events,
            ).producer_complete(
                state_path,
                "producer_a",
                "must not be persisted",
                "auditor_a",
            )

        self.assertFalse((project_dir / "artifacts").exists())
        self.assertEqual(
            self.vault.read_state(state_path).state,
            State.ACTIVE,
        )

    def test_stale_phase_task_cannot_consume_new_authorization(self):
        project_dir = self.root / "01_RESEARCH" / "STALE-TASK"
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="STALE-TASK",
                title="Stale Task",
                division=Division.RESEARCH,
                phase="T1",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-T1",
            ),
        )
        store = TaskStore(project_dir)
        store.create(
            Task(
                task_id="TASK-STALE-T0",
                project_id="STALE-TASK",
                phase="T0",
                task_type="PRODUCE_ARTIFACT",
                assigned_agent="producer_a",
                required_role=Role.PRODUCER.value,
                required_capability="produce_artifact",
                authorization_id="ROOT-T0",
                metadata={"auditor": "auditor_a"},
            )
        )
        store.assign("TASK-STALE-T0")

        with self.assertRaisesRegex(
            GovernanceError,
            "phase",
        ):
            ChiefOfStaff(self.vault).run_task(
                state_path,
                "TASK-STALE-T0",
            )

        self.assertEqual(
            store.get("TASK-STALE-T0").status,
            TaskStatus.BLOCKED,
        )
        self.assertEqual(
            self.vault.read_state(state_path).state,
            State.AUTHORIZED,
        )

    def test_runtime_result_cannot_impersonate_assigned_agent(self):
        project_dir = self.root / "01_RESEARCH" / "RESULT-OWNER"
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="RESULT-OWNER",
                title="Result Owner",
                division=Division.RESEARCH,
                phase="T0",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-RESULT",
            ),
        )
        store = TaskStore(project_dir)
        store.create(
            Task(
                task_id="TASK-RESULT-OWNER",
                project_id="RESULT-OWNER",
                phase="T0",
                task_type="PRODUCE_ARTIFACT",
                assigned_agent="producer_a",
                required_role=Role.PRODUCER.value,
                required_capability="produce_artifact",
                authorization_id="ROOT-RESULT",
                metadata={"auditor": "auditor_a"},
            )
        )
        store.assign("TASK-RESULT-OWNER")

        class ImpersonatingRuntime:
            @staticmethod
            def execute(task, context):
                return AgentResult(
                    task_id=task.task_id,
                    agent_id="auditor_a",
                    artifact_text="forged producer output",
                )

        with self.assertRaisesRegex(
            GovernanceError,
            "agent_id",
        ):
            ChiefOfStaff(
                self.vault,
                runtime=ImpersonatingRuntime(),
            ).run_task(
                state_path,
                "TASK-RESULT-OWNER",
            )

        self.assertEqual(
            store.get("TASK-RESULT-OWNER").status,
            TaskStatus.FAILED,
        )
        self.assertFalse(
            (project_dir / "artifacts").exists()
        )

    def test_interrupted_running_task_requires_explicit_recovery(self):
        project_dir = (
            self.root
            / "01_RESEARCH"
            / "INTERRUPTED-RUNTIME"
        )
        (project_dir / "artifacts").mkdir(
            parents=True
        )
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="INTERRUPTED-RUNTIME",
                title="Interrupted Runtime",
                division=Division.RESEARCH,
                phase="T0",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-INTERRUPTED",
            ),
        )
        store = TaskStore(project_dir)
        store.create(
            Task(
                task_id="TASK-INTERRUPTED",
                project_id="INTERRUPTED-RUNTIME",
                phase="T0",
                task_type="PRODUCE_ARTIFACT",
                assigned_agent="producer_a",
                required_role=Role.PRODUCER.value,
                required_capability="produce_artifact",
                authorization_id="ROOT-INTERRUPTED",
                metadata={"auditor": "auditor_a"},
            )
        )
        store.assign("TASK-INTERRUPTED")

        class InterruptedRuntime:
            @staticmethod
            def execute(task, context):
                raise KeyboardInterrupt(
                    "simulated process interruption"
                )

        with self.assertRaises(KeyboardInterrupt):
            ChiefOfStaff(
                self.vault,
                runtime=InterruptedRuntime(),
            ).run_task(
                state_path,
                "TASK-INTERRUPTED",
            )

        interrupted = store.get("TASK-INTERRUPTED")
        self.assertEqual(
            interrupted.status,
            TaskStatus.RUNNING,
        )
        self.assertEqual(interrupted.attempt, 1)

        recovered = store.recover_for_retry(
            "TASK-INTERRUPTED",
            "Recovered after interrupted runtime.",
        )
        self.assertEqual(
            recovered.status,
            TaskStatus.ASSIGNED,
        )
        self.assertEqual(
            recovered.metadata["recovery_history"],
            [
                {
                    "recovery_number": 1,
                    "attempt": 1,
                    "previous_status": "RUNNING",
                    "reason": (
                        "Recovered after interrupted runtime."
                    ),
                    "recovered_at": recovered.metadata[
                        "recovery_history"
                    ][0]["recovered_at"],
                }
            ],
        )

        completed = ChiefOfStaff(self.vault).run_task(
            state_path,
            "TASK-INTERRUPTED",
        )
        self.assertEqual(
            completed.status,
            TaskStatus.COMPLETED,
        )
        self.assertEqual(completed.attempt, 2)

    def test_committed_producer_evidence_reconciles_without_rerun(self):
        project_dir = (
            self.root
            / "01_RESEARCH"
            / "COMMITTED-PRODUCER"
        )
        (project_dir / "artifacts").mkdir(
            parents=True
        )
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="COMMITTED-PRODUCER",
                title="Committed Producer",
                division=Division.RESEARCH,
                phase="T0",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-COMMITTED",
            ),
        )
        store = TaskStore(project_dir)
        original = Task(
            task_id="TASK-COMMITTED-PRODUCER",
            project_id="COMMITTED-PRODUCER",
            phase="T0",
            task_type="PRODUCE_ARTIFACT",
            assigned_agent="producer_a",
            required_role=Role.PRODUCER.value,
            required_capability="produce_artifact",
            authorization_id="ROOT-COMMITTED",
            metadata={"auditor": "auditor_a"},
        )
        store.create(original)
        store.assign(original.task_id)
        unrelated = Task(
            task_id="TASK-UNRELATED-PRODUCER",
            project_id="COMMITTED-PRODUCER",
            phase="T0",
            task_type="PRODUCE_ARTIFACT",
            assigned_agent="producer_a",
            required_role=Role.PRODUCER.value,
            required_capability="produce_artifact",
            authorization_id="ROOT-COMMITTED",
            metadata={"auditor": "auditor_a"},
        )
        store.create(unrelated)
        store.assign(unrelated.task_id)

        with patch.object(
            TaskStore,
            "complete",
            side_effect=KeyboardInterrupt(
                "interrupted after project commit"
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                ChiefOfStaff(
                    self.vault,
                    runtime=MockAgentRuntime(
                        producer_output=(
                            "committed frozen output"
                        ),
                    ),
                ).run_task(
                    state_path,
                    original.task_id,
                )

        partial_state = self.vault.read_state(
            state_path
        )
        self.assertEqual(
            partial_state.state,
            State.PRODUCER_COMPLETE,
        )
        self.assertEqual(
            store.get(original.task_id).status,
            TaskStatus.RUNNING,
        )

        completed = ChiefOfStaff(
            self.vault
        ).recover_task(
            state_path,
            original.task_id,
            "Recover committed producer evidence.",
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
            completed.output_artifacts[0].sha256,
            partial_state.artifact_sha256,
        )
        self.assertEqual(
            self.vault.read_state(state_path),
            partial_state,
        )

        # Original creation evidence remains safely replayable.
        replay = store.ensure(original)
        self.assertEqual(
            replay.status,
            TaskStatus.COMPLETED,
        )
        actions = [
            event["action"]
            for event in self.events.read_all()
        ]
        self.assertEqual(
            actions.count("TASK_RECONCILED"),
            1,
        )
        produce_event = next(
            event
            for event in self.events.read_all()
            if event["action"] == "PRODUCE_ARTIFACT"
        )
        self.assertEqual(
            produce_event["correlation_id"],
            original.task_id,
        )
        self.assertEqual(
            produce_event["metadata"]["task_id"],
            original.task_id,
        )

        unrelated_result = ChiefOfStaff(
            self.vault
        ).recover_task(
            state_path,
            unrelated.task_id,
            "Must not claim another Task's artifact.",
        )
        self.assertEqual(
            unrelated_result.status,
            TaskStatus.ASSIGNED,
        )
        self.assertEqual(unrelated_result.attempt, 0)

    def test_state_only_producer_recovery_rejects_stale_sibling(self):
        project_dir = (
            self.root
            / "01_RESEARCH"
            / "STATE-ONLY-LINEAGE"
        )
        (project_dir / "artifacts").mkdir(
            parents=True
        )
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="STATE-ONLY-LINEAGE",
                title="State Only Lineage",
                division=Division.RESEARCH,
                phase="T0",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-STATE-ONLY",
            ),
        )
        store = TaskStore(project_dir)

        def producer_task(task_id):
            return Task(
                task_id=task_id,
                project_id="STATE-ONLY-LINEAGE",
                phase="T0",
                task_type="PRODUCE_ARTIFACT",
                assigned_agent="producer_a",
                required_role=Role.PRODUCER.value,
                required_capability="produce_artifact",
                authorization_id="ROOT-STATE-ONLY",
                metadata={"auditor": "auditor_a"},
            )

        stale = producer_task("TASK-STALE-SIBLING")
        real = producer_task("TASK-REAL-COMMIT")
        store.create(stale)
        store.assign(stale.task_id)
        store.start(stale.task_id)
        store.fail(stale.task_id, "Earlier failed attempt.")
        store.create(real)
        store.assign(real.task_id)

        original_append_once = EventLedger.append_once

        def interrupt_before_producer_event(ledger, event):
            if event.action == "PRODUCE_ARTIFACT":
                raise KeyboardInterrupt(
                    "state committed before event"
                )

            return original_append_once(ledger, event)

        with patch.object(
            EventLedger,
            "append_once",
            new=interrupt_before_producer_event,
        ):
            with self.assertRaises(KeyboardInterrupt):
                ChiefOfStaff(self.vault).run_task(
                    state_path,
                    real.task_id,
                )

        self.assertEqual(
            self.vault.read_state(state_path).state,
            State.PRODUCER_COMPLETE,
        )
        self.assertEqual(
            store.get(real.task_id).status,
            TaskStatus.RUNNING,
        )
        self.assertFalse(
            any(
                event["action"] == "PRODUCE_ARTIFACT"
                for event in self.events.read_all()
            )
        )

        stale_result = ChiefOfStaff(
            self.vault
        ).recover_task(
            state_path,
            stale.task_id,
            "Retry stale sibling.",
        )
        self.assertEqual(
            stale_result.status,
            TaskStatus.ASSIGNED,
        )

        real_result = ChiefOfStaff(
            self.vault
        ).recover_task(
            state_path,
            real.task_id,
            "Recover actual state commit.",
        )
        self.assertEqual(
            real_result.status,
            TaskStatus.COMPLETED,
        )
        producer_event = next(
            event
            for event in self.events.read_all()
            if event["action"] == "PRODUCE_ARTIFACT"
        )
        self.assertEqual(
            producer_event["correlation_id"],
            real.task_id,
        )
        self.assertEqual(
            store.get(stale.task_id).status,
            TaskStatus.ASSIGNED,
        )

    def test_competing_producer_task_is_blocked_only_once(self):
        project_dir = self.root / "01_RESEARCH" / "ONE-PRODUCER"
        (project_dir / "artifacts").mkdir(parents=True)
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="ONE-PRODUCER",
                title="One Producer Action",
                division=Division.RESEARCH,
                phase="T0",
                state=State.AUTHORIZED,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-ONE-PRODUCER",
            ),
        )
        store = TaskStore(project_dir)

        for task_id in ("TASK-PRODUCER-1", "TASK-PRODUCER-2"):
            store.create(
                Task(
                    task_id=task_id,
                    project_id="ONE-PRODUCER",
                    phase="T0",
                    task_type="PRODUCE_ARTIFACT",
                    assigned_agent="producer_a",
                    required_role=Role.PRODUCER.value,
                    required_capability="produce_artifact",
                    authorization_id="ROOT-ONE-PRODUCER",
                    metadata={"auditor": "auditor_a"},
                )
            )
            store.assign(task_id)

        first_tick = ChiefOfStaff(self.vault).tick()
        self.assertEqual(
            first_tick["tasks_completed"],
            ["TASK-PRODUCER-1"],
        )
        self.assertEqual(
            [
                failure["task_id"]
                for failure in first_tick["task_failures"]
            ],
            ["TASK-PRODUCER-2"],
        )
        self.assertEqual(
            store.get("TASK-PRODUCER-2").status,
            TaskStatus.BLOCKED,
        )

        second_tick = ChiefOfStaff(self.vault).tick()
        self.assertEqual(second_tick["task_failures"], [])

    def test_only_personal_root_may_hold_root_role(self):
        proposal = self.proposal(
            "CREATE_AGENT",
            "second_root",
            {
                "agent_id": "second_root",
                "division": "ROOT",
                "role": "ROOT",
                "status": "ACTIVE",
                "capabilities": [
                    "approve",
                    "reject",
                    "authorize",
                ],
            },
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "Only personal_root",
        ):
            AgentCreationEngine(self.vault).create_agent(proposal)

        with self.assertRaisesRegex(
            GovernanceError,
            "Only personal_root",
        ):
            AgentLifecycleEngine(self.vault).update_agent_role(
                self.proposal(
                    "UPDATE_AGENT_ROLE",
                    "worker_a",
                    {
                        "agent_id": "worker_a",
                        "new_role": "ROOT",
                    },
                )
            )

    def test_role_change_requires_role_capability(self):
        with self.assertRaisesRegex(
            GovernanceError,
            "requires capabilities: audit",
        ):
            AgentLifecycleEngine(self.vault).update_agent_role(
                self.proposal(
                    "UPDATE_AGENT_ROLE",
                    "worker_a",
                    {
                        "agent_id": "worker_a",
                        "new_role": "AUDITOR",
                    },
                )
            )

        self.assertEqual(
            self.registry.get("worker_a").role,
            AgentRole.SPECIALIST,
        )

    def test_archiving_agent_removes_active_project_binding(self):
        state_path = self.root / "01_RESEARCH" / "BOUND" / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="BOUND",
                title="Bound Worker",
                division=Division.RESEARCH,
                phase="T0",
                state=State.READY,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.SPECIALIST.value: ["worker_a"],
                },
                lineage=Lineage.CANONICAL,
            ),
        )

        proposal = self.proposal(
            "ARCHIVE_AGENT",
            "worker_a",
            {"agent_id": "worker_a"},
        )
        AgentLifecycleEngine(self.vault).archive_agent(proposal)

        self.assertEqual(
            self.registry.get("worker_a").status,
            AgentStatus.ARCHIVED,
        )
        state = self.vault.read_state(state_path)
        self.assertNotIn(
            "worker_a",
            state.agents.get(Role.SPECIALIST.value, []),
        )

    def test_authorization_requires_active_root_and_replays_once(self):
        state_path = ProjectCreationEngine(
            self.vault
        ).create_project(self.project_proposal("ROOT-CHECK"))
        agents = self.registry.load()
        root_index = next(
            index
            for index, agent in enumerate(agents)
            if agent.agent_id == "personal_root"
        )
        root = agents[root_index]
        agents[root_index] = AgentState(
            agent_id=root.agent_id,
            division=root.division,
            role=root.role,
            status=AgentStatus.PAUSED,
            owns=root.owns,
            capabilities=root.capabilities,
            notes=root.notes,
        )
        self.registry.save(agents)
        engine = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "not ACTIVE",
        ):
            engine.authorize(
                state_path,
                "ROOT-AUTH-ONCE",
                "Bounded scope.",
            )

        agents[root_index] = AgentState(
            agent_id=root.agent_id,
            division=root.division,
            role=root.role,
            status=AgentStatus.ACTIVE,
            owns=root.owns,
            capabilities=root.capabilities,
            notes=root.notes,
        )
        self.registry.save(agents)
        first = engine.authorize(
            state_path,
            "ROOT-AUTH-ONCE",
            "Bounded scope.",
        )
        second = engine.authorize(
            state_path,
            "ROOT-AUTH-ONCE",
            "Bounded scope.",
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "note changed",
        ):
            engine.authorize(
                state_path,
                "ROOT-AUTH-ONCE",
                "Different scope.",
            )

        self.assertEqual(first, second)
        events = [
            event
            for event in self.events.read_all()
            if event["event_id"]
            == "EVT-ROOT-CHECK-ROOT-AUTH-ONCE"
        ]
        self.assertEqual(len(events), 1)
        decision_log = (
            self.root / "00_ROOT" / "DECISION_LOG.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            decision_log.count("## ROOT-AUTH-ONCE\n"),
            1,
        )

    def test_decision_log_idempotency_is_project_scoped(self):
        first_path = ProjectCreationEngine(
            self.vault
        ).create_project(self.project_proposal("SHARED-A"))
        second_path = ProjectCreationEngine(
            self.vault
        ).create_project(self.project_proposal("SHARED-B"))
        engine = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        )

        engine.authorize(
            first_path,
            "SHARED-ID",
            "Scope A.",
        )
        engine.authorize(
            second_path,
            "SHARED-ID",
            "Scope B.",
        )

        log = (
            self.root / "00_ROOT" / "DECISION_LOG.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "control-tower-decision:SHARED-A:SHARED-ID",
            log,
        )
        self.assertIn(
            "control-tower-decision:SHARED-B:SHARED-ID",
            log,
        )
        self.assertEqual(log.count("## SHARED-ID\n"), 2)

    def test_repair_uses_new_phase_and_archives_resolved_gate(self):
        project_dir = self.root / "01_RESEARCH" / "REPAIR-LANE"
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="REPAIR-LANE",
                title="Repair Lane",
                division=Division.RESEARCH,
                phase="T0",
                state=State.WAITING_ROOT,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-T0",
                artifact_path=(
                    "01_RESEARCH/REPAIR-LANE/artifacts/T0.txt"
                ),
                artifact_sha256="a" * 64,
                auditor="auditor_a",
                latest_audit_verdict="PASS_WITH_REPAIRS",
                next_gate="ROOT_DECISION",
            ),
        )
        gate_path = (
            self.root
            / "00_ROOT"
            / "inbox"
            / "REPAIR-LANE_T0_GATE.md"
        )
        gate_path.write_text("# Root Gate\n", encoding="utf-8")
        engine = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        )

        repair_state = engine.root_decide(
            state_path,
            "ROOT-REPAIR-T0",
            "REPAIR",
            note="Repair the cited evidence.",
        )
        self.assertEqual(
            repair_state.state,
            State.REPAIR_REQUIRED,
        )
        self.assertFalse(gate_path.exists())
        archived_gates = list(
            (self.root / "00_ROOT" / "archive").glob(
                "*_REPAIR-LANE_T0_GATE.md"
            )
        )
        self.assertEqual(len(archived_gates), 1)

        # Exact replay is a no-op and does not archive a second copy.
        engine.root_decide(
            state_path,
            "ROOT-REPAIR-T0",
            "REPAIR",
            note="Repair the cited evidence.",
        )
        self.assertEqual(
            len(
                list(
                    (self.root / "00_ROOT" / "archive").glob(
                        "*_REPAIR-LANE_T0_GATE.md"
                    )
                )
            ),
            1,
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "distinct next phase",
        ):
            engine.authorize(
                state_path,
                "ROOT-REPAIR-AUTH",
                "Repair and re-audit.",
            )

        authorized = engine.authorize(
            state_path,
            "ROOT-REPAIR-AUTH",
            "Repair and re-audit.",
            next_phase="T0-REPAIR-1",
        )
        self.assertEqual(authorized.phase, "T0-REPAIR-1")
        self.assertEqual(authorized.state, State.AUTHORIZED)
        self.assertIsNone(authorized.artifact_path)
        self.assertIsNone(authorized.artifact_sha256)
        self.assertIsNone(authorized.auditor)
        self.assertIsNone(authorized.latest_audit_verdict)

    def test_root_decision_recovers_after_state_write_interruption(self):
        project_dir = self.root / "01_RESEARCH" / "RECOVER-GATE"
        state_path = project_dir / "STATE.md"
        self.vault.write_state(
            state_path,
            ProjectState(
                project_id="RECOVER-GATE",
                title="Recover Gate",
                division=Division.RESEARCH,
                phase="T0",
                state=State.WAITING_ROOT,
                owner="producer_a",
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: ["producer_a"],
                    Role.AUDITOR.value: ["auditor_a"],
                },
                lineage=Lineage.CANONICAL,
                latest_audit_verdict="FAIL",
                next_gate="ROOT_DECISION",
            ),
        )
        gate_path = (
            self.root
            / "00_ROOT"
            / "inbox"
            / "RECOVER-GATE_T0_GATE.md"
        )
        gate_path.write_text("# Root Gate\n", encoding="utf-8")

        class InterruptingLedger(EventLedger):
            def append_once(self, event):
                raise RuntimeError("simulated interruption")

        interrupted = DecisionEngine(
            self.vault,
            self.registry,
            InterruptingLedger(self.vault),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "simulated interruption",
        ):
            interrupted.root_decide(
                state_path,
                "ROOT-RECOVER-CLOSE",
                "CLOSE",
                note="Close after failed audit.",
            )

        partial = self.vault.read_state(state_path)
        self.assertEqual(partial.state, State.COMPLETE)
        self.assertEqual(
            partial.last_decision_id,
            "ROOT-RECOVER-CLOSE",
        )
        self.assertTrue(gate_path.exists())

        recovered = DecisionEngine(
            self.vault,
            self.registry,
            self.events,
        ).root_decide(
            state_path,
            "ROOT-RECOVER-CLOSE",
            "CLOSE",
            note="Close after failed audit.",
        )

        self.assertEqual(recovered.state, State.COMPLETE)
        self.assertFalse(gate_path.exists())
        event_ids = [
            event["event_id"]
            for event in self.events.read_all()
        ]
        self.assertEqual(
            event_ids.count(
                "EVT-RECOVER-GATE-ROOT-RECOVER-CLOSE"
            ),
            1,
        )
        log = (
            self.root / "00_ROOT" / "DECISION_LOG.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            log.count("## ROOT-RECOVER-CLOSE\n"),
            1,
        )

    def test_create_runtime_rejects_unknown_owner(self):
        registry_path = (
            self.root / "00_ROOT" / "PROJECT_REGISTRY.md"
        )
        registry_path.write_text(
            "# Project Registry\n\n"
            "| Project | Division | Owner | Status | Next Gate |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| LEGACY | RESEARCH | missing_owner | ACTIVE | demo |\n",
            encoding="utf-8",
        )
        proposal = create_sync_proposal(
            "LEGACY",
            str(
                self.root
                / "01_RESEARCH"
                / "LEGACY"
                / "STATE.md"
            ),
        )
        write_proposal(self.root, proposal)

        with self.assertRaisesRegex(
            GovernanceError,
            "Unknown project owner",
        ):
            approve_proposal(
                self.root,
                proposal.proposal_id,
            )

        with self.assertRaises(FileNotFoundError):
            self.vault.find_state_path("LEGACY")

    def test_event_id_replay_rejects_conflicting_evidence(self):
        original = Event(
            event_id="EVT-EVIDENCE-ONCE",
            actor="producer_a",
            action="PRODUCE_ARTIFACT",
            target="PROJECT-A",
            result=EventResult.SUCCESS,
            capability_checked="produce_artifact",
            note="Frozen candidate produced.",
            correlation_id="TASK-1",
            metadata={"sha256": "a" * 64},
        )
        self.assertTrue(self.events.append_once(original))
        self.assertFalse(
            self.events.append_once(
                Event(
                    event_id="EVT-EVIDENCE-ONCE",
                    actor="producer_a",
                    action="PRODUCE_ARTIFACT",
                    target="PROJECT-A",
                    result=EventResult.SUCCESS,
                    capability_checked="produce_artifact",
                    note="Frozen candidate produced.",
                    correlation_id="TASK-1",
                    metadata={"sha256": "a" * 64},
                )
            )
        )

        with self.assertRaisesRegex(
            EventConflictError,
            "conflicting evidence",
        ):
            self.events.append_once(
                Event(
                    event_id="EVT-EVIDENCE-ONCE",
                    actor="auditor_a",
                    action="AUDIT",
                    target="PROJECT-A",
                    result=EventResult.SUCCESS,
                    capability_checked="audit",
                    note="Different fact.",
                )
            )


if __name__ == "__main__":
    unittest.main()
