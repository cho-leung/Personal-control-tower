import unittest

from control_tower.models import (
    ProjectState,
    Division,
    Role,
    Lineage,
    State,
)

from control_tower.guardrails import (
    assert_transition,
    assert_actor_owns_action,
    GovernanceError,
)


def p(state=State.READY):
    return ProjectState(
        project_id="X",
        title="X",
        division=Division.RESEARCH,
        phase="P0",
        state=state,
        owner="producer_a",
        owner_role=Role.PRODUCER,
        lineage=Lineage.CANONICAL,
    )


class Tests(unittest.TestCase):

    def test_ready_cannot_jump_active(self):

        with self.assertRaises(
            GovernanceError
        ):
            assert_transition(
                State.READY,
                State.ACTIVE,
                Role.PRODUCER,
            )


    def test_only_root_authorizes(self):

        with self.assertRaises(
            GovernanceError
        ):
            assert_transition(
                State.READY,
                State.AUTHORIZED,
                Role.CONTROLLER,
            )

        assert_transition(
            State.READY,
            State.AUTHORIZED,
            Role.ROOT,
        )


    def test_producer_cannot_audit_self(self):

        with self.assertRaises(
            GovernanceError
        ):
            assert_actor_owns_action(
                p(State.AUDIT_PENDING),
                "producer_a",
                Role.AUDITOR,
                "audit",
            )


    def test_wrong_producer_conflict(self):

        with self.assertRaises(
            GovernanceError
        ):
            assert_actor_owns_action(
                p(State.AUTHORIZED),
                "producer_b",
                Role.PRODUCER,
                "produce",
            )


    def test_non_root_cannot_resolve_waiting_root(self):

        with self.assertRaises(
            GovernanceError
        ):
            assert_transition(
                State.WAITING_ROOT,
                State.COMPLETE,
                Role.CONTROLLER,
            )

        assert_transition(
            State.WAITING_ROOT,
            State.COMPLETE,
            Role.ROOT,
        )


    def test_waiting_root_can_require_repair(self):

        assert_transition(
            State.WAITING_ROOT,
            State.REPAIR_REQUIRED,
            Role.ROOT,
        )


if __name__ == "__main__":
    unittest.main()