from dataclasses import replace



class BindingEngine:


    def __init__(
        self,
        vault
    ):

        self.vault = vault



    def bind(
        self,
        proposal
    ):


        project_id = proposal.payload["project_id"]

        agent_id = proposal.payload["agent_id"]

        role = proposal.payload["role"]


        state_path = (

            self.vault.root

            /

            "01_RESEARCH"

            /

            project_id

            /

            "STATE.md"

        )


        state = self.vault.read_state(
            state_path
        )


        agents = state.agents or {}


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


        return state_path