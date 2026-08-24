import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib import error as urllib_error
from unittest.mock import patch

import yaml

from control_tower.agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from control_tower.chat.adapters import (
    DeterministicIntentAdapter,
    LLMAdapterError,
)
from control_tower.chat.config import (
    LLMConfigurationError,
    LLMSettings,
    build_intent_adapter,
    load_llm_settings,
)
from control_tower.chat.models import (
    AgentProposalRequest,
    IntentKind,
    ProjectProposalRequest,
    TaskProposalRequest,
)
from control_tower.chat import providers as provider_module
from control_tower.chat.providers import (
    LLMProvider,
    LLMProviderError,
    OpenAIResponsesProvider,
)
from control_tower.chat.shell import build_chat_service, run_chat
from control_tower.chat.structured_intent import (
    ProviderIntentAdapter,
    StructuredIntentError,
)
from control_tower.events import EventLedger
from control_tower.models import (
    Division,
    Lineage,
    ProjectState,
    Role,
    State,
)
from control_tower.tasks import TaskStore
from control_tower.vault import Vault


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class MockLLMProvider(LLMProvider):
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def generate_structured(self, *, message, instructions, schema):
        self.calls.append(
            {
                "message": message,
                "instructions": instructions,
                "schema": schema,
            }
        )

        if self.failure:
            raise self.failure

        return self.response


def wire_intent(
    kind,
    confidence=0.95,
    project_id=None,
    proposal_request=None,
):
    return json.dumps(
        {
            "kind": kind,
            "project_id": project_id,
            "confidence": confidence,
            "proposal_request": proposal_request,
        },
        separators=(",", ":"),
    )


