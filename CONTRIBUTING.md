# Contributing to Atomic-Stockfish

Thanks for your interest in the project. This page describes how a change gets
from an idea to `main`.

## Proposing a patch

- Branch off `main`. It is the default branch and the baseline every test runs
  against.
- Name the branch `UB<n>-<slug>` for search and evaluation work, or
  `MV-R<n>-<slug>` for move-generation and rules work.
- Keep one idea per branch. Independent branches are far easier to test and
  review than a stack of changes that only makes sense as a whole, and a
  single-idea diff gives a clean answer about whether that idea works.
- Explain in the pull request what the change does and why you expect it to
  help.

## Building and testing locally

Build the engine and run the C++ units and the move-generation gate against
that fresh binary:

```sh
cd src
make -j build ARCH=x86-64-bmi2
make -j atomic-unit-tests ARCH=x86-64-bmi2
make -j atomic-api-tests ARCH=x86-64-bmi2
```

```sh
tests/perft.sh src/atomic-stockfish
tests/atomic.sh --protocol-only src/atomic-stockfish
```

`x86-64-bmi2` assumes a CPU with BMI2/PEXT support; `make help` lists the other
architecture targets. `tests/perft.sh` covers Atomic and Atomic960 move
generation, explosions, Atomic check, en passant, promotions, castling rights
and terminal results.

If you touched the bindings, the README has the commands for the Python
extension and for both JavaScript module formats. The full release-oriented
runner is documented in
[`docs/atomic/hito4-validation.md`](docs/atomic/hito4-validation.md).

## Testing for strength

Functional changes are measured with an SPRT on our OpenBench instance at
<https://belzedar.duckdns.org>. Run the short time control first; if it passes,
run the long one on the same branch.

| Stage | Time control | SPRT bounds  |
| ----- | ------------ | ------------ |
| STC   | 8+0.08s      | [0.00, 3.00] |
| LTC   | 40+0.4s      | [0.00, 2.50] |

Link both tests in the pull request. Bench your change with the standard build
and put the number in the commit message. Details on how a worker builds the
engine and which network it uses are in
[`docs/atomic/openbench.md`](docs/atomic/openbench.md).

Non-functional changes — refactors, comments, documentation, build fixes — do
not need a strength test. Say so in the pull request description.

Rules fixes are a separate case. When a change makes the engine agree with the
frozen Fairy-Stockfish reference, an A/B against a base that does not implement
the rule cannot measure anything meaningful. Those changes are accepted on
correctness evidence instead: the perft numbers, the parity run against the
reference, and whatever else shows the new behaviour is the right one. Include
that evidence in the pull request.

## Pull requests

- Open the pull request against `main`.
- CI has to be green before it can be merged.
- Once merged, `main` becomes the new baseline for every later test.

## Code style

C++ changes should follow the style in [`.clang-format`](.clang-format). Running
`make format` from `src` applies it for you.

## License

By contributing you agree that your contributions are licensed under the GNU
General Public License v3.0, the same as the rest of the project. See
[Copying.txt](Copying.txt).
