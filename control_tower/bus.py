from dataclasses import replace
from .models import ProjectState,Division,Role,Lineage,State,AuditVerdict
from .guardrails import assert_transition,assert_actor_owns_action,assert_auditable,GovernanceError

class ControlTowerBus:
    def __init__(self,vault):
        self.vault=vault
        vault.ensure_structure()

    def create_research_project(self,project_id,title,owner,phase):
        d=self.vault.root/"01_RESEARCH"/project_id
        for sub in ["handoffs","claims","audits","artifacts","failed_routes"]:
            (d/sub).mkdir(parents=True,exist_ok=True)
        s=ProjectState(project_id,title,Division.RESEARCH,phase,State.READY,owner,Role.PRODUCER,
                       Lineage.CANONICAL,next_gate="ROOT_AUTHORIZATION",
                       notes="READY only. No execution authorization.")
        p=d/"STATE.md"; self.vault.write_state(p,s)
        self.vault.append_event({"type":"PROJECT_CREATED","project_id":project_id})
        return s,p

    def root_authorize(self,state_path,authorization_id,scope):
        s=self.vault.read_state(state_path)
        assert_transition(s.state,State.AUTHORIZED,Role.ROOT)
        s=replace(s,state=State.AUTHORIZED,authorization_id=authorization_id,
                  next_gate="PRODUCER_EXECUTION",notes=f"Root-authorized scope: {scope}")
        self.vault.write_state(state_path,s)
        self.vault.append_decision(
            f"## {authorization_id}\n- Project: `{s.project_id}`\n- Phase: `{s.phase}`\n"
            f"- Decision: **AUTHORIZED**\n- Scope: {scope}\n"
        )
        return s

    def start_execution(self,state_path,producer_name):
        s=self.vault.read_state(state_path)
        assert_actor_owns_action(s,producer_name,Role.PRODUCER,"produce")
        assert_transition(s.state,State.ACTIVE,Role.PRODUCER)
        if not s.authorization_id: raise GovernanceError("No Root authorization id.")
        s=replace(s,state=State.ACTIVE,next_gate="PRODUCER_COMPLETE")
        self.vault.write_state(state_path,s); return s

    def producer_complete(self,state_path,producer_name,artifact_text,auditor_name):
        s=self.vault.read_state(state_path)
        assert_actor_owns_action(s,producer_name,Role.PRODUCER,"produce")
        if s.state != State.ACTIVE: raise GovernanceError("Completion requires ACTIVE.")
        d=state_path.parent
        artifact=d/"artifacts"/f"{s.phase}_producer_artifact.txt"
        artifact.write_text(artifact_text,encoding="utf-8")
        sha=self.vault.freeze_artifact(artifact)
        assert_transition(State.ACTIVE,State.PRODUCER_COMPLETE,Role.PRODUCER)
        s=replace(s,state=State.PRODUCER_COMPLETE,
                  artifact_path=str(artifact.relative_to(self.vault.root)),
                  artifact_sha256=sha,auditor=auditor_name,
                  next_gate="CREATE_INDEPENDENT_AUDIT_HANDOFF",
                  notes="Producer complete. Artifact frozen. PRODUCED != AUDITED.")
        self.vault.write_state(state_path,s)
        assert_transition(State.PRODUCER_COMPLETE,State.AUDIT_PENDING,Role.CONTROLLER)
        s=replace(s,state=State.AUDIT_PENDING,next_gate="INDEPENDENT_AUDIT")
        self.vault.write_state(state_path,s)
        self.vault.write_handoff(
            d/"handoffs"/f"{s.phase}_producer_to_auditor.md",
            {
                "handoff_id":f"{s.project_id}-{s.phase}-AUDIT","from":producer_name,
                "to":auditor_name,"project":s.project_id,"phase":s.phase,
                "state":s.state.value,"lineage":s.lineage.value,
                "artifact_path":s.artifact_path,"artifact_sha256":s.artifact_sha256,
                "authorization_id":s.authorization_id
            },
            """# Producer → Independent Auditor

## Receiver MAY
- inspect exactly the frozen artifact;
- attack the claim;
- return PASS / PASS_WITH_REPAIRS / FAIL.

## Receiver MAY NOT
- modify the producer artifact;
- silently authorize the next phase;
- merge unrelated experimental material.
"""
        )
        return s

    def record_audit(self,state_path,auditor_name,verdict:AuditVerdict,audit_text):
        s=self.vault.read_state(state_path)
        assert_actor_owns_action(s,auditor_name,Role.AUDITOR,"audit")
        assert_auditable(s)
        d=state_path.parent
        (d/"audits"/f"{s.phase}_audit.md").write_text(
            f"# Independent Audit\n\n- Auditor: `{auditor_name}`\n"
            f"- Artifact SHA-256: `{s.artifact_sha256}`\n"
            f"- Verdict: **{verdict.value}**\n\n## Audit notes\n\n{audit_text}\n",
            encoding="utf-8"
        )
        vs=State(verdict.value)
        assert_transition(State.AUDIT_PENDING,vs,Role.AUDITOR)
        s=replace(s,state=vs,latest_audit_verdict=verdict.value,next_gate="ROOT_REVIEW",
                  notes=f"Audit returned {verdict.value}. No next phase auto-authorized.")
        self.vault.write_state(state_path,s)
        assert_transition(vs,State.WAITING_ROOT,Role.CONTROLLER)
        s=replace(s,state=State.WAITING_ROOT,next_gate="ROOT_DECISION")
        self.vault.write_state(state_path,s)
        self.vault.write_root_inbox(
            f"{s.project_id}_{s.phase}_GATE.md",
            f"""---
project_id: {s.project_id}
phase: {s.phase}
state: {s.state.value}
audit_verdict: {verdict.value}
artifact_sha256: {s.artifact_sha256}
---

# Root Gate Decision Required

Audit verdict: **{verdict.value}**

No next phase has been authorized automatically.

## Root options
- AUTHORIZE a specifically scoped next action
- MODIFY
- REPAIR
- HOLD
- KILL / CLOSE
"""
        )
        return s
