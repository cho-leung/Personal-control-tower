# Architecture

Personal Control Tower 1.0 is a Python 3.9+ CLI application backed by a local filesystem vault. It favors explicit transitions and inspectable evidence over background automation.

## Layers

```text
CLI and ControlTowerBus adapters
                |
                v
Root decisions / ChiefOfStaff reconciliation
                |
                v
Governance engines and guardrails
                |
                v
Project, proposal, agent, Task, Handoff, audit, event stores
                |
                v
Local Markdown / YAML / JSONL vault
```

### CLI and adapters

`control_tower.cli` parses commands and renders user-facing results. Global arguments, including `--vault`, are resolved before a subcommand. `ControlTowerBus` is the programmatic adapter used by the demo and integrations.

Neither layer owns canonical state. They coordinate the lower-level engines and stores.

### Governance engines

Engines own concrete state changes:

- project creation and project membership;
- agent creation and lifecycle mutations;
- Root authorization and post-audit decisions;
- producer execution and artifact freezing;
- audit-request admission;
- independent audit recording;
- proposal routing and execution.

Guardrails validate legal transitions, actor ownership, lifecycle status, role, capability, frozen evidence, assigned auditor identity, and producer/auditor independence.

### Work runtime

Tasks and Handoffs form the project-local work plane. The `ChiefOfStaff` selects eligible Tasks when `tick` is invoked, validates them against canonical project and agent state, calls an `AgentRuntime`, and persists the bounded result.

`MockAgentRuntime` is the default v1 executor. It is deterministic and local. Runtime output is data; governance engines decide whether that data is sufficient to change canonical state.

### Persistence

The vault stores machine-readable frontmatter alongside human-readable Markdown. Stores use safe identifiers, evidence comparison, exclusive creation, and atomic replacement where a mutable record must advance.

The event ledger is append-only JSON Lines. Deterministic event identities and `append_once` semantics prevent an exact replay from duplicating the recorded fact.

## Vault topology

```text
<vault>/
  00_ROOT/
    ACTIVE_BOARD.md
    AGENT_REGISTRY.md
    PROJECT_REGISTRY.md
    DECISION_LOG.md
    agents.yaml
    inbox/
      <proposal or Root gate>.md
    archive/
      <decided proposal>.md

  01_RESEARCH/
  02_BUSINESS/
  03_PERSONAL_GROWTH/
    <project>/
      STATE.md
      tasks/
        <task_id>.md
      handoffs/
        <handoff_id>.md
      artifacts/
      audits/
        <phase>_audit_request.md
        <phase>_audit.md
      claims/
      failed_routes/

  .control_tower/
    events.jsonl
```

The numbered division directories are peers. A project ID must not resolve to multiple division directories.

## Root proposal transaction

A proposal follows this execution boundary:

1. A command or automaton creates an immutable proposal identity and payload.
2. The proposal is written to `00_ROOT/inbox/` as `WAITING_ROOT`.
3. Root inspects the exact proposal or an unambiguous prefix.
4. `approve` or `reject` verifies Root capability and proposal state.
5. Approval routes the proposal type to one engine; rejection performs no proposed operation.
6. Successful evidence is recorded with a proposal-specific event ID.
7. The proposal becomes `EXECUTED` or `REJECTED`, moves to `archive/`, and is appended to `DECISION_LOG.md`.

An engine must validate before it writes. Exact replay is idempotent; conflicting evidence must fail rather than overwrite. Archived evidence is used to recognize a completed replay.

## Producer-to-auditor flow

```text
Root authorization
        |
        v
Producer Task / execution
        |
        v
artifact + SHA-256 frozen
        |
        v
CREATE_AUDIT_REQUEST proposal
        |
        v
Root approve
        |
        +--> <phase>_audit_request.md
        +--> AUDIT_PENDING
        |
        v
assigned independent auditor
        |
        +--> <phase>_audit.md
        +--> request COMPLETED
        +--> Root gate in inbox
        |
        v
Root decide
```

