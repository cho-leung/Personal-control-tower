from pathlib import Path
import re


class RegistryLoader:
    """
    Load Personal Control Tower markdown registries.

    Legacy Markdown registry compatibility for local reconciliation.
    """

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root
        self.root_dir = vault_root / "00_ROOT"


    def _read(self, filename: str) -> str:
        path = self.root_dir / filename

        if not path.exists():
            raise FileNotFoundError(
                f"Registry file missing: {path}"
            )

        return path.read_text(encoding="utf-8")


    def load_agents(self):
        """
        Parse AGENT_REGISTRY.md

        Returns:
        [
            {
                "agent": "...",
                "division": "...",
                "role": "...",
                "status": "..."
            }
        ]
        """

        text = self._read("AGENT_REGISTRY.md")

        agents = []

        for line in text.splitlines():

            if not line.startswith("|"):
                continue

            if "Agent" in line:
                continue

            if "---" in line:
                continue

            parts = [
                x.strip()
                for x in line.split("|")
                if x.strip()
            ]

            if len(parts) != 4:
                continue

            agents.append(
                {
                    "agent": parts[0],
                    "division": parts[1],
                    "role": parts[2],
                    "status": parts[3],
                }
            )

        return agents


    def load_projects(self):
        """
        Parse PROJECT_REGISTRY.md
        """

        text = self._read("PROJECT_REGISTRY.md")

        projects = []

        for line in text.splitlines():

            if not line.startswith("|"):
                continue

            if "Project" in line:
                continue

            if "---" in line:
                continue


            parts = [
                x.strip()
                for x in line.split("|")
                if x.strip()
            ]


            if len(parts) != 5:
                continue


            projects.append(
                {
                    "project": parts[0],
                    "division": parts[1],
                    "owner": parts[2],
                    "status": parts[3],
                    "next_gate": parts[4],
                }
            )

        return projects


    def summary(self):

        return {
            "agents": self.load_agents(),
            "projects": self.load_projects(),
        }