class ChatLLMProviderTests(unittest.TestCase):
    PROJECT_ID = "CAREER-OS"
    PRODUCER = "career_producer"
    AUDITOR = "career_auditor"
    API_KEY = "test-secret-never-print"

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
        self.settings = LLMSettings(
            provider="openai",
            model="test-model",
            api_key=self.API_KEY,
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def tree_fingerprint(root):
        root = Path(root)

        if not root.exists():
            return ()

        entries = []

        for path in sorted(root.rglob("*")):
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

    def proposal_documents(self):
        documents = []

        for path in sorted(
            (self.root / "00_ROOT" / "inbox").glob("*.md")
        ):
            metadata = self.frontmatter(path)

            if metadata.get("proposal_type"):
                documents.append((path, metadata))

        return documents

    def service(self, provider):
        return build_chat_service(
            self.root,
            settings=self.settings,
            provider=provider,
        )

    def test_mock_provider_query_returns_typed_intent_and_reads_only(self):
        provider = MockLLMProvider(
            wire_intent("ORGANIZATION_OVERVIEW")
        )
        adapter = ProviderIntentAdapter(provider)
        intent = adapter.classify("What should I focus on today?")

        self.assertEqual(
            intent.kind,
            IntentKind.ORGANIZATION_OVERVIEW,
        )
        before = self.tree_fingerprint(self.root)
        response = self.service(provider).respond(
            "What should I focus on today?"
        )
        self.assertIn("CAREER-OS", response)
        self.assertEqual(self.tree_fingerprint(self.root), before)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(
            provider.calls[0]["message"],
            "What should I focus on today?",
        )
        self.assertNotIn(str(self.root), repr(provider.calls))
        self.assertNotIn(self.API_KEY, repr(provider.calls))

    def test_mock_provider_task_draft_only_writes_governance_evidence(self):
        message = "Please move my AI career preparation forward"
        provider = MockLLMProvider(
            wire_intent(
                "DRAFT_CREATE_TASK",
                confidence=0.93,
                proposal_request={
                    "request_type": "TASK",
                    "project_hint": self.PROJECT_ID,
                },
            )
        )
        adapter = ProviderIntentAdapter(provider)
        typed = adapter.classify(message)
        self.assertIsInstance(
            typed.proposal_request,
            TaskProposalRequest,
        )
        self.assertEqual(typed.proposal_request.objective, message)
        state_before = self.state_path.read_bytes()
        registry_before = self.registry.path.read_bytes()

        with patch(
            "control_tower.decision.approve_proposal",
            side_effect=AssertionError("approve called"),
        ) as approve, patch(
            "control_tower.chief_of_staff.ChiefOfStaff.tick",
            side_effect=AssertionError("tick called"),
        ) as tick, patch(
            "control_tower.chief_of_staff.ChiefOfStaff.run_task",
            side_effect=AssertionError("runtime called"),
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
            response = self.service(provider).respond(message)

        self.assertIn("State: WAITING_ROOT", response)
        self.assertFalse(approve.called)
        self.assertFalse(tick.called)
        self.assertFalse(run_task.called)
        self.assertFalse(ensure.called)
        self.assertFalse(assign.called)
        self.assertFalse(save.called)
        self.assertFalse(write_state.called)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(self.registry.path.read_bytes(), registry_before)
        self.assertEqual(TaskStore(self.project_dir).list(), [])
        documents = self.proposal_documents()
        self.assertEqual(len(documents), 1)
        proposal = documents[0][1]
        self.assertEqual(proposal["proposal_type"], "CREATE_TASK")
        self.assertEqual(proposal["state"], "WAITING_ROOT")
        self.assertEqual(proposal["payload"]["description"], message)
        self.assertEqual(
            [
                event["action"]
                for event in EventLedger(self.vault).read_all()
            ],
            ["PROPOSAL_DRAFTED"],
        )

    def test_structured_intent_validation_fails_closed(self):
        valid_query = wire_intent("ORGANIZATION_OVERVIEW")
        cases = {
            "malformed": "{",
            "array": "[]",
            "trailing": valid_query + "{}",
            "markdown": "```json\n" + valid_query + "\n```",
            "unknown-kind": wire_intent("APPROVE"),
            "extra-top-level": valid_query[:-1] + ',"command":"tick"}',
            "missing-project-id": wire_intent(
                "PROJECT_DETAIL",
                project_id=None,
            ),
            "bool-confidence": wire_intent(
                "ORGANIZATION_OVERVIEW",
                confidence=True,
            ),
            "wrong-request": wire_intent(
                "DRAFT_CREATE_TASK",
                proposal_request={
                    "request_type": "PROJECT",
                    "project_hint": self.PROJECT_ID,
                },
            ),
            "canonical-smuggling": wire_intent(
                "DRAFT_CREATE_TASK",
                proposal_request={
                    "request_type": "TASK",
                    "project_hint": self.PROJECT_ID,
                    "authorization_id": "FAKE",
                },
            ),
            "duplicate": (
                '{"kind":"ORGANIZATION_OVERVIEW",'
                '"kind":"DRAFT_CREATE_TASK",'
                '"project_id":null,"confidence":0.9,'
                '"proposal_request":null}'
            ),
            "nan": (
                '{"kind":"ORGANIZATION_OVERVIEW",'
                '"project_id":null,"confidence":NaN,'
                '"proposal_request":null}'
            ),
            "deeply-nested": "[" * 1100 + "0" + "]" * 1100,
            "oversized": " " * 32769,
        }
        before = self.tree_fingerprint(self.root)

        for label, output in cases.items():
            with self.subTest(label=label):
                provider = MockLLMProvider(output)

                with self.assertRaises(
                    (StructuredIntentError, LLMAdapterError)
                ):
                    ProviderIntentAdapter(provider).classify(
                        "show my projects"
                    )

                self.assertEqual(
                    self.tree_fingerprint(self.root),
                    before,
                )

    def test_low_confidence_draft_does_not_create_proposal(self):
        provider = MockLLMProvider(
            wire_intent(
                "DRAFT_CREATE_TASK",
                confidence=0.4,
                proposal_request={
                    "request_type": "TASK",
                    "project_hint": self.PROJECT_ID,
                },
            )
        )
        before = self.tree_fingerprint(self.root)
        response = self.service(provider).respond(
            "Maybe do something about my career"
        )
        self.assertIn("不能安全确定", response)
        self.assertEqual(self.proposal_documents(), [])
        self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_privileged_mixed_request_is_blocked_before_provider(self):
        provider = MockLLMProvider(
            wire_intent(
                "DRAFT_CREATE_TASK",
                proposal_request={
                    "request_type": "TASK",
                    "project_hint": self.PROJECT_ID,
                },
            )
        )
        before = self.tree_fingerprint(self.root)
        response = self.service(provider).respond(
            "Create a task for CAREER-OS, approve it, then tick"
        )
        self.assertIn("没有创建 Proposal", response)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_provider_project_agent_and_detail_intents_stay_governed(self):
        before_state = self.state_path.read_bytes()
        before_registry = self.registry.path.read_bytes()
        project_provider = MockLLMProvider(
            wire_intent(
                "DRAFT_CREATE_PROJECT_REQUEST",
                proposal_request={
                    "request_type": "PROJECT",
                    "project_id": "INDEPENDENCE-OS",
                    "title": "Independence OS",
                    "division": "BUSINESS",
                    "owner": self.PRODUCER,
                    "phase": "T0",
                    "lineage": "CANONICAL",
                },
            )
        )
        project_intent = ProviderIntentAdapter(
            project_provider
        ).classify("Build my independence project")
        self.assertIsInstance(
            project_intent.proposal_request,
            ProjectProposalRequest,
        )
        project_response = self.service(project_provider).respond(
            "Build my independence project"
        )
        self.assertIn("CREATE_PROJECT_REQUEST", project_response)

        agent_provider = MockLLMProvider(
            wire_intent(
                "DRAFT_CREATE_AGENT_REQUEST",
                proposal_request={
                    "request_type": "AGENT",
                    "agent_id": "research_specialist",
                    "division": "RESEARCH",
                    "role": "SPECIALIST",
                    "capabilities": ["research"],
                    "status": "ACTIVE",
                },
            )
        )
        agent_intent = ProviderIntentAdapter(agent_provider).classify(
            "Add a research specialist"
        )
        self.assertIsInstance(
            agent_intent.proposal_request,
            AgentProposalRequest,
        )
        agent_response = self.service(agent_provider).respond(
            "Add a research specialist"
        )
        self.assertIn("CREATE_AGENT_REQUEST", agent_response)

        detail_provider = MockLLMProvider(
            wire_intent(
                "PROJECT_DETAIL",
                project_id=self.PROJECT_ID,
            )
        )
        detail_response = self.service(detail_provider).respond(
            "Where does my career program stand?"
        )
        self.assertIn("项目 CAREER-OS", detail_response)

        self.assertEqual(self.state_path.read_bytes(), before_state)
        self.assertEqual(self.registry.path.read_bytes(), before_registry)
        self.assertIsNone(self.registry.get("research_specialist"))

        with self.assertRaises(FileNotFoundError):
            self.vault.find_state_path("INDEPENDENCE-OS")

        proposal_types = {
            metadata["proposal_type"]
            for _, metadata in self.proposal_documents()
        }
        self.assertEqual(
            proposal_types,
            {"CREATE_PROJECT_REQUEST", "CREATE_AGENT_REQUEST"},
        )

    def test_provider_failure_is_sanitized_without_offline_fallback(self):
        provider = MockLLMProvider(
            failure=LLMProviderError(
                "upstream contained " + self.API_KEY
            )
        )
        errors = io.StringIO()
        before = self.tree_fingerprint(self.root)
        status = run_chat(
            self.root,
            message="show my projects",
            settings=self.settings,
            provider=provider,
            output_stream=io.StringIO(),
            error_stream=errors,
        )
        self.assertEqual(status, 2)
        self.assertIn("Configured LLM provider failed", errors.getvalue())
        self.assertNotIn(self.API_KEY, errors.getvalue())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_provider_switching_and_explicit_dotenv_precedence(self):
        offline = load_llm_settings(environ={})
        self.assertEqual(offline.provider, "offline")
        self.assertIsInstance(
            build_intent_adapter(offline),
            DeterministicIntentAdapter,
        )
        template_settings = load_llm_settings(
            config_path=REPOSITORY_ROOT / ".env.example",
            environ={},
        )
        self.assertEqual(template_settings.provider, "offline")

        config_path = Path(self.temporary.name) / "llm.env"
        config_path.write_text(
            "LLM_PROVIDER=openai\n"
            "LLM_MODEL=file-model\n"
            "OPENAI_API_KEY=file-secret\n",
            encoding="utf-8",
        )
        settings = load_llm_settings(
            config_path=config_path,
            environ={
                "LLM_MODEL": "environment-model",
                "OPENAI_API_KEY": self.API_KEY,
            },
        )
        self.assertEqual(settings.provider, "openai")
        self.assertEqual(settings.model, "environment-model")
        self.assertNotIn(self.API_KEY, repr(settings))
        provider = MockLLMProvider(
            wire_intent("ORGANIZATION_OVERVIEW")
        )
        self.assertIsInstance(
            build_intent_adapter(settings, provider=provider),
            ProviderIntentAdapter,
        )
        automatic = build_intent_adapter(settings)
        self.assertIsInstance(automatic, ProviderIntentAdapter)
        self.assertIsInstance(
            automatic.provider,
            OpenAIResponsesProvider,
        )
        self.assertEqual(automatic.provider.model, "environment-model")
        self.assertEqual(automatic.provider.timeout_seconds, 30.0)

        with self.assertRaises(LLMConfigurationError):
            load_llm_settings(
                environ={"LLM_PROVIDER": "gemini"}
            )

        with self.assertRaises(LLMConfigurationError):
            load_llm_settings(
                environ={
                    "LLM_PROVIDER": "openai",
                    "LLM_MODEL": "test-model",
                }
            )

        with self.assertRaises(LLMConfigurationError):
            load_llm_settings(
                config_path=(
                    self._write_unknown_config()
                ),
                environ={},
            )

    def _write_unknown_config(self):
        path = Path(self.temporary.name) / "unknown.env"
        path.write_text("LLM_PROVDER=openai\n", encoding="utf-8")
        return path

    def test_cli_provider_override_keeps_offline_mode_network_free(self):
        environment = os.environ.copy()
        environment.update(
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "test-model",
            }
        )
        environment.pop("OPENAI_API_KEY", None)
        before = self.tree_fingerprint(self.root)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "control_tower.cli",
                "--vault",
                str(self.root),
                "chat",
                "--provider",
                "offline",
                "--message",
                "帮我看看我现在所有项目状态",
            ],
            cwd=str(REPOSITORY_ROOT),
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        combined = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, msg=combined)
        self.assertIn("CAREER-OS", completed.stdout)
        self.assertNotIn("Traceback", combined)
        self.assertEqual(self.tree_fingerprint(self.root), before)

        with patch.object(
            provider_module.request,
            "build_opener",
            side_effect=AssertionError("network initialized"),
        ) as build_opener:
            status = run_chat(
                self.root,
                message="帮我看看我现在所有项目状态",
                settings=LLMSettings(),
                output_stream=io.StringIO(),
                error_stream=io.StringIO(),
            )

        self.assertEqual(status, 0)
        self.assertFalse(build_opener.called)

    def test_cli_missing_openai_key_fails_before_vault_creation(self):
        missing = Path(self.temporary.name) / "missing-vault"
        environment = os.environ.copy()

        for key in (
            "LLM_PROVIDER",
            "LLM_MODEL",
            "OPENAI_MODEL",
            "OPENAI_API_KEY",
            "LLM_TIMEOUT_SECONDS",
        ):
            environment.pop(key, None)

        environment.update(
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "test-model",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "control_tower.cli",
                "--vault",
                str(missing),
                "chat",
                "--message",
                "show my projects",
            ],
            cwd=str(REPOSITORY_ROOT),
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        combined = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2, msg=combined)
        self.assertNotIn("Traceback", combined)
        self.assertIn("requires OPENAI_API_KEY", combined)
        self.assertFalse(missing.exists())

    def test_integrated_shell_rejects_provider_smuggling(self):
        outputs = (
            wire_intent("APPROVE"),
            wire_intent(
                "DRAFT_CREATE_TASK",
                proposal_request={
                    "request_type": "TASK",
                    "project_hint": self.PROJECT_ID,
                    "proposal_id": "FORGED",
                },
            ),
        )
        before = self.tree_fingerprint(self.root)

        for index, output in enumerate(outputs):
            with self.subTest(index=index):
                errors = io.StringIO()
                status = run_chat(
                    self.root,
                    message="please organize my work",
                    settings=self.settings,
                    provider=MockLLMProvider(output),
                    output_stream=io.StringIO(),
                    error_stream=errors,
                )
                self.assertEqual(status, 2)
                self.assertNotIn("Traceback", errors.getvalue())
                self.assertEqual(self.tree_fingerprint(self.root), before)

    def test_openai_provider_request_has_schema_and_no_tools(self):
        captured = {}
        provider_output = wire_intent("ORGANIZATION_OVERVIEW")

        def transport(url, headers, payload, timeout):
            captured.update(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return {
                "status": "completed",
                "error": None,
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": provider_output,
                            }
                        ],
                    }
                ],
            }

        provider = OpenAIResponsesProvider(
            api_key=self.API_KEY,
            model="test-model",
            timeout_seconds=12,
            transport=transport,
        )
        result = provider.generate_structured(
            message="show projects",
            instructions="static instructions",
            schema={"type": "object"},
        )
        self.assertEqual(result, provider_output)
        self.assertEqual(
            captured["url"],
            "https://api.openai.com/v1/responses",
        )
        self.assertEqual(captured["payload"]["model"], "test-model")
        self.assertEqual(captured["payload"]["input"], "show projects")
        self.assertFalse(captured["payload"]["store"])
        self.assertNotIn("tools", captured["payload"])
        output_format = captured["payload"]["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertTrue(output_format["strict"])
        self.assertNotIn(self.API_KEY, json.dumps(captured["payload"]))
        self.assertNotIn(self.API_KEY, repr(provider))

    def test_openai_transport_error_does_not_leak_secret(self):
        def transport(url, headers, payload, timeout):
            raise RuntimeError("transport saw " + self.API_KEY)

        provider = OpenAIResponsesProvider(
            api_key=self.API_KEY,
            model="test-model",
            transport=transport,
        )

        with self.assertRaises(LLMProviderError) as captured:
            provider.generate_structured(
                message="show projects",
                instructions="instructions",
                schema={"type": "object"},
            )

        self.assertNotIn(self.API_KEY, str(captured.exception))

    def test_openai_refusal_and_incomplete_output_fail_closed(self):
        responses = (
            {
                "status": "completed",
                "error": None,
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "refusal",
                                "refusal": "No",
                            }
                        ]
                    }
                ],
            },
            {
                "status": "incomplete",
                "error": None,
                "output": [],
            },
        )

        for index, response in enumerate(responses):
            with self.subTest(index=index):
                provider = OpenAIResponsesProvider(
                    api_key=self.API_KEY,
                    model="test-model",
                    transport=(
                        lambda url, headers, payload, timeout, item=response: (
                            item
                        )
                    ),
                )

                with self.assertRaises(LLMProviderError):
                    provider.generate_structured(
                        message="show projects",
                        instructions="instructions",
                        schema={"type": "object"},
                    )

    def test_openai_malformed_collections_use_provider_error(self):
        responses = (
            {
                "status": "completed",
                "error": None,
                "output": None,
            },
            {
                "status": "completed",
                "error": None,
                "output": [
                    {"type": "message", "content": None}
                ],
            },
            {
                "status": "completed",
                "error": None,
                "output": ["not-an-item"],
            },
        )

        for index, response in enumerate(responses):
            with self.subTest(index=index):
                provider = OpenAIResponsesProvider(
                    api_key=self.API_KEY,
                    model="test-model",
                    transport=(
                        lambda url, headers, payload, timeout, item=response: (
                            item
                        )
                    ),
                )

                with self.assertRaises(LLMProviderError):
                    provider.generate_structured(
                        message="show projects",
                        instructions="instructions",
                        schema={"type": "object"},
                    )

    def test_openai_redirects_are_rejected_before_key_forwarding(self):
        class RedirectingOpener:
            def open(self, outbound, timeout):
                raise urllib_error.HTTPError(
                    outbound.full_url,
                    302,
                    "Redirect",
                    {},
                    None,
                )

        with patch.object(
            provider_module.request,
            "build_opener",
            return_value=RedirectingOpener(),
        ) as build_opener:
            with self.assertRaises(LLMProviderError):
                OpenAIResponsesProvider._post_json(
                    "https://api.openai.com/v1/responses",
                    {"Authorization": "Bearer " + self.API_KEY},
                    {"model": "test-model"},
                    10,
                )

        redirect_handler = build_opener.call_args.args[0]
        self.assertIsInstance(
            redirect_handler,
            provider_module._RejectRedirects,
        )
        self.assertIsNone(
            redirect_handler.redirect_request(
                None,
                None,
                302,
                "Redirect",
                {},
                "https://attacker.invalid/capture",
            )
        )


if __name__ == "__main__":
    unittest.main()
