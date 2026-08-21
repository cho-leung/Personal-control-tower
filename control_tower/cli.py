import argparse

from pathlib import Path

from .vault import Vault
from .demo import run_demo
from .status import render_status
from .sync import sync_runtime
from .decision import approve_proposal
from .dashboard import render_dashboard



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


    # -------------------------
    # basic commands
    # -------------------------

    for x in [
        "init",
        "demo",
        "status",
        "sync",
        "dashboard"
    ]:

        sub.add_parser(x)



    # -------------------------
    # approve command
    # -------------------------

    approve_parser = sub.add_parser(
        "approve"
    )


    approve_parser.add_argument(
        "proposal",
        help="Proposal id prefix"
    )



    args = p.parse_args()



    # -------------------------
    # INIT
    # -------------------------

    if args.cmd == "init":

        Vault(
            args.vault
        ).ensure_structure()


        print(
            f"Initialized: {args.vault.resolve()}"
        )



    # -------------------------
    # DEMO
    # -------------------------

    elif args.cmd == "demo":

        Vault(
            args.vault
        ).ensure_structure()


        run_demo(
            args.vault
        )


        print(
            render_status(
                args.vault
            )
        )



    # -------------------------
    # STATUS
    # -------------------------

    elif args.cmd == "status":

        Vault(
            args.vault
        ).ensure_structure()


        print(
            render_status(
                args.vault
            )
        )



    # -------------------------
    # SYNC
    # -------------------------

    elif args.cmd == "sync":

        Vault(
            args.vault
        ).ensure_structure()


        proposals = sync_runtime(
            args.vault
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



    # -------------------------
    # APPROVE
    # -------------------------

    elif args.cmd == "approve":

        Vault(
            args.vault
        ).ensure_structure()


        state_path = approve_proposal(
            args.vault,
            args.proposal
        )


        print(
            "Proposal approved."
        )


        print(
            f"Created runtime: {state_path}"
        )



    # -------------------------
    # DASHBOARD
    # -------------------------

    elif args.cmd == "dashboard":

        Vault(
            args.vault
        ).ensure_structure()


        print(
            render_dashboard(
                args.vault
            )
        )




if __name__ == "__main__":

    main()