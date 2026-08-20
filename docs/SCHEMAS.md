# Schemas

## STATE.md
YAML frontmatter holds machine-readable canonical state.

## Handoff
Every handoff records:
- FROM / TO
- project / phase / lineage
- exact artifact path + SHA-256
- authorization id
- MAY / MAY NOT

## Artifact identity
If a file changes, its hash changes. Prior audit does not silently transfer.

## Root inbox
Major gates appear under `00_ROOT/inbox/`.
That is the point: you review decisions, not every intermediate message.
