#!/usr/bin/env bash

set -euo pipefail

usage() {
    echo "usage: $0 PLATFORM ARCH VERSION SOURCE_DATE_EPOCH" >&2
    echo "  PLATFORM: linux | windows" >&2
    echo "  ARCH: x86-64 | x86-64-avx2 | x86-64-bmi2" >&2
    exit 2
}

die() {
    echo "build_atomic_native_release: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

[[ $# -eq 4 ]] || usage

platform=$1
architecture=$2
version=$3
source_date_epoch=$4

case "$platform" in
    linux | windows) ;;
    *) usage ;;
esac

case "$architecture" in
    x86-64 | x86-64-avx2 | x86-64-bmi2) ;;
    *) usage ;;
esac

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "VERSION must be a semantic x.y.z version"
[[ "$source_date_epoch" =~ ^(0|[1-9][0-9]*)$ ]] \
    || die "SOURCE_DATE_EPOCH must be canonical non-negative decimal"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
[[ "$(pwd -P)" == "$repo_root" ]] \
    || die "run this recipe from the repository root: $repo_root"

required_files=(
    AUTHORS
    CITATION.cff
    Copying.txt
    README.md
    docs/atomic/release-1.0-notes.md
    src/Makefile
    src/atomic_version.h
)
for path in "${required_files[@]}"; do
    [[ -f "$path" && ! -L "$path" ]] || die "missing regular release input: $path"
done

declared_version=$(
    sed -n 's/.*AtomicVersionString = "\([^"]*\)".*/\1/p' src/atomic_version.h
)
[[ -n "$declared_version" && "$declared_version" == "$version" ]] \
    || die "requested version $version differs from atomic_version.h ($declared_version)"

require_command make
require_command install
require_command find
require_command touch

[[ ! -e build ]] || die "build/ already exists; use a clean release root"
dirty_native=$(find src -maxdepth 1 \( -type f -o -type l \) \
    \( -name '*.o' -o -name '.build_*.txt' -o -name 'atomic-stockfish' \
       -o -name 'atomic-stockfish.exe' \) -print -quit)
[[ -z "$dirty_native" ]] || die "native build product already exists: $dirty_native"

export SOURCE_DATE_EPOCH=$source_date_epoch
export LC_ALL=C
export LANG=C
export TZ=UTC
umask 022

# Do not allow caller-provided build or archive flags to alter release bytes.
unset CXXFLAGS CPPFLAGS LDFLAGS DEPENDFLAGS EXTRACXXFLAGS EXTRALDFLAGS
unset MAKEFLAGS MFLAGS TAR_OPTIONS XZ_DEFAULTS XZ_OPT ZIPOPT

if [[ "$platform" == linux ]]; then
    require_command g++
    require_command tar
    require_command xz
    [[ "$(g++ -dumpmachine)" == x86_64-linux-gnu ]] \
        || die "Linux release compiler does not target x86_64-linux-gnu"
    tar --version | head -n 1 | grep -Fq 'GNU tar' \
        || die "Linux release packaging requires GNU tar"

    make -C src -j2 ARCH="$architecture" COMP=gcc \
        GIT_SHA= GIT_SHA_FULL= GIT_DATE= build
    engine=src/atomic-stockfish
    extension=tar.xz
else
    require_command cmake
    [[ -n "${CXX:-}" ]] || die "CXX must name the pinned MinGW compiler"
    [[ "$CXX" == /* && -x "$CXX" ]] \
        || die "CXX must be an executable absolute path: $CXX"
    [[ "$("$CXX" -dumpmachine)" == x86_64-w64-mingw32 ]] \
        || die "MinGW release compiler does not target x86_64-w64-mingw32"
    if [[ ${#source_date_epoch} -gt 10 ]] \
        || (( 10#$source_date_epoch < 315532800 )); then
        die "Windows ZIP epoch must be representable on or after 1980-01-01"
    fi

    make -C src -j2 ARCH="$architecture" COMP=mingw COMPCXX="$CXX" \
        GIT_SHA= GIT_SHA_FULL= GIT_DATE= build
    engine=src/atomic-stockfish.exe
    extension=zip
fi

[[ -f "$engine" && ! -L "$engine" ]] \
    || die "native engine was not produced as one regular file: $engine"

release_root="Atomic-Stockfish-$version"
stage_parent=build/release-stage
stage_root="$stage_parent/$release_root"
release_dir=build/release
asset_name="Atomic-Stockfish-$version-$platform-$architecture.$extension"
asset_path="$release_dir/$asset_name"

mkdir -p "$stage_root" "$release_dir"
install -m 0755 "$engine" \
    "$stage_root/$(basename -- "$engine")"
install -m 0644 AUTHORS CITATION.cff Copying.txt README.md "$stage_root/"
install -m 0644 docs/atomic/release-1.0-notes.md \
    "$stage_root/RELEASE_NOTES.md"

if [[ "$platform" == linux ]]; then
    tar --sort=name --format=gnu --mtime="@$SOURCE_DATE_EPOCH" \
        --owner=0 --group=0 --numeric-owner \
        -C "$stage_parent" -cf - "$release_root" \
        | xz --threads=1 -9e --check=crc64 > "$asset_path"
else
    find "$stage_root" -exec touch -d "@$SOURCE_DATE_EPOCH" {} +
    (
        cd "$stage_parent"
        cmake -E tar cf "../release/$asset_name" --format=zip "$release_root"
    )
fi

[[ -s "$asset_path" && ! -L "$asset_path" ]] \
    || die "release archive was not produced as one non-empty regular file"

shopt -s nullglob
release_outputs=("$release_dir"/*)
[[ ${#release_outputs[@]} -eq 1 && "${release_outputs[0]}" == "$asset_path" ]] \
    || die "release recipe produced an unexpected output set"

printf '%s\n' "$asset_path"
