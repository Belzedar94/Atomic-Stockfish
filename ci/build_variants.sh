#!/usr/bin/env bash
# Builds main and the commits under test, each merged into main, with
# `make profile-build`, one after the other in one checkout of the engine.
#
#   ci/build_variants.sh ENGINE_DIR NETWORK OUT_DIR EXE_SUFFIX [make arguments ...]
#
# ENGINE_DIR is a full clone of the engine (the merge needs the history).
# NETWORK is copied into ENGINE_DIR/src under its default name before every
# build, so the profiling bench evaluates with it (the engine has no other
# evaluation). The binaries land in OUT_DIR as atomic-<variant><EXE_SUFFIX>,
# next to their build logs.
#
# Needs MAIN_SHA, CAND_SHA, COST_SHA, NET_NAME and the expected trees
# TREE_MAIN, TREE_CAND and TREE_COST in the environment. The merge uses a fixed
# identity and date, so a variant is the same commit on every runner, and its
# tree must be the expected one: what is built is main plus the commit, nothing
# else. Node counts are not judged here (ci/engine_check.py does that), so a
# commit that changes the search is still built and reported.
set -euo pipefail

engine=$1 network=$2 out=$3 ext=$4
shift 4
mkdir -p "$out"

export GIT_AUTHOR_NAME=ci GIT_AUTHOR_EMAIL=ci@invalid
export GIT_COMMITTER_NAME=ci GIT_COMMITTER_EMAIL=ci@invalid
export GIT_AUTHOR_DATE=2026-11-01T00:00:00Z GIT_COMMITTER_DATE=2026-11-01T00:00:00Z

g() { git -C "$engine" "$@"; }

# The commits are heads of branches of this repository, which the full clone
# already holds; if one is missing, fetch the pinned commit itself.
for sha in "$CAND_SHA" "$COST_SHA"; do
    g cat-file -e "$sha^{commit}" 2>/dev/null || g fetch --quiet --no-tags origin "$sha"
    g cat-file -e "$sha^{commit}"
done

# name, expected tree, heads merged into main (in this order)
variants=(
    "main $TREE_MAIN"
    "blast $TREE_CAND $CAND_SHA"
    "cost $TREE_COST $COST_SHA"
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
        echo "::error::$name: the profiling run had no network, nothing was profiled"
        exit 1
    fi
    cp "$engine/src/atomic-$name$ext" "$out/atomic-$name$ext"
    (cd "$engine/src" && make clean > /dev/null 2>&1) || true
done

ls -l "$out"
