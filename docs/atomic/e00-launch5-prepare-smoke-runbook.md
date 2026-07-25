# E00 source v3 Launch5 terminal forensic record

Launch5 is terminal and immutable. This file is a tombstone, not a runnable
runbook. Never execute, recover, collect, analyse, edit, delete or reuse any
Launch5 design, smoke, full or capture root.

The committed Launch5 source was
`22607146ea2c3c204c5f509b458129b54c61c48f`, tree
`2bb3076a4d52ffecaaecd3a982251e21be80e6bb`. Its one-pair/two-game smoke
completed soundly and committed canonical evidence:

- design inventory SHA-256
  `8cc17c59eb5d188596dce2d2cbca7b1f7e1e77d6cdb1a7e73e29d523496ce1dd`;
- design receipt SHA-256
  `581287d7e3292b04b70c89cb738456d187bdb05e8851c2aa1f7d61a32251ead3`;
- games SHA-256
  `9d2c4dd5b069bb5058469b477a8b4c0b459a54b9ceb7ec0102ef986fd0c71856`;
- execution receipt SHA-256
  `861f3c487640eeb0906f87243375cafac6745be08eb0df14c3a45b0803419a86`;
- outer runner stdout SHA-256
  `278938eeaa9c0c8d6b50ca8fffc781b175362f9f89548c9036564430d7b16ea4`;
- runner stderr and rejection ledger were byte-empty;
- both games proved natural completion and zero descendants;
- the Launch5 full root remained absent.

The wrapper then failed closed on a validator-only false negative. Python
correctly hashed the 19 advertised UCI options, including six literal
`<empty>` occurrences, as
`0c2ef87b8de44c286f0f6032ad7f0906835a731df5038c903959ce189a8e6cf5`.
Windows PowerShell 5.1 `ConvertTo-Json` HTML-escaped those strings and the
stale validator computed
`91f5b6bc71720937f128ca2d72b9e439b7c7e3c10740b5a79234af413d2a0a08`.
There was no engine, referee, verifier, process or scientific failure.

The only active successor is
[`e00-launch6-prepare-smoke-runbook.md`](e00-launch6-prepare-smoke-runbook.md).
The detailed chronology is preserved in
[`evidence/mining-loop-v1/overnight-2026-07-25.md`](evidence/mining-loop-v1/overnight-2026-07-25.md).
