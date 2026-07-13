# H8.1 commit-bound evidence

This directory preserves the accepted H8.1 speed outputs and their
machine-readable artifact manifest. The candidate was built from clean commit
`85b2c909a5fa48c02c83104498567d32a454347d`, which contains the functional
commit `6153609c8b454e13bb3941789b9184f9b4825dad`. Later commits only add the
versioned commit A/B runner and correct this evidence record.

Build recipe:

```text
make -C src ARCH=x86-64-bmi2 COMP=mingw EXTRALDFLAGS='-Wl,--no-insert-timestamp' clean
make -C src -j4 ARCH=x86-64-bmi2 COMP=mingw EXTRALDFLAGS='-Wl,--no-insert-timestamp' build
```

The build emitted `GIT_SHA=85b2c909`, `.build_full_sha.txt` contained the full
commit, and the checkout was clean. `commit-ab.log` was produced by the tracked
`tests/atomic_bench_ab.py`; `fairy-gate.log` was produced by the normative
`tests/atomic_bench_compare.py`. Both runners authenticated their inputs before
and after execution.
