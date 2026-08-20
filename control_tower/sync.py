from pathlib import Path

from .registry import RegistryLoader
from .proposals import (
    create_sync_proposal,
    write_proposal
)


DIVISION_PATH = {
    "RESEARCH": "01_RESEARCH",
    "BUSINESS": "02_BUSINESS",
    "PERSONAL_GROWTH": "03_PERSONAL_GROWTH",
}


def has_pending_proposal(
    vault_path: Path,
    project_name: str
):
    inbox = (
        vault_path
        / "00_ROOT"
        / "inbox"
    )

    for p in inbox.glob("*.md"):

        text = p.read_text(
            encoding="utf-8"
        )

        if (
            f"target: {project_name}" in text
            and
            "state: WAITING_ROOT" in text
        ):
            return True

    return False



def check_registry_runtime(vault_path: Path):

    registry = RegistryLoader(vault_path)

    projects = registry.load_projects()

    missing = []

    for project in projects:

        folder = DIVISION_PATH.get(
            project["division"]
        )

        if not folder:
            continue

        runtime_path = (
            vault_path
            / folder
            / project["project"]
            / "STATE.md"
        )

        if not runtime_path.exists():

            missing.append(
                {
                    "project": project["project"],
                    "division": project["division"],
                    "expected": str(runtime_path)
                }
            )

    return missing



def sync_runtime(vault_path: Path):

    missing = check_registry_runtime(vault_path)

    proposals = []

    for item in missing:

        project_name = item["project"]


        # idempotency check:
        # existing pending proposal -> do not create duplicate
        if has_pending_proposal(
            vault_path,
            project_name
        ):
            continue


        proposal = create_sync_proposal(
            project_name,
            item["expected"]
        )


        path = write_proposal(
            vault_path,
            proposal
        )


        proposals.append(path)


    return proposals