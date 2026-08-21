from pathlib import Path
import yaml


class AgentCreationEngine:


    def __init__(
        self,
        vault
    ):

        self.vault = vault



    def create_agent(
        self,
        proposal
    ):

        payload = proposal.payload


        agent_id = payload["agent_id"]

        division = payload["division"]

        role = payload["role"]

        capabilities = payload.get(
            "capabilities",
            []
        )

        status = payload.get(
            "status",
            "ACTIVE"
        )


        agents_path = (

            self.vault.root
            /
            "00_ROOT"
            /
            "agents.yaml"

        )


        if agents_path.exists():

            data = yaml.safe_load(

                agents_path.read_text(
                    encoding="utf-8"
                )

            )

            if data is None:
                data = []


        else:

            data = []



        # 保持原 agents.yaml list 结构

        for agent in data:

            if agent.get("agent_id") == agent_id:

                return agents_path



        new_agent = {

            "agent_id": agent_id,

            "division": division,

            "role": role,

            "status": status,

            "owns": [],

            "capabilities": capabilities,

            "notes": "Created by AgentCreationEngine."

        }



        data.append(
            new_agent
        )


        agents_path.write_text(

            yaml.safe_dump(
                data,
                sort_keys=False,
                allow_unicode=True
            ),

            encoding="utf-8"

        )


        return agents_path