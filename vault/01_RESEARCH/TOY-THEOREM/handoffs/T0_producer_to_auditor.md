---
handoff_id: TOY-THEOREM-T0-AUDIT
from: toy_producer
to: toy_auditor
project: TOY-THEOREM
phase: T0
state: AUDIT_PENDING
lineage: CANONICAL
artifact_path: 01_RESEARCH/TOY-THEOREM/artifacts/T0_producer_artifact.txt
artifact_sha256: e2df6802c3d78b06fb56795cdd95bb514c60de56fdf90bf61343590b44b721ae
authorization_id: ROOT-DEMO-001
---
# Producer → Independent Auditor

## Receiver MAY
- inspect exactly the frozen artifact;
- attack the claim;
- return PASS / PASS_WITH_REPAIRS / FAIL.

## Receiver MAY NOT
- modify the producer artifact;
- silently authorize the next phase;
- merge unrelated experimental material.
