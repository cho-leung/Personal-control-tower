"""Provider-neutral natural-language to typed-intent adapters."""

from abc import ABC, abstractmethod
import re

from .models import Intent, IntentKind


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
    """Offline M1 adapter for a small bilingual read-only intent set."""

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
    _ACTION_RE = re.compile(
        r"(?:推进|安排|(?<!待)批准|(?<!待)审批|执行|运行|创建|"
        r"新增|修改|删除|"
        r"归档|授权|拒绝|开始|完成|优化|规划|计划|分析|下一步|"
        r"ignore\s+(?:the\s+)?rules?|approve|execute|run|create|"
        r"modify|delete|archive|authorize|reject|advance|plan|"
        r"schedule)",
        re.IGNORECASE,
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
