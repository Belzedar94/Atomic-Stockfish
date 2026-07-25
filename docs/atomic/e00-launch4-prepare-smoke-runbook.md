# E00 source v3 Launch4 — terminal forensic record

Launch4 is terminal and immutable. Its one-pair smoke science completed and
was independently revalidated, but the wrapper correctly failed closed before
its final seal: `run_e00_source.py` emitted the compact success summary through
Python `print`, which translated LF to CRLF on Windows, while the strict
launcher contract rejects every carriage return.

The Launch4 design, smoke and capture roots must never be executed, recovered,
collected, analysed, edited, deleted or reused. Its full root remains absent
and must never be created.

The active fresh-launch procedure is
[`e00-launch5-prepare-smoke-runbook.md`](e00-launch5-prepare-smoke-runbook.md).
The complete evidence and hashes are recorded in
[`evidence/mining-loop-v1/overnight-2026-07-25.md`](evidence/mining-loop-v1/overnight-2026-07-25.md).
