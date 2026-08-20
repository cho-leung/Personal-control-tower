from pathlib import Path
import shutil

from .vault import Vault
from .bus import ControlTowerBus
from .models import AuditVerdict
from .sync import sync_runtime
from .decision import approve_proposal
from .status import render_status


def run_demo(
    vault_path: Path,
    reset_demo=True
):

    if reset_demo and vault_path.exists():
        shutil.rmtree(vault_path)


    vault = Vault(vault_path)
    vault.ensure_structure()


    print("\n==============================")
    print("CONTROL TOWER DEMO")
    print("==============================\n")


    #
    # Part 1
    # Project workflow
    #

    print("[1] Project workflow")


    bus = ControlTowerBus(vault)


    _, state_path = bus.create_research_project(
        "TOY-THEOREM",
        "Synthetic Governance Test",
        "toy_producer",
        "T0"
    )


    bus.root_authorize(
        state_path,
        "ROOT-DEMO-001",
        "Execute synthetic T0 only."
    )


    bus.start_execution(
        state_path,
        "toy_producer"
    )


    bus.producer_complete(
        state_path,
        "toy_producer",
        "SYNTHETIC CLAIM\n",
        "toy_auditor"
    )


    bus.record_audit(
        state_path,
        "toy_auditor",
        AuditVerdict.PASS,
        "Synthetic audit passed."
    )


    print("    project lifecycle OK\n")


    #
    # Part 2
    # Sync workflow
    #

    print("[2] Registry reconciliation")


    # create fake registry drift
    registry_project = (
        vault.root
        / "00_ROOT"
        / "PROJECT_REGISTRY.md"
    )


    with registry_project.open(
        "a",
        encoding="utf-8"
    ) as f:
        f.write(
            "\n| DEMO-PROJECT | RESEARCH | demo_owner | ACTIVE | demo |\n"
        )


    proposals = sync_runtime(vault_path)


    print(
        f"    proposals created: {len(proposals)}"
    )


    if proposals:

        proposal = proposals[0]

        approve_proposal(
            vault_path,
            proposal.stem.split("_")[0]
        )


        print(
            "    root approval OK"
        )


    print("\n[3] Final status\n")

    print(
        render_status(vault_path)
    )