# Usage

All examples use the installed `control-tower` command. The equivalent fallback is `python -m control_tower.cli`.

The command shape is:

```text
control-tower --vault <path> <subcommand> [arguments]
```

`--vault` is a global option and must appear before the subcommand.

## Install

```bash
python3.9 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

PowerShell activation is:

```powershell
.venv\Scripts\Activate.ps1
```

Upgrading pip is recommended. A compatibility `setup.py` also supports a local
editable install when an offline legacy environment cannot satisfy the modern
build path; see [Old pip cannot install editable](#old-pip-cannot-install-editable).

## Initialize a persistent vault

Choose an explicit path and keep using the same one:

```bash
VAULT_PATH="$PWD/control-tower-vault"
control-tower --vault "$VAULT_PATH" init
control-tower --vault "$VAULT_PATH" status
control-tower --vault "$VAULT_PATH" dashboard
```

Do not rely on the default `vault` path for valuable data in scripts. An explicit path makes destructive demo operations and production data harder to confuse.

## Run the disposable demo

`demo --reset` deletes and rebuilds the selected vault. Never select a live or valuable vault.

On macOS or Linux:

```bash
DEMO_DIR="$(mktemp -d)"
control-tower --vault "$DEMO_DIR/vault" demo --reset
control-tower --vault "$DEMO_DIR/vault" dashboard
```

On Windows PowerShell:

```powershell
$DemoDir = Join-Path ([System.IO.Path]::GetTempPath()) ("control-tower-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $DemoDir | Out-Null
control-tower --vault (Join-Path $DemoDir "vault") demo --reset
control-tower --vault (Join-Path $DemoDir "vault") dashboard
```

Without `--reset`, `demo` does not delete the selected directory, but it still writes synthetic project data into it. A disposable vault remains the safe choice.

## End-to-end local workflow

The following builds a small Research lane using only local files and the deterministic mock runtime.

### 1. Create the producer

Agent creation is proposal-driven:

```bash
control-tower --vault "$VAULT_PATH" agent-create producer_a \
  --division RESEARCH \
  --role PRODUCER \
  --capability produce_artifact \
  --capability research

control-tower --vault "$VAULT_PATH" inspect CREATE_AGENT
control-tower --vault "$VAULT_PATH" approve CREATE_AGENT
```

Create and approve the independent auditor separately so the proposal prefix remains unambiguous:

```bash
control-tower --vault "$VAULT_PATH" agent-create auditor_a \
  --division RESEARCH \
  --role AUDITOR \
  --capability audit

control-tower --vault "$VAULT_PATH" inspect CREATE_AGENT
control-tower --vault "$VAULT_PATH" approve CREATE_AGENT
control-tower --vault "$VAULT_PATH" agent-list
```

### 2. Create the project

The owner must already be an active producer with `produce_artifact`:

```bash
control-tower --vault "$VAULT_PATH" project-create EXAMPLE \
  --title "Example Research Lane" \
  --division RESEARCH \
  --owner producer_a \
  --phase T0 \
  --lineage CANONICAL

control-tower --vault "$VAULT_PATH" inspect CREATE_PROJECT
control-tower --vault "$VAULT_PATH" approve CREATE_PROJECT
```

Bind the auditor through another Root proposal:

```bash
control-tower --vault "$VAULT_PATH" bind EXAMPLE auditor_a AUDITOR
control-tower --vault "$VAULT_PATH" inspect CREATE_BINDING
control-tower --vault "$VAULT_PATH" approve CREATE_BINDING
```

### 3. Authorize the phase

Initial authorization is an explicit Root action:

```bash
control-tower --vault "$VAULT_PATH" authorize \
  EXAMPLE ROOT-EXAMPLE-T0 \
  --scope "Produce and independently audit T0 only."
```

### 4. Create producer work

`task-create` writes and assigns a project-local Task. For a producer Task, omitted type and capability default to `PRODUCE_ARTIFACT` and `produce_artifact`.

```bash
control-tower --vault "$VAULT_PATH" task-create EXAMPLE \
  --task-id TASK-EXAMPLE-T0-PRODUCE \
  --agent producer_a \
  --role PRODUCER \
  --auditor auditor_a \
  --description "Produce the frozen T0 artifact."

control-tower --vault "$VAULT_PATH" task-list --project EXAMPLE
```

Run the current assigned work and event reconciliation:

```bash
control-tower --vault "$VAULT_PATH" tick
```

The mock producer result is frozen under the project `artifacts/` directory. The resulting `PRODUCE_ARTIFACT` event causes an audit proposal to appear in the Root inbox.

### 5. Approve audit admission

Review before deciding:

```bash
control-tower --vault "$VAULT_PATH" dashboard
control-tower --vault "$VAULT_PATH" inspect CREATE_AUDIT_REQUEST
control-tower --vault "$VAULT_PATH" approve CREATE_AUDIT_REQUEST
```

Approval verifies the artifact SHA-256 and independent auditor, then creates:

```text
audits/<phase>_audit_request.md
tasks/TASK-AUDIT-....md
handoffs/HANDOFF-AUDIT-....md
```

The audit Task is `ASSIGNED`, the Handoff is `CREATED`, and the project is `AUDIT_PENDING`. No verdict exists yet.

To refuse the request instead:

```bash
control-tower --vault "$VAULT_PATH" reject CREATE_AUDIT_REQUEST \
  --note "Audit route must be corrected."
```

Rejection archives the proposal and does not run an audit.

### 6. Run the independent audit

```bash
control-tower --vault "$VAULT_PATH" task-list --project EXAMPLE
control-tower --vault "$VAULT_PATH" handoff-list --project EXAMPLE
control-tower --vault "$VAULT_PATH" tick
```

The assigned mock auditor acknowledges the Handoff and the audit engine writes the actual audit. The request and Task become completed, the project enters `WAITING_ROOT`, and a Root gate appears in the inbox.

### 7. Make the post-audit Root decision

Inspect the project and dashboard:

```bash
control-tower --vault "$VAULT_PATH" inspect EXAMPLE
control-tower --vault "$VAULT_PATH" dashboard
```

After a `PASS`, authorize a distinct next phase:

```bash
control-tower --vault "$VAULT_PATH" decide \
  EXAMPLE AUTHORIZE ROOT-EXAMPLE-T1 \
  --next-phase T1 \
  --scope "Execute T1 only."
```

Other decisions are:

```bash
control-tower --vault "$VAULT_PATH" decide EXAMPLE MODIFY ROOT-MODIFY-001 --note "Revise scope."
control-tower --vault "$VAULT_PATH" decide EXAMPLE REPAIR ROOT-REPAIR-001 --note "Repair the audited defect."
control-tower --vault "$VAULT_PATH" decide EXAMPLE HOLD ROOT-HOLD-001 --note "Pause this lane."
control-tower --vault "$VAULT_PATH" decide EXAMPLE CLOSE ROOT-CLOSE-001 --note "Close this lane."
```

The selected transition must be legal for the current project state. `AUTHORIZE` requires a `PASS` and a new phase.

After `REPAIR` or `MODIFY`, prior artifact and audit evidence is never overwritten. Reauthorize the work under a distinct phase identity:

```bash
control-tower --vault "$VAULT_PATH" authorize \
  EXAMPLE ROOT-EXAMPLE-T0-REPAIR-1 \
  --next-phase T0-REPAIR-1 \
  --scope "Repair and independently re-audit the T0 finding."
```

## Command reference

### Observation and reconciliation

```text
init                         Create missing vault structure and defaults.
status                       Render registry/runtime consistency status.
dashboard                    Show projects, agents, Tasks, and Root inbox.
sync                         Propose missing runtime state from registries.
tick                         Reconcile pending events and all currently assigned Tasks.
agent-list                   List registered agents and lifecycle state.
```

`tick` processes a finite snapshot of assigned Tasks, records failures, reconciles pending automaton events before and after work, then stops at any Root gate. To run exactly one Task, use `task-run`.

### Inspect and decide proposals

```bash
control-tower --vault "$VAULT_PATH" inspect <reference>
control-tower --vault "$VAULT_PATH" approve <proposal-prefix>
control-tower --vault "$VAULT_PATH" reject <proposal-prefix> [--note TEXT]
```

`inspect` resolves proposals first, then Tasks, Handoffs, and projects. A prefix must identify exactly one object; use the full ID if similar items exist.

### Create projects and bindings

```text
project-create PROJECT_ID --title TITLE --division DIVISION --owner AGENT
  [--phase PHASE] [--lineage LINEAGE]

bind PROJECT_ID AGENT_ID ROLE
```

Both commands create Root proposals. They do not mutate the project until `approve` succeeds.

### Agent lifecycle

```text
agent-create AGENT_ID --division DIVISION --role ROLE
  --capability CAPABILITY [--capability CAPABILITY ...]

agent-archive AGENT_ID [--reason TEXT]
agent-role AGENT_ID ROLE [--reason TEXT]
agent-capability AGENT_ID CAPABILITY
  [--operation ADD|REMOVE] [--reason TEXT]
agent-list
```

Every mutating lifecycle command creates a proposal. Inspect and approve or reject it separately.

Archiving retains the registry record. Root cannot be archived or re-roled, and required Root capabilities cannot be removed. Active owners, assigned auditors, and agents with unfinished Tasks are protected from incompatible lifecycle changes.

### Root authorization and gate decisions

```text
authorize PROJECT_ID AUTHORIZATION_ID --scope TEXT [--next-phase PHASE]

decide PROJECT_ID AUTHORIZE DECISION_ID --next-phase PHASE --scope TEXT
decide PROJECT_ID MODIFY|REPAIR|HOLD|CLOSE DECISION_ID [--note TEXT]
```

These commands execute explicit Root authority directly and record events and decision history.

`--next-phase` is required when reauthorizing a project that already carries frozen artifact or audit evidence. This preserves the prior phase instead of overwriting it.

### Tasks

```text
task-create PROJECT_ID
  [--task-id ID]
  [--agent AGENT]
  [--role ROLE]
  [--task-type TYPE]
  [--capability CAPABILITY]
  [--description TEXT]
  [--auditor AGENT]
  [--input-ref PATH ...]

task-list [--project PROJECT_ID] [--status STATUS]
task-run PROJECT_ID TASK_ID
task-retry PROJECT_ID TASK_ID
```

`task-create` immediately assigns the new Task. `--input-ref` is repeatable and records context references. `task-run` executes one assigned Task. `task-retry` is an explicit ROOT recovery operation. It first checks whether the governed side effect was already committed. A matching frozen producer artifact or independent audit is reconciled to `COMPLETED` without running the Agent again, and a `TASK_RECONCILED` event is retained. Otherwise eligible `RUNNING`, `FAILED`, or `BLOCKED` work returns to `ASSIGNED` with recovery history and a `TASK_RETRIED` event. Earlier attempts and run records are never erased. `tick` does not retry interrupted work implicitly.

### Handoffs

```text
handoff-list [--project PROJECT_ID] [--receiver AGENT_ID]
```

Handoffs are normally generated by governed flows such as audit admission. They are acknowledged by the designated receiver when that receiver's Task runs.

## Inspect the vault directly

The CLI is the supported mutation path, but all evidence is readable:

```text
00_ROOT/inbox/                 pending proposals and Root gates
00_ROOT/archive/               decided proposals
00_ROOT/agents.yaml            agent authority
00_ROOT/DECISION_LOG.md        human decision history
<project>/STATE.md             canonical project state
<project>/tasks/               bounded work records
<project>/handoffs/            transfer and acknowledgement evidence
<project>/artifacts/           frozen producer outputs
<project>/audits/              audit requests and results
.control_tower/events.jsonl    append-only facts
.control_tower/runs/           per-attempt runtime records
```

Direct edits can bypass governance checks. Use the CLI for changes and treat direct inspection as read-only operational practice.

## Troubleshooting

### “Ambiguous reference”

Several objects share the prefix. Copy the complete ID from `dashboard`, `task-list`, `handoff-list`, or the filename and retry.

### “Illegal transition”

Inspect the project or Task and follow the allowed transition in [State machines](STATE_MACHINE.md). Retrying does not bypass a missing authorization or Root gate.

### “Agent lacks capability” or role mismatch

Use `agent-list`, inspect project bindings, and create the appropriate Root-governed lifecycle or binding proposal. Do not work around producer/auditor independence.

### Frozen artifact mismatch

The artifact changed after its recorded SHA-256. The old audit request is not transferable. Produce and freeze new evidence, then create a new audit request.

### Old pip cannot install editable

pip 21.2 still invokes the PEP 517 metadata path when `pyproject.toml` declares a
backend. If the environment has setuptools 58 and no `wheel`, then
`pip install -e . --no-build-isolation` fails before it can reach the legacy
editable command. `--no-use-pep517` is also unavailable because this project
explicitly declares its backend.

For a fully offline legacy environment that already contains PyYAML, bypass pip's
build frontend and invoke the compatibility shim directly:

```bash
python setup.py develop --no-deps
```

`--no-deps` prevents network dependency resolution; it therefore requires
PyYAML to be present already. This is only a legacy fallback. Upgrading pip and
using `python -m pip install -e .` remains the recommended path when build
requirements are available.
