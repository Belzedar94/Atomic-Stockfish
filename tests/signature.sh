#!/bin/bash
# obtain and optionally verify Bench / signature
# if no reference is given, the output is deliberately limited to just the signature

STDOUT_FILE=$(mktemp)
STDERR_FILE=$(mktemp)

error()
{
  echo "running bench for signature failed on line $1"
  echo "===== STDOUT ====="
  cat "$STDOUT_FILE"
  echo "===== STDERR ====="
  cat "$STDERR_FILE"
  rm -f "$STDOUT_FILE" "$STDERR_FILE"
  exit 1
}
trap 'error ${LINENO}' ERR

# Obtain an Atomic-only signature with the frozen Legacy Atomic V1 network.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXE=${EXE:-$REPO_ROOT/src/atomic-stockfish}
NET=${ATOMIC_NNUE_NET:-$REPO_ROOT/../atomic_run3b_e202_l05.nnue}

[[ -x "$EXE" ]] || { echo "missing engine: $EXE" >&2; exit 2; }
[[ -f "$NET" ]] || { echo "missing Atomic NNUE: $NET" >&2; exit 2; }

{
  printf 'setoption name EvalFile value %s\n' "$NET"
  printf 'setoption name Use NNUE value true\n'
  printf 'bench 16 1 13 default depth\n'
  printf 'quit\n'
} | "$EXE" > "$STDOUT_FILE" 2> "$STDERR_FILE" || error ${LINENO}
signature=$(grep "Nodes searched  : " "$STDERR_FILE" | awk '{print $4}')

rm -f "$STDOUT_FILE" "$STDERR_FILE"

if [ $# -gt 0 ]; then
   # compare to given reference
   if [ "$1" != "$signature" ]; then
      if [ -z "$signature" ]; then
         echo "No signature obtained from bench. Code crashed or assert triggered ?"
      else
         echo "signature mismatch: reference $1 obtained: $signature ."
      fi
      exit 1
   else
      echo "signature OK: $signature"
   fi
else
   # just report signature
   echo $signature
fi
