#!/usr/bin/env bash
# Builds main and the pull requests under test, each merged into main, with
# `make profile-build`, one after the other in one checkout of the engine.
#
#   ci/build_variants.sh ENGINE_DIR NETWORK OUT_DIR EXE_SUFFIX [make arguments ...]
#
# ENGINE_DIR is a full clone of the engine (the merges need the history).
# NETWORK is copied into ENGINE_DIR/src under its default name before every
# build, so the profiling run evaluates with NNUE. The binaries land in OUT_DIR
# as atomic-<variant><EXE_SUFFIX>, next to their build logs.
#
# Needs MAIN_SHA, PR67_SHA, PR68_SHA, PR70_SHA, NET_NAME and the expected trees
# TREE_MAIN, TREE_PR67, TREE_PR68, TREE_PR70 and TREE_ALL in the environment.
# The merges use a fixed identity and date, so a variant is the same commit on
# every runner, and its tree must be the expected one: what is built is main
# plus the pull request(s), nothing else. Node counts are not judged here
# (ci/engine_check.py does that), so a pull request that changes the search is
# still built and reported.
set -euo pipefail

engine=$1 network=$2 out=$3 ext=$4
shift 4
mkdir -p "$out"

export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@invalid
export GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@invalid
export GIT_AUTHOR_DATE=2026-10-19T00:00:00Z GIT_COMMITTER_DATE=2026-10-19T00:00:00Z

g() { git -C "$engine" "$@"; }

# The pull request heads live in a fork; the base repository serves them as
# refs/pull/<n>/head. If a head moved since, fetch the pinned commit itself.
g fetch --quiet --no-tags origin refs/pull/67/head refs/pull/68/head refs/pull/70/head
for sha in "$PR67_SHA" "$PR68_SHA" "$PR70_SHA"; do
    g cat-file -e "$sha^{commit}" 2>/dev/null || g fetch --quiet --no-tags origin "$sha"
    g cat-file -e "$sha^{commit}"
done

# name, expected tree, heads merged into main (in this order)
variants=(
    "main $TREE_MAIN"
    "pr67 $TREE_PR67 $PR67_SHA"
    "pr68 $TREE_PR68 $PR68_SHA"
    "pr70 $TREE_PR70 $PR70_SHA"
    "all3 $TREE_ALL $PR67_SHA $PR68_SHA $PR70_SHA"
)

for v in "${variants[@]}"; do
    read -r name tree heads <<< "$v"
    g checkout --quiet --force --detach "$MAIN_SHA"
    g clean -ffdxq
    for sha in $heads; do
        g merge --quiet --no-ff --no-edit -m "Merge ${sha:0:8} into ${MAIN_SHA:0:8} (measurement only)" "$sha"
    done
    got=$(g rev-parse "HEAD^{tree}")
    if [ "$got" != "$tree" ]; then
        echo "::error::$name: tree $got, expected $tree"
        exit 1
    fi
    echo "== $name: commit $(g rev-parse --short=8 HEAD), tree $got"

    cp "$network" "$engine/src/$NET_NAME"
    log="$out/build-$name.log"
    rc=0
    (cd "$engine/src" && make -j4 profile-build EXE="atomic-$name$ext" "$@") > "$log" 2>&1 || rc=$?
    grep -E "^Step|Nodes searched|WARNING" "$log" || true
    if [ "$rc" -ne 0 ]; then
        tail -n 60 "$log"
        exit "$rc"
    fi
    if grep -q "WARNING: the profiling run found no NNUE network" "$log"; then
        echo "::error::$name: the profiling run had no network, NNUE was not profiled"
        exit 1
    fi
    cp "$engine/src/atomic-$name$ext" "$out/atomic-$name$ext"
    (cd "$engine/src" && make clean > /dev/null 2>&1) || true
done

ls -l "$out"
