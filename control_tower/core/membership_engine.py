from dataclasses import replace


class MembershipEngine:


    def __init__(
        self,
        vault
    ):

        self.vault = vault



    def bind_agent(
        self,
        state_path,
        agent_id,
        role
    ):

        state = self.vault.read_state(
            state_path
        )


        agents = getattr(
            state,
            "agents",
            None
        )


        if agents is None:

            agents = {}



        if role not in agents:

            agents[role] = []



        if agent_id not in agents[role]:

            agents[role].append(
                agent_id
            )



        state = replace(
            state,
            agents=agents
        )


        self.vault.write_state(
            state_path,
            state
        )


        return state