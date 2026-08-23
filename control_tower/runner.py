import json

from .automaton import PersonalAutomaton
from .event_stream import EventStreamReader


class AutomatonRunner:
    """Single-process, cursor-based event consumer for local v1."""

    def __init__(self, vault):
        self.vault = vault
        self.reader = EventStreamReader(vault)
        self.automaton = PersonalAutomaton(vault)
        self.cursor_path = (
            vault.machine_dir
            / "automaton_cursor.json"
        )

    def _read_cursor(self):
        if not self.cursor_path.exists():
            return 0

        data = json.loads(
            self.cursor_path.read_text(
                encoding="utf-8"
            )
        )
        return int(data.get("next_offset", 0))

    def _write_cursor(self, next_offset, event):
        self.cursor_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        temporary = self.cursor_path.with_suffix(
            ".json.tmp"
        )
        temporary.write_text(
            json.dumps(
                {
                    "next_offset": next_offset,
                    "last_event_id": event.get(
                        "event_id"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.cursor_path)

    def run_once(self):
        offset = self._read_cursor()
        events = self.reader.read_after(offset)

        if not events:
            return None

        event = events[0]
        result = self.automaton.process(event)
        self._write_cursor(offset + 1, event)
        return result

    def run_pending(self, limit=100):
        results = []

        while len(results) < limit:
            result = self.run_once()

            if result is None:
                break

            results.append(result)

        return results
