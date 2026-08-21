from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class ProjectManifest:
    """
    External project creation contract.
    """

    project_id: str

    title: str

    division: str

    owner: str

    phase: str = "T0"

    lineage: str = "CANONICAL"


    def to_dict(self) -> Dict:

        return asdict(self)



@dataclass
class AgentManifest:
    """
    External agent creation contract.
    """

    agent_id: str

    division: str

    role: str

    capabilities: list

    status: str = "ACTIVE"


    def to_dict(self) -> Dict:

        return asdict(self)