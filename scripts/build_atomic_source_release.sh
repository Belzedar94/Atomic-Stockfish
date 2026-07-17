#!/bin/sh
# Build one final source artifact from a clean exact-commit source root.
set -eu

usage() {
    echo "usage: $0 source|sdist VERSION SOURCE_DATE_EPOCH" >&2
    exit 2
}

die() {
    echo "build_atomic_source_release: $*" >&2
    exit 1
}

[ "$#" -eq 3 ] || usage
kind=$1
version=$2
epoch=$3
case "$kind" in
    source|sdist) ;;
    *) usage ;;
esac
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || usage
case "$epoch" in
    ''|*[!0-9]*) usage ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
[ "$(pwd -P)" = "$repo_root" ] || die "run this recipe from the repository root"
[ ! -e .git ] || die "release recipe requires an exported source root without .git"
[ ! -e build ] || die "build/ already exists; use a clean release root"
for path in AUTHORS CITATION.cff Copying.txt README.md MANIFEST.in \
    setup.py pyproject.toml src/atomic_version.h \
    scripts/atomic_reproducible_sdist.py \
    tests/release-build-requirements.txt; do
    [ -f "$path" ] && [ ! -L "$path" ] || die "missing regular release input: $path"
done

declared_version=$(sed -n \
    's/.*AtomicVersionString = "\([^"]*\)".*/\1/p' src/atomic_version.h)
[ "$declared_version" = "$version" ] || \
    die "requested version differs from atomic_version.h"

export SOURCE_DATE_EPOCH=$epoch
export PYTHONHASHSEED=0
export PYTHONNOUSERSITE=1
export LC_ALL=C
export LANG=C
export TZ=UTC
umask 022
unset TAR_OPTIONS XZ_DEFAULTS XZ_OPT GZIP PYTHONPATH PYTHONHOME

temporary=$(mktemp -d "${TMPDIR:-/tmp}/atomic-source-release.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
release_dir=build/release

if [ "$kind" = source ]; then
    command -v cp >/dev/null 2>&1 || die "cp is unavailable"
    command -v tar >/dev/null 2>&1 || die "tar is unavailable"
    command -v xz >/dev/null 2>&1 || die "xz is unavailable"
    tar --version | head -n 1 | grep -Fq 'GNU tar' || \
        die "source packaging requires GNU tar"
    release_root="Atomic-Stockfish-$version"
    stage_parent="$temporary/stage"
    stage_root="$stage_parent/$release_root"
    mkdir -p "$stage_root"
    cp -a ./. "$stage_root/"
    mkdir -p "$release_dir"
    asset="$release_dir/$release_root-source.tar.xz"
    tar --sort=name --format=gnu --mtime="@$SOURCE_DATE_EPOCH" \
        --owner=0 --group=0 --numeric-owner \
        -C "$stage_parent" -cf - "$release_root" |
        xz --threads=1 -9e --check=crc64 > "$asset"
else
    command -v python3 >/dev/null 2>&1 || die "python3 is unavailable"
    raw_dist="$temporary/raw-dist"
    dependencies="$temporary/build-dependencies"
    mkdir -p "$raw_dist" "$dependencies" "$release_dir"
    python3 -m pip install --disable-pip-version-check --no-cache-dir \
        --force-reinstall --no-deps --only-binary=:all: --require-hashes \
        --target "$dependencies" \
        -r tests/release-build-requirements.txt
    PYTHONPATH="$dependencies" python3 setup.py sdist --dist-dir "$raw_dist"
    set -- "$raw_dist"/atomic_pyffish-*.tar.gz
    [ "$#" -eq 1 ] && [ -f "$1" ] || die "setuptools produced an invalid sdist set"
    asset="$release_dir/atomic_pyffish-$version.tar.gz"
    PYTHONPATH="$dependencies" python3 scripts/atomic_reproducible_sdist.py \
        --source "$1" --destination "$asset" \
        --root "atomic_pyffish-$version" --source-date-epoch "$epoch"
fi

[ -s "$asset" ] && [ ! -L "$asset" ] || die "release artifact is not regular"
set -- "$release_dir"/*
[ "$#" -eq 1 ] && [ "$1" = "$asset" ] || die "unexpected release output set"
printf '%s\n' "$asset"
