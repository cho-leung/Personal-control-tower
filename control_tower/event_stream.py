import json


class EventStreamReader:


    def __init__(
        self,
        vault
    ):

        self.path = (
            vault.machine_dir
            /
            "events.jsonl"
        )



    def read_all(self):

        if not self.path.exists():

            return []


        events = []


        with self.path.open(
            encoding="utf-8"
        ) as f:

            for line in f:

                if line.strip():

                    events.append(
                        json.loads(line)
                    )


        return events



    def latest(self):

        events = self.read_all()


        if not events:

            return None


        return events[-1]


    def read_after(self, offset):

        events = self.read_all()

        if offset < 0:
            offset = 0

        return events[offset:]
