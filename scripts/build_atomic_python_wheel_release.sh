#!/usr/bin/env bash
# Build one authoritative Atomic pyffish wheel from an authenticated sdist.
set -euo pipefail
IFS=$'\n\t'

readonly RECIPE_VERSION=1
readonly MANYLINUX_X86_64_IMAGE='quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae'
readonly WINDOWS_WHEEL_FINGERPRINT_DOCUMENT='docs/atomic/windows-wheel-fingerprint-v2.json'

usage() {
    echo "usage: $0 PLATFORM SDIST OUTPUT_DIR CACHE_DIR VERSION SOURCE_DATE_EPOCH FINGERPRINT_OUTPUT EXPECTED_FINGERPRINT_SHA256" >&2
    exit 2
}

die() {
    echo "build_atomic_python_wheel_release: $*" >&2
    exit 1
}

canonical_existing_file() {
    local requested=$1
    local label=$2
    local parent leaf parent_real
    [ -f "$requested" ] && [ ! -L "$requested" ] && [ -s "$requested" ] || \
        die "$label must be a nonempty regular file: $requested"
    parent=$(dirname -- "$requested")
    leaf=$(basename -- "$requested")
    parent_real=$(CDPATH= cd -- "$parent" && pwd -P) || \
        die "cannot resolve $label parent: $parent"
    printf '%s/%s\n' "$parent_real" "$leaf"
}

canonical_new_directory() {
    local requested=$1
    local label=$2
    local parent leaf parent_real
    [ -n "$requested" ] || die "$label path is empty"
    [ ! -e "$requested" ] && [ ! -L "$requested" ] || \
        die "$label already exists: $requested"
    parent=$(dirname -- "$requested")
    leaf=$(basename -- "$requested")
    [ -n "$leaf" ] && [ "$leaf" != . ] && [ "$leaf" != .. ] || \
        die "$label has an invalid final component: $requested"
    [ -d "$parent" ] && [ ! -L "$parent" ] || \
        die "$label parent must be an existing regular directory: $parent"
    parent_real=$(CDPATH= cd -- "$parent" && pwd -P) || \
        die "cannot resolve $label parent: $parent"
    printf '%s/%s\n' "$parent_real" "$leaf"
}

