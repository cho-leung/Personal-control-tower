from .models import State, Role


class GovernanceError(RuntimeError):
    pass


ALLOWED = {
    State.PROPOSED: {
        State.READY,
        State.HOLD,
        State.ARCHIVED,
    },

    State.READY: {
        State.AUTHORIZED,
        State.HOLD,
        State.ARCHIVED,
    },

    State.AUTHORIZED: {
        State.ACTIVE,
        State.PAUSED,
        State.HOLD,
    },

    State.ACTIVE: {
        State.PRODUCER_COMPLETE,
        State.WAITING,
        State.BLOCKED,
        State.PAUSED,
        State.HOLD,
        State.COMPLETE,
    },

    State.PRODUCER_COMPLETE: {
        State.AUDIT_PENDING,
        State.WAITING_ROOT,
    },

    State.AUDIT_PENDING: {
        State.PASS,
        State.PASS_WITH_REPAIRS,
        State.FAIL,
        State.BLOCKED,
    },

    State.PASS: {
        State.WAITING_ROOT,
        State.COMPLETE,
    },

    State.PASS_WITH_REPAIRS: {
        State.REPAIR_REQUIRED,
        State.WAITING_ROOT,
    },

    State.FAIL: {
        State.WAITING_ROOT,
        State.COMPLETE,
        State.HOLD,
    },

    State.REPAIR_REQUIRED: {
        State.AUTHORIZED,
        State.HOLD,
    },

    State.WAITING_ROOT: {
        State.AUTHORIZED,
        State.WAITING,
        State.REPAIR_REQUIRED,
        State.COMPLETE,
        State.HOLD,
    },

    State.WAITING: {
        State.AUTHORIZED,
        State.ACTIVE,
        State.HOLD,
        State.ARCHIVED,
    },

    State.BLOCKED: {
        State.WAITING,
        State.AUTHORIZED,
        State.HOLD,
        State.ARCHIVED,
    },

    State.PAUSED: {
        State.AUTHORIZED,
        State.HOLD,
        State.ARCHIVED,
    },

    State.HOLD: {
        State.AUTHORIZED,
        State.ARCHIVED,
    },

    State.COMPLETE: {
        State.ARCHIVED,
        State.AUTHORIZED,
    },

    State.ARCHIVED: set(),
}


def assert_transition(
    current,
    new,
    actor_role,
):
    if new not in ALLOWED.get(current, set()):
        raise GovernanceError(
            f"Illegal transition: "
            f"{current.value} -> {new.value}"
        )

    if (
        new == State.AUTHORIZED
        and actor_role != Role.ROOT
    ):
        raise GovernanceError(
            "Only ROOT may create AUTHORIZED state."
        )

    if (
        current == State.WAITING_ROOT
        and actor_role != Role.ROOT
    ):
        raise GovernanceError(
            "Only ROOT may resolve WAITING_ROOT."
        )


def assert_actor_owns_action(
    project,
    actor_name,
    actor_role,
    action,
):
    if action == "produce":

        if actor_role != Role.PRODUCER:
            raise GovernanceError(
                "Only PRODUCER may produce."
            )

        if actor_name != project.owner:
            raise GovernanceError(
                f"Owner conflict: "
                f"{actor_name} != {project.owner}"
            )

    if action == "audit":

        if actor_role != Role.AUDITOR:
            raise GovernanceError(
                "Only AUDITOR may independently audit."
            )

        if actor_name == project.owner:
            raise GovernanceError(
                "PRODUCER / AUDITOR INDEPENDENCE CONFLICT"
            )


def assert_valid_auditor(
    project,
    agent,
):
    """Validate the registered auditor and producer/auditor separation."""

    if not agent:
        raise GovernanceError(
            "Assigned auditor is not registered."
        )

    agent_id = agent.agent_id
    status = getattr(
        agent.status,
        "value",
        agent.status,
    )
    role_value = getattr(
        agent.role,
        "value",
        agent.role,
    )

    if status != "ACTIVE":
        raise GovernanceError(
            f"Inactive auditor: {agent_id}"
        )

    if "audit" not in agent.capabilities:
        raise GovernanceError(
            f"Missing capability: audit ({agent_id})"
        )

    try:
        actor_role = Role(role_value)
    except ValueError as exc:
        raise GovernanceError(
            f"Unknown auditor role: {role_value}"
        ) from exc

    assert_actor_owns_action(
        project,
        agent_id,
        actor_role,
        "audit",
    )

    if not project.auditor:
        raise GovernanceError(
            "No auditor assigned to the artifact."
        )

    if agent_id != project.auditor:
        raise GovernanceError(
            "Only the assigned auditor may audit: "
            f"{agent_id} != {project.auditor}"
        )

    bound_auditors = []

    for role, members in (project.agents or {}).items():
        role_value = getattr(role, "value", role)

        if str(role_value).upper() != Role.AUDITOR.value:
            continue

        if isinstance(members, str):
            bound_auditors.append(members)
        else:
            bound_auditors.extend(members or [])

    if not bound_auditors:
        raise GovernanceError(
            "Project has no AUDITOR binding."
        )

    if agent_id not in bound_auditors:
        raise GovernanceError(
            f"Auditor is not bound to project: {agent_id}"
        )


def assert_frozen_artifact(project):

    if (
        not project.artifact_path
        or not project.artifact_sha256
    ):
        raise GovernanceError(
            "No frozen artifact + SHA-256."
        )


def assert_auditable(project):

    if project.state != State.AUDIT_PENDING:
        raise GovernanceError(
            "Artifact is not AUDIT_PENDING."
        )

    assert_frozen_artifact(project)
