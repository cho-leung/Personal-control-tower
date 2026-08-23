from .proposal_factory import ProposalFactory
from .proposals import write_proposal



class ActionExecutor:


    def __init__(
        self,
        vault
    ):

        self.vault = vault

        self.factory = ProposalFactory(
            vault
        )



    def execute(
        self,
        action
    ):


        proposal = self.factory.create(
            action
        )


        if proposal is None:

            return None



        path = write_proposal(

            self.vault.root,

            proposal

        )


        return path