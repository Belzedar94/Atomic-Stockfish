#!/usr/bin/env bash

set -euo pipefail

usage() {
    echo "usage: $0 VERSION SOURCE_DATE_EPOCH" >&2
    exit 2
}

die() {
    echo "build_atomic_uci_wasm_release: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

[[ $# -eq 2 ]] || usage

version=$1
source_date_epoch=$2

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
    docs/atomic/node-uci-wasm-release.md
    scripts/atomic_verify_uci_wasm_archive.py
    src/atomic_version.h
    tests/wasm-engine/build.py
    tests/wasm-engine/build.ps1
)
for path in "${required_files[@]}"; do
    [[ -f "$path" && ! -L "$path" ]] || die "missing regular release input: $path"
done

require_command python3
require_command em++
require_command install
require_command find
require_command tar
require_command xz
tar --version | head -n 1 | grep -Fq 'GNU tar' \
    || die "UCI WASM release packaging requires GNU tar"

declared_version=$(
    sed -n 's/.*AtomicVersionString = "\([^"]*\)".*/\1/p' src/atomic_version.h
)
[[ -n "$declared_version" && "$declared_version" == "$version" ]] \
    || die "requested version $version differs from atomic_version.h ($declared_version)"

[[ ! -e build ]] || die "build/ already exists; use a clean release root"

export SOURCE_DATE_EPOCH=$source_date_epoch
export LC_ALL=C
export LANG=C
export TZ=UTC
umask 022

unset CXXFLAGS CPPFLAGS LDFLAGS EMCC_CFLAGS EMCC_DEBUG
unset TAR_OPTIONS XZ_DEFAULTS XZ_OPT NODE_OPTIONS

runtime_dir=build/uci-wasm-runtime
stage_parent=build/uci-wasm-stage
release_root="Atomic-Stockfish-$version-node-uci-nnue-wasm"
stage_root="$stage_parent/$release_root"
release_dir=build/release
asset_path="$release_dir/$release_root.tar.xz"

python3 tests/wasm-engine/build.py --out-dir "$runtime_dir"

mkdir -p "$stage_root" "$release_dir"
unexpected_runtime=$(find "$runtime_dir" -mindepth 1 -maxdepth 1 ! -type f -print -quit)
[[ -z "$unexpected_runtime" ]] \
    || die "UCI WASM runtime contains a non-regular entry: $unexpected_runtime"

shopt -s nullglob
runtime_files=("$runtime_dir"/*)
[[ ${#runtime_files[@]} -gt 0 ]] || die "UCI WASM build produced no runtime files"
for runtime_file in "${runtime_files[@]}"; do
    [[ -f "$runtime_file" && ! -L "$runtime_file" ]] \
        || die "UCI WASM runtime entry is not one regular file: $runtime_file"
    install -m 0644 "$runtime_file" "$stage_root/$(basename -- "$runtime_file")"
done

install -m 0644 docs/atomic/node-uci-wasm-release.md "$stage_root/README.md"
install -m 0644 AUTHORS CITATION.cff Copying.txt "$stage_root/"

tar --sort=name --format=gnu --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 --group=0 --numeric-owner \
    -C "$stage_parent" -cf - "$release_root" \
    | xz --threads=1 -9e --check=crc64 > "$asset_path"

[[ -s "$asset_path" && ! -L "$asset_path" ]] \
    || die "UCI WASM archive was not produced as one non-empty regular file"

python3 scripts/atomic_verify_uci_wasm_archive.py \
    --archive "$asset_path" \
    --version "$version" \
    --source-date-epoch "$source_date_epoch" \
    --readme docs/atomic/node-uci-wasm-release.md

release_outputs=("$release_dir"/*)
[[ ${#release_outputs[@]} -eq 1 && "${release_outputs[0]}" == "$asset_path" ]] \
    || die "UCI WASM recipe produced an unexpected output set"

printf '%s\n' "$asset_path"
