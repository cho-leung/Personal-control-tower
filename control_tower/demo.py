from pathlib import Path
import shutil
import yaml

from .vault import Vault
from .bus import ControlTowerBus
from .agents import (
    AgentRegistry,
    AgentRole,
    AgentState,
    AgentStatus,
)
from .chief_of_staff import ChiefOfStaff
from .runner import AutomatonRunner
from .sync import sync_runtime
from .decision import approve_proposal
from .proposals import (
    create_binding_proposal,
    write_proposal,
)
from .status import render_status


def run_demo(
    vault_path: Path,
    reset_demo=True
):

    if reset_demo and vault_path.exists():
        shutil.rmtree(vault_path)

    if (
        not reset_demo
        and vault_path.exists()
        and any(vault_path.glob("0[123]_*/*/STATE.md"))
    ):
        raise RuntimeError(
            "Demo requires an empty vault or explicit --reset."
        )


    vault = Vault(vault_path)
    vault.ensure_structure()


    registry = AgentRegistry(vault.root)
    agents = registry.load()
    known = {agent.agent_id for agent in agents}


    if "toy_producer" not in known:
        agents.append(
            AgentState(
                agent_id="toy_producer",
                division="RESEARCH",
                role=AgentRole.PRODUCER,
                status=AgentStatus.ACTIVE,
                owns=["TOY-THEOREM"],
                capabilities=["produce_artifact"],
                notes="Deterministic demo producer.",
            )
        )


    if "toy_auditor" not in known:
        agents.append(
            AgentState(
                agent_id="toy_auditor",
                division="RESEARCH",
                role=AgentRole.AUDITOR,
                status=AgentStatus.ACTIVE,
                owns=["TOY-THEOREM"],
                capabilities=["audit"],
                notes="Deterministic demo auditor.",
            )
        )


    registry.save(agents)


    print("\n==============================")
    print("CONTROL TOWER DEMO")
    print("==============================\n")


    #
    # Part 1
    # Project workflow
    #

    print("[1] Project workflow")


    bus = ControlTowerBus(vault)


    project_proposal, _ = bus.create_research_project(
        "TOY-THEOREM",
        "Synthetic Governance Test",
        "toy_producer",
        "T0"
    )


    approve_proposal(
        vault_path,
        project_proposal.proposal_id,
    )


    state_path = vault.find_state_path(
        "TOY-THEOREM"
    )


    binding_proposal = create_binding_proposal(
        "TOY-THEOREM",
        "toy_auditor",
        "AUDITOR",
    )


    write_proposal(
        vault_path,
        binding_proposal,
    )


    approve_proposal(
        vault_path,
        binding_proposal.proposal_id,
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


    AutomatonRunner(vault).run_pending()


    approve_proposal(
        vault_path,
        "CREATE_AUDIT_REQUEST"
    )


    ChiefOfStaff(vault).tick()


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
            "\n| DEMO-PROJECT | RESEARCH | toy_producer | ACTIVE | demo |\n"
        )


    proposals = sync_runtime(vault_path)


    print(
        f"    proposals created: {len(proposals)}"
    )


    if proposals:

        proposal = proposals[0]

        metadata = yaml.safe_load(
            proposal.read_text(
                encoding="utf-8"
            ).split("---", 2)[1]
        )


        approve_proposal(
            vault_path,
            metadata["proposal_id"]
        )


        print(
            "    root approval OK"
        )


    print("\n[3] Final status\n")

    print(
        render_status(vault_path)
    )
