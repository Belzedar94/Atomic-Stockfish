#!/usr/bin/env bash

set -euo pipefail

usage() {
    echo "usage: $0 VERSION SOURCE_DATE_EPOCH" >&2
    exit 2
}

die() {
    echo "build_atomic_board_wasm_release: $*" >&2
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
    src/Makefile_js
    src/atomic_version.h
    tests/js/README.md
    tests/js/ffish.d.ts
    tests/js/package.json
)
for path in "${required_files[@]}"; do
    [[ -f "$path" && ! -L "$path" ]] || die "missing regular release input: $path"
done

require_command make
require_command em++
require_command node
require_command npm
require_command install

declared_version=$(
    sed -n 's/.*AtomicVersionString = "\([^"]*\)".*/\1/p' src/atomic_version.h
)
[[ -n "$declared_version" && "$declared_version" == "$version" ]] \
    || die "requested version $version differs from atomic_version.h ($declared_version)"

package_version=$(node -e \
    "const p=require('./tests/js/package.json'); process.stdout.write(String(p.version));")
[[ "$package_version" == "$version" ]] \
    || die "requested version $version differs from package.json ($package_version)"

[[ ! -e build ]] || die "build/ already exists; use a clean release root"
[[ ! -e tests/js/dist ]] || die "tests/js/dist already exists; use a clean release root"
for generated_metadata in tests/js/AUTHORS tests/js/CITATION.cff tests/js/LICENSE; do
    [[ ! -e "$generated_metadata" ]] \
        || die "generated package metadata already exists: $generated_metadata"
done

export SOURCE_DATE_EPOCH=$source_date_epoch
export LC_ALL=C
export LANG=C
export TZ=UTC
export npm_config_audit=false
export npm_config_fund=false
export npm_config_offline=true
export npm_config_package_lock=false
export npm_config_update_notifier=false
umask 022

unset CXXFLAGS CPPFLAGS LDFLAGS EMCC_CFLAGS EMCC_DEBUG
unset MAKEFLAGS MFLAGS NODE_OPTIONS TAR_OPTIONS GZIP

# The explicit clean goal makes this recipe the complete Board build boundary.
make -C src -f Makefile_js clean
make -C src -f Makefile_js all

expected_dist=(
    tests/js/dist/cjs/ffish.js
    tests/js/dist/cjs/ffish.wasm
    tests/js/dist/esm/ffish.mjs
    tests/js/dist/esm/ffish.wasm
)
for artifact in "${expected_dist[@]}"; do
    [[ -s "$artifact" && ! -L "$artifact" ]] \
        || die "Board WASM build omitted a regular artifact: $artifact"
done
actual_dist=$(find tests/js/dist -type f | LC_ALL=C sort)
expected_dist_text=$(printf '%s\n' "${expected_dist[@]}" | LC_ALL=C sort)
[[ "$actual_dist" == "$expected_dist_text" ]] \
    || die "Board WASM build produced an unexpected dist file set"

install -m 0644 AUTHORS tests/js/AUTHORS
install -m 0644 CITATION.cff tests/js/CITATION.cff
install -m 0644 Copying.txt tests/js/LICENSE

(
    cd tests/js
    npm test
)

release_dir=build/release
mkdir -p "$release_dir"
(
    cd tests/js
    npm pack --pack-destination "$repo_root/$release_dir"
)

expected_asset="$release_dir/atomic-stockfish-ffish-$version.tgz"
[[ -s "$expected_asset" && ! -L "$expected_asset" ]] \
    || die "npm pack did not produce the expected release package: $expected_asset"

shopt -s nullglob
release_outputs=("$release_dir"/*.tgz)
[[ ${#release_outputs[@]} -eq 1 && "${release_outputs[0]}" == "$expected_asset" ]] \
    || die "Board WASM recipe produced an unexpected package set"

printf '%s\n' "$expected_asset"
