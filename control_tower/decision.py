from pathlib import Path

import yaml

from .agents import (
    AgentRegistry,
    AgentRole,
    AgentStatus,
)
from .core.agent_creation_engine import AgentCreationEngine
from .core.agent_lifecycle_engine import AgentLifecycleEngine
from .core.audit_request_engine import AuditRequestEngine
from .core.binding_engine import BindingEngine
from .core.project_creation_engine import ProjectCreationEngine
from .core.task_creation_engine import (
    TaskCreationCommand,
    TaskCreationEngine,
)
from .events import Event, EventLedger, EventResult
from .guardrails import GovernanceError
from .models import (
    Proposal,
    ProposalState,
)
from .proposal_router import ProposalRouter
from .vault import Vault


def _read_metadata(path):
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)

    if len(parts) < 3:
        raise ValueError(
            f"Invalid proposal format: {path}"
        )

    metadata = yaml.safe_load(parts[1])

    if not isinstance(metadata, dict):
        raise ValueError(
            f"Invalid proposal metadata: {path}"
        )

    return metadata


def _find_proposal_path(
    vault_path: Path,
    proposal_name: str,
):
    inbox = vault_path / "00_ROOT" / "inbox"
    raw_candidates = sorted(
        inbox.glob(f"{proposal_name}*.md")
    )
    candidates = []
    exact = []

    for candidate in raw_candidates:
        try:
            metadata = _read_metadata(candidate)

            if not (
                metadata.get("proposal_id")
                and metadata.get("proposal_type")
            ):
                continue

            candidates.append(candidate)

            if metadata.get("proposal_id") == proposal_name:
                exact.append(candidate)
        except ValueError:
            continue

    if not candidates:
        raise FileNotFoundError(
            f"No proposal found: {proposal_name}"
        )

    if len(exact) == 1:
        return exact[0]

    if len(candidates) > 1:
        names = ", ".join(
            candidate.name for candidate in candidates
        )
        raise ValueError(
            "Ambiguous proposal prefix. Use a full proposal id: "
            f"{names}"
        )

    return candidates[0]


def _load_proposal(path):
    return Proposal.from_dict(
        _read_metadata(path)
    )


def _require_root(vault_path, capability):
    root = AgentRegistry(vault_path).get(
        "personal_root"
    )

    if not root:
        raise GovernanceError(
            "Root agent missing."
        )

    if root.status != AgentStatus.ACTIVE:
        raise GovernanceError(
            "Root agent is not ACTIVE."
        )

    if root.role != AgentRole.ROOT:
        raise GovernanceError(
            "personal_root does not have ROOT role."
        )

    if capability not in root.capabilities:
        raise GovernanceError(
            f"Root lacks capability: {capability}"
        )

    return root


def update_proposal_state(
    proposal_path: Path,
    state: ProposalState,
    decided_by: str = "personal_root",
    note: str = "",
):
    metadata = _read_metadata(proposal_path)
    metadata["state"] = state.value
    metadata["decided_by"] = decided_by
    metadata["decision_note"] = note

    payload_text = yaml.safe_dump(
        metadata.get("payload", {}),
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    serialized = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
    )
    body = f"""
# Root Proposal

## Current State

{state.value}

## Proposal

- ID: `{metadata.get('proposal_id')}`
- Type: `{metadata.get('proposal_type')}`
- Target: `{metadata.get('target')}`
- Created by: `{metadata.get('created_by')}`

## Reason

{metadata.get('reason') or 'None.'}

## Decision

- By: `{decided_by}`
- Note: {note or 'None.'}

## Payload

```yaml
{payload_text}
```
"""
    proposal_path.write_text(
        "---\n" + serialized + "---\n" + body.lstrip(),
        encoding="utf-8",
    )


