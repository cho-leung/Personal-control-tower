from dataclasses import replace

from ..models import (
    Role,
    State,
)

from ..guardrails import (
    assert_transition,
)



class DecisionEngine:


    def __init__(
        self,
        vault,
        agent_registry,
        event_ledger
    ):

        self.vault = vault
        self.agent_registry = agent_registry
        self.event_ledger = event_ledger



    def authorize(
        self,
        state_path,
        authorization_id,
        scope
    ):


        agent = self.agent_registry.get(
            "personal_root"
        )


        if not agent:

            raise Exception(
                "Root agent missing."
            )


        if "authorize" not in agent.capabilities:

            raise Exception(
                "Root cannot authorize."
            )


        state = self.vault.read_state(
            state_path
        )


        assert_transition(
            state.state,
            State.AUTHORIZED,
            Role.ROOT
        )


        state = replace(
            state,
            state=State.AUTHORIZED,
            authorization_id=authorization_id,
            next_gate="PRODUCER_EXECUTION",
            notes=f"Root-authorized scope: {scope}"
        )


        self.vault.write_state(
            state_path,
            state
        )


        self.event_ledger.append(

            __import__(
                "control_tower.events",
                fromlist=["Event"]
            ).Event(

                event_id=
                    f"EVT-{state.project_id}-AUTHORIZE",

                actor="personal_root",

                action="AUTHORIZE",

                target=state.project_id,

                result=
                    __import__(
                        "control_tower.events",
                        fromlist=["EventResult"]
                    ).EventResult.SUCCESS,

                capability_checked="authorize"
            )

        )


        self.vault.append_decision(
            f"""
## {authorization_id}

- Project:
`{state.project_id}`

- Decision:
AUTHORIZED

- Scope:
{scope}

"""
        )


        return state