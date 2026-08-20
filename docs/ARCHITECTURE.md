# Architecture

## Three layers

1. **ChatGPT Projects / UI** — human thinking, review, Root decisions.
2. **Python runtime** — actual message bus, routing, guards, artifact freezing.
3. **Vault** — canonical shared state, readable in Obsidian.

A chat is replaceable. Canonical state should survive the chat.

## Research flow

```text
READY
  | Root only
AUTHORIZED
  |
ACTIVE
  |
PRODUCER_COMPLETE
  |
freeze SHA-256
  |
AUDIT_PENDING
  |
PASS / PASS_WITH_REPAIRS / FAIL
  |
WAITING_ROOT
```

No automatic next phase.

## Business later

Start with internal automation and draft generation.
Keep external sends, pricing commitments, payments, customer promises,
and production-system changes behind explicit Root approval.
