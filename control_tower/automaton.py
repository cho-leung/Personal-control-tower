from .event_router import EventRouter
from .action_executor import ActionExecutor



class PersonalAutomaton:


    def __init__(
        self,
        vault
    ):

        self.router = EventRouter(
            vault
        )

        self.executor = ActionExecutor(
            vault
        )



    def process(
        self,
        event
    ):


        action = self.router.route(
            event
        )


        result = self.executor.execute(
            action
        )


        return {

            "event":
            event,

            "action":
            action,

            "result":
            result

        }