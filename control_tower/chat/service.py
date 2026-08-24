"""Chief-of-Staff queries and governed Proposal-drafting presentation."""

import re
import shlex

from .adapters import LLMAdapter
from .models import (
    DRAFT_INTENTS,
    Intent,
    IntentKind,
    IntentValidationError,
    READ_ONLY_INTENTS,
)
from .planner import ProposalPlanner
from .proposal_draft import ProposalDraftSubmitter
from .query import ControlTowerQueryService


_TERMINAL_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f]"
)


def _safe_text(value) -> str:
    text = _TERMINAL_CONTROL_RE.sub("", str(value or "-"))
    return " ".join(text.split())


class ConversationalChiefOfStaff:
    """A coordinator with queries plus a bounded ROOT-inbox writer."""

    def __init__(
        self,
        adapter: LLMAdapter,
        query_service: ControlTowerQueryService,
        planner: ProposalPlanner = None,
        proposal_submitter: ProposalDraftSubmitter = None,
    ):
        self.adapter = adapter
        self.query_service = query_service
        self.planner = planner
        self.proposal_submitter = proposal_submitter

    def _interpret(self, message: str) -> Intent:
        intent = self.adapter.classify(message)

        if not isinstance(intent, Intent):
            raise IntentValidationError(
                "LLM adapter did not return a typed Intent."
            )

        if (
            intent.kind not in READ_ONLY_INTENTS
            and intent.kind not in DRAFT_INTENTS
            and intent.kind not in {
                IntentKind.UNSUPPORTED_ACTION,
                IntentKind.UNKNOWN,
            }
        ):
            raise IntentValidationError(
                "LLM adapter returned a non-allowlisted intent."
            )

        return intent

    @staticmethod
    def _footer():
        return (
            "\n\n本次回复只读取 Vault；未创建 Proposal、未执行 Task、"
            "未修改组织状态。"
        )

    @classmethod
    def _projects(cls, projects):
        if not projects:
            return ["- 当前没有已登记项目。"]

        return [
            (
                f"- {_safe_text(project.project_id)} | "
                f"{_safe_text(project.division)} | "
                f"phase={_safe_text(project.phase)} | "
                f"state={_safe_text(project.state)} | "
                f"owner={_safe_text(project.owner)} | "
                f"gate={_safe_text(project.next_gate)}"
            )
            for project in projects
        ]

    @classmethod
    def _overview(cls, snapshot):
        lines = [
            "Chief of Staff｜Control Tower 当前快照",
            "",
            (
                f"项目 {len(snapshot.projects)} | "
                f"Agents {len(snapshot.agents)} | "
                f"Tasks {len(snapshot.tasks)} | "
                f"待 ROOT Proposal "
                f"{len(snapshot.pending_proposals)}"
            ),
            "",
            "项目状态：",
            *cls._projects(snapshot.projects),
            "",
            "需要关注：",
        ]

        if snapshot.attention:
            lines.extend(
                f"- {item.item_type} "
                f"{_safe_text(item.item_id)}: "
                f"{_safe_text(item.status)}"
                for item in snapshot.attention
            )
        else:
            lines.append("- 当前没有 blocked/failed/WAITING_ROOT 项。")

        return "\n".join(lines)

    @classmethod
    def _project_detail(cls, snapshot, project_id):
        matches = [
            project
            for project in snapshot.projects
            if project.project_id.casefold()
            == project_id.casefold()
        ]

        if not matches:
            return (
                "Chief of Staff｜没有找到项目："
                f"{_safe_text(project_id)}"
            )

        project = matches[0]
        tasks = [
            task
            for task in snapshot.tasks
            if task.project_id == project.project_id
        ]
        return "\n".join(
            [
                f"Chief of Staff｜项目 {_safe_text(project.project_id)}",
                "",
                f"Division: {_safe_text(project.division)}",
                f"Phase: {_safe_text(project.phase)}",
                f"State: {_safe_text(project.state)}",
                f"Owner: {_safe_text(project.owner)}",
                f"Next Gate: {_safe_text(project.next_gate)}",
                f"Tasks: {len(tasks)}",
            ]
        )

    @staticmethod
    def _help():
        return "\n".join(
            [
                "Chief of Staff｜Milestone 1 + 2 能力",
                "",
                "你可以问：",
                "- 帮我看看我现在所有项目状态",
                "- 查看项目 Vision OS",
                "- 查看任务 / Agents / 待审批 Proposal",
                "- 哪些项目需要关注",
                "- 查看最近事件",
                "",
                "你也可以生成需要 ROOT 审批的 Proposal：",
                "- Help me advance my AI career",
                "- advance project CAREER-OS",
                "- create project CAREER-OS "
                "title=\"Career OS\" division=PERSONAL_GROWTH "
                "owner=career_producer",
                "- create agent career_producer "
                "division=PERSONAL_GROWTH role=PRODUCER "
                "capabilities=produce_artifact",
                "",
                "Chat 只登记 WAITING_ROOT Proposal；批准、拒绝、"
                "执行和 tick 必须继续使用受治理 CLI。",
            ]
        )

    def _proposal_response(self, draft, submission):
        proposal = submission.proposal
        vault_argument = shlex.quote(
            str(self.query_service.root)
        )

        if submission.pending_root:
            registration = (
                "已登记新的 ROOT Proposal。"
                if submission.created
                else "相同 Proposal 已在 ROOT Inbox，未重复创建。"
            )
            next_step = (
                "下一步：control-tower --vault "
                f"{vault_argument} inspect {proposal.proposal_id}\n"
                "ROOT 决定后再运行：control-tower --vault "
                f"{vault_argument} approve "
                f"{proposal.proposal_id}"
            )
        else:
            registration = (
                "相同 Proposal 已经完成 ROOT 决定；没有重新打开。"
            )
            next_step = "如需重新提案，请明确修改请求内容。"

        return "\n".join(
            [
                "Chief of Staff｜Proposal Draft",
                "",
                registration,
                f"Draft ID: {_safe_text(draft.draft_id)}",
                f"Proposal ID: {_safe_text(proposal.proposal_id)}",
                f"Type: {_safe_text(proposal.proposal_type)}",
                f"Target: {_safe_text(proposal.target)}",
                f"State: {_safe_text(proposal.state.value)}",
                f"Reason: {_safe_text(proposal.reason)}",
                "",
                "未批准、未执行，也没有修改 Project、Agent 或 Task。",
                next_step,
            ]
        )

    def respond(self, message: str) -> str:
        intent = self._interpret(message)

        if intent.kind in DRAFT_INTENTS:
            if not self.planner or not self.proposal_submitter:
                raise IntentValidationError(
                    "Proposal drafting is not configured."
                )

            snapshot = self.query_service.snapshot()
            draft = self.planner.plan(intent, snapshot)
            submission = self.proposal_submitter.submit(draft)
            return self._proposal_response(draft, submission)

        if intent.kind == IntentKind.UNSUPPORTED_ACTION:
            return (
                "Chief of Staff｜这个请求包含审批、执行、未支持操作，"
                "或者缺少生成安全 Proposal 所需的明确字段。"
                "我没有创建 Proposal、没有执行 Task，也没有改变 Vault。"
            )

        if intent.kind == IntentKind.UNKNOWN:
            return (
                "Chief of Staff｜我目前不能安全确定你的意图。"
                "请输入“帮助”查看查询与 Proposal Draft 示例；"
                "我没有执行任何操作。"
            )

        if intent.kind == IntentKind.HELP:
            return self._help()

        snapshot = self.query_service.snapshot()

        if intent.kind == IntentKind.ORGANIZATION_OVERVIEW:
            body = self._overview(snapshot)
        elif intent.kind == IntentKind.PROJECT_LIST:
            body = "\n".join(
                [
                    "Chief of Staff｜项目列表",
                    "",
                    *self._projects(snapshot.projects),
                ]
            )
        elif intent.kind == IntentKind.PROJECT_DETAIL:
            body = self._project_detail(
                snapshot,
                intent.project_id,
            )
        elif intent.kind == IntentKind.AGENT_LIST:
            lines = ["Chief of Staff｜Agent Registry", ""]
            lines.extend(
                (
                    f"- {_safe_text(agent.agent_id)} | "
                    f"{_safe_text(agent.division)} | "
                    f"{_safe_text(agent.role)} | "
                    f"{_safe_text(agent.status)} | "
                    "capabilities={}".format(
                        ",".join(
                            _safe_text(item)
                            for item in agent.capabilities
                        )
                        or "-"
                    )
                )
                for agent in snapshot.agents
            )
            body = "\n".join(
                lines + ([] if snapshot.agents else ["- 无"])
            )
        elif intent.kind == IntentKind.TASK_LIST:
            lines = ["Chief of Staff｜Tasks", ""]
            lines.extend(
                (
                    f"- {_safe_text(task.task_id)} | "
                    f"project={_safe_text(task.project_id)} | "
                    f"{_safe_text(task.status)} | "
                    f"agent={_safe_text(task.assigned_agent)}"
                )
                for task in snapshot.tasks
            )
            body = "\n".join(
                lines + ([] if snapshot.tasks else ["- 无"])
            )
        elif intent.kind == IntentKind.ROOT_INBOX:
            lines = ["Chief of Staff｜ROOT Inbox", ""]
            lines.extend(
                (
                    f"- {_safe_text(proposal.proposal_id)} | "
                    f"{_safe_text(proposal.proposal_type)} | "
                    f"target={_safe_text(proposal.target)} | "
                    f"{_safe_text(proposal.state)}"
                )
                for proposal in snapshot.pending_proposals
            )

            if snapshot.root_documents:
                lines.append(
                    "- Gate/Documents: "
                    + ", ".join(
                        _safe_text(item)
                        for item in snapshot.root_documents
                    )
                )

            if not snapshot.pending_proposals and not snapshot.root_documents:
                lines.append("- 空")

            body = "\n".join(lines)
        elif intent.kind == IntentKind.ATTENTION_ITEMS:
            lines = ["Chief of Staff｜需要关注", ""]
            lines.extend(
                f"- {item.item_type} {_safe_text(item.item_id)}: "
                f"{_safe_text(item.status)}"
                for item in snapshot.attention
            )
            body = "\n".join(
                lines + ([] if snapshot.attention else ["- 无"])
            )
        elif intent.kind == IntentKind.RECENT_EVENTS:
            lines = ["Chief of Staff｜最近事件", ""]
            lines.extend(
                (
                    f"- {_safe_text(event.event_id)} | "
                    f"{_safe_text(event.action)} | "
                    f"{_safe_text(event.result)} | "
                    f"actor={_safe_text(event.actor)} | "
                    f"target={_safe_text(event.target)}"
                )
                for event in snapshot.recent_events
            )
            body = "\n".join(
                lines + ([] if snapshot.recent_events else ["- 无"])
            )
        else:
            raise IntentValidationError(
                f"Unhandled intent: {intent.kind.value}"
            )

        return body + self._footer()
