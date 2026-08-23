from .proposals import create_proposal


class ProposalFactory:


    def __init__(
        self,
        vault
    ):

        self.vault = vault



    def create(
        self,
        action
    ):

        action_type = action.get(
            "action"
        )


        if action_type == "CREATE_AUDIT_REQUEST":

            return self.create_audit_request(
                action
            )


        return None



    def create_audit_request(
        self,
        action
    ):

        target = action["target"]


        payload = dict(
            action.get(
                "payload",
                {}
            )
        )


        state_path = self.vault.find_state_path(
            target
        )


        state = self.vault.read_state(
            state_path
        )


        payload.setdefault(
            "phase",
            state.phase
        )

        payload.setdefault(
            "artifact_path",
            state.artifact_path
        )

        payload.setdefault(
            "artifact_sha256",
            state.artifact_sha256
        )

        payload.setdefault(
            "auditor",
            state.auditor
        )


        proposal = create_proposal(

            proposal_type="CREATE_AUDIT_REQUEST",

            target=target,

            reason=payload.get(
                "reason",
                "Automaton generated audit request."
            ),

            created_by="personal_automaton",

            payload=payload

        )


        if state.artifact_sha256:

            proposal.proposal_id = (
                "CREATE_AUDIT_REQUEST-"
                f"{state.phase}-"
                f"{state.artifact_sha256[:12]}"
            )


        return proposal
