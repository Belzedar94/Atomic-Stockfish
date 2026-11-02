#!/usr/bin/env bash
# Builds one engine commit twice with `make profile-build`, one build after the
# other in one checkout, with the same compiler and flags; only the training run
# of the profile differs:
#   A  the Makefile's own training run, `bench` (as the AtomicDB worker engines
#      are published, ci/worker-engine-pgo-1102);
#   B  AtomicDB-like searches instead: ci/pgo_load.py over ci/positions/train.tsv.
#
#   ci/build_pgo_pair.sh ENGINE_DIR NETWORK OUT_DIR EXE_SUFFIX [make arguments ...]
#
# The training run is given to make as PGOBENCH, the variable its profile-build
# recipe runs in step 2 (`$(PGOBENCH) > PGOBENCH.out 2>&1`); for both builds it is
# ci/pgo_train.sh, which runs the training and then copies the raw profile to
# OUT_DIR/profile-<A|B>/, because step 4 deletes it. For A it runs exactly the
# Makefile's default, printf 'bench\nquit\n' | ./EXE.
#
# NETWORK is copied into ENGINE_DIR/src under its default name before each build,
# so both training runs evaluate with it (the engine has no other evaluation). The
# binaries land in OUT_DIR as atomic-<A|B><EXE_SUFFIX>, next to their build logs.
#
# Needs ENGINE_SHA, ENGINE_TREE and NET_NAME in the environment (and PY, the
# python of ci/pgo_load.py, where python3 is not it). The checkout must be the
# expected tree: what is built is that commit, nothing else.
set -euo pipefail

engine=$1 network=$2 out=$3 ext=$4
shift 4
ci=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$out"
out=$(cd "$out" && pwd)

g() { git -C "$engine" "$@"; }

g checkout --quiet --force --detach "$ENGINE_SHA"
got=$(g rev-parse "HEAD^{tree}")
if [ "$got" != "$ENGINE_TREE" ]; then
    echo "::error::tree $got, expected $ENGINE_TREE"
    exit 1
fi
echo "== engine: commit $(g rev-parse --short=8 HEAD), tree $got"

for v in A B; do
    g clean -ffdxq
    cp "$network" "$engine/src/$NET_NAME"
    exe="atomic-$v$ext"
    if [ "$v" = A ]; then
        train="bash $ci/pgo_train.sh bench ./$exe $out/profile-$v"
    else
        train="bash $ci/pgo_train.sh load ./$exe $out/profile-$v $ci/positions/train.tsv"
    fi
    echo "== $v: make profile-build EXE=$exe PGOBENCH=\"$train\" $*"
    log="$out/build-$v.log"
    rc=0
    (cd "$engine/src" && make -j4 profile-build EXE="$exe" PGOBENCH="$train" "$@") > "$log" 2>&1 || rc=$?
    grep -E "^Step|pgo_train.sh|Nodes searched|Total time|PGO training|^searches:|llvm-profdata merge|WARNING|FAILED" \
        "$log" || true
    if [ "$rc" -ne 0 ]; then
        tail -n 60 "$log"
        exit "$rc"
    fi
    if grep -q "WARNING: the profiling run found no NNUE network" "$log"; then
        echo "::error::$v: the profiling run had no network, nothing was profiled"
        exit 1
    fi
    grep -q -- "-fprofile-use=stockfish.profdata" "$log"
    cp "$engine/src/$exe" "$out/$exe"
    (cd "$engine/src" && make clean > /dev/null 2>&1) || true
done

ls -l "$out" "$out"/profile-A "$out"/profile-B
