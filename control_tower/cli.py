import argparse
from pathlib import Path
import sys

from .agents import AgentRegistry
from .chief_of_staff import ChiefOfStaff
from .chat.shell import run_chat
from .chat.config import (
    LLMConfigurationError,
    load_llm_settings,
)
from .core.decision_engine import DecisionEngine
from .core.task_creation_engine import (
    TaskCreationCommand,
    TaskCreationEngine,
)
from .dashboard import render_dashboard
from .decision import (
    approve_proposal,
    inspect_proposal,
    reject_proposal,
)
from .demo import run_demo
from .events import EventLedger
from .handoffs import HandoffStore
from .models import Role
from .proposals import (
    create_agent_proposal,
    create_archive_agent_proposal,
    create_binding_proposal,
    create_project_proposal,
    create_update_agent_capability_proposal,
    create_update_agent_role_proposal,
    write_proposal,
)
from .status import render_status
from .sync import sync_runtime
from .tasks import TaskStatus, TaskStore
from .vault import Vault


def _project_state_paths(vault):
    paths = []

    for division in (
        "01_RESEARCH",
        "02_BUSINESS",
        "03_PERSONAL_GROWTH",
    ):
        paths.extend(
            sorted(
                (vault.root / division).glob(
                    "*/STATE.md"
                )
            )
        )

    return paths


def _find_project_item(vault, reference, kind):
    matches = []
    folder = "tasks" if kind == "task" else "handoffs"

    for state_path in _project_state_paths(vault):
        matches.extend(
            sorted(
                (state_path.parent / folder).glob(
                    f"{reference}*.md"
                )
            )
        )

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous {kind} reference: {reference}"
        )

    return matches[0] if matches else None


def inspect_reference(vault_path, reference):
    try:
        return inspect_proposal(
            vault_path,
            reference,
        )
    except FileNotFoundError:
        pass

    vault = Vault(vault_path)
    task_path = _find_project_item(
        vault,
        reference,
        "task",
    )

    if task_path:
        task = TaskStore(
            task_path.parent.parent
        ).get(task_path.stem)
        print("TASK")
        print(f"ID: {task.task_id}")
        print(f"Project: {task.project_id}")
        print(f"Phase: {task.phase}")
        print(f"Type: {task.task_type}")
        print(f"Agent: {task.assigned_agent}")
        print(f"Role: {task.required_role}")
        print(f"Status: {task.status.value}")
        print(f"Created Event: {task.created_event}")
        print(f"Inputs: {len(task.input_artifacts)}")
        print(f"Outputs: {len(task.output_artifacts)}")
        return task

    handoff_path = _find_project_item(
        vault,
        reference,
        "handoff",
    )

    if handoff_path:
        handoff = HandoffStore(
            handoff_path.parent.parent
        ).get(handoff_path.stem)
        print("HANDOFF")
        print(f"ID: {handoff.handoff_id}")
        print(f"Project: {handoff.project_id}")
        print(f"Sender: {handoff.sender}")
        print(f"Receiver: {handoff.receiver}")
        print(f"Status: {handoff.status.value}")
        print(f"Task: {handoff.task_id}")
        print(f"Reason: {handoff.reason}")
        return handoff

    try:
        state_path = vault.find_state_path(reference)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No proposal, task, handoff, or project: {reference}"
        ) from exc

    state = vault.read_state(state_path)
    print("PROJECT")
    print(f"ID: {state.project_id}")
    print(f"Division: {state.division.value}")
    print(f"Phase: {state.phase}")
    print(f"State: {state.state.value}")
    print(f"Owner: {state.owner}")
    print(f"Next Gate: {state.next_gate}")
    return state


def _write_and_report(vault_path, proposal):
    path = write_proposal(vault_path, proposal)
    print(f"Proposal created: {path}")
    print(f"Proposal ID: {proposal.proposal_id}")
    return path


