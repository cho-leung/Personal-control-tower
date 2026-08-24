"""Provider-neutral natural-language to typed-intent adapters."""

from abc import ABC, abstractmethod
import re

from .models import (
    AgentProposalRequest,
    Intent,
    IntentKind,
    ProjectProposalRequest,
    TaskProposalRequest,
)


class LLMAdapterError(RuntimeError):
    """A provider adapter failed before returning a typed Intent."""


class LLMAdapter(ABC):
    """Interpret text without Vault access.

    Provider implementations should translate SDK and network failures into
    ``LLMAdapterError`` so the terminal shell can fail closed without a
    traceback.
    """

    @abstractmethod
    def classify(self, message: str) -> Intent:
        raise NotImplementedError


class DeterministicIntentAdapter(LLMAdapter):
    """Offline adapter for bounded bilingual query and draft intents."""

    _ROOT_INBOX_TERMS = (
        "收件箱",
        "待审批",
        "待批准",
        "提案",
        "proposal",
        "inbox",
    )
    _QUERY_TERMS = (
        "查看",
        "看看",
        "检查",
        "显示",
        "列出",
        "哪些",
        "多少",
        "状态",
        "show",
        "list",
        "what",
        "which",
        "pending",
        "status",
    )
    _PRIVILEGED_ACTION_RE = re.compile(
        r"(?:忽略规则|(?<!待)批准|(?<!待)审批|拒绝|执行|运行|"
        r"授权|删除|归档|修改\s*(?:state|vault|registry)|"
        r"ignore\s+(?:the\s+)?rules?|\bapprove\b|\breject\b|"
        r"\bexecute\b|\brun\b|\btick\b|\bauthorize\b|"
        r"\bdelete\b|\barchive\b)",
        re.IGNORECASE,
    )
    _ACTION_RE = re.compile(
        r"(?:推进|安排|(?<!待)批准|(?<!待)审批|执行|运行|创建|"
        r"新增|修改|删除|"
        r"归档|授权|拒绝|开始|完成|优化|规划|计划|分析|下一步|"
        r"ignore\s+(?:the\s+)?rules?|approve|execute|run|create|"
        r"modify|delete|archive|authorize|reject|advance|plan|"
        r"schedule)",
        re.IGNORECASE,
    )
    _PROJECT_CREATE_RE = re.compile(
        r"(?:\bcreate\s+project\b|创建项目)\s+"
        r"([A-Za-z0-9._-]+)",
        re.IGNORECASE,
    )
    _AGENT_CREATE_RE = re.compile(
        r"(?:\bcreate\s+agent\b|创建\s*(?:Agent|智能体))\s+"
        r"([A-Za-z0-9._-]+)",
        re.IGNORECASE,
    )
    _ASSIGNMENT_RE = re.compile(
        r"(title|division|owner|phase|lineage|role|"
        r"capability|capabilities|status|标题|部门|负责人|"
        r"阶段|谱系|角色|能力)\s*=\s*"
        r"(?:\"([^\"]+)\"|'([^']+)'|([^\s，;]+))",
        re.IGNORECASE,
    )
    _TASK_ACTION_RE = re.compile(
        r"(?:\badvance\b|推进|\bcreate\b.{0,40}\btask\b|"
        r"创建.{0,40}任务|新增.{0,40}任务|下一步|规划.{0,40}项目)",
        re.IGNORECASE,
    )
    _TASK_HINT_PATTERNS = (
        re.compile(
            r"\badvance\s+project\s+([A-Za-z0-9._-]+)",
            re.IGNORECASE,
        ),
        re.compile(r"推进项目\s*([A-Za-z0-9._-]+)"),
        re.compile(
            r"\bcreate\b.{0,20}\btask\b\s+"
            r"\bfor\s+([A-Za-z0-9._-]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"为\s*([A-Za-z0-9._-]+)\s*创建.{0,40}任务"
        ),
    )
    _PROJECT_DETAIL_PATTERNS = (
        re.compile(
            r"^(?:查看|检查|看看|显示)?\s*项目\s*[:：]?\s*"
            r"[\"']?(.+?)[\"']?\s*(?:的?状态)?$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:show|check)?\s*project\s*[:：]?\s*"
            r"[\"']?(.+?)[\"']?\s*(?:status)?$",
            re.IGNORECASE,
        ),
    )

    @staticmethod
    def _contains_any(message, terms):
        return any(term in message for term in terms)

    @classmethod
    def _assignments(cls, message):
        aliases = {
            "标题": "title",
            "部门": "division",
            "负责人": "owner",
            "阶段": "phase",
            "谱系": "lineage",
            "角色": "role",
            "能力": "capabilities",
            "capability": "capabilities",
        }
        values = {}

        for match in cls._ASSIGNMENT_RE.finditer(message):
            key = match.group(1).casefold()
            key = aliases.get(key, key)
            value = next(
                group
                for group in match.groups()[1:]
                if group is not None
            ).strip(" ,，;")
            values[key] = value

        return values

    @classmethod
    def _project_request(cls, message, match):
        values = cls._assignments(message)

        if not {"title", "division", "owner"} <= set(values):
            return None

        return ProjectProposalRequest(
            project_id=match.group(1),
            title=values["title"],
            division=values["division"].upper(),
            owner=values["owner"],
            phase=values.get("phase", "T0"),
            lineage=values.get("lineage", "CANONICAL").upper(),
        )

    @classmethod
    def _agent_request(cls, message, match):
        values = cls._assignments(message)

        if not {"division", "role", "capabilities"} <= set(values):
            return None

        capabilities = tuple(
            value.strip()
            for value in re.split(
                r"[,，+]",
                values["capabilities"],
            )
            if value.strip()
        )
        return AgentProposalRequest(
            agent_id=match.group(1),
            division=values["division"].upper(),
            role=values["role"].upper(),
            capabilities=capabilities,
            status=values.get("status", "ACTIVE").upper(),
        )

    def classify(self, message: str) -> Intent:
        if not isinstance(message, str):
            raise TypeError("Chat message must be text.")

        normalized = " ".join(message.strip().split())

        if not normalized:
            return Intent(IntentKind.UNKNOWN, confidence=1.0)

        if len(normalized) > 4000:
            raise ValueError("Chat message exceeds 4000 characters.")

        lowered = normalized.lower()

        if lowered in {
            "help",
            "?",
            "帮助",
            "你能做什么",
            "可以做什么",
        }:
            return Intent(IntentKind.HELP, confidence=1.0)

        if self._PRIVILEGED_ACTION_RE.search(normalized):
            return Intent(
                IntentKind.UNSUPPORTED_ACTION,
                confidence=1.0,
            )

        project_match = self._PROJECT_CREATE_RE.search(normalized)

        if project_match:
            request = self._project_request(
                normalized,
                project_match,
            )

            if request is None:
                return Intent(
                    IntentKind.UNSUPPORTED_ACTION,
                    confidence=1.0,
                )

            return Intent(
                IntentKind.DRAFT_CREATE_PROJECT_REQUEST,
                confidence=1.0,
                proposal_request=request,
            )

        agent_match = self._AGENT_CREATE_RE.search(normalized)

        if agent_match:
            request = self._agent_request(
                normalized,
                agent_match,
            )

            if request is None:
                return Intent(
                    IntentKind.UNSUPPORTED_ACTION,
                    confidence=1.0,
                )

            return Intent(
                IntentKind.DRAFT_CREATE_AGENT_REQUEST,
                confidence=1.0,
                proposal_request=request,
            )

        if self._TASK_ACTION_RE.search(normalized):
            project_hint = None

            for pattern in self._TASK_HINT_PATTERNS:
                match = pattern.search(normalized)

                if match:
                    project_hint = match.group(1)
                    break

            return Intent(
                IntentKind.DRAFT_CREATE_TASK,
                confidence=0.9,
                proposal_request=TaskProposalRequest(
                    objective=normalized,
                    project_hint=project_hint,
                ),
            )

        if self._ACTION_RE.search(normalized):
            return Intent(
                IntentKind.UNSUPPORTED_ACTION,
                confidence=1.0,
            )

        if (
            self._contains_any(lowered, self._ROOT_INBOX_TERMS)
            and self._contains_any(lowered, self._QUERY_TERMS)
        ):
            return Intent(IntentKind.ROOT_INBOX, confidence=0.95)

        for pattern in self._PROJECT_DETAIL_PATTERNS:
            match = pattern.match(normalized)

            if match:
                project_id = match.group(1).strip()

                if project_id not in {
                    "我的项目",
                    "所有项目",
                    "projects",
                }:
                    return Intent(
                        IntentKind.PROJECT_DETAIL,
                        project_id=project_id,
                        confidence=0.9,
                    )

        if self._contains_any(
            lowered,
            (
                "阻塞",
                "失败",
                "需要关注",
                "待处理",
                "attention",
                "blocked",
                "failed",
            ),
        ):
            return Intent(
                IntentKind.ATTENTION_ITEMS,
                confidence=0.95,
            )

        if self._contains_any(
            lowered,
            ("事件", "历史", "event", "recent activity"),
        ):
            return Intent(
                IntentKind.RECENT_EVENTS,
                confidence=0.9,
            )

        if self._contains_any(
            lowered,
            ("任务", "tasks", "task list"),
        ):
            return Intent(IntentKind.TASK_LIST, confidence=0.95)

        if self._contains_any(
            lowered,
            ("agents", "agent list", "成员", "智能体"),
        ):
            return Intent(IntentKind.AGENT_LIST, confidence=0.9)

        if self._contains_any(
            lowered,
            (
                "所有项目",
                "项目状态",
                "检查我的项目",
                "看看我的项目",
                "control tower",
                "组织状态",
                "organization overview",
                "dashboard",
                "overview",
            ),
        ):
            return Intent(
                IntentKind.ORGANIZATION_OVERVIEW,
                confidence=0.95,
            )

        if lowered in {
            "项目",
            "projects",
            "project list",
            "列出项目",
            "查看项目",
        }:
            return Intent(IntentKind.PROJECT_LIST, confidence=0.95)

        return Intent(IntentKind.UNKNOWN, confidence=0.5)
