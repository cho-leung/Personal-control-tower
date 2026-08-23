# Control Tower overview

Personal Control Tower is a local operating system for coordinating governed work. “Operating system” here means a control plane, not a kernel or security boundary: the CLI applies rules, records decisions, routes bounded tasks, and keeps durable evidence in a filesystem vault.

The design separates five questions that are often blurred together:

1. **May this work happen?** Root authorization and proposal decisions answer this.
2. **Who may do it?** Agent status, role, capability, ownership, and project binding answer this.
3. **What exact work is pending?** A Task answers this.
4. **What evidence moved between owners?** A Handoff answers this.
5. **What actually happened?** Project state, immutable artifacts, audits, decisions, and events answer this.

No chat transcript is canonical. The vault is.

## The control plane

```text
Human / automation
        |
        v
      CLI
        |
        +---------------- Root governance ----------------+
        |                                                  |
        v                                                  v
   Proposals ---- inspect / approve / reject ----> Projects and agents
        |                                                  |
        +-----------------------+--------------------------+
                                |
                                v
                       ChiefOfStaff tick
                                |
                     validate and select Task
                                |
                                v
                       MockAgentRuntime
                                |
                    result / artifact / Handoff
                                |
                                v
                    state, events, next gate
```

The CLI is the normal entrypoint. Engines enforce transitions and invariants. Stores serialize the durable records. The `ChiefOfStaff` performs a bounded reconciliation step when `tick` is invoked. The mock runtime returns deterministic local results; it does not perform network or external-system actions.

## Root governance

A proposal is a request for authority, not authority itself. Creation writes it to `00_ROOT/inbox/` in `WAITING_ROOT`. Root may inspect it, then explicitly approve or reject it. A successful decision records an event and decision-log entry and archives the proposal.

This applies to structural or consequential changes such as creating projects or agents, binding an agent to a project, creating missing runtime state, and admitting a frozen artifact into independent audit.

Approval is evidence-sensitive. Replaying the same identity with the same evidence is safe. Reusing an identity for different evidence is a conflict. An old audit proposal whose exact work is already fulfilled may be closed idempotently, but it cannot move a completed project backward.

## Projects and phases

`STATE.md` is the canonical governance state for one project. It records the division, phase, state, owner, authorization, frozen artifact, assigned auditor, latest verdict, bindings, and next gate.

Root authorization permits a bounded phase; it does not grant open-ended authority. A producer may execute only inside that authorization. Completion freezes an artifact and its SHA-256 before independent audit. An audit verdict returns the project to Root. A new phase never starts automatically.

The three default divisions are:

- `01_RESEARCH`
- `02_BUSINESS`
- `03_PERSONAL_GROWTH`

Project identifiers must resolve to exactly one canonical `STATE.md` across those divisions.

## Agents

`00_ROOT/agents.yaml` is the canonical agent registry. An agent has an identifier, division, role, lifecycle status, owned scope, capabilities, and notes.

The registry answers whether an identity exists and is active. Project bindings answer whether that identity participates in a particular project and in which role. A capability answers whether it may perform a concrete action.

These checks are cumulative. For example, an auditor must be registered, `ACTIVE`, have the `AUDITOR` role and `audit` capability, be the auditor assigned to the frozen artifact, and remain independent of the producer. Merely possessing an `audit` string is not sufficient.

Lifecycle commands are Root-governed and evented. Archiving an agent prevents new work without erasing historical evidence. Role and capability changes cannot rewrite the identity of earlier events, tasks, or handoffs.

## Tasks

A Task is a bounded unit of project-local work stored under `<project>/tasks/`. It includes:

- project and phase;
- task type and description;
- assigned agent;
- required role and capability;
- authorization and causation references;
- input artifacts and context references;
- status, attempt, result, and error evidence;
- an idempotency key.

Tasks follow their own state machine. They do not replace project governance. A completed Task may supply evidence used by an engine, but it cannot authorize a phase, approve a proposal, or declare its own audit valid.

## Handoffs

A Handoff is durable evidence that one owner transferred a defined package to another. It records sender, receiver, project, phase, reason, artifact references, context references, and optional Task linkage.

Handoffs are created once and then acknowledged by the designated receiver. Acknowledgement does not modify the original transfer evidence; it adds receiver evidence. Reusing a handoff identity with different evidence is rejected.

## Mock runtime and `tick`

`MockAgentRuntime` gives the local control loop a deterministic executor. It can return producer-like, auditor-like, or generic structured output without calling an LLM or external service.

One `ChiefOfStaff` tick performs a bounded reconciliation pass over the current assigned-Task snapshot:

1. Load eligible Tasks and their project state.
2. Validate authorization, assigned agent, status, role, capability, and evidence.
3. Claim or start each selected Task through a legal transition.
4. Call the configured runtime once for each selected Task.
5. Persist each result, artifact reference, Handoff, event, and any next proposal or gate.

A tick is a reconciler, not a sovereign actor. It cannot approve its own proposal, impersonate Root, let a producer audit itself, or automatically authorize the next project phase. If no eligible work exists, it makes no governance change. Use `task-run` when exactly one assigned Task should be executed.

## Independent audit

An audit request and an audit result are separate records.

Approving `CREATE_AUDIT_REQUEST` verifies the proposal snapshot, artifact path and SHA-256, assigned auditor, lifecycle status, role, capability, project binding, and producer/auditor independence. It writes:

```text
<project>/audits/<phase>_audit_request.md
```

and admits eligible work into `AUDIT_PENDING`. It never writes a verdict.

The assigned auditor later records the actual audit in:

```text
<project>/audits/<phase>_audit.md
```

The completed audit creates a Root gate under `00_ROOT/inbox/`. Root then chooses whether to authorize a new phase, request modification or repair, hold, or close.

## Evidence and trust boundary

The vault uses transparent formats:

- Markdown plus YAML frontmatter for states, proposals, Tasks, Handoffs, and audit evidence;
- YAML for the agent registry;
- JSON Lines for the append-only event ledger;
- Markdown for human-readable decision history.

File operations use explicit identifiers, evidence comparison, and atomic replacement where state changes require it. The design provides operational governance and auditability inside a trusted local filesystem. It is not a defense against a user or process that can directly rewrite vault files.

See [State machines](STATE_MACHINE.md), [Usage](USAGE.md), and [Schemas](SCHEMAS.md) for the concrete contracts.
