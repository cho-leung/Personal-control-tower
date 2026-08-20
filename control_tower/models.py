from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

class Division(str, Enum):
    ROOT="ROOT"; RESEARCH="RESEARCH"; BUSINESS="BUSINESS"; PERSONAL_GROWTH="PERSONAL_GROWTH"

class Role(str, Enum):
    ROOT="ROOT"; CONTROLLER="CONTROLLER"; PRODUCER="PRODUCER"; AUDITOR="AUDITOR"
    VALIDATOR="VALIDATOR"; BUILDER="BUILDER"; SPECIALIST="SPECIALIST"

class Lineage(str, Enum):
    CANONICAL="CANONICAL"; EXPERIMENTAL="EXPERIMENTAL_NONCANONICAL"
    HISTORICAL="HISTORICAL"; UNKNOWN="UNKNOWN"

class State(str, Enum):
    PROPOSED="PROPOSED"; READY="READY"; AUTHORIZED="AUTHORIZED"; ACTIVE="ACTIVE"
    PRODUCER_COMPLETE="PRODUCER_COMPLETE"; AUDIT_PENDING="AUDIT_PENDING"
    PASS="PASS"; PASS_WITH_REPAIRS="PASS_WITH_REPAIRS"; FAIL="FAIL"
    REPAIR_REQUIRED="REPAIR_REQUIRED"; WAITING_ROOT="WAITING_ROOT"; WAITING="WAITING"
    BLOCKED="BLOCKED"; COMPLETE="COMPLETE"; PAUSED="PAUSED"; HOLD="HOLD"; ARCHIVED="ARCHIVED"

class AuditVerdict(str, Enum):
    PASS="PASS"; PASS_WITH_REPAIRS="PASS_WITH_REPAIRS"; FAIL="FAIL"

class ProposalState(str, Enum):
    CREATED="CREATED"
    WAITING_ROOT="WAITING_ROOT"
    APPROVED="APPROVED"
    REJECTED="REJECTED"
    EXECUTED="EXECUTED"

@dataclass
class ProjectState:
    project_id: str
    title: str
    division: Division
    phase: str
    state: State
    owner: str
    owner_role: Role
    lineage: Lineage = Lineage.CANONICAL
    authorization_id: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_sha256: Optional[str] = None
    auditor: Optional[str] = None
    latest_audit_verdict: Optional[str] = None
    next_gate: Optional[str] = None
    notes: str = ""

    def to_dict(self):
        d = asdict(self)
        for k,v in list(d.items()):
            if isinstance(v, Enum):
                d[k]=v.value
        return d

    @classmethod
    def from_dict(cls,d):
        return cls(
            project_id=d["project_id"], title=d["title"], division=Division(d["division"]),
            phase=d["phase"], state=State(d["state"]), owner=d["owner"],
            owner_role=Role(d["owner_role"]), lineage=Lineage(d.get("lineage","CANONICAL")),
            authorization_id=d.get("authorization_id"), artifact_path=d.get("artifact_path"),
            artifact_sha256=d.get("artifact_sha256"), auditor=d.get("auditor"),
            latest_audit_verdict=d.get("latest_audit_verdict"), next_gate=d.get("next_gate"),
            notes=d.get("notes","")
        )

@dataclass
class Proposal:

    proposal_id: str
    proposal_type: str
    target: str
    reason: str
    state: ProposalState = ProposalState.CREATED

    created_by: str = "SYSTEM"
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None


    def to_dict(self):
        d = asdict(self)

        for k,v in list(d.items()):
            if isinstance(v, Enum):
                d[k]=v.value

        return d


    @classmethod
    def from_dict(cls,d):

        return cls(
            proposal_id=d["proposal_id"],
            proposal_type=d["proposal_type"],
            target=d["target"],
            reason=d["reason"],
            state=ProposalState(d.get(
                "state",
                "CREATED"
            )),
            created_by=d.get(
                "created_by",
                "SYSTEM"
            ),
            decided_by=d.get(
                "decided_by"
            ),
            decision_note=d.get(
                "decision_note"
            )
        )