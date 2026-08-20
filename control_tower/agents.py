from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import List, Optional
import yaml


class AgentStatus(str, Enum):

    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"



class AgentRole(str, Enum):

    ROOT = "ROOT"
    CONTROLLER = "CONTROLLER"
    PRODUCER = "PRODUCER"
    AUDITOR = "AUDITOR"
    VALIDATOR = "VALIDATOR"
    BUILDER = "BUILDER"
    SPECIALIST = "SPECIALIST"



@dataclass
class AgentState:

    agent_id: str

    division: str

    role: AgentRole

    status: AgentStatus

    owns: List[str]

    capabilities: List[str]

    notes: str = ""


    def to_dict(self):

        d = asdict(self)

        for k,v in list(d.items()):

            if isinstance(v, Enum):
                d[k] = v.value

        return d



    @classmethod
    def from_dict(cls,d):

        return cls(

            agent_id=d["agent_id"],

            division=d["division"],

            role=AgentRole(
                d["role"]
            ),

            status=AgentStatus(
                d["status"]
            ),

            owns=d.get(
                "owns",
                []
            ),

            capabilities=d.get(
                "capabilities",
                []
            ),

            notes=d.get(
                "notes",
                ""
            )
        )



class AgentRegistry:


    def __init__(
        self,
        root: Path
    ):

        self.root = root


    @property
    def path(self):

        return (
            self.root
            /
            "00_ROOT"
            /
            "agents.yaml"
        )



    def save(
        self,
        agents: List[AgentState]
    ):

        data = [

            a.to_dict()

            for a in agents

        ]

        self.path.write_text(

            yaml.safe_dump(
                data,
                sort_keys=False,
                allow_unicode=True
            ),

            encoding="utf-8"

        )



    def load(self):

        if not self.path.exists():

            return []


        data = yaml.safe_load(

            self.path.read_text(
                encoding="utf-8"
            )

        )


        return [

            AgentState.from_dict(x)

            for x in data

        ]



    def get(
        self,
        agent_id: str
    ):

        for agent in self.load():

            if agent.agent_id == agent_id:

                return agent


        return None



    def can_execute(
        self,
        agent_id: str,
        capability: str
    ):

        agent = self.get(
            agent_id
        )

        if not agent:

            return False


        return capability in agent.capabilities