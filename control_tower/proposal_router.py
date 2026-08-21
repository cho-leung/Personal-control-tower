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

        proposal_type = proposal.proposal_type


        routes = {


            "CREATE_RUNTIME": {

                "handler": "SyncEngine",

                "action": "CREATE_RUNTIME"

            },


            "CREATE_PROJECT": {

                "handler": "ProjectEngine",

                "action": "CREATE_PROJECT"

            },


            "CREATE_AGENT": {

                "handler": "AgentRegistry",

                "action": "CREATE_AGENT"

            },


            "CREATE_BINDING": {

                "handler": "BindingEngine",

                "action": "CREATE_BINDING"

            },

        }


        if proposal_type not in routes:

            raise ValueError(

                f"Unknown proposal type: {proposal_type}"

            )


        result = routes[proposal_type]


        return {

            **result,

            "target": proposal.target

        }