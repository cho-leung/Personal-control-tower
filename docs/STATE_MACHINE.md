# State machines

Personal Control Tower keeps separate state machines for governance, work, evidence transfer, and identities. This separation is intentional: completing a Task is not the same as completing a project phase, and approving an audit request is not the same as recording an audit verdict.

## Project governance

The normal phase lifecycle is:

```text
PROPOSED
   |
   v
 READY -------------------------------> HOLD / ARCHIVED
   | Root authorize
   v
AUTHORIZED ---------------------------> PAUSED / HOLD
   | producer starts
   v
 ACTIVE ------------------------------> WAITING / BLOCKED / PAUSED / HOLD
   | producer completes and freezes artifact
   v
PRODUCER_COMPLETE
   | Root approves independent audit request
   v
AUDIT_PENDING
   | assigned auditor records verdict
   +------------+-------------------+
   v            v                   v
 PASS   PASS_WITH_REPAIRS          FAIL
   +------------+-------------------+
                |
                v
          WAITING_ROOT
                | Root decision
      +---------+---------+-------------+------------+
      v                   v             v            v
AUTHORIZED             WAITING   REPAIR_REQUIRED    HOLD
new phase               modify       repair
      |
      +---------------------------------------------> COMPLETE
```

Important rules:

- Only Root creates `AUTHORIZED`.
- An authorization is phase- and scope-specific.
- `READY` cannot jump directly to `ACTIVE`.
- Production freezes the artifact path and SHA-256 before audit admission.
- Root approval of `CREATE_AUDIT_REQUEST` may move `PRODUCER_COMPLETE` to `AUDIT_PENDING`; it never creates a verdict.
- A matching approval replay while already `AUDIT_PENDING` is safe.
- A stale proposal for an already completed audit is fulfilled without moving `WAITING_ROOT` backward.
- The audit engine records the verdict, then returns the project to `WAITING_ROOT` with a Root gate.
- `AUTHORIZE` after a `PASS` starts a distinct new phase and clears prior-phase artifact and audit evidence from the new phase state.
- Reauthorizing `REPAIR_REQUIRED` or `WAITING` work that already has frozen evidence also requires an explicit distinct phase; repair never overwrites the earlier artifact or audit request.
- Applying a Root gate decision archives that gate, so the Main Control Room does not keep reporting an already-resolved item.

### Root decisions at `WAITING_ROOT`

| Decision | Result | Meaning |
| --- | --- | --- |
| `AUTHORIZE` | `AUTHORIZED` | Permit a distinct next phase after `PASS`. |
| `MODIFY` | `WAITING` | Require a changed plan or scope before continuation. |
| `REPAIR` | `REPAIR_REQUIRED` | Require repair and later explicit authorization. |
| `HOLD` | `HOLD` | Pause the governed line for Root review. |
| `CLOSE` | `COMPLETE` | Close the phase or project. |

There is no automatic next-phase authorization.

## Proposal decisions

New proposals enter the Root inbox directly in `WAITING_ROOT`:

```text
                  +---- Root approve ----> EXECUTED ----> archive
WAITING_ROOT -----|
                  +---- Root reject -----> REJECTED ----> archive
```

`CREATED` is retained for legacy proposal compatibility. `APPROVED` exists in the serialized model but successful v1 execution is recorded as `EXECUTED` after the proposed operation succeeds.

Execution order matters: validation and the proposed operation must succeed before success is recorded. Event IDs and proposal evidence make an exact replay idempotent. Reusing the same identity for different target or payload evidence is a conflict.

## Independent audit request

Audit admission has its own evidence status:

```text
PENDING ---- assigned auditor records matching audit ----> COMPLETED
```

The request file is `<project>/audits/<phase>_audit_request.md`. `PENDING` means Root has approved a specific artifact hash and auditor assignment. `COMPLETED` links that request to a verdict and `<phase>_audit.md`.

The request cannot silently transfer to a changed phase, artifact, hash, producer, or auditor.

## Task execution

Tasks are project-local bounded work records:

```text
CREATED ----> ASSIGNED ----> RUNNING ----> COMPLETED
   |              |              |
   |              |              +-------> FAILED
   |              |              |
   +--------------+--------------+-------> BLOCKED
                                      
FAILED  ---- explicit reassignment ----> ASSIGNED
BLOCKED ---- explicit reassignment ----> ASSIGNED
BLOCKED -------------------------------> FAILED
RUNNING -- explicit interruption recovery --> FAILED --> ASSIGNED
RUNNING -- matching committed evidence ----> COMPLETED
```

Rules:

- A new Task starts in `CREATED`.
- `RUNNING` increments the attempt counter.
- A result may be attached only to `COMPLETED`.
- `FAILED` requires an error message.
- `FAILED` and `BLOCKED` never resume implicitly; they require reassignment.
- A process interruption can leave durable work in `RUNNING`. Only ROOT's
  explicit `task-retry` may resolve it. The Control Tower first reconciles
  exact frozen artifact or audit evidence that was already committed; a match
  repairs the Task to `COMPLETED` without re-running the Agent. Only when no
  committed evidence exists does it record the interrupted attempt and return
  the Task to `ASSIGNED`. `tick` never guesses that work is safe to replay.
- `COMPLETED` is terminal.
- Replaying the same transition with the same result or error is safe; different evidence conflicts.

Recovery and reconciliation history are mutable operational evidence, not
Task creation identity. They therefore do not break an exact replay of the
original Task creation command.

Task state does not authorize a project transition. The ChiefOfStaff must call the relevant governed engine with the Task result and evidence.

## Handoff acknowledgement

```text
CREATED ---- designated receiver acknowledges ----> ACKNOWLEDGED
```

Creation evidence is immutable. Only the named receiver may acknowledge the handoff. Repeated acknowledgement by that receiver is safe; a different receiver or different evidence is rejected.

## Agent lifecycle

Agent lifecycle status is stored in `00_ROOT/agents.yaml`:

```text
ACTIVE / WAITING / PAUSED ---- Root archive ----> ARCHIVED
```

`ARCHIVED` preserves the identity and history but prevents new work. It is terminal in the v1 CLI.

Role and capability updates are Root-governed mutations, not status transitions. They are evidence-checked and idempotent:

- `UPDATE_AGENT_ROLE` may carry `expected_role`; a stale expected role is a conflict.
- `UPDATE_AGENT_CAPABILITY` uses `ADD` or `REMOVE`; repeating the same operation is safe.
- `personal_root` cannot be archived or re-roled, and its `approve`, `reject`, and `authorize` capabilities cannot be removed.
- An active project owner, assigned auditor, or agent with unfinished Tasks cannot be archived.
- Project bindings are reconciled when a non-owner agent role changes.

## ChiefOfStaff tick

`tick` is an explicit reconciliation boundary rather than a persistent state:

```text
no eligible Task -------------------------------------> no-op
assigned snapshot -> validate each -> run each once -> persist evidence
validation/runtime failure -> persist BLOCKED or FAILED evidence
Root gate encountered -------------------------------> wait for Root
```

A tick processes the finite assigned-Task snapshot and then stops. It never loops through an unbounded queue, approves a proposal, authorizes a phase, or treats mock output as independent audit authority. `task-run` is the explicit single-Task command.
