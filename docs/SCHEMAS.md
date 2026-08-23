# Schemas and on-disk evidence

The vault uses UTF-8 text. Canonical Markdown records begin with YAML frontmatter delimited by `---`; the Markdown body is a human-readable rendering of the same record. Code reads the frontmatter, not prose.

Identifiers used as filenames must be a single safe path component. Paths stored inside the vault are normally relative to the vault root.

## Project state

Path:

```text
<division>/<project_id>/STATE.md
```

Required fields:

```yaml
project_id: EXAMPLE
title: Example Project
division: RESEARCH
phase: T0
state: READY
owner: producer_a
owner_role: PRODUCER
```

Governance and evidence fields:

```yaml
agents:
  PRODUCER:
    - producer_a
  AUDITOR:
    - auditor_a
lineage: CANONICAL
authorization_id: null
artifact_path: null
artifact_sha256: null
auditor: null
latest_audit_verdict: null
next_gate: ROOT_AUTHORIZATION
notes: Waiting for Root authorization.
last_decision_id: null
last_decision_action: null
last_decision_evidence: {}
```

Older state files without `agents` are read as `agents: {}`. An artifact path and SHA-256 are a pair; audit admission verifies both against the current file.

The `last_decision_*` fields are recovery evidence. They let an exact Root-decision retry complete a missing event, decision-log entry, or gate archive if the local process stopped after writing `STATE.md`. They do not grant new authority.

## Proposal

Pending path:

```text
00_ROOT/inbox/<proposal_id>_<target>.md
```

After decision, the same proposal moves to `00_ROOT/archive/`, optionally with a collision-safe timestamp prefix.

```yaml
proposal_id: CREATE_AUDIT_REQUEST-20260823130829
proposal_type: CREATE_AUDIT_REQUEST
target: EXAMPLE
reason: Frozen artifact requires independent audit.
state: WAITING_ROOT
created_by: personal_automaton
decided_by: null
decision_note: null
payload:
  phase: T0
  artifact_path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
  artifact_sha256: <sha256>
  auditor: auditor_a
```

New proposals start in `WAITING_ROOT`. Decided states are `EXECUTED` and `REJECTED`; `CREATED` is accepted for legacy records.

Agent lifecycle proposal payloads are:

```yaml
# ARCHIVE_AGENT
agent_id: agent_a

# UPDATE_AGENT_ROLE
agent_id: agent_a
role: AUDITOR
# new_role is also accepted by the engine.
# expected_role may be supplied by API callers as a stale-write guard.

# UPDATE_AGENT_CAPABILITY
agent_id: agent_a
operation: ADD           # ADD or REMOVE
capability: audit
```

For every agent lifecycle proposal, `target` must equal `payload.agent_id`.

## Agent registry

Path:

```text
00_ROOT/agents.yaml
```

It is a YAML list:

```yaml
- agent_id: auditor_a
  division: RESEARCH
  role: AUDITOR
  status: ACTIVE
  owns:
    - EXAMPLE
  capabilities:
    - audit
  notes: Independent auditor.
```

Roles are `ROOT`, `CONTROLLER`, `PRODUCER`, `AUDITOR`, `VALIDATOR`, `BUILDER`, and `SPECIALIST`. Lifecycle statuses are `ACTIVE`, `WAITING`, `PAUSED`, and `ARCHIVED`.

Historical Tasks, Handoffs, and events retain their agent identifiers after an agent is archived.

## Task

Path:

```text
<project>/tasks/<task_id>.md
```

```yaml
task_id: TASK-AUDIT-EXAMPLE-T0-abcdef123456
project_id: EXAMPLE
phase: T0
task_type: INDEPENDENT_AUDIT
assigned_agent: auditor_a
required_role: AUDITOR
required_capability: audit
description: Audit the frozen producer artifact.
status: ASSIGNED
request_path: 01_RESEARCH/EXAMPLE/audits/T0_audit_request.md
input_artifacts:
  - path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
    sha256: <sha256>
    metadata:
      kind: producer_artifact
output_artifacts: []
context_refs:
  - 01_RESEARCH/EXAMPLE/STATE.md
authorization_id: CREATE_AUDIT_REQUEST-20260823130829
parent_task_id: null
causation_event_id: null
idempotency_key: AUDIT-EXAMPLE-T0-abcdef123456
attempt: 0
result: {}
error: null
created_at: <ISO-8601 UTC timestamp>
updated_at: <ISO-8601 UTC timestamp>
metadata: {}
```

Task statuses are `CREATED`, `ASSIGNED`, `RUNNING`, `COMPLETED`, `FAILED`, and `BLOCKED`. `result` is valid only for completed work; `error` is evidence for failed or blocked work. `metadata.recovery_history` records explicit retries, while `metadata.reconciliation_history` records completion repaired from already-committed governed evidence. These system-maintained histories are excluded from immutable Task creation evidence, so an exact creation replay remains idempotent.

