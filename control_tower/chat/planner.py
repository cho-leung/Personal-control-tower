"""Pure Chief-of-Staff analysis from typed intents to Proposal drafts."""

import hashlib
import json
import re

from .models import (
    AgentProposalRequest,
    DRAFT_INTENTS,
    Intent,
    IntentKind,
    ProjectProposalRequest,
    TaskProposalRequest,
)
from .proposal_draft import ProposalDraft, ProposalDraftType


class ProposalPlanningError(RuntimeError):
    pass


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "advance",
    "ai",
    "create",
    "for",
    "help",
    "me",
    "my",
    "os",
    "plan",
    "project",
    "task",
    "the",
    "to",
}


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _tokens(value):
    return {
        token
        for token in _TOKEN_RE.findall(value.casefold())
        if len(token) >= 3 and token not in _STOPWORDS
    }


class ProposalPlanner:
    """Analyze a safe snapshot; never receive a Vault or writer."""

    def plan(self, intent: Intent, snapshot) -> ProposalDraft:
        if not isinstance(intent, Intent):
            raise ProposalPlanningError(
                "Planner requires a typed Intent."
            )

        if intent.kind not in DRAFT_INTENTS:
            raise ProposalPlanningError(
                "Intent is not an allowlisted Proposal request."
            )

        if intent.kind == IntentKind.DRAFT_CREATE_TASK:
            return self._task(intent.proposal_request, snapshot)

        if (
            intent.kind
            == IntentKind.DRAFT_CREATE_PROJECT_REQUEST
        ):
            return self._project(
                intent.proposal_request,
                snapshot,
            )

        if (
            intent.kind
            == IntentKind.DRAFT_CREATE_AGENT_REQUEST
        ):
            return self._agent(
                intent.proposal_request,
                snapshot,
            )

        raise ProposalPlanningError("Unhandled Proposal intent.")

    @staticmethod
    def _agent_by_id(snapshot, agent_id):
        matches = [
            agent
            for agent in snapshot.agents
            if agent.agent_id.casefold() == agent_id.casefold()
        ]

        if len(matches) != 1:
            raise ProposalPlanningError(
                f"Agent is missing or ambiguous: {agent_id}"
            )

        return matches[0]

    @staticmethod
    def _exact_projects(snapshot, value):
        folded = value.casefold()
        return [
            project
            for project in snapshot.projects
            if project.project_id.casefold() == folded
            or project.title.casefold() == folded
        ]

    def _resolve_project(self, request, snapshot):
        if request.project_hint:
            matches = self._exact_projects(
                snapshot,
                request.project_hint,
            )

            if len(matches) != 1:
                raise ProposalPlanningError(
                    "Project target is missing or ambiguous: "
                    f"{request.project_hint}"
                )

            return matches[0]

        objective_tokens = _tokens(request.objective)
        scored = []

        for project in snapshot.projects:
            project_tokens = _tokens(
                project.project_id + " " + project.title
            )
            score = len(objective_tokens & project_tokens)

            if score:
                scored.append((score, project))

        if not scored:
            raise ProposalPlanningError(
                "I cannot safely identify the target project; "
                "name its exact project ID."
            )

        best_score = max(score for score, _ in scored)
        matches = [
            project
            for score, project in scored
            if score == best_score
        ]

        if len(matches) != 1:
            raise ProposalPlanningError(
                "Project target is ambiguous; name its exact project ID."
            )

        return matches[0]

    def _task(self, request, snapshot):
        if not isinstance(request, TaskProposalRequest):
            raise ProposalPlanningError(
                "CREATE_TASK request is malformed."
            )

        project = self._resolve_project(request, snapshot)

        if project.state not in {"AUTHORIZED", "ACTIVE"}:
            raise ProposalPlanningError(
                f"Project {project.project_id} must be Root-authorized "
                "before a Task Proposal can be drafted."
            )

        if not project.authorization_id:
            raise ProposalPlanningError(
                f"Project {project.project_id} has no authorization."
            )

        producer = self._agent_by_id(snapshot, project.owner)

        if (
            producer.status != "ACTIVE"
            or producer.role != "PRODUCER"
            or "produce_artifact" not in producer.capabilities
        ):
            raise ProposalPlanningError(
                "Project owner is not an eligible PRODUCER."
            )

        auditor_id = project.auditor

        if auditor_id:
            if auditor_id not in project.bound_auditors:
                raise ProposalPlanningError(
                    "Assigned auditor is not bound to the project."
                )
        elif len(project.bound_auditors) == 1:
            auditor_id = project.bound_auditors[0]
        else:
            raise ProposalPlanningError(
                "Project needs one unambiguous bound auditor."
            )

        auditor = self._agent_by_id(snapshot, auditor_id)

        if (
            auditor.agent_id == producer.agent_id
            or auditor.status != "ACTIVE"
            or auditor.role != "AUDITOR"
            or "audit" not in auditor.capabilities
        ):
            raise ProposalPlanningError(
                "Project auditor is not independently eligible."
            )

        related_tasks = [
            task
            for task in snapshot.tasks
            if task.project_id == project.project_id
            and task.phase == project.phase
            and task.required_role == "PRODUCER"
        ]
        unfinished = [
            task
            for task in related_tasks
            if task.status != "COMPLETED"
        ]

        if unfinished:
            raise ProposalPlanningError(
                "Project phase already has an unfinished PRODUCER Task: "
                + ", ".join(task.task_id for task in unfinished)
            )

        context = _hash(
            {
                "intent": IntentKind.DRAFT_CREATE_TASK.value,
                "objective": request.objective,
                "project": {
                    "project_id": project.project_id,
                    "phase": project.phase,
                    "state": project.state,
                    "authorization_id": project.authorization_id,
                    "owner": producer.agent_id,
                    "auditor": auditor.agent_id,
                },
                "prior_tasks": [
                    {
                        "task_id": task.task_id,
                        "status": task.status,
                    }
                    for task in related_tasks
                ],
            }
        )
        task_id = (
            f"TASK-{project.project_id}-{project.phase}-"
            f"CHAT-{context[:12]}"
        )
        reason = (
            f"Create a Root-reviewed task for {project.project_id}: "
            f"{request.objective[:800]}"
        )

        return ProposalDraft(
            proposal_type=ProposalDraftType.CREATE_TASK,
            target=project.project_id,
            reason=reason,
            payload={
                "task_id": task_id,
                "project_id": project.project_id,
                "phase": project.phase,
                "task_type": "PRODUCE_ARTIFACT",
                "assigned_agent": producer.agent_id,
                "required_role": "PRODUCER",
                "required_capability": "produce_artifact",
                "description": request.objective,
                "context_refs": [],
                "authorization_id": project.authorization_id,
                "auditor": auditor.agent_id,
            },
            idempotency_context=context,
        )

    def _project(self, request, snapshot):
        if not isinstance(request, ProjectProposalRequest):
            raise ProposalPlanningError(
                "CREATE_PROJECT_REQUEST is malformed."
            )

        if self._exact_projects(snapshot, request.project_id):
            raise ProposalPlanningError(
                f"Project already exists: {request.project_id}"
            )

        owner = self._agent_by_id(snapshot, request.owner)

        if (
            owner.status != "ACTIVE"
            or owner.role != "PRODUCER"
            or "produce_artifact" not in owner.capabilities
        ):
            raise ProposalPlanningError(
                "Project owner is not an eligible PRODUCER."
            )

        payload = {
            "project_id": request.project_id,
            "title": request.title,
            "division": request.division,
            "owner": owner.agent_id,
            "phase": request.phase,
            "lineage": request.lineage,
        }
        context = _hash(
            {
                "intent": (
                    IntentKind.DRAFT_CREATE_PROJECT_REQUEST.value
                ),
                "payload": payload,
                "owner": {
                    "status": owner.status,
                    "role": owner.role,
                    "capabilities": owner.capabilities,
                },
            }
        )
        return ProposalDraft(
            proposal_type=(
                ProposalDraftType.CREATE_PROJECT_REQUEST
            ),
            target=request.project_id,
            reason=(
                f"Create project {request.title} in "
                f"{request.division}, owned by {owner.agent_id}."
            ),
            payload=payload,
            idempotency_context=context,
        )

    def _agent(self, request, snapshot):
        if not isinstance(request, AgentProposalRequest):
            raise ProposalPlanningError(
                "CREATE_AGENT_REQUEST is malformed."
            )

        if any(
            agent.agent_id.casefold()
            == request.agent_id.casefold()
            for agent in snapshot.agents
        ):
            raise ProposalPlanningError(
                f"Agent already exists: {request.agent_id}"
            )

        payload = {
            "agent_id": request.agent_id,
            "division": request.division,
            "role": request.role,
            "capabilities": list(request.capabilities),
            "status": request.status,
        }
        context = _hash(
            {
                "intent": (
                    IntentKind.DRAFT_CREATE_AGENT_REQUEST.value
                ),
                "payload": payload,
            }
        )
        return ProposalDraft(
            proposal_type=(
                ProposalDraftType.CREATE_AGENT_REQUEST
            ),
            target=request.agent_id,
            reason=(
                f"Create {request.role} agent {request.agent_id} "
                f"in {request.division}."
            ),
            payload=payload,
            idempotency_context=context,
        )