def execute_create_runtime(
    vault_path: Path,
    proposal: Proposal,
):
    from .registry import RegistryLoader

    vault = Vault(vault_path)
    projects = RegistryLoader(vault_path).load_projects()
    target = next(
        (
            project
            for project in projects
            if project["project"] == proposal.target
        ),
        None,
    )

    if not target:
        raise RuntimeError(
            f"Project not found: {proposal.target}"
        )

    runtime_project = Proposal(
        proposal_id=proposal.proposal_id,
        proposal_type="CREATE_PROJECT",
        target=proposal.target,
        reason=proposal.reason,
        state=proposal.state,
        created_by=proposal.created_by,
        payload={
            "project_id": proposal.target,
            "title": proposal.target,
            "division": target["division"],
            "owner": target["owner"],
            "phase": "T0",
            "lineage": "CANONICAL",
        },
    )

    return ProjectCreationEngine(
        vault
    ).create_project(runtime_project)


def execute_create_project(vault_path, proposal):
    return ProjectCreationEngine(
        Vault(vault_path)
    ).create_project(proposal)


def execute_create_agent(vault_path, proposal):
    return AgentCreationEngine(
        Vault(vault_path)
    ).create_agent(proposal)


def execute_create_task(vault_path, proposal):
    payload = proposal.payload
    expected_keys = {
        "task_id",
        "project_id",
        "phase",
        "task_type",
        "assigned_agent",
        "required_role",
        "required_capability",
        "description",
        "context_refs",
        "authorization_id",
        "auditor",
    }

    if set(payload) != expected_keys:
        raise GovernanceError(
            "CREATE_TASK proposal payload schema mismatch."
        )

    project_id = payload["project_id"]

    if proposal.target != project_id:
        raise GovernanceError(
            "Task proposal target does not match project_id."
        )

    if not isinstance(payload["context_refs"], list) or not all(
        isinstance(ref, str) for ref in payload["context_refs"]
    ):
        raise GovernanceError(
            "CREATE_TASK context_refs must be a list of strings."
        )

    result = TaskCreationEngine(
        Vault(vault_path)
    ).create(
        TaskCreationCommand(
            project_id=project_id,
            description=payload["description"],
            task_id=payload["task_id"],
            assigned_agent=payload["assigned_agent"],
            role=payload["required_role"],
            task_type=payload["task_type"],
            capability=payload["required_capability"],
            auditor=payload["auditor"],
            context_refs=tuple(payload["context_refs"]),
            expected_phase=payload["phase"],
            expected_authorization_id=(
                payload["authorization_id"]
            ),
            proposal_id=proposal.proposal_id,
        )
    )
    return result.path


def execute_create_binding(vault_path, proposal):
    return BindingEngine(
        Vault(vault_path)
    ).bind(proposal)


def execute_create_audit_request(
    vault_path,
    proposal,
):
    vault = Vault(vault_path)
    return AuditRequestEngine(
        vault,
        AgentRegistry(vault_path),
    ).approve(proposal)


def execute_agent_lifecycle(vault_path, proposal):
    return AgentLifecycleEngine(
        Vault(vault_path)
    ).execute(proposal)


def execute_proposal(
    vault_path: Path,
    proposal: Proposal,
):
    vault = Vault(vault_path)
    ProposalRouter(vault).route(proposal)
    handlers = {
        "CREATE_RUNTIME": execute_create_runtime,
        "CREATE_PROJECT": execute_create_project,
        "CREATE_PROJECT_REQUEST": execute_create_project,
        "CREATE_AGENT": execute_create_agent,
        "CREATE_AGENT_REQUEST": execute_create_agent,
        "CREATE_TASK": execute_create_task,
        "CREATE_BINDING": execute_create_binding,
        "CREATE_AUDIT_REQUEST": (
            execute_create_audit_request
        ),
        "ARCHIVE_AGENT": execute_agent_lifecycle,
        "UPDATE_AGENT_ROLE": execute_agent_lifecycle,
        "UPDATE_AGENT_CAPABILITY": (
            execute_agent_lifecycle
        ),
    }
    handler = handlers.get(proposal.proposal_type)

    if not handler:
        raise ValueError(
            "Unsupported proposal type: "
            f"{proposal.proposal_type}"
        )

    return handler(vault_path, proposal)


