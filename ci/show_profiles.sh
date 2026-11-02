#!/usr/bin/env bash
# What the two profiles of ci/build_pgo_pair.sh hold: each raw profile it kept is
# merged again with the compiler's own llvm-profdata, as the build's step 3 merged
# it (the merge is deterministic), and the two are summarised and compared.
#
#   ci/show_profiles.sh LLVM_PROFDATA OUT_DIR
#
# Writes OUT_DIR/profile-<A|B>.profdata and OUT_DIR/profiles.txt. Fails if a merge
# fails or if the two merged profiles are the same file.
set -uo pipefail

profdata=$1 out=$2
cd "$out" || exit 1

report() {
    for v in A B; do
        echo "== profile $v"
        echo "training run, as make ran it (its echo of PGOBENCH):"
        grep -m1 "pgo_train.sh" "build-$v.log" | sed 's/^/  /'
        echo "last lines of the training run's output:"
        tail -n 4 "profile-$v/train.log" | sed 's/^/  /'
        echo "the build's merge of the raw profile, and its use:"
        grep -m1 "llvm-profdata merge" "build-$v.log" | sed 's/^/  /'
        echo "  $(grep -c -- "-fprofile-use=stockfish.profdata" "build-$v.log") compile/link lines with -fprofile-use=stockfish.profdata"
        echo "raw profile kept:"
        ls -l profile-$v/*.profraw | sed 's/^/  /'
        "$profdata" merge -output="profile-$v.profdata" profile-$v/*.profraw || return 1
        sha256sum "profile-$v.profdata" | sed 's/^/  /'
        "$profdata" show --detailed-summary "profile-$v.profdata" \
            | grep -E "Total functions|Maximum function count|Maximum internal block count|Total number of blocks|Total count" \
            | sed 's/^/  /'
        echo
    done

    echo "== entry counts of some functions in each profile (mangled-name fragment: functions matched, summed count)"
    # The bench driver, the UCI go command (`bench` never calls it), the thread pool
    # resize, and the search itself.
    for f in UCIEngine5bench Benchmark11setup_bench UCIEngine2go ThreadPool3set Worker19iterative_deepening \
             Worker6search Worker7qsearch Position7do_move; do
        for v in A B; do
            n=$("$profdata" show --function="$f" "profile-$v.profdata" \
                | awk '/Function count:/ {s += $3; k++} END {printf "%d function(s), entry count %d", k, s}')
            echo "  $f in $v: $n"
        done
    done
    echo

    echo "== overlap of A (base) and B (test)"
    "$profdata" overlap profile-A.profdata profile-B.profdata | head -n 40 | sed 's/^/  /'
    echo

    for v in A B; do
        echo "== hottest functions of $v (largest internal block counts)"
        "$profdata" show --topn=12 "profile-$v.profdata" | sed -n '/Top/,$p' | head -n 14 | sed 's/^/  /'
        echo
    done
}

report 2>&1 | tee profiles.txt
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "::error::could not merge a kept raw profile"
    exit 1
fi
a=$(sha256sum < profile-A.profdata | cut -d' ' -f1)
b=$(sha256sum < profile-B.profdata | cut -d' ' -f1)
if [ "$a" = "$b" ]; then
    echo "::error::the two profiles are the same file"
    exit 1
fi
echo "profile-A.profdata and profile-B.profdata differ"