is_absolute_fingerprint_path() {
    local requested=$1
    [[ "$requested" == /* || "$requested" =~ ^[A-Za-z]:[/\\] ]]
}

[ "$#" -eq 8 ] || usage
platform=$1
sdist=$2
output_dir=$3
cache_dir=$4
version=$5
epoch=$6
fingerprint_output=$7
expected_fingerprint_sha256=$8

case "$platform" in
    linux|windows) ;;
    *) usage ;;
esac
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || usage
[[ "$epoch" =~ ^(0|[1-9][0-9]*)$ ]] || usage

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
[ "$(pwd -P)" = "$repo_root" ] || die "run this recipe from the repository root"

for required in \
    "$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT" \
    scripts/atomic_windows_wheel_fingerprint.py \
    tests/release-build-requirements.txt \
    tests/release-wheel-test-requirements.txt; do
    [ -f "$required" ] && [ ! -L "$required" ] && [ -s "$required" ] || \
        die "missing regular release input: $required"
done

command -v python >/dev/null 2>&1 || die "python is unavailable"

sdist_absolute=$(canonical_existing_file "$sdist" "sdist")
[ "$(basename -- "$sdist_absolute")" = "atomic_pyffish-$version.tar.gz" ] || \
    die "sdist filename does not match requested version $version"
output_absolute=$(canonical_new_directory "$output_dir" "output directory")
cache_absolute=$(canonical_new_directory "$cache_dir" "cache directory")
[ "$output_absolute" != "$cache_absolute" ] || die "output and cache directories must differ"
[[ "$output_absolute" != "$cache_absolute"/* ]] || \
    die "output directory must not be inside the cache directory"
[[ "$cache_absolute" != "$output_absolute"/* ]] || \
    die "cache directory must not be inside the output directory"

case "$platform" in
    linux)
        [ "$fingerprint_output" = none ] || \
            die "Linux FINGERPRINT_OUTPUT must be 'none'"
        [ "$expected_fingerprint_sha256" = none ] || \
            die "Linux EXPECTED_FINGERPRINT_SHA256 must be 'none'"
        ;;
    windows)
        is_absolute_fingerprint_path "$fingerprint_output" || \
            die "Windows FINGERPRINT_OUTPUT must be absolute"
        [[ "$expected_fingerprint_sha256" =~ ^[0-9A-Fa-f]{64}$ ]] || \
            die "Windows EXPECTED_FINGERPRINT_SHA256 must be 64 hexadecimal characters"
        [[ "$fingerprint_output" != *'"'* ]] || \
            die "Windows FINGERPRINT_OUTPUT cannot contain a double quote"
        normalized_expected=$(printf '%s' "$expected_fingerprint_sha256" | tr 'A-F' 'a-f')
        frozen_fingerprint_sha256=$(python -I -c \
            'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
            "$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT") || \
            die "cannot hash frozen Windows wheel fingerprint document"
        [ "$frozen_fingerprint_sha256" = "$normalized_expected" ] || \
            die "Windows expected fingerprint SHA-256 does not match the frozen document"
        ;;
esac

mkdir -- "$output_absolute"
mkdir -- "$cache_absolute"
[ -d "$output_absolute" ] && [ ! -L "$output_absolute" ] || \
    die "output directory was not created safely"
[ -d "$cache_absolute" ] && [ ! -L "$cache_absolute" ] || \
    die "cache directory was not created safely"

sdist_for_cibuildwheel=$sdist_absolute
output_for_cibuildwheel=$output_absolute
cache_for_cibuildwheel=$cache_absolute
fingerprint_for_cibuildwheel=$fingerprint_output
fingerprint_for_host=$fingerprint_output
if [ "$platform" = windows ] && command -v cygpath >/dev/null 2>&1; then
    sdist_for_cibuildwheel=$(cygpath -w -- "$sdist_absolute")
    output_for_cibuildwheel=$(cygpath -w -- "$output_absolute")
    cache_for_cibuildwheel=$(cygpath -w -- "$cache_absolute")
    fingerprint_for_cibuildwheel=$(cygpath -w -- "$fingerprint_output")
    fingerprint_for_host=$(cygpath -u -- "$fingerprint_for_cibuildwheel")
fi
if [ "$platform" = windows ]; then
    [ ! -e "$fingerprint_for_host" ] && [ ! -L "$fingerprint_for_host" ] || \
        die "Windows fingerprint output already exists: $fingerprint_output"
fi

# Platform-specific CIBW_* variables can silently override generic values.
# Clear every inherited cibuildwheel setting before constructing this recipe.
while IFS= read -r inherited_cibw; do
    unset "$inherited_cibw"
done < <(compgen -A variable CIBW_ || true)

export SOURCE_DATE_EPOCH=$epoch
export PYTHONHASHSEED=0
export PYTHONNOUSERSITE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1
export LC_ALL=C
export LANG=C
export TZ=UTC
umask 022
unset PYTHONHOME PYTHONPATH

export CIBW_BUILD='cp39-*'
export CIBW_ENVIRONMENT="SOURCE_DATE_EPOCH=$epoch PYTHONHASHSEED=0"
export CIBW_BUILD_FRONTEND='pip; args: --no-build-isolation'
export CIBW_CACHE_PATH=$cache_for_cibuildwheel
export CIBW_BEFORE_BUILD='python -m pip install --disable-pip-version-check --force-reinstall --no-deps --only-binary=:all: --require-hashes -r "{project}/tests/release-build-requirements.txt"'
export CIBW_BEFORE_TEST='python -m pip install --disable-pip-version-check --force-reinstall --no-deps --only-binary=:all: --require-hashes -r "{project}/tests/release-wheel-test-requirements.txt"'

version_major=${version%%.*}
version_tail=${version#*.}
version_minor=${version_tail%%.*}
version_patch=${version##*.}
export CIBW_TEST_COMMAND="python -c \"import pyffish; assert pyffish.version() == ($version_major, $version_minor, $version_patch); assert pyffish.variants() == ['atomic']; assert pyffish.perft('atomic', pyffish.start_fen('atomic'), 1) == 20; assert pyffish.info().startswith('Atomic-Stockfish $version ')\" && python -m mypy -m pyffish --no-incremental --no-error-summary"

case "$platform" in
    linux)
        export CIBW_ARCHS=x86_64
        export CIBW_MANYLINUX_X86_64_IMAGE=$MANYLINUX_X86_64_IMAGE
        [ "$CIBW_MANYLINUX_X86_64_IMAGE" = "$MANYLINUX_X86_64_IMAGE" ] || \
            die "manylinux image pin changed unexpectedly"
        ;;
    windows)
        export CIBW_ARCHS=AMD64
        fingerprint_command="python \"{project}/scripts/atomic_windows_wheel_fingerprint.py\" --output \"$fingerprint_for_cibuildwheel\""
        export CIBW_BEFORE_BUILD="$CIBW_BEFORE_BUILD && $fingerprint_command"
        ;;
esac

python -m cibuildwheel "$sdist_for_cibuildwheel" --output-dir "$output_for_cibuildwheel"

shopt -s nullglob
wheels=("$output_absolute"/*.whl)
[ "${#wheels[@]}" -eq 1 ] || \
    die "expected exactly one wheel output, found ${#wheels[@]}"
[ -f "${wheels[0]}" ] && [ ! -L "${wheels[0]}" ] && [ -s "${wheels[0]}" ] || \
    die "wheel output is not a nonempty regular file"
if [ "$platform" = windows ]; then
    [ -f "$fingerprint_for_host" ] && [ ! -L "$fingerprint_for_host" ] && \
        [ -s "$fingerprint_for_host" ] || \
        die "Windows build did not create its toolchain fingerprint"
    actual_fingerprint_sha256=$(python -I -c \
        'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
        "$fingerprint_for_host") || \
        die "cannot hash actual Windows wheel fingerprint"
    [ "$actual_fingerprint_sha256" = "$normalized_expected" ] || \
        die "actual Windows wheel fingerprint does not match the frozen document: $actual_fingerprint_sha256"
    cmp -s -- "$fingerprint_for_host" "$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT" || \
        die "actual Windows wheel fingerprint bytes do not match the frozen document"
fi

printf '%s\n' "${wheels[0]}"
