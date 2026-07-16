from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "atomic-release.yml"
CHECKLIST = ROOT / "docs" / "atomic" / "release-1.0-checklist.md"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def job(text: str, name: str, next_name: str) -> str:
    return text.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]


def test_python_wheels_consume_only_the_authenticated_source_job_sdist() -> None:
    text = workflow()
    wheels = job(text, "python-wheels", "source")
    assert "      - source\n" in wheels
    assert "name: release-source" in wheels
    assert "atomic_verify_release_asset.py" in wheels
    assert "EXPECTED_SDIST_SHA256: ${{ needs.source.outputs.sdist_sha256 }}" in wheels
    assert "setup.py sdist" not in wheels
    assert wheels.count('python -m cibuildwheel "$RELEASE_SDIST"') == 3
    assert "SOURCE_DATE_EPOCH=${{ needs.validate.outputs.epoch }}" in wheels
    assert "CIBW_TEST_REQUIRES" not in wheels
    assert "CIBW_BEFORE_TEST: >-" in wheels
    assert "-r {project}/tests/release-wheel-test-requirements.txt" in wheels
    assert "-r tests/release-ci-requirements.txt" in wheels
    assert "python -m mypy -m pyffish" in wheels
    assert 'cmp "${first[0]}" "${second[0]}"' in wheels
    assert 'python -m abi3audit --strict "${first[0]}"' in wheels


def test_release_python_installs_are_closed_or_pre_authenticated() -> None:
    text = workflow()
    commands = text.count("python -m pip install")
    assert commands == 5
    assert text.count("--only-binary=:all: --require-hashes") == 4
    assert text.count("-r tests/release-ci-requirements.txt") == 3
    assert (
        text.count("-r {project}/tests/release-wheel-test-requirements.txt")
        == 1
    )
    assert text.count("--no-index --no-deps") == 1


def test_python_wheel_builder_is_digest_pinned_and_in_provenance() -> None:
    text = workflow()
    wheels = job(text, "python-wheels", "source")
    image = (
        "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
        "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
    )
    assert f"PYTHON_MANYLINUX_X86_64_IMAGE: {image}" in text
    assert 'if [[ "$WHEEL_PLATFORM" == linux ]]; then' in wheels
    assert (
        'export CIBW_MANYLINUX_X86_64_IMAGE="$PYTHON_MANYLINUX_X86_64_IMAGE"'
        in wheels
    )
    assert 'builder="$PYTHON_MANYLINUX_X86_64_IMAGE"' in wheels
    assert "builder=$builder" in wheels
    for line in text.splitlines():
        if "manylinux_2_28_x86_64" in line:
            assert "@sha256:" in line


def test_native_toolchains_are_digest_pinned_and_reproduced_in_isolated_roots() -> None:
    text = workflow()
    linux = job(text, "native-linux", "native-windows")
    windows = job(text, "native-windows", "native-windows-smoke")
    smoke = job(text, "native-windows-smoke", "python-wheels")

    assert "gcc:14.2.0-bookworm@sha256:b99b86a" in text
    assert "sha256:82549aa8f90ada3236a8be70c74543132" in text
    assert "dockcross/windows-static-x64@sha256:e5fde458" in text
    assert 'for build_id in a b; do' in linux
    assert "build/native-linux-a/src/atomic-stockfish" in linux
    assert "build/native-linux-b/src/atomic-stockfish" in linux
    assert "COMP=gcc build" in linux
    assert "docker run --rm --platform linux/amd64" in linux
    assert 'for build_id in a b; do' in windows
    assert "build/native-windows-a/src/atomic-stockfish.exe" in windows
    assert "build/native-windows-b/src/atomic-stockfish.exe" in windows
    assert windows.count("COMP=mingw") >= 3
    assert 'COMPCXX="$CXX" build' in windows
    assert "runs-on: windows-2022" in smoke
    assert "name: release-windows-${{ matrix.arch }}" in smoke
    assert "tests/release_protocol_smoke.py" in smoke