def _create_task(vault, args):
    result = TaskCreationEngine(vault).create(
        TaskCreationCommand(
            project_id=args.project,
            description=args.description,
            task_id=args.task_id,
            assigned_agent=args.agent,
            role=args.role,
            task_type=args.task_type,
            capability=args.capability,
            auditor=args.auditor,
            context_refs=tuple(args.input_ref or []),
        )
    )
    print(f"Task assigned: {result.task.task_id}")
    print(f"Task file: {result.path}")
    return result.task


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Personal Control Tower v3-alpha.1"
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path("vault"),
    )
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
    )

    for command in (
        "init",
        "status",
        "sync",
        "dashboard",
        "tick",
        "agent-list",
    ):
        sub.add_parser(command)

    demo = sub.add_parser("demo")
    demo.add_argument(
        "--reset",
        action="store_true",
        help="Delete and rebuild only the explicitly selected demo vault.",
    )

    chat = sub.add_parser(
        "chat",
        help="Open the governed v3-alpha.1 Chief of Staff chat.",
    )
    chat.add_argument(
        "--message",
        help="Run one governed chat turn and exit.",
    )
    chat.add_argument(
        "--provider",
        choices=("offline", "openai"),
        help="Override LLM_PROVIDER for this chat session.",
    )
    chat.add_argument(
        "--model",
        help="Override LLM_MODEL for this chat session.",
    )
    chat.add_argument(
        "--llm-config",
        type=Path,
        help="Read LLM settings from an explicit dotenv file.",
    )

    approve = sub.add_parser("approve")
    approve.add_argument("proposal")

    reject = sub.add_parser("reject")
    reject.add_argument("proposal")
    reject.add_argument("--note", default="")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("reference")

    project = sub.add_parser("project-create")
    project.add_argument("project_id")
    project.add_argument("--title", required=True)
    project.add_argument(
        "--division",
        choices=(
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        ),
        required=True,
    )
    project.add_argument("--owner", required=True)
    project.add_argument("--phase", default="T0")
    project.add_argument("--lineage", default="CANONICAL")

    agent = sub.add_parser("agent-create")
    agent.add_argument("agent_id")
    agent.add_argument(
        "--division",
        choices=(
            "ROOT",
            "RESEARCH",
            "BUSINESS",
            "PERSONAL_GROWTH",
        ),
        required=True,
    )
    agent.add_argument("--role", required=True)
    agent.add_argument(
        "--capability",
        action="append",
        required=True,
    )

    archive = sub.add_parser("agent-archive")
    archive.add_argument("agent_id")
    archive.add_argument("--reason", default="")

    role = sub.add_parser("agent-role")
    role.add_argument("agent_id")
    role.add_argument("role")
    role.add_argument("--reason", default="")

    capability = sub.add_parser("agent-capability")
    capability.add_argument("agent_id")
    capability.add_argument("capability")
    capability.add_argument(
        "--operation",
        choices=("ADD", "REMOVE"),
        default="ADD",
    )
    capability.add_argument("--reason", default="")

    binding = sub.add_parser("bind")
    binding.add_argument("project")
    binding.add_argument("agent")
    binding.add_argument("role")

    authorize = sub.add_parser("authorize")
    authorize.add_argument("project")
    authorize.add_argument("authorization_id")
    authorize.add_argument("--scope", required=True)
    authorize.add_argument("--next-phase")

    decide = sub.add_parser("decide")
    decide.add_argument("project")
    decide.add_argument(
        "decision",
        choices=(
            "AUTHORIZE",
            "MODIFY",
            "REPAIR",
            "HOLD",
            "CLOSE",
        ),
    )
    decide.add_argument("decision_id")
    decide.add_argument("--note", default="")
    decide.add_argument("--next-phase")
    decide.add_argument("--scope")

    task = sub.add_parser("task-create")
    task.add_argument("project")
    task.add_argument("--task-id")
    task.add_argument("--agent")
    task.add_argument(
        "--role",
        choices=tuple(role.value for role in Role),
        default="PRODUCER",
    )
    task.add_argument("--task-type")
    task.add_argument("--capability")
    task.add_argument("--description", default="")
    task.add_argument("--auditor")
    task.add_argument("--input-ref", action="append")

    task_list = sub.add_parser("task-list")
    task_list.add_argument("--project")
    task_list.add_argument("--status")

    task_run = sub.add_parser("task-run")
    task_run.add_argument("project")
    task_run.add_argument("task_id")

    task_retry = sub.add_parser("task-retry")
    task_retry.add_argument("project")
    task_retry.add_argument("task_id")

    handoff_list = sub.add_parser("handoff-list")
    handoff_list.add_argument("--project")
    handoff_list.add_argument("--receiver")

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    # Chat is deliberately routed before Vault initialization. A missing
    # Vault fails closed instead of being created by a query or draft.
    if args.cmd == "chat":
        try:
            settings = load_llm_settings(
                config_path=args.llm_config,
                provider_override=args.provider,
                model_override=args.model,
            )
        except LLMConfigurationError as exc:
            print(
                "Chat unavailable; no action was taken: "
                f"{exc}",
                file=sys.stderr,
            )
            return 2

        return run_chat(
            args.vault,
            message=args.message,
            settings=settings,
        )

    vault = Vault(args.vault)
    vault.ensure_structure()

    if args.cmd == "init":
        print(f"Initialized: {args.vault.resolve()}")

    elif args.cmd == "demo":
        run_demo(
            args.vault,
            reset_demo=args.reset,
        )

    elif args.cmd == "status":
        print(render_status(args.vault))

    elif args.cmd == "sync":
        proposals = sync_runtime(args.vault)
        print("SYNC COMPLETE")

        if proposals:
            for proposal in proposals:
                print(f"- Proposal created: {proposal}")
        else:
            print("No drift detected.")

    elif args.cmd == "dashboard":
        render_dashboard(args.vault)

    elif args.cmd == "approve":
        result_path = approve_proposal(
            args.vault,
            args.proposal,
        )
        print("Proposal approved and executed.")
        print(f"Updated path: {result_path}")

    elif args.cmd == "reject":
        archived = reject_proposal(
            args.vault,
            args.proposal,
            args.note,
        )
        print("Proposal rejected.")
        print(f"Archived: {archived}")

    elif args.cmd == "inspect":
        inspect_reference(
            args.vault,
            args.reference,
        )

    elif args.cmd == "tick":
        result = ChiefOfStaff(vault).tick()
        print("CONTROL TOWER TICK")
        print(
            "Events processed: "
            f"{result['events_processed']}"
        )
        print(
            "Tasks completed: "
            f"{len(result['tasks_completed'])}"
        )
        print(
            "Task failures: "
            f"{len(result['task_failures'])}"
        )
        print(
            "Pending Root items: "
            f"{result['pending_root_items']}"
        )

    elif args.cmd == "project-create":
        _write_and_report(
            args.vault,
            create_project_proposal(
                args.project_id,
                args.title,
                args.division,
                args.owner,
                args.phase,
                args.lineage,
            ),
        )

    elif args.cmd == "agent-create":
        _write_and_report(
            args.vault,
            create_agent_proposal(
                args.agent_id,
                args.division,
                args.role,
                args.capability,
            ),
        )

    elif args.cmd == "agent-archive":
        _write_and_report(
            args.vault,
            create_archive_agent_proposal(
                args.agent_id,
                args.reason or (
                    "Archive agent by Root request."
                ),
            ),
        )

    elif args.cmd == "agent-role":
        _write_and_report(
            args.vault,
            create_update_agent_role_proposal(
                args.agent_id,
                args.role,
                args.reason or (
                    "Update agent role by Root request."
                ),
            ),
        )

    elif args.cmd == "agent-capability":
        _write_and_report(
            args.vault,
            create_update_agent_capability_proposal(
                args.agent_id,
                args.capability,
                args.operation,
                args.reason or (
                    "Update agent capability by Root request."
                ),
            ),
        )

    elif args.cmd == "bind":
        _write_and_report(
            args.vault,
            create_binding_proposal(
                args.project,
                args.agent,
                args.role,
            ),
        )

    elif args.cmd == "authorize":
        state_path = vault.find_state_path(
            args.project
        )
        state = DecisionEngine(
            vault,
            AgentRegistry(args.vault),
            EventLedger(vault),
        ).authorize(
            state_path,
            args.authorization_id,
            args.scope,
            args.next_phase,
        )
        print(
            f"Authorized {state.project_id} "
            f"for {state.phase}."
        )

    elif args.cmd == "decide":
        state_path = vault.find_state_path(
            args.project
        )
        state = DecisionEngine(
            vault,
            AgentRegistry(args.vault),
            EventLedger(vault),
        ).root_decide(
            state_path=state_path,
            decision_id=args.decision_id,
            decision=args.decision,
            note=args.note,
            next_phase=args.next_phase,
            scope=args.scope,
        )
        print(
            f"Root decision applied: "
            f"{state.state.value}"
        )

    elif args.cmd == "task-create":
        _create_task(vault, args)

    elif args.cmd == "task-list":
        for state_path in _project_state_paths(vault):
            if (
                args.project
                and state_path.parent.name != args.project
            ):
                continue

            tasks = TaskStore(state_path.parent).list(
                status=args.status,
            )

            for task in tasks:
                print(
                    f"{task.task_id} | {task.project_id} | "
                    f"{task.status.value} | {task.assigned_agent}"
                )

    elif args.cmd == "task-run":
        state_path = vault.find_state_path(
            args.project
        )
        task = ChiefOfStaff(vault).run_task(
            state_path,
            args.task_id,
        )
        print(
            f"Task result: {task.status.value}"
        )

    elif args.cmd == "task-retry":
        state_path = vault.find_state_path(
            args.project
        )
        previous = TaskStore(
            state_path.parent
        ).get(args.task_id)
        reason = (
            "Recovered after interrupted runtime."
            if previous.status == TaskStatus.RUNNING
            else (
                "Explicit Root retry from "
                f"{previous.status.value}."
            )
        )
        task = ChiefOfStaff(vault).recover_task(
            state_path,
            args.task_id,
            reason,
        )
        print(
            f"Task recovery result: {task.task_id} "
            f"{task.status.value}"
        )

    elif args.cmd == "handoff-list":
        for state_path in _project_state_paths(vault):
            if (
                args.project
                and state_path.parent.name != args.project
            ):
                continue

            handoffs = HandoffStore(
                state_path.parent
            ).list(receiver=args.receiver)

            for handoff in handoffs:
                print(
                    f"{handoff.handoff_id} | "
                    f"{handoff.sender} -> {handoff.receiver} | "
                    f"{handoff.status.value}"
                )

    elif args.cmd == "agent-list":
        for agent in AgentRegistry(args.vault).load():
            print(
                f"{agent.agent_id} | {agent.division} | "
                f"{agent.role.value} | {agent.status.value} | "
                f"{','.join(agent.capabilities)}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
