#!/usr/bin/env bash
# The training run of `make profile-build`, given to it as PGOBENCH, plus a copy of
# the raw profile it leaves, which the build deletes once it has used it.
#
#   PGOBENCH="bash ci/pgo_train.sh bench ./EXE ARCHIVE_DIR"
#       the Makefile's own training run, unchanged: printf 'bench\nquit\n' | ./EXE
#   PGOBENCH="bash ci/pgo_train.sh load ./EXE ARCHIVE_DIR POSITIONS_TSV"
#       the AtomicDB-like searches of ci/pgo_load.py instead (python from $PY)
#
# Make runs it in src/ with stdout and stderr going to PGOBENCH.out, and prints the
# last four lines of that file; nothing is printed after the training run, so those
# lines are still the run's own summary. The instrumented binary writes its raw
# profile (default_<signature>.profraw) into src/ when it exits, where the
# Makefile's llvm-profdata merge reads it in the next step. A copy goes to
# ARCHIVE_DIR with the output of the run, so the profile can be shown and compared
# after the build.
set -euo pipefail

mode=$1 exe=$2 archive=$3
ci=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$archive"

case "$mode" in
    bench)
        printf 'bench\nquit\n' | "$exe" 2>&1 | tee "$archive/train.log"
        ;;
    load)
        "${PY:-python3}" "$ci/pgo_load.py" --engine "$exe" --positions "$4" \
            --log "$archive/train-uci.log" 2>&1 | tee "$archive/train.log"
        ;;
    *)
        echo "pgo_train.sh: unknown mode $mode" >&2
        exit 2
        ;;
esac

# no raw profile, no profile-guided build: fail here rather than in the merge
ls ./*.profraw > /dev/null
cp ./*.profraw "$archive/"
