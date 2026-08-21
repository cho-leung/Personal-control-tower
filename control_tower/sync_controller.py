from pathlib import Path

from .schemas import (
    ProjectManifest,
    AgentManifest,
)

from .proposals import (
    create_project_proposal,
    create_agent_proposal,
    write_proposal,
)


class SyncController:

    def __init__(
        self,
        vault_path: Path
    ):

        self.vault_path = vault_path


    def propose_project_create(
        self,
        manifest: ProjectManifest
    ):
        """
        Convert an external ProjectManifest
        into a Root-governed CREATE_PROJECT proposal.
        """

        proposal = create_project_proposal(

            project_id=
                manifest.project_id,

            title=
                manifest.title,

            division=
                manifest.division,

            owner=
                manifest.owner,

            phase=
                manifest.phase,

            lineage=
                manifest.lineage
        )

        return write_proposal(
            self.vault_path,
            proposal
        )


    def propose_agent_create(
        self,
        manifest: AgentManifest
    ):
        """
        Convert an external AgentManifest
        into a Root-governed CREATE_AGENT proposal.
        """

        proposal = create_agent_proposal(

            agent_id=
                manifest.agent_id,

            division=
                manifest.division,

            role=
                manifest.role,

            capabilities=
                manifest.capabilities,

            status=
                manifest.status
        )

        return write_proposal(
            self.vault_path,
            proposal
        )