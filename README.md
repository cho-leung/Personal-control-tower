# Personal Control Tower 3.0 Alpha

Personal Control Tower is a local-first CLI control plane for governed projects, agents, tasks, and handoffs. Its vault is made of readable Markdown, YAML, and JSONL files, so the operating record remains inspectable without a database or hosted service.

The v3 alpha is an incremental upgrade over the tagged v1 governance kernel, not a rewrite. Milestones 1 and 2 add a Chief of Staff chat entry for read-only organization queries and typed Proposal drafting while preserving every v1 command and authority boundary. Root still approves consequential changes, producers and auditors remain independent, artifacts are frozen by SHA-256, and the included task runtime is deterministic. It does not send messages, spend money, deploy software, or perform other external actions.

## What it provides

- Root-gated proposals for projects, agents, bindings, runtimes, and independent audits.
- A project state machine from authorization through production, audit, and the next Root decision.
- Agent lifecycle management for status, role, capabilities, and project membership.
- Durable project-local Tasks and immutable, acknowledged Handoffs.
- A deterministic `MockAgentRuntime` and one-step `ChiefOfStaff` tick loop.
- A provider-neutral `LLMAdapter` contract and offline `control-tower chat` interface for typed queries and Root-gated Proposal drafting.
- An append-only event ledger, decision log, dashboard, and human-readable evidence files.
- Idempotent creation and replay checks: the same identity plus the same evidence is safe; conflicting evidence is rejected.

The core governance rule is:

```text
proposal -> Root decision -> bounded task -> evidence -> independent audit -> Root gate
```

## Requirements and installation

- Python 3.9 or newer
- macOS, Linux, or Windows

From the repository root:

```bash
python3.9 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

If you must use pip 21.2 offline and the environment has PyYAML but no `wheel`,
use the repository's legacy compatibility shim instead:

```bash
python setup.py develop --no-deps
```

This fallback does not install dependencies. The normal pip command above is
recommended whenever the declared build requirements can be installed.

Installation exposes both of these equivalent forms:

```bash
control-tower --help
python -m control_tower.cli --help
```

Global options such as `--vault` come before the subcommand.

## Start a persistent vault

Always choose the vault explicitly for data you want to keep:

```bash
VAULT_PATH="$PWD/control-tower-vault"
control-tower --vault "$VAULT_PATH" init
control-tower --vault "$VAULT_PATH" dashboard
```

The principal commands are:

```text
chat, dashboard, status, sync
inspect, approve, reject
project-create, bind, authorize, decide
agent-create, agent-archive, agent-role, agent-capability
task-create, tick
```

For complete examples and command arguments, see [Usage](docs/USAGE.md).

## Talk to the Chief of Staff

The default adapter supports bounded natural-language queries and Proposal requests with no API key:

```bash
control-tower --vault "$VAULT_PATH" chat
```

Or run one turn for scripts and tests:

```bash
control-tower --vault "$VAULT_PATH" chat \
  --message "帮我看看我现在所有项目状态"
```

The local query service reads project state, Agent Registry, Tasks, Root inbox, attention items, and recent events. It does not read artifact bodies, and it never exposes private notes to the adapter or response. Read turns do not append Events.

Milestone 2 adds three typed, strictly allowlisted draft types:

- `CREATE_TASK`
- `CREATE_PROJECT_REQUEST`
- `CREATE_AGENT_REQUEST`

For example, if an authorized `CAREER-OS` has an eligible Producer and independent Auditor:

```bash
control-tower --vault "$VAULT_PATH" chat \
  --message "Help me advance my AI career"
```

This registers a deterministic `WAITING_ROOT` Proposal and a `PROPOSAL_DRAFTED` Event. It does not create or run a Task. Root must inspect and decide the Proposal separately:

```bash
control-tower --vault "$VAULT_PATH" inspect <FULL_PROPOSAL_ID>
control-tower --vault "$VAULT_PATH" approve <FULL_PROPOSAL_ID>
```

Chat never accepts approval, rejection, execution, `tick`, or direct organization changes. Mixed requests such as “create and approve this task” fail closed. A missing, damaged, ambiguous, or stale Vault also fails closed.

## Safe synthetic demo

`demo --reset` deletes and rebuilds the vault path passed to it. Never point that command at a live or valuable vault. Running `demo` without `--reset` still writes synthetic data to the selected vault.

Use a disposable directory:

```bash
DEMO_DIR="$(mktemp -d)"
control-tower --vault "$DEMO_DIR/vault" demo --reset
control-tower --vault "$DEMO_DIR/vault" dashboard
```

On Windows, create a disposable directory first and pass its full path with `--vault`; see [Usage](docs/USAGE.md#run-the-disposable-demo).

## A Root-gated workflow

Commands that change governed structure create a proposal or require explicit Root authority. Review the inbox from the dashboard, inspect the exact proposal, then approve or reject it:

```bash
control-tower --vault "$VAULT_PATH" dashboard
control-tower --vault "$VAULT_PATH" inspect CREATE_AUDIT_REQUEST
control-tower --vault "$VAULT_PATH" approve CREATE_AUDIT_REQUEST
```

Approving `CREATE_AUDIT_REQUEST` only registers an independent audit request and, when appropriate, moves `PRODUCER_COMPLETE` to `AUDIT_PENDING`. It never records a verdict. The assigned auditor performs that later as a separate action.

`tick` advances at most the bounded work selected for that invocation. It does not bypass proposal, authorization, capability, role, artifact, audit, or Root-decision gates.

## Vault layout

```text
vault/
  00_ROOT/
    agents.yaml
    inbox/
    archive/
    DECISION_LOG.md
  01_RESEARCH/<project>/
  02_BUSINESS/<project>/
  03_PERSONAL_GROWTH/<project>/
    STATE.md
    tasks/
    handoffs/
    artifacts/
    audits/
  .control_tower/
    events.jsonl
```

`STATE.md` is the canonical project governance state. Tasks describe bounded work inside that project; they do not independently authorize a project transition. Handoffs record evidence transfer and acknowledgement. Events record facts that occurred.

## Verification

The core test suite has no optional agent-SDK dependency:

```bash
python -m compileall -q control_tower tests
python -m unittest discover -s tests -v
```

The optional API-side agent twins remain separate:

```bash
python -m pip install -r requirements-agents.txt
```

The local bus and vault remain authoritative even when optional model-backed agents are used.

## Documentation

- [Control Tower overview](docs/CONTROL_TOWER_OVERVIEW.md)
- [Architecture](docs/ARCHITECTURE.md)
- [State machines](docs/STATE_MACHINE.md)
- [Usage](docs/USAGE.md)
- [Schemas and on-disk evidence](docs/SCHEMAS.md)
- [Obsidian setup](docs/OBSIDIAN_SETUP.md)
