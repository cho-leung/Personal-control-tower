from pathlib import Path

from .models import Proposal


class ProposalRouter:


    def __init__(
        self,
        vault
    ):

        self.vault = vault



    def route(
        self,
        proposal: Proposal
    ):

        """
        Decide which engine handles proposal.

        Routing only.
        No execution.
        """


        if proposal.proposal_type == "CREATE_PROJECT":

            return self.handle_project_create(
                proposal
            )


        if proposal.proposal_type == "CREATE_AGENT":

            return self.handle_agent_create(
                proposal
            )


        if proposal.proposal_type == "CREATE_RUNTIME":

            return self.handle_runtime_create(
                proposal
            )


        raise ValueError(
            f"Unknown proposal type: {proposal.proposal_type}"
        )



    def handle_project_create(
        self,
        proposal
    ):

        return {

            "handler":
            "ProjectEngine",

            "action":
            "CREATE_PROJECT",

            "target":
            proposal.target

        }



    def handle_agent_create(
        self,
        proposal
    ):

        return {

            "handler":
            "AgentRegistry",

            "action":
            "CREATE_AGENT",

            "target":
            proposal.target

        }



    def handle_runtime_create(
        self,
        proposal
    ):

        return {

            "handler":
            "SyncEngine",

            "action":
            "CREATE_RUNTIME",

            "target":
            proposal.target

        }