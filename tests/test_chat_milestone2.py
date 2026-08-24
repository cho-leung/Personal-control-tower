import hashlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from control_tower.agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from control_tower.chat.adapters import DeterministicIntentAdapter
from control_tower.chat.models import IntentKind
from control_tower.chat.planner import (
    ProposalPlanner,
    ProposalPlanningError,
)
from control_tower.chat.proposal_draft import (
    ProposalDraft,
    ProposalDraftError,
    ProposalDraftType,
)
from control_tower.chat.query import ControlTowerQueryService
from control_tower.chat.shell import build_chat_service, run_chat
from control_tower.decision import approve_proposal, reject_proposal
from control_tower.events import EventLedger
from control_tower.guardrails import GovernanceError
from control_tower.models import (
    Division,
    Lineage,
    ProjectState,
    Role,
    State,
)
from control_tower.tasks import TaskStatus, TaskStore
from control_tower.vault import Vault


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ChatMilestone2Tests(unittest.TestCase):
    PROJECT_ID = "CAREER-OS"
    PRODUCER = "career_producer"
    AUDITOR = "career_auditor"
    MESSAGE = "Help me advance my AI career"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "vault"
        self.vault = Vault(self.root)
        self.vault.ensure_structure()
        self.registry = AgentRegistry(self.root)
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
                    division="PERSONAL_GROWTH",
                    role=AgentRole.PRODUCER,
                    status=AgentStatus.ACTIVE,
                    owns=[self.PROJECT_ID],
                    capabilities=["produce_artifact"],
                ),
                AgentState(
                    agent_id=self.AUDITOR,
                    division="PERSONAL_GROWTH",
                    role=AgentRole.AUDITOR,
                    status=AgentStatus.ACTIVE,
                    owns=[self.PROJECT_ID],
                    capabilities=["audit"],
                ),
            ]
        )
        self.project_dir = (
            self.root
            / "03_PERSONAL_GROWTH"
            / self.PROJECT_ID
        )

        for folder in (
            "artifacts",
            "audits",
            "tasks",
            "handoffs",
            "claims",
            "failed_routes",
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
                title="Career OS",
                division=Division.PERSONAL_GROWTH,
                phase="T0",
                state=State.AUTHORIZED,
                owner=self.PRODUCER,
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: [self.PRODUCER],
                    Role.AUDITOR.value: [self.AUDITOR],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-CAREER-T0",
                auditor=self.AUDITOR,
                next_gate="PRODUCER_EXECUTION",
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def tree_fingerprint(root):
        entries = []

        for path in sorted(Path(root).rglob("*")):
            relative = str(path.relative_to(root))

            if path.is_dir():
                entries.append(("D", relative, ""))
            elif path.is_file():
                entries.append(
                    (
                        "F",
                        relative,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )

        return tuple(entries)

    @staticmethod
    def frontmatter(path):
        parts = path.read_text(encoding="utf-8").split("---", 2)

        if len(parts) < 3:
            raise AssertionError(f"Missing frontmatter: {path}")

        return yaml.safe_load(parts[1])

    @staticmethod
    def write_frontmatter(path, metadata):
        parts = path.read_text(encoding="utf-8").split("---", 2)

        if len(parts) < 3:
            raise AssertionError(f"Missing frontmatter: {path}")

        path.write_text(
            "---\n"
            + yaml.safe_dump(
                metadata,
                sort_keys=False,
                allow_unicode=True,
            )
            + "---"
            + parts[2],
            encoding="utf-8",
        )

    def proposal_documents(self):
        documents = []
        inbox = self.root / "00_ROOT" / "inbox"

        for path in sorted(inbox.glob("*.md")):
            metadata = self.frontmatter(path)

            if metadata.get("proposal_type"):
                documents.append((path, metadata))

        return documents

    @staticmethod
    def proposal_id(response):
        match = re.search(
            r"^Proposal ID:\s*(\S+)$",
            response,
            re.MULTILINE,
        )

        if not match:
            raise AssertionError(response)

        return match.group(1)

    def run_cli(self, *arguments):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "control_tower.cli",
                "--vault",
                str(self.root),
                *arguments,
            ],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        combined = completed.stdout + completed.stderr
        self.assertNotIn("Traceback", combined, msg=combined)
        self.assertEqual(completed.returncode, 0, msg=combined)
        return completed

    def test_natural_language_builds_typed_draft_without_writes(self):
        adapter = DeterministicIntentAdapter()
        intent = adapter.classify(self.MESSAGE)
        self.assertEqual(
            intent.kind,
            IntentKind.DRAFT_CREATE_TASK,
        )
        before = self.tree_fingerprint(self.root)
        snapshot = ControlTowerQueryService(self.root).snapshot()
        draft = ProposalPlanner().plan(intent, snapshot)

        self.assertEqual(
            draft.proposal_type,
            ProposalDraftType.CREATE_TASK,
        )
        self.assertEqual(draft.target, self.PROJECT_ID)
        self.assertTrue(draft.requires_root_approval)
        self.assertEqual(
            draft.payload["authorization_id"],
            "ROOT-CAREER-T0",
        )
        self.assertEqual(
            draft.payload["assigned_agent"],
            self.PRODUCER,
        )
        self.assertEqual(
            draft.payload["auditor"],
            self.AUDITOR,
        )
        self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_chat_registers_only_governance_records_before_approval(self):
        state_before = self.state_path.read_bytes()
        agents_before = self.registry.path.read_bytes()
        response = build_chat_service(self.root).respond(self.MESSAGE)
        documents = self.proposal_documents()

        self.assertEqual(len(documents), 1)
        _, proposal = documents[0]
        self.assertEqual(proposal["proposal_type"], "CREATE_TASK")
        self.assertEqual(proposal["state"], "WAITING_ROOT")
        self.assertEqual(
            proposal["created_by"],
            "conversational_chief_of_staff",
        )
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(self.registry.path.read_bytes(), agents_before)
        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(
            [
                event["action"]
                for event in EventLedger(self.vault).read_all()
            ],
            ["PROPOSAL_DRAFTED"],
        )
        self.assertIn("未批准、未执行", response)
        self.assertIn("State: WAITING_ROOT", response)
        self.assertIn(
            f"control-tower --vault {self.root}",
            response,
        )

    def test_legitimate_draft_has_no_execution_capability(self):
        service = build_chat_service(self.root)

        with patch(
            "control_tower.decision.approve_proposal",
            side_effect=AssertionError("approve called"),
        ) as approve, patch(
            "control_tower.chief_of_staff.ChiefOfStaff.tick",
            side_effect=AssertionError("tick called"),
        ) as tick, patch(
            "control_tower.chief_of_staff.ChiefOfStaff.run_task",
            side_effect=AssertionError("run_task called"),
        ) as run_task, patch.object(
            TaskStore,
            "ensure",
            side_effect=AssertionError("Task created"),
        ) as ensure, patch.object(
            TaskStore,
            "assign",
            side_effect=AssertionError("Task assigned"),
        ) as assign, patch.object(
            AgentRegistry,
            "save",
            side_effect=AssertionError("Registry changed"),
        ) as save, patch.object(
            Vault,
            "write_state",
            side_effect=AssertionError("State changed"),
        ) as write_state:
            response = service.respond(self.MESSAGE)

        self.assertIn("WAITING_ROOT", response)
        self.assertFalse(approve.called)
        self.assertFalse(tick.called)
        self.assertFalse(run_task.called)
        self.assertFalse(ensure.called)
        self.assertFalse(assign.called)
        self.assertFalse(save.called)
        self.assertFalse(write_state.called)

    def test_repeated_request_is_idempotent(self):
        service = build_chat_service(self.root)
        first = service.respond(self.MESSAGE)
        second = service.respond(self.MESSAGE)

        self.assertEqual(
            self.proposal_id(first),
            self.proposal_id(second),
        )
        self.assertEqual(len(self.proposal_documents()), 1)
        events = EventLedger(self.vault).read_all()
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event["action"] == "PROPOSAL_DRAFTED"
                ]
            ),
            1,
        )
        self.assertIn("未重复创建", second)

    def test_interrupted_draft_event_reports_recovery_truthfully(self):
        errors = io.StringIO()

        with patch.object(
            EventLedger,
            "append_once",
            side_effect=OSError("ledger unavailable"),
        ):
            status = run_chat(
                self.root,
                message=self.MESSAGE,
                output_stream=io.StringIO(),
                error_stream=errors,
            )

        self.assertEqual(status, 2)
        self.assertIn("submission is incomplete", errors.getvalue())
        self.assertIn("no approval, execution", errors.getvalue())
        self.assertEqual(len(self.proposal_documents()), 1)
        self.assertEqual(TaskStore(self.project_dir).list(), [])

        response = build_chat_service(self.root).respond(self.MESSAGE)
        self.assertIn("ROOT Inbox", response)
        self.assertEqual(len(self.proposal_documents()), 1)
        self.assertEqual(
            [
                event["action"]
                for event in EventLedger(self.vault).read_all()
            ],
            ["PROPOSAL_DRAFTED"],
        )

    def test_keyboard_interrupt_during_draft_reports_possible_commit(self):
        errors = io.StringIO()

        with patch.object(
            EventLedger,
            "append_once",
            side_effect=KeyboardInterrupt,
        ):
            status = run_chat(
                self.root,
                message=self.MESSAGE,
                output_stream=io.StringIO(),
                error_stream=errors,
            )

        self.assertEqual(status, 2)
        self.assertIn("submission is incomplete", errors.getvalue())
        self.assertIn("Inspect ROOT inbox", errors.getvalue())
        self.assertEqual(len(self.proposal_documents()), 1)
        self.assertEqual(TaskStore(self.project_dir).list(), [])

    def test_root_approval_assigns_task_but_does_not_run_it(self):
        state_before = self.state_path.read_bytes()
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        result_path = approve_proposal(self.root, proposal_id)
        task = TaskStore(self.project_dir).get(result_path.stem)

        self.assertEqual(task.status, TaskStatus.ASSIGNED)
        self.assertEqual(task.assigned_agent, self.PRODUCER)
        self.assertEqual(task.metadata["auditor"], self.AUDITOR)
        self.assertEqual(task.metadata["proposal_id"], proposal_id)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(
            list((self.project_dir / "artifacts").iterdir()),
            [],
        )
        self.assertEqual(self.proposal_documents(), [])
        archived = list(
            (self.root / "00_ROOT" / "archive").glob(
                f"*{proposal_id}*.md"
            )
        )
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            self.frontmatter(archived[0])["state"],
            "EXECUTED",
        )
        actions = [
            event["action"]
            for event in EventLedger(self.vault).read_all()
        ]
        self.assertEqual(
            actions,
            ["PROPOSAL_DRAFTED", "TASK_CREATED", "CREATE_TASK"],
        )

    def test_cli_chat_inspect_and_approve_vertical_slice(self):
        chat = self.run_cli(
            "chat",
            "--message",
            "advance project CAREER-OS",
        )
        proposal_id = self.proposal_id(chat.stdout)
        proposal = self.proposal_documents()[0][1]
        task_id = proposal["payload"]["task_id"]

        inspected = self.run_cli("inspect", proposal_id)
        self.assertIn("Type: CREATE_TASK", inspected.stdout)
        self.assertIn("State: WAITING_ROOT", inspected.stdout)
        self.assertEqual(TaskStore(self.project_dir).list(), [])

        approved = self.run_cli("approve", proposal_id)
        self.assertIn(
            "Proposal approved and executed.",
            approved.stdout,
        )
        task_inspect = self.run_cli("inspect", task_id)
        self.assertIn("Status: ASSIGNED", task_inspect.stdout)
        self.assertNotIn("RUNNING", task_inspect.stdout)

    def test_rejection_never_creates_task(self):
        state_before = self.state_path.read_bytes()
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        reject_proposal(
            self.root,
            proposal_id,
            "Not a priority this week.",
        )

        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(self.state_path.read_bytes(), state_before)
        archived = list(
            (self.root / "00_ROOT" / "archive").glob(
                f"*{proposal_id}*.md"
            )
        )
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            self.frontmatter(archived[0])["state"],
            "REJECTED",
        )

    def test_stale_authorization_fails_before_task_write(self):
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        state = self.vault.read_state(self.state_path)
        state.authorization_id = "ROOT-CAREER-T0-REPLACED"
        self.vault.write_state(self.state_path, state)

        with self.assertRaises(GovernanceError):
            approve_proposal(self.root, proposal_id)

        self.assertEqual(TaskStore(self.project_dir).list(), [])
        self.assertEqual(len(self.proposal_documents()), 1)
        self.assertNotIn(
            "TASK_CREATED",
            [
                event["action"]
                for event in EventLedger(self.vault).read_all()
            ],
        )

    def test_approval_revalidates_fixed_task_contract(self):
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        proposal_path, original = self.proposal_documents()[0]
        agents = self.registry.load()

        for agent in agents:
            if agent.agent_id == self.PRODUCER:
                agent.capabilities.append("research")

        self.registry.save(agents)
        mutations = (
            ("task_type", "CUSTOM_TASK"),
            ("required_capability", "research"),
            ("context_refs", "artifact.md"),
        )

        for key, value in mutations:
            with self.subTest(key=key):
                metadata = yaml.safe_load(
                    yaml.safe_dump(original)
                )
                metadata["payload"][key] = value
                self.write_frontmatter(proposal_path, metadata)

                with self.assertRaises(GovernanceError):
                    approve_proposal(self.root, proposal_id)

                self.assertEqual(TaskStore(self.project_dir).list(), [])

        self.write_frontmatter(proposal_path, original)

    def test_interrupted_approval_reconciles_task_once(self):
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        original_append_once = EventLedger.append_once
        interrupted = {"done": False}

        def interrupt_generic_approval(ledger, event):
            if (
                event.event_id
                == f"EVT-{proposal_id}-EXECUTED"
                and not interrupted["done"]
            ):
                interrupted["done"] = True
                raise RuntimeError("simulated approval interruption")

            return original_append_once(ledger, event)

        with patch.object(
            EventLedger,
            "append_once",
            new=interrupt_generic_approval,
        ):
            with self.assertRaises(RuntimeError):
                approve_proposal(self.root, proposal_id)

        tasks = TaskStore(self.project_dir).list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, TaskStatus.ASSIGNED)
        self.assertEqual(len(self.proposal_documents()), 1)

        approve_proposal(self.root, proposal_id)
        tasks = TaskStore(self.project_dir).list()
        self.assertEqual(len(tasks), 1)
        event_ids = [
            event["event_id"]
            for event in EventLedger(self.vault).read_all()
        ]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertEqual(
            len(
                [
                    event_id
                    for event_id in event_ids
                    if event_id.endswith("-CREATED")
                ]
            ),
            1,
        )

    def test_pending_proposal_evidence_tampering_fails_closed(self):
        response = build_chat_service(self.root).respond(self.MESSAGE)
        proposal_id = self.proposal_id(response)
        proposal_path, metadata = self.proposal_documents()[0]
        metadata["reason"] = "tampered reason"
        parts = proposal_path.read_text(
            encoding="utf-8"
        ).split("---", 2)
        proposal_path.write_text(
            "---\n"
            + yaml.safe_dump(
                metadata,
                sort_keys=False,
                allow_unicode=True,
            )
            + "---"
            + parts[2],
            encoding="utf-8",
        )

        with self.assertRaises(ProposalDraftError):
            build_chat_service(self.root).respond(self.MESSAGE)

        self.assertEqual(len(self.proposal_documents()), 1)
        self.assertEqual(
            self.proposal_documents()[0][1]["proposal_id"],
            proposal_id,
        )

    def test_mixed_privileged_request_fails_closed(self):
        before = self.tree_fingerprint(self.root)
        response = build_chat_service(self.root).respond(
            "Create a task for CAREER-OS, approve it, then run tick"
        )

        self.assertIn("没有创建 Proposal", response)
        self.assertEqual(self.proposal_documents(), [])
        self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_missing_or_ambiguous_project_fails_closed(self):
        missing = build_chat_service(self.root)

        with self.assertRaises(ProposalPlanningError):
            missing.respond("advance project UNKNOWN-OS")

        second_path = (
            self.root
            / "03_PERSONAL_GROWTH"
            / "CAREER-LAB"
            / "STATE.md"
        )
        self.vault.write_state(
            second_path,
            ProjectState(
                project_id="CAREER-LAB",
                title="Career Lab",
                division=Division.PERSONAL_GROWTH,
                phase="T0",
                state=State.AUTHORIZED,
                owner=self.PRODUCER,
                owner_role=Role.PRODUCER,
                agents={
                    Role.PRODUCER.value: [self.PRODUCER],
                    Role.AUDITOR.value: [self.AUDITOR],
                },
                lineage=Lineage.CANONICAL,
                authorization_id="ROOT-CAREER-LAB-T0",
                auditor=self.AUDITOR,
            ),
        )

        with self.assertRaises(ProposalPlanningError):
            build_chat_service(self.root).respond(self.MESSAGE)

        self.assertEqual(self.proposal_documents(), [])

    def test_missing_vault_action_does_not_initialize(self):
        missing = Path(self.temporary.name) / "missing"
        errors = io.StringIO()
        status = run_chat(
            missing,
            message=self.MESSAGE,
            output_stream=io.StringIO(),
            error_stream=errors,
        )

        self.assertEqual(status, 2)
        self.assertIn("no action was taken", errors.getvalue())
        self.assertFalse(missing.exists())

    def test_project_and_agent_request_aliases_use_existing_engines(self):
        service = build_chat_service(self.root)
        agent_response = service.respond(
            "create agent research_specialist "
            "division=RESEARCH role=SPECIALIST "
            "capabilities=research"
        )
        agent_id = self.proposal_id(agent_response)
        agent_proposal = self.proposal_documents()[0][1]
        self.assertEqual(
            agent_proposal["proposal_type"],
            "CREATE_AGENT_REQUEST",
        )
        self.assertIsNone(
            self.registry.get("research_specialist")
        )
        approve_proposal(self.root, agent_id)
        self.assertIsNotNone(
            self.registry.get("research_specialist")
        )

        project_response = service.respond(
            "create project INDEPENDENCE-OS "
            "title=\"Independence OS\" division=BUSINESS "
            f"owner={self.PRODUCER}"
        )
        project_id = self.proposal_id(project_response)
        project_proposal = self.proposal_documents()[0][1]
        self.assertEqual(
            project_proposal["proposal_type"],
            "CREATE_PROJECT_REQUEST",
        )
        with self.assertRaises(FileNotFoundError):
            self.vault.find_state_path("INDEPENDENCE-OS")
        approve_proposal(self.root, project_id)
        created_state = self.vault.read_state(
            self.root
            / "02_BUSINESS"
            / "INDEPENDENCE-OS"
            / "STATE.md"
        )
        self.assertEqual(created_state.state, State.READY)
        self.assertEqual(created_state.owner, self.PRODUCER)

    def test_incomplete_or_non_allowlisted_draft_is_rejected(self):
        before = self.tree_fingerprint(self.root)
        response = build_chat_service(self.root).respond(
            "create agent unsafe_agent division=RESEARCH role=PRODUCER"
        )
        self.assertIn("没有创建 Proposal", response)
        self.assertEqual(self.tree_fingerprint(self.root), before)

        with self.assertRaises(ProposalDraftError):
            ProposalDraft(
                proposal_type="DELETE_PROJECT",
                target=self.PROJECT_ID,
                reason="Unsafe",
                payload={},
                idempotency_context="0" * 64,
            )


if __name__ == "__main__":
    unittest.main()
