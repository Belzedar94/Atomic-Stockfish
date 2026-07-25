# E00 source v3 Launch3 — terminal forensic record

Launch3 is terminal and immutable. The prepare/seal process exited fail-closed
before smoke execution because PowerShell serialized the design directory and
file inventories as nested arrays. The design and external captures must never
be executed, recovered, edited, deleted or reused.

The active fresh-launch procedure is
[`e00-launch7-prepare-smoke-runbook.md`](e00-launch7-prepare-smoke-runbook.md).
The full evidence and hashes are recorded in
[`evidence/mining-loop-v1/overnight-2026-07-25.md`](evidence/mining-loop-v1/overnight-2026-07-25.md).
