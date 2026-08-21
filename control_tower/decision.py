from pathlib import Path
import yaml

from .models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
    ProposalState,
    Proposal,
)

from .vault import Vault

from .proposal_router import ProposalRouter
from .core.project_creation_engine import ProjectCreationEngine
from .core.agent_creation_engine import AgentCreationEngine



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
        +
        new_meta
        +
        "---\n"
        +
        body,

        encoding="utf-8"

    )





def execute_create_runtime(
    vault_path: Path,
    proposal: Proposal
):

    vault = Vault(
        vault_path
    )


    project = proposal.target


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
        /
        folder
        /
        project
        /
        "STATE.md"

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





def execute_create_project(
    vault_path: Path,
    proposal: Proposal
):

    """
    Execute CREATE_PROJECT proposal.

    Creates a new project runtime.
    """


    vault = Vault(
        vault_path
    )


    engine = ProjectCreationEngine(
        vault
    )


    return engine.create_project(
        proposal
    )





def execute_proposal(
    vault_path: Path,
    proposal: Proposal
):

    vault = Vault(
        vault_path
    )


    router = ProposalRouter(
        vault
    )


    route = router.route(
        proposal
    )


    print(
        "ROUTED:",
        route
    )



    if proposal.proposal_type == "CREATE_RUNTIME":

        return execute_create_runtime(
            vault_path,
            proposal
        )



    if proposal.proposal_type == "CREATE_PROJECT":

        return execute_create_project(
            vault_path,
            proposal
        )



    if proposal.proposal_type == "CREATE_AGENT":

        vault = Vault(
            vault_path
        )

        engine = AgentCreationEngine(
            vault
        )

        result = engine.create_agent(
            proposal
        )

        return result


    raise ValueError(
        f"Unsupported proposal type: {proposal.proposal_type}"
    )





def approve_proposal(
    vault_path: Path,
    proposal_name: str
):


    inbox = (

        vault_path
        /
        "00_ROOT"
        /
        "inbox"

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



    text = proposal_path.read_text(
        encoding="utf-8"
    )


    meta = text.split(
        "---",
        2
    )[1]


    proposal_data = yaml.safe_load(
        meta
    )


    proposal = Proposal.from_dict(
        proposal_data
    )



    state_path = execute_proposal(
        vault_path,
        proposal
    )



    update_proposal_state(

        proposal_path,

        ProposalState.EXECUTED,

        decided_by="ROOT",

        note="Proposal executed."

    )



    vault = Vault(
        vault_path
    )


    archived_path = vault.archive_root_item(
        proposal_path
    )



    vault.append_decision(

        f"""
## ROOT APPROVAL


- Proposal:

`{archived_path.name}`


- Type:

{proposal.proposal_type}


- Result:

EXECUTED


- Runtime:

{state_path}

"""

    )


    return state_path