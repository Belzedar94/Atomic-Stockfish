# ADR 0002: Preserve structurally valid Atomic analysis FENs

- Status: accepted; implemented
- Date: 2026-07-11

## Context

Fairy-Stockfish accepts structurally valid Atomic FENs in which the side to
move attacks the opposing king. Such a position need not represent the next
state of an orthodox legal-game history, but it is a valid analysis input and
is required by fixtures for direct king captures, adjacent explosions, and
other terminal Atomic transitions.

For example, both `4k3/8/8/1B6/8/8/8/4K3 w - - 0 1` and the direct-capture
fixture `7k/7R/8/8/8/8/8/K7 w - - 0 1` remain valid analysis inputs. The
latter is required to test the terminal move `h7h8` through every public
surface.

## Decision

`validate_fen` validates the FEN encoding and Atomic board structure; it does
not perform a retrograde reachability test. It therefore preserves these
analysis positions when all other constraints are valid. Move legality and
terminal outcome remain the responsibility of the normal Atomic rule APIs.

Structurally invalid inputs remain rejected, including malformed fields or
board geometry, unsupported pieces, pawns on a back rank, duplicate kings or
both kings missing, impossible material counts, invalid side-to-move,
inconsistent castling or en-passant data, and invalid move counters. A single
missing king remains representable only as an Atomic terminal-explosion state.

## Consequences

The validator remains compatible with Fairy fixtures and Atomic analysis
workflows without weakening syntax or board-integrity checks. Callers that
need proof of historical reachability must enforce that separate policy
outside `validate_fen`.
