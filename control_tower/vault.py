from pathlib import Path

from datetime import datetime, timezone

import hashlib
import json
import yaml

from .models import ProjectState



class Vault:


    def __init__(
        self,
        root: Path
    ):

        self.root = root



    @property
    def machine_dir(self):

        return self.root / ".control_tower"



    def ensure_structure(self):

        for d in [

            self.root / "00_ROOT" / "inbox",

            self.root / "00_ROOT" / "archive",

            self.root / "01_RESEARCH",

            self.root / "02_BUSINESS",

            self.root / "03_PERSONAL_GROWTH",

            self.machine_dir

        ]:

            d.mkdir(
                parents=True,
                exist_ok=True
            )


        defaults = {

            self.root / "00_ROOT" / "AGENT_REGISTRY.md":
                "# Agent Registry\n",


            self.root / "00_ROOT" / "ACTIVE_BOARD.md":
                "# Active Board\n",


            self.root / "00_ROOT" / "DECISION_LOG.md":
                "# Decision Log\n",


            self.root / "00_ROOT" / "agents.yaml":

"""
- agent_id: personal_root

  division: ROOT

  role: ROOT

  status: ACTIVE

  owns:
    - ALL

  capabilities:
    - approve
    - reject
    - authorize

  notes: Personal Control Tower Root
"""

        }


        for path, content in defaults.items():

            if not path.exists():

                path.write_text(
                    content,
                    encoding="utf-8"
                )





    def write_state(
        self,
        path,
        state: ProjectState
    ):

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )


        meta = yaml.safe_dump(
            state.to_dict(),
            sort_keys=False,
            allow_unicode=True
        )


        body = f"""
# {state.title}


## Current State


- **Project:** `{state.project_id}`

- **Phase:** `{state.phase}`

- **State:** `{state.state.value}`

- **Owner:** `{state.owner}` ({state.owner_role.value})

- **Lineage:** `{state.lineage.value}`

- **Authorization:** `{state.authorization_id or 'NONE'}`

- **Artifact SHA-256:** `{state.artifact_sha256 or 'NONE'}`

- **Auditor:** `{state.auditor or 'NONE'}`

- **Audit verdict:** `{state.latest_audit_verdict or 'NONE'}`

- **Next gate:** `{state.next_gate or 'NONE'}`


## Notes


{state.notes or 'None.'}

"""


        path.write_text(
            "---\n"
            +
            meta
            +
            "---\n"
            +
            body,
            encoding="utf-8"
        )





    def read_state(
        self,
        path
    ):

        text = path.read_text(
            encoding="utf-8"
        )


        parts = text.split(
            "---",
            2
        )


        if len(parts) < 3:

            raise ValueError(
                f"Invalid STATE format: {path}"
            )


        data = yaml.safe_load(
            parts[1]
        )


        if data is None:

            raise ValueError(
                f"Empty STATE metadata: {path}"
            )


        # backward compatibility
        if "agents" not in data:

            data["agents"] = {}


        return ProjectState.from_dict(
            data
        )





    def append_event(
        self,
        event
    ):

        self.machine_dir.mkdir(
            parents=True,
            exist_ok=True
        )


        event = dict(event)


        event.setdefault(
            "timestamp_utc",
            datetime.now(
                timezone.utc
            ).isoformat()
        )


        with (
            self.machine_dir / "events.jsonl"
        ).open(
            "a",
            encoding="utf-8"
        ) as f:


            f.write(
                json.dumps(
                    event,
                    ensure_ascii=False
                )
                +
                "\n"
            )





    def append_decision(
        self,
        text
    ):

        with (
            self.root
            /
            "00_ROOT"
            /
            "DECISION_LOG.md"
        ).open(
            "a",
            encoding="utf-8"
        ) as f:


            f.write(
                "\n"
                +
                text.rstrip()
                +
                "\n"
            )





    def write_root_inbox(
        self,
        name,
        content
    ):

        p = (
            self.root
            /
            "00_ROOT"
            /
            "inbox"
            /
            name
        )


        p.write_text(
            content,
            encoding="utf-8"
        )


        return p





    def archive_root_item(
        self,
        path: Path
    ):

        archive = (
            self.root
            /
            "00_ROOT"
            /
            "archive"
        )


        archive.mkdir(
            parents=True,
            exist_ok=True
        )


        target = archive / path.name


        path.replace(
            target
        )


        return target





    @staticmethod
    def freeze_artifact(
        path
    ):

        return hashlib.sha256(
            path.read_bytes()
        ).hexdigest()





    def write_handoff(
        self,
        path,
        metadata,
        body
    ):

        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )


        meta = yaml.safe_dump(
            metadata,
            sort_keys=False,
            allow_unicode=True
        )


        path.write_text(
            "---\n"
            +
            meta
            +
            "---\n"
            +
            body.strip()
            +
            "\n",
            encoding="utf-8"
        )