`AuditRequestEngine` never calls `AuditEngine.record_audit`. It verifies the frozen artifact and auditor assignment, then registers admission to audit. `AuditEngine` independently revalidates the request and actor before writing a verdict.

This separation ensures that Root approval is not mistaken for an audit and that a producer cannot create its own independent verdict.

## Task and Handoff flow

A Task binds a project, phase, assigned agent, role, capability, inputs, and authorization context to one unit of work. A Handoff binds a sender, receiver, and evidence package to an explicit transfer.

The typical work path is:

```text
task-create
    |
    v
CREATED -> ASSIGNED
    |
  tick
    |
    v
RUNNING -> runtime result -> COMPLETED / FAILED / BLOCKED
                              |
                              v
                         Handoff CREATED
                              |
                              v
                     receiver ACKNOWLEDGED
```

If the local process stops while a Task is `RUNNING`, the Task stays durable
and is not replayed by `tick`. ROOT uses `task-retry`. The Control Tower first
reconciles matching committed evidence: a frozen Producer artifact or a
persisted independent audit completes the Task and repairs missing handoff,
event, run, or Root-gate evidence without repeating the side effect. With no
committed evidence, it records the interrupted attempt, passes through
`FAILED`, and returns the Task to `ASSIGNED` for a new attempt.

Producer completion events carry the originating `task_id` as both correlation
and metadata lineage. Reconciliation requires an attempted Task and an exact
lineage match; an unstarted or competing same-phase Task cannot claim another
Task's artifact. Audit lineage is bound by `audit_request.task_id`.

If state was committed before its Producer event, the reconciler accepts only
an unambiguous candidate: the sole currently `RUNNING` Task, or—when none is
running—the sole prior attempted Task. Multiple plausible candidates fail
closed instead of inventing lineage.

The Task and Handoff records do not supersede `STATE.md`. A service must deliberately apply validated work evidence to the project state machine.

## Agent lifecycle and bindings

Agent identity is global to the vault; project participation is local to a project.

- `agents.yaml` is authoritative for identity, status, role, and capability.
- `STATE.md` bindings are authoritative for participation in that project.
- Project ownership is stronger than an ordinary binding and cannot be silently reassigned by a role update.

Agent lifecycle changes are proposal-driven and Root-governed. Safety checks protect `personal_root`, active owners and auditors, and agents with unfinished Tasks. Role changes reconcile non-owner bindings. Archiving preserves the registry record and historical references.

## ChiefOfStaff boundary

The ChiefOfStaff is a deterministic coordinator with no independent governance authority. Each tick:

1. discovers eligible work;
2. validates project state, authorization, agent status, role, capability, and inputs;
3. makes legal Task transitions over the finite assigned-Task snapshot;
4. invokes one bounded runtime operation per selected Task;
5. persists results and events for each attempt;
6. creates any required Handoff, proposal, or Root gate;
7. stops.

It does not approve proposals, change Root policy, authorize phases, or execute external side effects. A future durable queue can call the same bounded operation without changing these authority boundaries.

## Failure and replay model

- Invalid input fails before canonical state changes.
- Missing or conflicting evidence raises an explicit governance or conflict error.
- The same creation identity with identical evidence returns the existing record.
- A repeated terminal transition with identical result is a no-op.
- A different payload, result, receiver, artifact hash, or expected role under the same identity is a conflict.
- Events use stable IDs so retries do not multiply facts.
- Root gates wait in the inbox; no background loop decides them.

The design is local-first, not tamper-proof. Processes with direct filesystem access can bypass the CLI. Git, filesystem permissions, backups, and restricted OS access remain operational responsibilities.

## Extension points

The stable interfaces are the on-disk schemas, state machines, `AgentRuntime`, and bounded engine operations. A model-backed runtime, durable queue, or notification system may be added later if it preserves:

- Root authority;
- role and capability checks;
- producer/auditor independence;
- immutable artifact identity;
- proposal, Task, Handoff, and event idempotency;
- no automatic next-phase authorization.
