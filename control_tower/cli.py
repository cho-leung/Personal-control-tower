import argparse

from pathlib import Path

from .vault import Vault
from .demo import run_demo
from .status import render_status
from .sync import sync_runtime
from .decision import approve_proposal


def main():

    p = argparse.ArgumentParser()

    p.add_argument(
        "--vault",
        type=Path,
        default=Path("vault")
    )

    sub = p.add_subparsers(
        dest="cmd",
        required=True
    )


    # simple commands
    for x in [
        "init",
        "demo",
        "status",
        "sync"
    ]:
        sub.add_parser(x)


    # approve command
    approve_parser = sub.add_parser(
        "approve"
    )

    approve_parser.add_argument(
        "proposal",
        help="Proposal id prefix"
    )


    a = p.parse_args()


    if a.cmd == "init":

        Vault(a.vault).ensure_structure()

        print(
            f"Initialized: {a.vault.resolve()}"
        )


    elif a.cmd == "demo":

        run_demo(a.vault)

        print(
            render_status(a.vault)
        )


    elif a.cmd == "status":

        Vault(a.vault).ensure_structure()

        print(
            render_status(a.vault)
        )


    elif a.cmd == "sync":

        Vault(a.vault).ensure_structure()

        proposals = sync_runtime(
            a.vault
        )

        print(
            "SYNC COMPLETE"
        )

        if proposals:

            for proposal in proposals:
                print(
                    f"- Proposal created: {proposal}"
                )

        else:

            print(
                "No drift detected."
            )


    elif a.cmd == "approve":

        Vault(a.vault).ensure_structure()

        state_path = approve_proposal(
            a.vault,
            a.proposal
        )

        print(
            "Proposal approved."
        )

        print(
            f"Created runtime: {state_path}"
        )


if __name__ == "__main__":
    main()