from datetime import datetime, timezone
from pathlib import Path
import yaml

from .models import Proposal, ProposalState


def create_sync_proposal(
    project_name: str,
    expected_path: str
):
    """
    Create a proposal when registry and runtime disagree.

    This does NOT execute anything.
    It only creates a Root decision request.
    """

    proposal_id = (
        "SYNC-"
        + datetime.now(timezone.utc)
        .strftime("%Y%m%d%H%M%S")
    )

    return Proposal(
        proposal_id=proposal_id,
        proposal_type="CREATE_RUNTIME",
        target=project_name,
        reason=(
            "Registry entry exists but runtime missing: "
            f"{expected_path}"
        ),
        state=ProposalState.WAITING_ROOT,
        created_by="SYNC_CONTROLLER"
    )


def write_proposal(
    vault_path: Path,
    proposal: Proposal
):
    """
    Persist proposal into Root inbox.

    Proposal is only recorded.
    It is not approved or executed.
    """

    inbox = vault_path / "00_ROOT" / "inbox"

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

    body = f"""
# Root Proposal

## Decision Required

Root approval required.

## Proposal Type

{proposal.proposal_type}

## Target

{proposal.target}

## Current State

{proposal.state.value}

## Reason

{proposal.reason}

## Created By

{proposal.created_by}

---

Possible decisions:

- APPROVE
- REJECT
- HOLD

"""

    path.write_text(
        "---\n"
        + metadata
        + "---\n"
        + body,
        encoding="utf-8"
    )

    return path