def approve_proposal(
    vault_path: Path,
    proposal_name: str,
):
    vault = Vault(vault_path)
    _require_root(vault_path, "approve")
    proposal_path = _find_proposal_path(
        vault_path,
        proposal_name,
    )
    proposal = _load_proposal(proposal_path)

    if proposal.state not in {
        ProposalState.CREATED,
        ProposalState.WAITING_ROOT,
        ProposalState.APPROVED,
        ProposalState.EXECUTED,
    }:
        raise GovernanceError(
            "Proposal cannot be approved from state: "
            f"{proposal.state.value}"
        )

    result_path = execute_proposal(
        vault_path,
        proposal,
    )
    ledger = EventLedger(vault)
    ledger.append_once(
        Event(
            event_id=(
                f"EVT-{proposal.proposal_id}-EXECUTED"
            ),
            actor="personal_root",
            action=proposal.proposal_type,
            target=proposal.target,
            result=EventResult.SUCCESS,
            capability_checked="approve",
            note="Root-approved proposal executed.",
            correlation_id=proposal.proposal_id,
            metadata={
                "proposal_id": proposal.proposal_id,
            },
        )
    )
    update_proposal_state(
        proposal_path,
        ProposalState.EXECUTED,
        decided_by="personal_root",
        note="Proposal approved and executed.",
    )
    archived_path = vault.archive_root_item(
        proposal_path
    )
    vault.append_decision(
        f"""
## ROOT APPROVAL

- Proposal: `{archived_path.name}`
- Type: `{proposal.proposal_type}`
- Target: `{proposal.target}`
- Result: **EXECUTED**
- Result path: `{result_path}`
"""
    )
    return result_path


def reject_proposal(
    vault_path: Path,
    proposal_name: str,
    note: str = "",
):
    vault = Vault(vault_path)
    _require_root(vault_path, "reject")
    proposal_path = _find_proposal_path(
        vault_path,
        proposal_name,
    )
    proposal = _load_proposal(proposal_path)

    if proposal.state not in {
        ProposalState.CREATED,
        ProposalState.WAITING_ROOT,
    }:
        raise GovernanceError(
            "Proposal cannot be rejected from state: "
            f"{proposal.state.value}"
        )

    EventLedger(vault).append_once(
        Event(
            event_id=(
                f"EVT-{proposal.proposal_id}-REJECTED"
            ),
            actor="personal_root",
            action="REJECT_PROPOSAL",
            target=proposal.target,
            result=EventResult.SUCCESS,
            capability_checked="reject",
            note=note or "Proposal rejected by Root.",
            correlation_id=proposal.proposal_id,
            metadata={
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
            },
        )
    )
    update_proposal_state(
        proposal_path,
        ProposalState.REJECTED,
        decided_by="personal_root",
        note=note or "Proposal rejected.",
    )
    archived_path = vault.archive_root_item(
        proposal_path
    )
    vault.append_decision(
        f"""
## ROOT REJECTION

- Proposal: `{archived_path.name}`
- Type: `{proposal.proposal_type}`
- Target: `{proposal.target}`
- Result: **REJECTED**
- Note: {note or 'None.'}
"""
    )
    return archived_path


def inspect_proposal(
    vault_path: Path,
    proposal_name: str,
):
    proposal_path = _find_proposal_path(
        vault_path,
        proposal_name,
    )
    proposal = _load_proposal(proposal_path)
    print()
    print("=" * 50)
    print("PROPOSAL INSPECTOR")
    print("=" * 50)
    print()
    print(f"ID: {proposal.proposal_id}")
    print(f"Type: {proposal.proposal_type}")
    print(f"Target: {proposal.target}")
    print(f"State: {proposal.state.value}")
    print(f"Created By: {proposal.created_by}")
    print()
    print("Reason:")
    print(proposal.reason)
    print()
    print("Payload:")

    if proposal.payload:
        for key, value in proposal.payload.items():
            print(f"  {key}: {value}")
    else:
        print("  None")

    return proposal
