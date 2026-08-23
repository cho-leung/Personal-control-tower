from .events import EventLedger


class EventRouter:


    def __init__(
        self,
        vault
    ):

        self.vault = vault


    def route(
        self,
        event
    ):

        action = event["action"]


        if action == "CREATE_BINDING":

            return {
                "action": "INFO",
                "message": "Agent binding completed."
            }


        if action == "PRODUCE_ARTIFACT":

            return {
                "action":
                "CREATE_AUDIT_REQUEST",

                "target":
                event["target"],

                "payload":
                {
                    "reason":
                    "Artifact requires audit.",

                    "created_event":
                    event.get("event_id")
                }
            }


        if action == "AUDIT":

            return {
                "action": "UPDATE_STATE",
                "message": "Audit completed."
            }


        return {
            "action": "NO_ACTION"
        }
