# E00 source v3 Launch6 terminal forensic record

Launch6 is terminal and immutable. This file is a tombstone, not a runnable
runbook. Never execute, recover, collect, analyse, edit, delete or reuse any
Launch6 design, smoke, full or capture root.

The committed Launch6 source was
`4ef0af27833a871b3ddce6f0305b60a099e0bb93`, tree
`508817593e6fe9a474977f4590b8da63bed9751d`. Its one-pair/two-game
`Threads=1` smoke completed and committed internally consistent evidence:

- design inventory SHA-256
  `e59e9181b36c11c493fa251e533e957c193a0ec2a13460c2c6ada015db104b35`;
- design receipt SHA-256
  `54f48a2bd7bbe2db8f643a0b7b09247eb83f4df4a12e630a831ea5889e8bf49b`;
- games SHA-256
  `019572d88882989483ac519fafe8e07e78153ca4da5d86a67f83ecaa55d56cf2`;
- execution receipt SHA-256
  `e65db654d050d8649107029cd0fd115bd78a2b3d20261f7fc760845a5db50262`;
- smoke inventory SHA-256
  `f06222a5064e38ba70d03fd5b56c8b86bf30d28eb5bd8ce78467508e483eb2b1`;
- outer runner stdout SHA-256
  `0b9168ae29964b73e01046bbf69a2d628596948e5e1e5dd951d37d8a3ec93705`;
- all captured stderr and the rejection ledger were byte-empty;
- both child/controller executions exited zero;
- both games proved natural cleanup with zero descendants;
- the Launch6 full root remained absent.

The outer wrapper then failed closed while validating child import inventory
row 124. The row identifies the legitimate empty Python standard-library file
`urllib\__init__.py`, source `python-installation`, size zero and the canonical
empty-file SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
That exact row is present in runtime discovery, runtime manifest, build
receipt, smoke snapshot and all four referee/verifier inventories.

The Python producer correctly accepts a non-negative size. The Launch6
PowerShell deep validator incorrectly required at least one byte. Independent
read-only forensics returned P0=0/P1=1 and proved that changing only the
minimum from one to zero lets the complete smoke assertion pass. This was a
validator-only false negative, not an engine, referee, verifier, process or
scientific failure. Launch6 nevertheless remains terminal under the
no-reuse rule.

The only active successor is
[`e00-launch7-prepare-smoke-runbook.md`](e00-launch7-prepare-smoke-runbook.md).
The detailed chronology is preserved in
[`evidence/mining-loop-v1/overnight-2026-07-25.md`](evidence/mining-loop-v1/overnight-2026-07-25.md).
