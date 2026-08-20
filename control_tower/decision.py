from pathlib import Path
import yaml

from .models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
    ProposalState,
)

from .vault import Vault


DIVISION_PATH = {
    "RESEARCH": "01_RESEARCH",
    "BUSINESS": "02_BUSINESS",
    "PERSONAL_GROWTH": "03_PERSONAL_GROWTH",
}


def update_proposal_state(
    proposal_path: Path,
    state: ProposalState,
    decided_by: str = "ROOT",
    note: str = ""
):
    """
    Update proposal lifecycle state.
    """

    text = proposal_path.read_text(
        encoding="utf-8"
    )

    parts = text.split(
        "---",
        2
    )

    meta = yaml.safe_load(
        parts[1]
    )

    meta["state"] = state.value
    meta["decided_by"] = decided_by
    meta["decision_note"] = note


    new_meta = yaml.safe_dump(
        meta,
        sort_keys=False,
        allow_unicode=True
    )


    body = f"""
# Root Proposal

## Current State

{state.value}


## Decision By

{decided_by}


## Decision Note

{note}

"""


    proposal_path.write_text(
        "---\n"
        + new_meta
        + "---\n"
        + body,
        encoding="utf-8"
    )



def approve_create_runtime(
    vault_path: Path,
    proposal_path: Path
):
    """
    Root approves CREATE_RUNTIME proposal.
    Creates missing STATE.md runtime.
    """

    vault = Vault(vault_path)


    text = proposal_path.read_text(
        encoding="utf-8"
    )

    meta = text.split(
        "---",
        2
    )[1]

    proposal = yaml.safe_load(
        meta
    )

    project = proposal["target"]


    from .registry import RegistryLoader

    registry = RegistryLoader(
        vault_path
    )

    projects = registry.load_projects()


    target = None

    for p in projects:

        if p["project"] == project:
            target = p
            break


    if not target:

        raise RuntimeError(
            f"Project not found: {project}"
        )


    folder = DIVISION_PATH[
        target["division"]
    ]


    state_path = (
        vault_path
        / folder
        / project
        / "STATE.md"
    )


    if state_path.exists():

        return state_path



    state = ProjectState(

        project_id=project,

        title=project,

        division=Division(
            target["division"]
        ),

        phase="T0",

        state=State.READY,

        owner=target["owner"],

        owner_role=Role.PRODUCER,

        lineage=Lineage.CANONICAL,

        next_gate="ROOT_AUTHORIZATION",

        notes="Runtime created by Root approval."

    )


    vault.write_state(
        state_path,
        state
    )


    return state_path



def approve_proposal(
    vault_path: Path,
    proposal_name: str
):

    inbox = (
        vault_path
        / "00_ROOT"
        / "inbox"
    )


    candidates = list(
        inbox.glob(
            f"{proposal_name}*.md"
        )
    )


    if not candidates:

        raise FileNotFoundError(
            f"No proposal found: {proposal_name}"
        )


    proposal_path = candidates[0]


    state_path = approve_create_runtime(
        vault_path,
        proposal_path
    )


    vault = Vault(
        vault_path
    )


    update_proposal_state(
        proposal_path,
        ProposalState.EXECUTED,
        decided_by="ROOT",
        note="Runtime created successfully."
    )


    archived_path = vault.archive_root_item(
        proposal_path
    )


    vault.append_decision(
        f"""
## ROOT APPROVAL

- Proposal:
`{archived_path.name}`

- Action:
CREATE_RUNTIME

- Result:
APPROVED

- Proposal State:
EXECUTED

- Created Runtime:
`{state_path}`

- Archived:
`{archived_path}`

"""
    )


    return state_path