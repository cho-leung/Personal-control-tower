import tempfile
import unittest
from pathlib import Path

from control_tower.agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from control_tower.core.agent_lifecycle_engine import (
    AgentLifecycleEngine,
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
from control_tower.vault import Vault


class AgentLifecycleEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = Vault(self.root)
        self.vault.ensure_structure()
        self.registry = AgentRegistry(self.root)
        self.engine = AgentLifecycleEngine(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def add_agent(
        self,
        agent_id,
        role=AgentRole.SPECIALIST,
        capabilities=None,
        status=AgentStatus.ACTIVE,
    ):
        agents = self.registry.load()
        agents.append(
            AgentState(
                agent_id=agent_id,
                division=Division.RESEARCH.value,
                role=role,
                status=status,
                owns=[],
                capabilities=list(capabilities or ["research"]),
                notes="Test agent.",
            )
        )
        self.registry.save(agents)

    def proposal(self, proposal_type, agent_id, **payload):
        data = {"agent_id": agent_id}
        data.update(payload)
        return Proposal(
            proposal_id=f"{proposal_type}-TEST",
            proposal_type=proposal_type,
            target=agent_id,
            reason="Lifecycle test.",
            state=ProposalState.WAITING_ROOT,
            created_by="TEST",
            payload=data,
        )

    def write_project(
        self,
        project_id,
        owner,
        state=State.ACTIVE,
        agents=None,
        auditor=None,
    ):
        state_path = (
            self.root
            / "01_RESEARCH"
            / project_id
            / "STATE.md"
        )
        project = ProjectState(
            project_id=project_id,
            title=project_id,
            division=Division.RESEARCH,
            phase="T0",
            state=state,
            owner=owner,
            owner_role=Role.PRODUCER,
            agents=agents or {
                Role.PRODUCER.value: [owner],
            },
            lineage=Lineage.CANONICAL,
            auditor=auditor,
        )
        self.vault.write_state(state_path, project)
        return state_path

    def test_archive_is_idempotent_and_preserves_agent(self):
        self.add_agent("worker")
        proposal = self.proposal("ARCHIVE_AGENT", "worker")

        first = self.engine.execute(proposal)
        second = self.engine.execute(proposal)

        self.assertEqual(first, self.registry.path)
        self.assertEqual(second, self.registry.path)
        archived = self.registry.get("worker")
        self.assertIsNotNone(archived)
        self.assertEqual(archived.status, AgentStatus.ARCHIVED)
        self.assertEqual(archived.capabilities, ["research"])

    def test_personal_root_cannot_be_archived(self):
        with self.assertRaises(GovernanceError):
            self.engine.archive_agent(
                self.proposal("ARCHIVE_AGENT", "personal_root")
            )

        self.assertEqual(
            self.registry.get("personal_root").status,
            AgentStatus.ACTIVE,
        )

    def test_archive_rejects_active_project_owner(self):
        self.add_agent(
            "producer",
            role=AgentRole.PRODUCER,
            capabilities=["produce_artifact"],
        )
        self.write_project("ACTIVE-PROJECT", "producer")

        with self.assertRaisesRegex(
            GovernanceError,
            "active project owner",
        ):
            self.engine.archive_agent(
                self.proposal("ARCHIVE_AGENT", "producer")
            )

        self.assertEqual(
            self.registry.get("producer").status,
            AgentStatus.ACTIVE,
        )

    def test_archive_rejects_unfinished_task(self):
        self.add_agent("worker")
        tasks_dir = (
            self.root
            / "01_RESEARCH"
            / "TASK-PROJECT"
            / "tasks"
        )
        tasks_dir.mkdir(parents=True)
        task_path = tasks_dir / "TASK-1.md"
        task_path.write_text(
            """---
task_id: TASK-1
assigned_agent: worker
status: RUNNING
---
# Task
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "unfinished tasks",
        ):
            self.engine.archive_agent(
                self.proposal("ARCHIVE_AGENT", "worker")
            )

        task_path.write_text(
            """---
task_id: TASK-1
assigned_agent: worker
status: FAILED
---
# Task
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "unfinished tasks",
        ):
            self.engine.archive_agent(
                self.proposal("ARCHIVE_AGENT", "worker")
            )

        task_path.write_text(
            """---
task_id: TASK-1
assigned_agent: worker
status: COMPLETED
---
# Task
""",
            encoding="utf-8",
        )
        self.engine.archive_agent(
            self.proposal("ARCHIVE_AGENT", "worker")
        )
        self.assertEqual(
            self.registry.get("worker").status,
            AgentStatus.ARCHIVED,
        )

    def test_role_update_syncs_non_owner_bindings(self):
        self.add_agent(
            "reviewer",
            role=AgentRole.AUDITOR,
            capabilities=["audit"],
        )
        state_path = self.write_project(
            "BOUND-PROJECT",
            "another_producer",
            agents={
                Role.PRODUCER.value: ["another_producer"],
                Role.AUDITOR.value: ["reviewer"],
            },
        )
        proposal = self.proposal(
            "UPDATE_AGENT_ROLE",
            "reviewer",
            expected_role="AUDITOR",
            new_role="VALIDATOR",
        )

        self.engine.update_agent_role(proposal)
        self.engine.update_agent_role(proposal)

        self.assertEqual(
            self.registry.get("reviewer").role,
            AgentRole.VALIDATOR,
        )
        state = self.vault.read_state(state_path)
        self.assertNotIn("reviewer", state.agents.get("AUDITOR", []))
        self.assertEqual(
            state.agents["VALIDATOR"],
            ["reviewer"],
        )

    def test_role_update_rejects_project_owner(self):
        self.add_agent(
            "producer",
            role=AgentRole.PRODUCER,
            capabilities=["produce_artifact"],
        )
        self.write_project("OWNED-PROJECT", "producer")

        with self.assertRaisesRegex(
            GovernanceError,
            "project owner",
        ):
            self.engine.update_agent_role(
                self.proposal(
                    "UPDATE_AGENT_ROLE",
                    "producer",
                    new_role="SPECIALIST",
                )
            )

        self.assertEqual(
            self.registry.get("producer").role,
            AgentRole.PRODUCER,
        )

    def test_role_update_retry_repairs_stale_binding(self):
        self.add_agent(
            "reviewer",
            role=AgentRole.VALIDATOR,
            capabilities=["audit"],
        )
        state_path = self.write_project(
            "PARTIAL-UPDATE",
            "another_producer",
            agents={
                Role.PRODUCER.value: ["another_producer"],
                Role.AUDITOR.value: ["reviewer"],
            },
        )

        self.engine.update_agent_role(
            self.proposal(
                "UPDATE_AGENT_ROLE",
                "reviewer",
                expected_role="AUDITOR",
                new_role="VALIDATOR",
            )
        )

        state = self.vault.read_state(state_path)
        self.assertNotIn("reviewer", state.agents.get("AUDITOR", []))
        self.assertEqual(
            state.agents["VALIDATOR"],
            ["reviewer"],
        )

    def test_role_update_rejects_stale_expected_role(self):
        self.add_agent("worker")

        with self.assertRaisesRegex(
            GovernanceError,
            "idempotency conflict",
        ):
            self.engine.update_agent_role(
                self.proposal(
                    "UPDATE_AGENT_ROLE",
                    "worker",
                    expected_role="AUDITOR",
                    new_role="VALIDATOR",
                )
            )

    def test_role_update_rejects_unfinished_task(self):
        self.add_agent("worker")
        tasks_dir = (
            self.root
            / "01_RESEARCH"
            / "TASK-PROJECT"
            / "tasks"
        )
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "TASK-ROLE.md").write_text(
            """---
task_id: TASK-ROLE
assigned_agent: worker
status: ASSIGNED
---
# Task
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "unfinished tasks",
        ):
            self.engine.update_agent_role(
                self.proposal(
                    "UPDATE_AGENT_ROLE",
                    "worker",
                    new_role="VALIDATOR",
                )
            )

    def test_capability_add_remove_are_idempotent(self):
        self.add_agent("worker")
        add = self.proposal(
            "UPDATE_AGENT_CAPABILITY",
            "worker",
            operation="ADD",
            capability="summarize",
        )
        remove = self.proposal(
            "UPDATE_AGENT_CAPABILITY",
            "worker",
            operation="REMOVE",
            capability="research",
        )

        self.engine.update_agent_capability(add)
        self.engine.update_agent_capability(add)
        self.engine.update_agent_capability(remove)
        self.engine.update_agent_capability(remove)

        self.assertEqual(
            self.registry.get("worker").capabilities,
            ["summarize"],
        )

    def test_cannot_remove_required_root_capability(self):
        with self.assertRaisesRegex(
            GovernanceError,
            "required personal_root capability",
        ):
            self.engine.update_agent_capability(
                self.proposal(
                    "UPDATE_AGENT_CAPABILITY",
                    "personal_root",
                    operation="REMOVE",
                    capability="approve",
                )
            )

    def test_cannot_remove_capability_required_by_active_binding(self):
        self.add_agent(
            "reviewer",
            role=AgentRole.AUDITOR,
            capabilities=["audit"],
        )
        self.write_project(
            "AUDIT-PROJECT",
            "another_producer",
            agents={
                Role.PRODUCER.value: ["another_producer"],
                Role.AUDITOR.value: ["reviewer"],
            },
        )

        with self.assertRaisesRegex(
            GovernanceError,
            "required by active projects",
        ):
            self.engine.update_agent_capability(
                self.proposal(
                    "UPDATE_AGENT_CAPABILITY",
                    "reviewer",
                    operation="REMOVE",
                    capability="audit",
                )
            )

    def test_target_payload_mismatch_is_rejected(self):
        self.add_agent("worker")
        proposal = self.proposal(
            "ARCHIVE_AGENT",
            "worker",
        )
        proposal.target = "someone_else"

        with self.assertRaisesRegex(
            GovernanceError,
            "does not match",
        ):
            self.engine.archive_agent(proposal)


if __name__ == "__main__":
    unittest.main()
