from .models import State, Role

class GovernanceError(RuntimeError):
    pass

ALLOWED = {
    State.PROPOSED:{State.READY,State.HOLD,State.ARCHIVED},
    State.READY:{State.AUTHORIZED,State.HOLD,State.ARCHIVED},
    State.AUTHORIZED:{State.ACTIVE,State.PAUSED,State.HOLD},
    State.ACTIVE:{State.PRODUCER_COMPLETE,State.WAITING,State.BLOCKED,State.PAUSED,State.HOLD,State.COMPLETE},
    State.PRODUCER_COMPLETE:{State.AUDIT_PENDING,State.WAITING_ROOT},
    State.AUDIT_PENDING:{State.PASS,State.PASS_WITH_REPAIRS,State.FAIL,State.BLOCKED},
    State.PASS:{State.WAITING_ROOT,State.COMPLETE},
    State.PASS_WITH_REPAIRS:{State.REPAIR_REQUIRED,State.WAITING_ROOT},
    State.FAIL:{State.WAITING_ROOT,State.COMPLETE,State.HOLD},
    State.REPAIR_REQUIRED:{State.AUTHORIZED,State.HOLD},
    State.WAITING_ROOT:{State.AUTHORIZED,State.COMPLETE,State.HOLD},
    State.WAITING:{State.AUTHORIZED,State.ACTIVE,State.HOLD,State.ARCHIVED},
    State.BLOCKED:{State.WAITING,State.AUTHORIZED,State.HOLD,State.ARCHIVED},
    State.PAUSED:{State.AUTHORIZED,State.HOLD,State.ARCHIVED},
    State.HOLD:{State.AUTHORIZED,State.ARCHIVED},
    State.COMPLETE:{State.ARCHIVED,State.AUTHORIZED},
    State.ARCHIVED:set(),
}

def assert_transition(current,new,actor_role):
    if new not in ALLOWED.get(current,set()):
        raise GovernanceError(f"Illegal transition: {current.value} -> {new.value}")
    if new == State.AUTHORIZED and actor_role != Role.ROOT:
        raise GovernanceError("Only ROOT may create AUTHORIZED state.")

def assert_actor_owns_action(project, actor_name, actor_role, action):
    if action=="produce":
        if actor_role != Role.PRODUCER:
            raise GovernanceError("Only PRODUCER may produce.")
        if actor_name != project.owner:
            raise GovernanceError(f"Owner conflict: {actor_name} != {project.owner}")
    if action=="audit":
        if actor_role != Role.AUDITOR:
            raise GovernanceError("Only AUDITOR may independently audit.")
        if actor_name == project.owner:
            raise GovernanceError("PRODUCER / AUDITOR INDEPENDENCE CONFLICT")

def assert_auditable(project):
    if project.state != State.AUDIT_PENDING:
        raise GovernanceError("Artifact is not AUDIT_PENDING.")
    if not project.artifact_path or not project.artifact_sha256:
        raise GovernanceError("No frozen artifact + SHA-256.")
