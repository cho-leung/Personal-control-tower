import unittest
from control_tower.models import ProjectState,Division,Role,Lineage,State
from control_tower.guardrails import assert_transition,assert_actor_owns_action,GovernanceError

def p(state=State.READY):
    return ProjectState("X","X",Division.RESEARCH,"P0",state,"producer_a",Role.PRODUCER,Lineage.CANONICAL)

class Tests(unittest.TestCase):
    def test_ready_cannot_jump_active(self):
        with self.assertRaises(GovernanceError):
            assert_transition(State.READY,State.ACTIVE,Role.PRODUCER)

    def test_only_root_authorizes(self):
        with self.assertRaises(GovernanceError):
            assert_transition(State.READY,State.AUTHORIZED,Role.CONTROLLER)
        assert_transition(State.READY,State.AUTHORIZED,Role.ROOT)

    def test_producer_cannot_audit_self(self):
        with self.assertRaises(GovernanceError):
            assert_actor_owns_action(p(State.AUDIT_PENDING),"producer_a",Role.AUDITOR,"audit")

    def test_wrong_producer_conflict(self):
        with self.assertRaises(GovernanceError):
            assert_actor_owns_action(p(State.AUTHORIZED),"producer_b",Role.PRODUCER,"produce")

if __name__=="__main__": unittest.main()
