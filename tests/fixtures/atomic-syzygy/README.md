# Atomic Syzygy six-man fixtures

The repository stores only metadata and two small EPD books. The real
`KPPPPvK.atbw/.atbz` files remain external because no separate license for the
published table data was identified. Their exact URLs, byte counts, MD5 values
from the publisher manifests and independently computed SHA-256 values are in
`six-man-fixtures.json`.

That manifest also freezes the KPPPPvK probe position at internal WDL `+2`,
DTZ `1` and the seven legal winning moves returned by the Lichess Atomic API.
The same-checkout C++ driver verifies WDL/DTZ directly; the UCI test separately
verifies the public limit, hit, move and score behavior.

`six-man-endgames.epd` contains five six-man wins selected from Niklas Fiekas'
published Atomic statistics and checked against the Lichess API. Each has one
API winning move. It is both a direct UCI conformance corpus and the challenge
book for the same-binary Syzygy-on versus Syzygy-off OpenBench gate. It does
not replace the normal Atomic opening book.

The complete OpenBench gate also consumes the acquisition artifact
`source-manifests/remote-inventory.json` (SHA-256
`3D4B7FD0AB387F4F60DA2078F612C9E8890E6026F551AEBE8631EFC157788F23`).
It is kept beside the external 220 GiB corpus rather than copied into the
engine repository.
