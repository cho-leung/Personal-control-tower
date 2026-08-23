from pathlib import Path

import yaml

from .agents import AgentRegistry
from .events import EventLedger
from .models import State
from .tasks import TaskStatus, TaskStore
from .vault import Vault


def read_proposal_metadata(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)

        if len(parts) < 3:
            return None

        metadata = yaml.safe_load(parts[1])
        return metadata if isinstance(metadata, dict) else None
    except (OSError, UnicodeError, yaml.YAMLError):
        return None


def _state_paths(vault):
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


def _heading(title):
    print()
    print(title)
    print("-" * 60)


def render_dashboard(vault_path: Path):
    vault = Vault(vault_path)
    vault.ensure_structure()
    state_paths = _state_paths(vault)
    project_states = [
        (path, vault.read_state(path))
        for path in state_paths
    ]

    print("=" * 60)
    print("PERSONAL CONTROL TOWER v1 — MAIN CONTROL ROOM")
    print("=" * 60)

    _heading("PROJECTS")

    if not project_states:
        print("(none)")

    for _, state in project_states:
        print(
            f"{state.project_id} | {state.division.value} | "
            f"{state.phase} | {state.state.value} | "
            f"owner={state.owner} | gate={state.next_gate or '-'}"
        )
        bindings = []

        for role, members in (state.agents or {}).items():
            members = (
                [members]
                if isinstance(members, str)
                else (members or [])
            )
            bindings.append(
                f"{role}={','.join(members)}"
            )

        if bindings:
            print("  agents: " + "; ".join(bindings))

    _heading("AGENTS")
    agents = AgentRegistry(vault.root).load()

    if not agents:
        print("(none)")

    for agent in agents:
        print(
            f"{agent.agent_id} | {agent.division} | "
            f"{agent.role.value} | {agent.status.value} | "
            f"capabilities={','.join(agent.capabilities) or '-'}"
        )

    _heading("TASKS")
    tasks = []

    for state_path, _ in project_states:
        tasks.extend(
            TaskStore(state_path.parent).list()
        )

    if not tasks:
        print("(none)")

    for task in sorted(
        tasks,
        key=lambda item: (item.created_at, item.task_id),
    ):
        print(
            f"{task.task_id} | {task.project_id} | "
            f"{task.task_type} | {task.status.value} | "
            f"agent={task.assigned_agent}"
        )

    _heading("ROOT INBOX")
    inbox = vault.root / "00_ROOT" / "inbox"
    proposals = []
    documents = []

    for item in sorted(inbox.glob("*.md")):
        metadata = read_proposal_metadata(item)

        if metadata and metadata.get("proposal_type"):
            proposals.append((item, metadata))
        else:
            documents.append(item)

    if not proposals and not documents:
        print("(empty)")

    for item, metadata in proposals:
        print(
            f"PROPOSAL | {metadata.get('proposal_id')} | "
            f"{metadata.get('proposal_type')} | "
            f"{metadata.get('state')} | "
            f"target={metadata.get('target')}"
        )

    for item in documents:
        print(f"GATE/DOCUMENT | {item.name}")

    _heading("BLOCKED / ATTENTION")
    attention = []

    for _, state in project_states:
        if state.state in {
            State.BLOCKED,
            State.WAITING_ROOT,
        }:
            attention.append(
                f"PROJECT {state.project_id}: {state.state.value}"
            )

    for task in tasks:
        if task.status in {
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
        }:
            attention.append(
                f"TASK {task.task_id}: {task.status.value}"
            )

    if proposals:
        attention.append(
            f"ROOT: {len(proposals)} proposal(s) awaiting decision"
        )

    if not attention:
        print("(none)")
    else:
        for item in attention:
            print(item)

    _heading("RECENT EVENTS")
    recent = EventLedger(vault).read_all()[-10:]

    if not recent:
        print("(none)")

    for event in recent:
        print(
            f"{event.get('event_id')} | "
            f"{event.get('action')} | "
            f"{event.get('result')} | "
            f"actor={event.get('actor')} | "
            f"target={event.get('target')}"
        )

    print()
    print("END MAIN CONTROL ROOM")
