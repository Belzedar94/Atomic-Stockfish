#!/bin/sh
# Build one final source artifact from an exact commit inside a pinned image.
set -eu

usage() {
    echo "usage: $0 source|sdist VERSION SOURCE_DATE_EPOCH COMMIT OUTPUT_DIR" >&2
    exit 2
}

[ "$#" -eq 5 ] || usage
kind=$1
version=$2
epoch=$3
commit=$4
output_dir=$5

case "$kind" in
    source|sdist) ;;
    *) usage ;;
esac
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || usage
case "$epoch" in
    ''|*[!0-9]*) usage ;;
esac
printf '%s\n' "$commit" | grep -Eq '^[0-9a-f]{40}$' || usage

export SOURCE_DATE_EPOCH=$epoch
export PYTHONHASHSEED=0
export PYTHONNOUSERSITE=1

mkdir -p "$output_dir"
test -d "$output_dir"
commit_type=$(git -c safe.directory="$(pwd)" cat-file -t "$commit")
test "$commit_type" = commit
test "$(git -c safe.directory="$(pwd)" rev-parse "$commit^{commit}")" = "$commit"

if [ "$kind" = source ]; then
    asset="Atomic-Stockfish-$version-source.tar.xz"
    test ! -e "$output_dir/$asset"
    git -c safe.directory="$(pwd)" archive \
        --format=tar --prefix="Atomic-Stockfish-$version/" "$commit" |
        xz -9e > "$output_dir/$asset"
    test -s "$output_dir/$asset"
    exit 0
fi

asset="atomic_pyffish-$version.tar.gz"
test ! -e "$output_dir/$asset"
temporary=$(mktemp -d "${TMPDIR:-/tmp}/atomic-sdist.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
source_root="$temporary/source"
raw_dist="$temporary/raw-dist"
dependencies="$temporary/build-dependencies"
mkdir -p "$source_root" "$raw_dist" "$dependencies"

git -c safe.directory="$(pwd)" archive "$commit" | tar -x -C "$source_root"
python3 -m pip install --disable-pip-version-check --no-cache-dir \
    --only-binary=:all: --require-hashes --target "$dependencies" \
    -r "$source_root/tests/release-build-requirements.txt"
(
    cd "$source_root"
    PYTHONPATH="$dependencies" python3 setup.py sdist --dist-dir "$raw_dist"
)
set -- "$raw_dist"/atomic_pyffish-*.tar.gz
[ "$#" -eq 1 ]
[ -f "$1" ]
PYTHONPATH="$dependencies" python3 \
    "$source_root/scripts/atomic_reproducible_sdist.py" \
    --source "$1" --destination "$output_dir/$asset" \
    --root "atomic_pyffish-$version" --source-date-epoch "$epoch"
test -s "$output_dir/$asset"
