# Obsidian Setup

V0 needs no plugin.

1. Run `python -m control_tower.cli init`.
2. In Obsidian, choose **Open folder as vault**.
3. Select the repo's `vault/` directory.

Suggested pins:
- `00_ROOT/ACTIVE_BOARD.md`
- `00_ROOT/DECISION_LOG.md`
- `00_ROOT/inbox/`

After the demo works, initialize Git:

```bash
git init
git add .
git commit -m "Control Tower Bus V0"
```

Never commit API keys or `.env`.
