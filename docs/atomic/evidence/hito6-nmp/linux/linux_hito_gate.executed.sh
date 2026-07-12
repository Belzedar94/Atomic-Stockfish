#!/usr/bin/env bash

set -Eeuo pipefail

required=(
  ATOMIC_GATE_JOB
  ATOMIC_GATE_HEAD
  ATOMIC_GATE_SHORT_SHA
  ATOMIC_GATE_GIT_DATE
  ATOMIC_GATE_EXPECTED_NET
  ATOMIC_GATE_COMP
  ATOMIC_GATE_CXX
  ATOMIC_GATE_MODE
  SOURCE_DATE_EPOCH
)

workdir="${ATOMIC_GATE_WORKDIR:-/work}"
net="${ATOMIC_GATE_NET:-/fixtures/atomic.nnue}"
tables="${ATOMIC_GATE_TABLES:-/fixtures/tables}"

if [[ -n "${ATOMIC_GATE_LOG:-}" ]]; then
  if [[ "$ATOMIC_GATE_LOG" != /* ]]; then
    printf 'ATOMIC_GATE_LOG must be an absolute path: %s\n' "$ATOMIC_GATE_LOG" >&2
    exit 2
  fi
  exec >"$ATOMIC_GATE_LOG" 2>&1
fi

trap 'rc=$?; printf "\ngate_exit_code=%s\n" "$rc"; exit "$rc"' EXIT

for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf 'missing required environment variable: %s\n' "$name" >&2
    exit 2
  fi
done

if [[ ! "$ATOMIC_GATE_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'ATOMIC_GATE_HEAD must be a full lowercase commit SHA: %s\n' \
    "$ATOMIC_GATE_HEAD" >&2
  exit 2
fi
if [[ ! "$ATOMIC_GATE_SHORT_SHA" =~ ^[0-9a-f]{7,40}$ ]] \
  || [[ "${ATOMIC_GATE_HEAD:0:${#ATOMIC_GATE_SHORT_SHA}}" != "$ATOMIC_GATE_SHORT_SHA" ]]; then
  printf 'ATOMIC_GATE_SHORT_SHA must prefix ATOMIC_GATE_HEAD: %s vs %s\n' \
    "$ATOMIC_GATE_SHORT_SHA" "$ATOMIC_GATE_HEAD" >&2
  exit 2
fi
if [[ ! "$ATOMIC_GATE_GIT_DATE" =~ ^[0-9]{8}$ ]]; then
  printf 'ATOMIC_GATE_GIT_DATE must use YYYYMMDD: %s\n' "$ATOMIC_GATE_GIT_DATE" >&2
  exit 2
fi
if [[ ! "$ATOMIC_GATE_EXPECTED_NET" =~ ^[0-9a-fA-F]{64}$ ]]; then
  printf 'ATOMIC_GATE_EXPECTED_NET must be a SHA-256 digest\n' >&2
  exit 2
fi
if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
  printf 'SOURCE_DATE_EPOCH must be a non-negative integer: %s\n' \
    "$SOURCE_DATE_EPOCH" >&2
  exit 2
fi

export LC_ALL=C
export TZ=UTC
umask 022

cd "$workdir"

printf 'job=%s\nhead=%s\nmode=%s\ncomp=%s\ncxx=%s\nsource_date_epoch=%s\n' \
  "$ATOMIC_GATE_JOB" "$ATOMIC_GATE_HEAD" "$ATOMIC_GATE_MODE" \
  "$ATOMIC_GATE_COMP" "$ATOMIC_GATE_CXX" "$SOURCE_DATE_EPOCH"
cat /etc/os-release
uname -a
"$ATOMIC_GATE_CXX" --version
python3 --version
make --version

printf '%s  %s\n' "$ATOMIC_GATE_EXPECTED_NET" "$net" \
  | sha256sum --check --strict

common=(
  ARCH=x86-64
  "COMP=$ATOMIC_GATE_COMP"
  "COMPCXX=$ATOMIC_GATE_CXX"
  "GIT_SHA=$ATOMIC_GATE_SHORT_SHA"
  "GIT_DATE=$ATOMIC_GATE_GIT_DATE"
)
config=()
if [[ "$ATOMIC_GATE_MODE" == debug ]]; then
  config=(debug=yes optimize=no)
elif [[ "$ATOMIC_GATE_MODE" != release ]]; then
  printf 'unsupported gate mode: %s\n' "$ATOMIC_GATE_MODE" >&2
  exit 2
fi

make -C src "${common[@]}" "${config[@]}" objclean
make -C src -j2 "${common[@]}" "${config[@]}" build
make -C src -j2 "${common[@]}" "${config[@]}" atomic-unit-tests
make -C src -j2 "${common[@]}" "${config[@]}" atomic-api-tests
make -C src -j2 "${common[@]}" "${config[@]}" atomic-syzygy-driver

test "$(cat src/.build_sha.txt)" = "$ATOMIC_GATE_SHORT_SHA"
test "$(cat src/.build_date.txt)" = "$ATOMIC_GATE_GIT_DATE"

bash tests/perft.sh src/atomic-stockfish

python3 tests/atomic_search.py \
  --candidate src/atomic-stockfish \
  --use-nnue false \
  --timeout 60

python3 tests/atomic_search.py \
  --candidate src/atomic-stockfish \
  --eval-file "$net" \
  --use-nnue true \
  --timeout 60

python3 tests/atomic_syzygy.py \
  --driver src/atomic-syzygy-driver.bin \
  --tables "$tables"

python3 tests/atomic_syzygy_uci.py \
  --engine src/atomic-stockfish \
  --tables "$tables" \
  --eval-file "$net" \
  --timeout 60

python3 tests/xboard_protocol.py \
  --candidate src/atomic-stockfish \
  --timeout 60

printf '\nartifact hashes and sizes:\n'
sha256sum \
  src/atomic-stockfish \
  src/atomic-unit-tests.bin \
  src/atomic-api-tests.bin \
  src/atomic-syzygy-driver.bin
stat -c '%n|%s' \
  src/atomic-stockfish \
  src/atomic-unit-tests.bin \
  src/atomic-api-tests.bin \
  src/atomic-syzygy-driver.bin
