from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import json



class EventResult(str, Enum):

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class EventConflictError(RuntimeError):
    pass



@dataclass
class Event:

    event_id: str

    actor: str

    action: str

    target: str

    result: EventResult

    capability_checked: Optional[str] = None

    note: str = ""

    correlation_id: Optional[str] = None

    causation_id: Optional[str] = None

    metadata: Dict[str, Any] = field(
        default_factory=dict
    )

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



    def contains(
        self,
        event_id: str
    ):

        return any(
            event.get("event_id") == event_id
            for event in self.read_all()
        )



    def append_once(
        self,
        event: Event
    ):
        expected = event.to_dict()
        expected.pop("timestamp_utc", None)

        defaults = {
            "capability_checked": None,
            "note": "",
            "correlation_id": None,
            "causation_id": None,
            "metadata": {},
        }

        for existing in self.read_all():
            if existing.get("event_id") != event.event_id:
                continue

            actual = {
                key: existing.get(
                    key,
                    defaults.get(key),
                )
                for key in expected
            }

            if actual != expected:
                differing = sorted(
                    key
                    for key in expected
                    if actual.get(key) != expected.get(key)
                )
                raise EventConflictError(
                    "Event id already exists with conflicting evidence: "
                    f"{event.event_id} ({', '.join(differing)})"
                )

            return False

        self.append(event)

        return True



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
