from pathlib import Path

import yaml

from .vault import Vault



def render_dashboard(
    vault_path: Path
):

    vault = Vault(
        vault_path
    )

    vault.ensure_structure()


    print(
        "=" * 60
    )

    print(
        "PERSONAL CONTROL TOWER DASHBOARD"
    )

    print(
        "=" * 60
    )



    print()

    print(
        "PROJECT STATES"
    )

    print(
        "-" * 40
    )


    research_dir = (
        vault.root
        /
        "01_RESEARCH"
    )


    if not research_dir.exists():

        print(
            "No projects."
        )

        return



    for project_dir in research_dir.iterdir():

        if not project_dir.is_dir():

            continue


        state_path = (
            project_dir
            /
            "STATE.md"
        )


        if not state_path.exists():

            continue



        state = vault.read_state(
            state_path
        )


        print()

        print(
            state.project_id
        )


        print(
            f"Division: {state.division.value}"
        )


        print(
            f"Phase: {state.phase}"
        )


        print(
            f"State: {state.state.value}"
        )


        print(
            f"Owner: {state.owner}"
        )


        print()


        print(
            "Agents:"
        )


        agents = getattr(
            state,
            "agents",
            {}
        )


        if not agents:

            print(
                "  None"
            )


        elif isinstance(
            agents,
            dict
        ):


            for role, members in agents.items():

                print(
                    f"  {role}:"
                )

                for agent in members:

                    print(
                        f"    - {agent}"
                    )


        else:

            # backward compatibility
            for agent in agents:

                print(
                    f"  - {agent}"
                )


        print()

        print(
            f"Next Gate: {state.next_gate}"
        )

        print(
            "-" * 40
        )



    print()

    print(
        "ROOT INBOX"
    )

    print(
        "-" * 40
    )


    inbox = (
        vault.root
        /
        "00_ROOT"
        /
        "inbox"
    )


    if inbox.exists():

        items = list(
            inbox.glob("*.md")
        )


        if items:

            for item in items:

                print(
                    f"- {item.name}"
                )

        else:

            print(
                "Empty"
            )

    else:

        print(
            "Empty"
        )