## Artifact reference

Tasks and Handoffs embed artifact references:

```yaml
path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
sha256: <sha256>
metadata:
  kind: producer_artifact
  phase: T0
```

The reference identifies evidence; it does not copy the artifact.

## Handoff

Path:

```text
<project>/handoffs/<handoff_id>.md
```

```yaml
handoff_id: HANDOFF-AUDIT-EXAMPLE-T0-abcdef123456
project_id: EXAMPLE
sender: producer_a
receiver: auditor_a
reason: Root approved independent audit.
artifact_refs:
  - path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
    sha256: <sha256>
    metadata:
      kind: producer_artifact
context_refs:
  - 01_RESEARCH/EXAMPLE/audits/T0_audit_request.md
  - 01_RESEARCH/EXAMPLE/STATE.md
status: CREATED
task_id: TASK-AUDIT-EXAMPLE-T0-abcdef123456
phase: T0
authorization_id: CREATE_AUDIT_REQUEST-20260823130829
timestamp: <ISO-8601 UTC timestamp>
acknowledged_at: null
acknowledged_by: null
metadata:
  may:
    - audit the frozen artifact
  may_not:
    - modify the producer artifact
```

Acknowledgement changes `status` to `ACKNOWLEDGED` and records `acknowledged_at` and `acknowledged_by`. Only the designated receiver may acknowledge.

## Independent audit request

Path:

```text
<project>/audits/<phase>_audit_request.md
```

```yaml
request_id: AUDIT-EXAMPLE-T0-abcdef123456
proposal_id: CREATE_AUDIT_REQUEST-20260823130829
project_id: EXAMPLE
phase: T0
status: AUDIT_PENDING
artifact_path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
artifact_sha256: <sha256>
producer: producer_a
auditor: auditor_a
approved_by: personal_root
reason: Frozen artifact requires independent audit.
task_id: TASK-AUDIT-EXAMPLE-T0-abcdef123456
handoff_id: HANDOFF-AUDIT-EXAMPLE-T0-abcdef123456
```

On completion, the record adds:

```yaml
status: COMPLETED
audit_verdict: PASS
audit_path: 01_RESEARCH/EXAMPLE/audits/T0_audit.md
```

The project, phase, artifact path, hash, producer, and auditor are immutable request evidence.

## Independent audit

Path:

```text
<project>/audits/<phase>_audit.md
```

```yaml
project_id: EXAMPLE
phase: T0
auditor: auditor_a
artifact_path: 01_RESEARCH/EXAMPLE/artifacts/T0_producer_artifact.txt
artifact_sha256: <sha256>
verdict: PASS
audit_text_sha256: <sha256 of audit notes>
```

Verdicts are `PASS`, `PASS_WITH_REPAIRS`, and `FAIL`. A matching retry may reuse existing audit evidence; a different verdict, actor, artifact hash, or audit text conflicts.

## Root gate

Path:

```text
00_ROOT/inbox/<project_id>_<phase>_GATE.md
```

```yaml
project_id: EXAMPLE
phase: T0
state: WAITING_ROOT
audit_verdict: PASS
artifact_sha256: <sha256>
```

This is a decision document, not a proposal. Root options are `AUTHORIZE`, `MODIFY`, `REPAIR`, `HOLD`, and `CLOSE`.

## Event ledger

Path:

```text
.control_tower/events.jsonl
```

Each non-empty line is one JSON object:

```json
{
  "event_id": "EVT-TASK-AUDIT-EXAMPLE-T0-COMPLETED",
  "actor": "auditor_a",
  "action": "TASK_COMPLETED",
  "target": "EXAMPLE",
  "result": "SUCCESS",
  "capability_checked": "audit",
  "note": "Audit task completed.",
  "correlation_id": "TASK-AUDIT-EXAMPLE-T0",
  "causation_id": null,
  "metadata": {"task_id": "TASK-AUDIT-EXAMPLE-T0"},
  "timestamp_utc": "<ISO-8601 UTC timestamp>"
}
```

`event_id` is the idempotency identity. Correlation groups a workflow; causation points to the fact that triggered this event.

For v1 Producer execution, `PRODUCE_ARTIFACT` sets both `correlation_id` and
`metadata.task_id` to the originating Task. Recovery requires that exact
lineage, so another same-phase Task cannot claim the frozen artifact. Legacy
Producer events without Task lineage remain readable but fail closed for
automatic Task reconciliation.

## Decision log

Path:

```text
00_ROOT/DECISION_LOG.md
```

The decision log is append-only human-readable history. Machine decisions remain recoverable from archived proposal frontmatter and the event ledger; the log is not a substitute for those records.