def test_exact_release_wheels_run_across_the_supported_abi3_matrix() -> None:
    text = workflow()
    smoke = job(text, "python-wheel-smoke", "board-wasm")

    assert "      - python-wheels\n" in smoke
    assert smoke.count("platform: linux") == 3
    assert smoke.count("platform: windows") == 3
    for version in ("3.9", "3.12", "3.14"):
        assert smoke.count(f"python: '{version}'") == 2
    assert "name: release-python-${{ matrix.platform }}" in smoke
    assert "atomic_verify_release_asset.py" in smoke
    assert '"${wheels[0]}.provenance.json"' in smoke
    assert "hashlib.sha256(open" not in smoke
    assert "--no-index --no-deps" in smoke
    assert 'cd "$RUNNER_TEMP"' in smoke
    assert "pyffish.perft" in smoke
    assemble = job(text, "assemble", "publication-gate")
    assert "      - python-wheel-smoke\n" in assemble


def test_publication_requires_annotated_tag_immutable_policy_and_same_tag_ci() -> None:
    text = workflow()
    validate = job(text, "validate", "native-linux")
    gate = job(text, "publication-gate", "publish")
    publish = text.split("  publish:\n", 1)[1]

    assert 'test "$(jq -r \'.object.type\' <<<"$ref_json")" = tag' in validate
    assert 'git rev-parse "refs/tags/$tag^{tag}"' in validate
    assert 'git rev-parse "refs/tags/$tag^{}"' in validate
    assert "actions: read" in gate
    assert "immutable-releases" in gate
    assert '.enabled == true' in gate
    assert text.count("ATOMIC_RELEASE_POLICY_TOKEN") == 2
    assert text.count("IMMUTABLE_RELEASES_TOKEN") == 6
    assert text.count('test -n "$IMMUTABLE_RELEASES_TOKEN"') == 2
    assert (
        text.count('immutable=$(GH_TOKEN="$IMMUTABLE_RELEASES_TOKEN" gh api \\')
        == 2
    )
    assert text.count("$GITHUB_REPOSITORY/immutable-releases") == 2
    assert text.count(
        "if: github.event_name == 'push' && github.ref_type == 'tag' "
        "&& github.ref_name == 'v1.0.0'"
    ) == 2
    assert "pull_request" not in text.split("permissions:", 1)[0]
    assert '.name == "Atomic CI"' in gate
    assert '.event == "push"' in gate
    assert '.head_branch == $tag' in gate
    assert '.head_sha == $commit' in gate
    assert '.path == ".github/workflows/atomic.yml"' in gate
    assert '-f branch="$tag"' in gate
    assert "workflow file without a" in gate
    assert "atomic.yml@refs/tags/" not in gate
    assert "contents: write" not in gate

    assert publish.count("contents: write") == 1
    assert "draft: true" in publish
    assert "draft: false" not in publish
    assert "gh release download" in publish
    assert "atomic_verify_release_download.py" in publish
    assert ".draft == true" in publish
    assert ".published_at == null" in publish
    assert "id: reserve_draft" in publish
    assert "id: upload_draft" in publish
    assert "id: verify_draft" in publish
    assert "steps.upload_draft.outputs.id" in publish
    assert 'test "$UPLOAD_RELEASE_ID" = "$RELEASE_ID"' in publish
    assert "if: always() && steps.reserve_draft.outputs.release_id != ''" in publish
    assert '"repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID"' in publish
    assert "Delete only this workflow's invalid draft" in publish
    assert "--paginate --slurp" in publish
    assert "gh release edit" not in publish
    assert "--draft=false" not in publish


