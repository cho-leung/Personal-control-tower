from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import json



class EventResult(str, Enum):

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"



@dataclass
class Event:

    event_id: str

    actor: str

    action: str

    target: str

    result: EventResult

    capability_checked: Optional[str] = None

    note: str = ""

    timestamp_utc: str = ""


    def __post_init__(self):

        if not self.timestamp_utc:

            self.timestamp_utc = (
                datetime.now(
                    timezone.utc
                )
                .isoformat()
            )


    def to_dict(self):

        d = asdict(self)

        if isinstance(
            self.result,
            Enum
        ):

            d["result"] = (
                self.result.value
            )

        return d



class EventLedger:


    def __init__(
        self,
        vault
    ):

        self.path = (
            vault.machine_dir
            /
            "events.jsonl"
        )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )



    def append(
        self,
        event: Event
    ):


        with self.path.open(
            "a",
            encoding="utf-8"
        ) as f:

            f.write(
                json.dumps(
                    event.to_dict(),
                    ensure_ascii=False
                )
                +
                "\n"
            )



    def read_all(self):

        if not self.path.exists():

            return []


        events = []


        with self.path.open(
            encoding="utf-8"
        ) as f:


            for line in f:

                events.append(
                    json.loads(line)
                )


        return events