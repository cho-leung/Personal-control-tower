from pathlib import Path

from ..models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
)


DIVISION_PATH = {

    "RESEARCH": "01_RESEARCH",

    "BUSINESS": "02_BUSINESS",

    "PERSONAL_GROWTH": "03_PERSONAL_GROWTH",

}



class ProjectCreationEngine:


    def __init__(self, vault):

        self.vault = vault



    def create_project(
        self,
        proposal
    ):

        payload = proposal.payload


        project_id = payload["project_id"]

        title = payload["title"]

        division = payload["division"]

        owner = payload["owner"]

        phase = payload.get(
            "phase",
            "T0"
        )

        lineage = payload.get(
            "lineage",
            "CANONICAL"
        )



        folder = DIVISION_PATH[
            division
        ]



        project_dir = (

            self.vault.root

            /

            folder

            /

            project_id

        )



        for sub in [

            "artifacts",

            "audits",

            "handoffs",

            "claims",

            "failed_routes",

        ]:

            (
                project_dir / sub
            ).mkdir(

                parents=True,

                exist_ok=True

            )



        state = ProjectState(

            project_id,

            title,

            Division(division),

            phase,

            State.READY,

            owner,

            Role.PRODUCER,

            Lineage(lineage),

            next_gate="ROOT_AUTHORIZATION",

            notes="Created by ProjectCreationEngine."

        )



        state_path = (

            project_dir

            /

            "STATE.md"

        )



        self.vault.write_state(

            state_path,

            state

        )


        return state_path