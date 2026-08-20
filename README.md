# Personal Control Tower Bus V0

A minimal local-first orchestration prototype:

**ROOT AUTHORIZATION → PRODUCER → frozen artifact + SHA-256 → INDEPENDENT AUDITOR → ROOT INBOX**

V0 uses a synthetic research task. It does not automate real-world external actions.

## Rules enforced in code

- READY ≠ AUTHORIZED
- PRODUCED ≠ AUDITED
- REPAIRED ≠ RE-AUDITED
- one concrete action → one owner
- producer cannot independently audit its own artifact
- artifact identity is frozen with SHA-256 before audit
- new major phases return to Root instead of auto-starting

## Quick start

```bash
cd personal-control-tower-bus-v0
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m control_tower.cli init
python -m control_tower.cli demo
python -m control_tower.cli status
python -m unittest discover -s tests -v
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.

## Obsidian

After `init`, open the repo's `vault/` folder as an Obsidian vault.
No plugin or MCP is required for V0.

## Optional OpenAI Agents SDK

After V0 passes:

```bash
pip install -r requirements-agents.txt
```

Set `OPENAI_API_KEY` in the shell, never inside the vault.

See `docs/` for architecture and next steps.