def test_wasm_provenance_records_real_digest_pinned_docker_commands() -> None:
    text = workflow()
    board = job(text, "board-wasm", "uci-wasm")
    uci = job(text, "uci-wasm", "assemble")
    digest = (
        "emscripten/emsdk:4.0.10@sha256:"
        "90b757eb11fa9a0e3ce4d2d9f76d932a56018e4accc37b5a28b2783751e60eb7"
    )
    assert digest in text
    assert 'toolchain="image=$EMSCRIPTEN_IMAGE;' in board
    assert "docker run --rm --env" in board
    assert "--workdir /src/src" in board
    assert "make -f Makefile_js repro" in board
    assert 'toolchain="image=$EMSCRIPTEN_IMAGE;' in uci
    assert "docker run --rm --env" in uci
    assert "tests/wasm-engine/build.py" in uci


def test_board_wasm_executes_both_exports_from_the_exact_installed_tgz() -> None:
    board = job(workflow(), "board-wasm", "uci-wasm")

    assert 'cmp "${first[0]}" "${second[0]}"' in board
    assert 'PACKAGE_TGZ=/src/build/release/$package_file' in board
    assert "mktemp -d /tmp/atomic-installed-npm.XXXXXX" in board
    assert "npm install --package-lock=false --ignore-scripts" in board
    assert "ATOMIC_FORBIDDEN_SOURCE_ROOT=/src" in board
    assert '--volume "$GITHUB_WORKSPACE:/src:ro" --workdir /tmp' in board
    assert "test-installed-commonjs.cjs" in board
    assert "test-installed-esm.mjs" in board
    assert "--workdir /src/tests/js" not in board.split(
        'package_file="$(basename "${assets[0]}")"', 1
    )[1].split('toolchain="image=$EMSCRIPTEN_IMAGE;', 1)[0]


def test_uci_wasm_archive_uses_and_authenticates_release_documentation() -> None:
    uci = job(workflow(), "uci-wasm", "assemble")

    assert 'cp docs/atomic/node-uci-wasm-release.md "$root/README.md"' in uci
    assert "cp tests/wasm-engine/README.md" not in uci
    assert "atomic_verify_uci_wasm_archive.py" in uci
    assert '--source-date-epoch "$SOURCE_DATE_EPOCH"' in uci
    assert "build/wasm-archive-smoke" in uci
    assert '--volume "$GITHUB_WORKSPACE:/src:ro"' in uci
    assert "node ./atomic-stockfish-nnue-node.mjs" in uci
    assert "Nodes searched: 20" in uci


def test_manual_publish_requires_immediate_pre_and_post_trust_rechecks() -> None:
    checklist = CHECKLIST.read_text(encoding="utf-8")
    pre = checklist.index("Immediately before the manual publish click")
    publish = checklist.index("Manually publish release notes")
    post = checklist.index("Immediately after publication")

    assert pre < publish < post
    before = checklist[pre:publish]
    after = checklist[post:]
    for marker in (
        "exact tag ref",
        "annotated tag object",
        "direct peeled commit",
        "immutable-releases policy",
        "exact names and bytes",
        "SHA256SUMS",
    ):
        assert marker in before
    for marker in (
        "immutable-releases policy",
        "same exact ID",
        "draft=false",
        "annotated tag object",
        "direct peeled commit",
        "recorded SHAs unchanged",
        "byte equality",
        "SHA256SUMS",
    ):
        assert marker in after


def test_uci_wasm_precreates_every_later_host_write_root() -> None:
    uci = job(workflow(), "uci-wasm", "assemble")
    build = uci.split(
        "      - name: Build twice and require identical complete engine\n", 1
    )[1].split("      - name: Package complete UCI WASM and provenance\n", 1)[0]
    package = uci.split(
        "      - name: Package complete UCI WASM and provenance\n", 1
    )[1]
    first_container = build.index("docker run --rm")
    precreate = build[build.index("mkdir -p ") : first_container]

    for directory in (
        "build/wasm-release-a",
        "build/wasm-release-b",
        "build/wasm-fixtures",
        "build/wasm-stage",
        "build/release",
    ):
        assert directory in precreate

    assert build.index("python3 tests/create_synthetic_zero_nnue.py") > first_container
    assert "cp -a build/wasm-release-a/." not in package
    assert (
        'cp -R --no-preserve=ownership build/wasm-release-a/. "$root/"'
        in package
    )
