import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CliV1EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *arguments):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "control_tower.cli",
                "--vault",
                str(self.vault),
                *arguments,
            ],
            cwd=str(REPOSITORY_ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        combined = completed.stdout + completed.stderr
        self.assertNotIn(
            "Traceback",
            combined,
            msg=combined,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=combined,
        )
        return completed

    def create_proposal(self, *arguments):
        completed = self.run_cli(*arguments)
        match = re.search(
            r"^Proposal ID:\s*(\S+)\s*$",
            completed.stdout,
            re.MULTILINE,
        )
        self.assertIsNotNone(
            match,
            msg=completed.stdout,
        )
        return match.group(1)

    @staticmethod
    def read_frontmatter(path):
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)

        if len(parts) < 3:
            raise AssertionError(
                f"Missing YAML frontmatter: {path}"
            )

        return yaml.safe_load(parts[1])

    @staticmethod
    def write_frontmatter(path, data):
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)

        if len(parts) < 3:
            raise AssertionError(
                f"Missing YAML frontmatter: {path}"
            )

        path.write_text(
            "---\n"
            + yaml.safe_dump(
                data,
                sort_keys=False,
                allow_unicode=True,
            )
            + "---"
            + parts[2],
            encoding="utf-8",
        )

    def archived_proposal(self, proposal_id):
        matches = sorted(
            (
                self.vault
                / "00_ROOT"
                / "archive"
            ).glob(f"*{proposal_id}*.md")
        )
        self.assertEqual(
            len(matches),
            1,
            msg=f"Archived matches for {proposal_id}: {matches}",
        )
        return matches[0]

    def read_events(self):
        path = (
            self.vault
            / ".control_tower"
            / "events.jsonl"
        )

        if not path.exists():
            return []

        return [
            json.loads(line)
            for line in path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

    def test_fresh_vault_control_room_commands(self):
        initialized = self.run_cli("init")
        self.assertIn("Initialized:", initialized.stdout)

        status = self.run_cli("status")
        self.assertIn("STATUS", status.stdout)

        dashboard = self.run_cli("dashboard")
        lines = dashboard.stdout.rstrip().splitlines()
        self.assertTrue(lines)
        self.assertEqual(
            lines[-1],
            "END MAIN CONTROL ROOM",
        )
        self.assertNotEqual(lines[-1], "None")

    def test_root_governed_organization_lifecycle(self):
        self.run_cli("init")

        producer_proposal = self.create_proposal(
            "agent-create",
            "e2e_producer",
            "--division",
            "RESEARCH",
            "--role",
            "PRODUCER",
            "--capability",
            "produce_artifact",
        )
        inspected = self.run_cli(
            "inspect",
            producer_proposal,
        )
        self.assertIn("Type: CREATE_AGENT", inspected.stdout)
        self.assertIn("State: WAITING_ROOT", inspected.stdout)
        self.run_cli("approve", producer_proposal)
        self.assertEqual(
            self.read_frontmatter(
                self.archived_proposal(producer_proposal)
            )["state"],
            "EXECUTED",
        )

        project_proposal = self.create_proposal(
            "project-create",
            "E2E-PROJECT",
            "--title",
            "CLI End-to-End Project",
            "--division",
            "RESEARCH",
            "--owner",
            "e2e_producer",
        )
        self.run_cli("approve", project_proposal)

        auditor_proposal = self.create_proposal(
            "agent-create",
            "e2e_auditor",
            "--division",
            "RESEARCH",
            "--role",
            "AUDITOR",
            "--capability",
            "audit",
        )
        self.run_cli("approve", auditor_proposal)

        binding_proposal = self.create_proposal(
            "bind",
            "E2E-PROJECT",
            "e2e_auditor",
            "AUDITOR",
        )
        self.run_cli("approve", binding_proposal)

        rejected_proposal = self.create_proposal(
            "agent-create",
            "rejected_agent",
            "--division",
            "RESEARCH",
            "--role",
            "SPECIALIST",
            "--capability",
            "research",
        )
        self.run_cli(
            "reject",
            rejected_proposal,
            "--note",
            "Not needed for this organization.",
        )
        self.assertEqual(
            self.read_frontmatter(
                self.archived_proposal(rejected_proposal)
            )["state"],
            "REJECTED",
        )

        worker_proposal = self.create_proposal(
            "agent-create",
            "lifecycle_worker",
            "--division",
            "RESEARCH",
            "--role",
            "SPECIALIST",
            "--capability",
            "research",
        )
        self.run_cli("approve", worker_proposal)

        capability_proposal = self.create_proposal(
            "agent-capability",
            "lifecycle_worker",
            "summarize",
            "--operation",
            "ADD",
        )
        self.run_cli("approve", capability_proposal)

        role_proposal = self.create_proposal(
            "agent-role",
            "lifecycle_worker",
            "VALIDATOR",
        )
        self.run_cli("approve", role_proposal)

        archive_proposal = self.create_proposal(
            "agent-archive",
            "lifecycle_worker",
        )
        self.run_cli("approve", archive_proposal)

        agents = yaml.safe_load(
            (
                self.vault
                / "00_ROOT"
                / "agents.yaml"
            ).read_text(encoding="utf-8")
        )
        by_id = {
            agent["agent_id"]: agent
            for agent in agents
        }
        self.assertNotIn("rejected_agent", by_id)
        self.assertEqual(
            by_id["lifecycle_worker"]["role"],
            "VALIDATOR",
        )
        self.assertEqual(
            by_id["lifecycle_worker"]["status"],
            "ARCHIVED",
        )
        self.assertIn(
            "summarize",
            by_id["lifecycle_worker"]["capabilities"],
        )

        project_state = self.read_frontmatter(
            self.vault
            / "01_RESEARCH"
            / "E2E-PROJECT"
            / "STATE.md"
        )
        self.assertEqual(project_state["state"], "READY")
        self.assertEqual(
            project_state["agents"]["PRODUCER"],
            ["e2e_producer"],
        )
        self.assertEqual(
            project_state["agents"]["AUDITOR"],
            ["e2e_auditor"],
        )

        events_before_tick = self.read_events()
        event_ids = [
            event["event_id"]
            for event in events_before_tick
        ]
        self.assertEqual(
            len(event_ids),
            len(set(event_ids)),
        )
        actions = {
            event["action"]
            for event in events_before_tick
        }

        for action in (
            "CREATE_AGENT",
            "CREATE_PROJECT",
            "CREATE_BINDING",
            "UPDATE_AGENT_CAPABILITY",
            "UPDATE_AGENT_ROLE",
            "ARCHIVE_AGENT",
            "REJECT_PROPOSAL",
        ):
            self.assertIn(action, actions)

        first_tick = self.run_cli("tick")
        second_tick = self.run_cli("tick")
        self.assertIn("CONTROL TOWER TICK", first_tick.stdout)
        self.assertIn(
            "Events processed: 0",
            second_tick.stdout,
        )
        self.assertEqual(
            self.read_events(),
            events_before_tick,
        )

        dashboard = self.run_cli("dashboard")
        self.assertIn("E2E-PROJECT", dashboard.stdout)
        self.assertIn("e2e_producer", dashboard.stdout)
        self.assertIn("e2e_auditor", dashboard.stdout)
        self.assertIn("lifecycle_worker", dashboard.stdout)
        self.assertEqual(
            dashboard.stdout.rstrip().splitlines()[-1],
            "END MAIN CONTROL ROOM",
        )

    def test_demo_reset_completes(self):
        completed = self.run_cli("demo", "--reset")
        self.assertIn(
            "project lifecycle OK",
            completed.stdout,
        )
        self.assertTrue(
            (
                self.vault
                / "01_RESEARCH"
                / "TOY-THEOREM"
                / "STATE.md"
            ).exists()
        )

    def test_root_retry_recovers_interrupted_task_once(self):
        self.run_cli("init")
        producer = self.create_proposal(
            "agent-create",
            "retry_producer",
            "--division",
            "RESEARCH",
            "--role",
            "PRODUCER",
            "--capability",
            "produce_artifact",
        )
        self.run_cli("approve", producer)
        auditor = self.create_proposal(
            "agent-create",
            "retry_auditor",
            "--division",
            "RESEARCH",
            "--role",
            "AUDITOR",
            "--capability",
            "audit",
        )
        self.run_cli("approve", auditor)
        project = self.create_proposal(
            "project-create",
            "RETRY-PROJECT",
            "--title",
            "Retry Project",
            "--division",
            "RESEARCH",
            "--owner",
            "retry_producer",
        )
        self.run_cli("approve", project)
        binding = self.create_proposal(
            "bind",
            "RETRY-PROJECT",
            "retry_auditor",
            "AUDITOR",
        )
        self.run_cli("approve", binding)
        self.run_cli(
            "authorize",
            "RETRY-PROJECT",
            "ROOT-RETRY-T0",
            "--scope",
            "Execute the bounded retry test.",
        )
        self.run_cli(
            "task-create",
            "RETRY-PROJECT",
            "--task-id",
            "TASK-RETRY-INTERRUPTED",
            "--description",
            "Simulate interrupted local runtime.",
        )

        task_path = (
            self.vault
            / "01_RESEARCH"
            / "RETRY-PROJECT"
            / "tasks"
            / "TASK-RETRY-INTERRUPTED.md"
        )
        task = self.read_frontmatter(task_path)
        task["status"] = "RUNNING"
        task["attempt"] = 1
        self.write_frontmatter(task_path, task)

        self.run_cli(
            "task-retry",
            "RETRY-PROJECT",
            "TASK-RETRY-INTERRUPTED",
        )
        first = self.read_frontmatter(task_path)
        self.assertEqual(first["status"], "ASSIGNED")
        self.assertEqual(
            first["metadata"]["recovery_history"][0][
                "previous_status"
            ],
            "RUNNING",
        )

        self.run_cli(
            "task-retry",
            "RETRY-PROJECT",
            "TASK-RETRY-INTERRUPTED",
        )
        second = self.read_frontmatter(task_path)
        self.assertEqual(
            second["metadata"]["recovery_history"],
            first["metadata"]["recovery_history"],
        )
        retry_events = [
            event
            for event in self.read_events()
            if event["action"] == "TASK_RETRIED"
        ]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(
            retry_events[0]["metadata"][
                "previous_status"
            ],
            "RUNNING",
        )


if __name__ == "__main__":
    unittest.main()
