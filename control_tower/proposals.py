from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

import yaml

from .models import (
    Proposal,
    ProposalState,
)


def create_proposal(
    proposal_type: str,
    target: str,
    reason: str,
    created_by: str,
    payload: Optional[Dict[str, Any]] = None
):
    """
    Generic proposal factory.

    Creating a proposal never executes the action.
    Root approval is still required.
    """

    proposal_id = (

        proposal_type.upper()
        +
        "-"
        +
        datetime.now(timezone.utc)
        .strftime("%Y%m%d%H%M%S")

    )

    return Proposal(

        proposal_id=proposal_id,

        proposal_type=proposal_type,

        target=target,

        reason=reason,

        state=ProposalState.WAITING_ROOT,

        created_by=created_by,

        payload=payload or {}
    )


def create_sync_proposal(
    project_name: str,
    expected_path: str
):
    """
    Backward-compatible proposal used by
    registry-runtime reconciliation.
    """

    return create_proposal(

        proposal_type="CREATE_RUNTIME",

        target=project_name,

        reason=(
            "Registry entry exists but runtime missing: "
            f"{expected_path}"
        ),

        created_by="SYNC_CONTROLLER",

        payload={
            "expected_path": expected_path
        }
    )


def create_project_proposal(
    project_id: str,
    title: str,
    division: str,
    owner: str,
    phase: str = "T0",
    lineage: str = "CANONICAL"
):
    """
    Create a structured project-creation proposal.
    """

    return create_proposal(

        proposal_type="CREATE_PROJECT",

        target=project_id,

        reason=(
            f"Create project '{title}' "
            f"in division {division} "
            f"owned by {owner}."
        ),

        created_by="SYNC_CONTROLLER",

        payload={

            "project_id":
                project_id,

            "title":
                title,

            "division":
                division,

            "owner":
                owner,

            "phase":
                phase,

            "lineage":
                lineage
        }
    )


def create_agent_proposal(
    agent_id: str,
    division: str,
    role: str,
    capabilities,
    status: str = "ACTIVE"
):
    """
    Create a structured agent-creation proposal.
    """

    return create_proposal(

        proposal_type="CREATE_AGENT",

        target=agent_id,

        reason=(
            f"Create agent '{agent_id}' "
            f"with role {role} "
            f"in division {division}."
        ),

        created_by="SYNC_CONTROLLER",

        payload={

            "agent_id":
                agent_id,

            "division":
                division,

            "role":
                role,

            "capabilities":
                list(capabilities),

            "status":
                status
        }
    )


def write_proposal(
    vault_path: Path,
    proposal: Proposal
):
    """
    Persist proposal into Root inbox.

    This function never executes it.
    """

    inbox = (
        vault_path
        /
        "00_ROOT"
        /
        "inbox"
    )

    inbox.mkdir(
        parents=True,
        exist_ok=True
    )

    path = inbox / (
        f"{proposal.proposal_id}_"
        f"{proposal.target}.md"
    )

    metadata = yaml.safe_dump(
        proposal.to_dict(),
        sort_keys=False,
        allow_unicode=True
    )

    payload_text = yaml.safe_dump(
        proposal.payload,
        sort_keys=False,
        allow_unicode=True
    ).rstrip()

    body = f"""
# Root Proposal

## Decision Required

Root approval required.

## Proposal Type

{proposal.proposal_type}

## Target

{proposal.target}

## State

{proposal.state.value}

## Reason

{proposal.reason}

## Created By

{proposal.created_by}

## Payload

```yaml
{payload_text}


---

Possible decisions:

- APPROVE

- REJECT

- HOLD

"""


    path.write_text(
        "---\n"
        +
        metadata
        +
        "---\n"
        +
        body,
        encoding="utf-8"
    )


    return path