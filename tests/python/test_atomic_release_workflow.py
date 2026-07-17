import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.run_atomic_release_contract_tests import (
    discover_release_contract_tests,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "atomic-release.yml"
ATOMIC_WORKFLOW = ROOT / ".github" / "workflows" / "atomic.yml"
RELEASE_PR_WORKFLOW = ROOT / ".github" / "workflows" / "atomic-release-pr.yml"
UPSTREAM_STOCKFISH_WORKFLOW = ROOT / ".github" / "workflows" / "stockfish.yml"
CHECKLIST = ROOT / "docs" / "atomic" / "release-1.0-checklist.md"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def atomic_workflow() -> str:
    return ATOMIC_WORKFLOW.read_text(encoding="utf-8")


def release_pr_workflow() -> str:
    return RELEASE_PR_WORKFLOW.read_text(encoding="utf-8")


def recipe(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_one_authoritative_runner_discovers_every_release_contract_module() -> None:
    expected = {
        "test_atomic_release_manifest.py",
        "test_atomic_release_exact_tag_gates.py",
        "test_atomic_release_exact_tag_orchestrator.py",
        "test_atomic_release_main_trust.py",
        "test_atomic_python_wheel_release_recipe.py",
        "test_atomic_release_verification.py",
        "test_atomic_release_workflow.py",
        "test_atomic_reproducible_sdist.py",
        "test_atomic_syzygy_openbench_evidence.py",
        "test_atomic_uci_wasm_release.py",
        "test_atomic_windows_wheel_fingerprint.py",
        "test_release_dependency_locks.py",
        "test_release_version.py",
        "test_wasm_source_date_epoch.py",
    }
    discovered = {path.name for path in discover_release_contract_tests()}
    assert discovered == expected

    release_workflow = workflow()
    atomic_workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    release_invocation = "python -I scripts/run_atomic_release_contract_tests.py"
    ci_invocation = "python scripts/run_atomic_release_contract_tests.py"
    assert release_workflow.count(release_invocation) == 1
    assert atomic_workflow.count(ci_invocation) == 1


def test_release_identity_builds_pyffish_before_collecting_contracts() -> None:
    validate = job(workflow(), "validate", "main-trust")
    scrub = "unset PYTHONHOME PYTHONPATH"
    build = "python -I setup.py build_ext --inplace"
    contracts = "python -I scripts/run_atomic_release_contract_tests.py"
    assert validate.count(scrub) == 1
    assert validate.count(build) == 1
    assert validate.count(contracts) == 1
    assert validate.index(scrub) < validate.index(build)
    assert validate.index(build) < validate.index(contracts)


def test_workflow_dispatch_builds_exact_sha_without_publication() -> None:
    text = workflow()
    trigger = text.split("\nconcurrency:", 1)[0]
    assert "  workflow_dispatch:\n" in trigger
    assert "Exact reviewed 40-hex commit SHA" in trigger
    assert "        required: true\n" in trigger
    assert "        default: main\n" not in trigger

    validate = job(text, "validate", "main-trust")
    assert "github.event_name == 'workflow_dispatch' && inputs.ref" in validate
    assert "REQUESTED_REF: ${{ inputs.ref }}" in validate
    assert '[[ "$REQUESTED_REF" =~ ^[0-9a-f]{40}$ ]]' in validate
    assert 'test "$commit" = "$REQUESTED_REF"' in validate

    assemble = job(text, "assemble", "exact-tag-external")
    assert "    if:" not in assemble.split("    steps:\n", 1)[0]
    assert "pattern: release-*" in assemble
    assert "contents: write" not in assemble

    tag_condition = (
        "if: github.event_name == 'push' && github.ref_type == 'tag' "
        "&& github.ref_name == 'v1.0.3'"
    )
    main_trust = job(text, "main-trust", "windows-fingerprint-capture")
    exact = job(text, "exact-tag-external", "publication-gate")
    publication = job(text, "publication-gate", "publish")
    publish = text.split("  publish:\n", 1)[1]
    assert "if: github.event_name == 'push'" in main_trust.split("    steps:\n", 1)[0]
    assert "refs/tags/v1.0.3" in main_trust.split("    steps:\n", 1)[0]
    assert "if: github.event_name == 'push'" in exact.split("    steps:\n", 1)[0]
    assert "refs/tags/v1.0.3" in exact.split("    steps:\n", 1)[0]
    assert tag_condition in publication.split("    steps:\n", 1)[0]
    assert tag_condition in publish.split("    steps:\n", 1)[0]


def test_release_recovery_is_chained_to_the_original_1_0_merge() -> None:
    text = workflow()
    required_base = "66b030907c35b3a4a91a35653162b7882fc6fd49"

    assert text.count("--release-pr 48") == 4
    assert "--release-pr 47" not in text
    assert "--release-pr 46" not in text
    assert "--release-pr 44" not in text
    assert text.count("--required-release-pr-base-sha " + required_base) == 4


def test_orthodox_upstream_workflow_cannot_gate_atomic_release_tags() -> None:
    text = UPSTREAM_STOCKFISH_WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("\nconcurrency:", 1)[0]
    assert '      - "sf_*"\n' in trigger
    assert '      - "*"\n' not in trigger
    assert "      - v*\n" not in trigger


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
    assert wheels.count("scripts/build_atomic_python_wheel_release.sh") == 2
    assert "build_ids=(a b c d)" in wheels
    assert 'for build_id in "${build_ids[@]}"; do' in wheels
    assert '"build/wheelhouse-$build_id" "build/cibw-cache-$build_id"' in wheels
    assert "SOURCE_DATE_EPOCH: ${{ needs.validate.outputs.epoch }}" in wheels
    assert "-r tests/release-ci-requirements.txt" in wheels
    assert 'for build_id in "${build_ids[@]:1}"; do' in wheels
    assert 'cmp "${first[0]}" "${candidate[0]}"' in wheels
    assert "windows-wheel-fingerprint-a.json" in wheels
    assert "windows-wheel-fingerprint-$build_id.json" in wheels
    assert "Preserve wheel reproducibility evidence" in wheels
    assert (
        "diagnostic-python-repro-${{ matrix.platform }}-${{ github.run_attempt }}"
        in wheels
    )
    assert "name: release-python-repro-" not in wheels
    assert 'python -m abi3audit --strict "${first[0]}"' in wheels


def test_release_python_installs_are_closed_or_pre_authenticated() -> None:
    text = workflow()
    exact = job(text, "exact-tag-external", "publication-gate")
    wheel_recipe = recipe("build_atomic_python_wheel_release.sh")
    source_recipe = recipe("build_atomic_source_release.sh")
    assert text.count("python -m pip install") == 5
    assert text.count("--only-binary=:all: --require-hashes") == 10
    assert text.count("-r tests/release-ci-requirements.txt") == 5
    assert exact.count("--only-binary=:all: --require-hashes") == 6
    assert exact.count("--dry-run --ignore-installed --quiet") == 2
    assert exact.count("-r tests/pip-bootstrap-requirements.txt") == 2
    assert exact.count("-r tests/release-ci-requirements.txt") == 2
    assert exact.count("-r tests/legacy_pipeline-ci-requirements.txt") == 2
    assert exact.count("-I -m pip --isolated") == 8
    assert exact.count("-I -m pip --isolated check") == 2
    assert "python-version: '3.12.10'" in exact
    assert "python-version: '3.10.18'" in exact
    assert exact.count("-I -m venv --copies") == 2
    assert 'assert psutil.__version__ == "7.2.2"' in exact
    assert 'assert torch.__version__ == "2.1.2+cpu"' in exact
    assert "pipeline_python = $env:PIPELINE_PYTHON" in exact
    assert wheel_recipe.count("python -m pip install") == 2
    assert wheel_recipe.count("--force-reinstall --no-deps") == 2
    assert wheel_recipe.count("--only-binary=:all: --require-hashes") == 2
    assert wheel_recipe.count('-r "{project}/tests/release-build-requirements.txt"') == 1
    assert wheel_recipe.count('-r "{project}/tests/release-wheel-test-requirements.txt"') == 1
    assert source_recipe.count("python3 -m pip install") == 1
    assert "--force-reinstall --no-deps --only-binary=:all: --require-hashes" in source_recipe
    assert text.count("--no-index --no-deps") == 1


def test_exact_tag_job_can_honor_every_sequential_gate_timeout() -> None:
    exact = job(workflow(), "exact-tag-external", "publication-gate")
    plan = json.loads(
        (ROOT / "scripts" / "atomic-release-exact-tag-plan-v1.json").read_text(
            encoding="utf-8"
        )
    )
    expected_timeouts = {
        "hito4-release": 10_800,
        "legacy-v1-strong-local": 7_200,
        "hito5-release": 32_400,
        "syzygy-real-3-to-6": 1_800,
        "atomic-bin-v2-strong-local": 10_800,
        "bmi2-vs-fairy": 3_600,
    }
    actual_timeouts = {
        gate["id"]: gate["timeoutSeconds"] for gate in plan["gates"]
    }
    assert actual_timeouts == expected_timeouts
    gate_budget_seconds = sum(actual_timeouts.values())
    job_budget_minutes = 1260
    setup_and_cleanup_seconds = 150 * 60
    github_token_ceiling_minutes = 24 * 60
    inventory = json.loads(
        (ROOT / "docs" / "atomic" / "release-1.0-inventory.json").read_text(
            encoding="utf-8"
        )
    )["releasePolicy"]["exactTagExternalGates"]
    assert gate_budget_seconds == 66_600
    assert inventory["timeoutBudgetSeconds"] == gate_budget_seconds
    assert inventory["jobTimeoutMinutes"] == job_budget_minutes
    assert f"timeout-minutes: {job_budget_minutes}" in exact
    assert job_budget_minutes * 60 == gate_budget_seconds + setup_and_cleanup_seconds
    assert job_budget_minutes < github_token_ceiling_minutes
    assert github_token_ceiling_minutes - job_budget_minutes == 180


def test_python_wheel_builder_is_digest_pinned_and_in_provenance() -> None:
    text = workflow()
    wheels = job(text, "python-wheels", "source")
    image = (
        "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
        "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
    )
    assert f"PYTHON_MANYLINUX_X86_64_IMAGE: {image}" in text
    wheel_recipe = recipe("build_atomic_python_wheel_release.sh")
    assert image in wheel_recipe
    assert "export CIBW_MANYLINUX_X86_64_IMAGE=$MANYLINUX_X86_64_IMAGE" in wheel_recipe
    assert 'builder="$PYTHON_MANYLINUX_X86_64_IMAGE"' in wheels
    assert "builder=$builder" in wheels
    assert "python-version: '3.9.13'" not in wheels
    assert "/opt/python/cp39-cp39/bin/python -VV" in wheels
    assert '["python"]["sysVersion"]' in wheels
    assert "CPython 3.9 stable ABI" in wheels
    assert "pip 26.0.1 / setuptools 80.9.0 / wheel 0.45.1" in wheels
    assert "fingerprintSha256=$fingerprint_sha256" in wheels
    assert "build/cibw-cache-a" in wheels and "build/cibw-cache-$build_id" in wheels
    assert (
        "WINDOWS_WHEEL_FINGERPRINT_SHA256: "
        "9af3078f7f7d2635e5fe20c913c6948e53dbdc5f1ec81ba22f081d21e6a3f23d"
        in text
    )
    assert "WINDOWS_WHEEL_IMAGE_OS: win22" in text
    assert "WINDOWS_WHEEL_IMAGE_VERSION: 20260714.244.1" in text
    assert wheels.count("docs/atomic/windows-wheel-fingerprint-v2.json") >= 2
    assert '"runner"]["imageOS"]' in wheels
    assert '"runner"]["imageVersion"]' in wheels
    atomic_ci = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    assert "Capture the candidate Windows wheel toolchain fingerprint" not in atomic_ci
    assert "--expected-sha256 $env:ATOMIC_WINDOWS_WHEEL_FINGERPRINT_SHA256" not in atomic_ci
    for line in text.splitlines():
        if "manylinux_2_28_x86_64" in line:
            assert "@sha256:" in line


def test_windows_fingerprint_capture_is_real_cibuildwheel_and_cannot_publish() -> None:
    text = workflow()
    capture = job(text, "windows-fingerprint-capture", "native-linux")
    assert "capture_windows_fingerprint" in text
    assert "inputs.capture_windows_fingerprint" in capture
    assert "runs-on: windows-2022" in capture
    assert "python-version: '3.12.10'" in capture
    assert "python-version: '3.12.11'" not in text
    assert "foreach ($buildId in @('a', 'b'))" in capture
    assert "build/fingerprint-cache-$buildId" in capture
    assert "$env:CIBW_BUILD = 'cp39-*'" in capture
    assert "$env:CIBW_ARCHS = 'AMD64'" in capture
    assert "$env:CIBW_BEFORE_BUILD" in capture
    assert "scripts/atomic_windows_wheel_fingerprint.py" in capture
    assert "--force-reinstall --no-deps --only-binary=:all: --require-hashes" in capture
    assert "-r tests/release-ci-requirements.txt" in capture
    assert "-r \"{project}/tests/release-build-requirements.txt\"" in capture
    assert "--expected-sha256" not in capture
    assert "SequenceEqual" in capture
    assert "windows-wheel-fingerprint-v2.json" in capture
    assert "actions/upload-artifact@" in capture
    for forbidden in (
        "contents: write",
        "gh release",
        "releases/",
        "draft=false",
        "ATOMIC_RELEASE_POLICY_TOKEN",
    ):
        assert forbidden not in capture


def test_release_pr_reproduces_real_windows_wheel_and_frozen_fingerprint() -> None:
    text = release_pr_workflow()
    assert "pull_request:" in text
    assert "paths:" in text
    trigger = text.split("\nconcurrency:", 1)[0]
    for dependency in (
        "AUTHORS",
        "CITATION.cff",
        "Copying.txt",
        "MANIFEST.in",
        "README.md",
        "pyffish.pyi",
        "schemas/atomic-release-*",
        "scripts/atomic-release-*",
        "scripts/atomic_process_containment.py",
        "scripts/atomic_reproducible_sdist.py",
        "scripts/build_atomic_python_wheel_release.sh",
        "scripts/build_atomic_source_release.sh",
        "setup.py",
        "src/**",
        "tests/release-*.txt",
        "tests/run_atomic_release_exact_tag_gate.py",
        "tests/python/test_wheel_layout.py",
    ):
        assert f"      - {dependency}\n" in trigger
    source = job(text, "source_sdist", "linux_wheel")
    linux = job(text, "linux_wheel", "windows_wheel")
    gate = text.split("  windows_wheel:\n", 1)[1]
    assert "ref: ${{ github.event.pull_request.head.sha }}" in source
    assert "EXPECTED_HEAD: ${{ github.event.pull_request.head.sha }}" in source
    assert 'test "$commit" = "$EXPECTED_HEAD"' in source
    assert "--only-binary=:all: --require-hashes" in source
    assert "-r tests/release-ci-requirements.txt" in source
    assert "build_atomic_source_release.sh" in source
    assert "git archive \"$commit\"" in source
    assert "build/sdist-a" in source and "build/sdist-b" in source
    assert 'cmp "$first" "$second"' in source
    assert "atomic_release_provenance.py" in source
    assert 'cp "$first.provenance.json"' in source
    assert "buildLockSha256=" in source
    assert "name: release-pr-source" in source
    assert "needs: source_sdist" in linux
    assert "runs-on: ubuntu-24.04" in linux
    assert "python-version: '3.12.10'" in linux
    assert "name: release-pr-source" in linux
    assert "atomic_verify_release_asset.py" in linux
    assert 'test "$(git rev-parse HEAD)" = "$COMMIT"' in linux
    assert "for build_id in a b; do" in linux
    assert 'linux "${sdists[0]}"' in linux
    assert '"build/wheelhouse-$build_id" "build/cibw-cache-$build_id"' in linux
    assert "none none" in linux
    assert "cp39-abi3-manylinux*x86_64.whl" in linux
    assert 'cmp "${first[0]}" "${second[0]}"' in linux
    assert 'python -m abi3audit --strict "${first[0]}"' in linux
    assert 'python tests/python/test_wheel_layout.py "${first[0]}"' in linux
    assert "name: release-pr-linux-wheel" in linux
    assert "setup.py sdist" not in linux
    assert "git archive" not in linux
    assert "needs: source_sdist" in gate
    assert "runs-on: windows-2022" in gate
    assert "python-version: '3.12.10'" in gate
    assert "atomic_verify_release_asset.py" in gate
    assert "name: release-pr-source" in gate
    assert "for build_id in a b c d; do" in gate
    assert "scripts/build_atomic_python_wheel_release.sh" in gate
    assert 'windows "${sdists[0]}"' in gate
    assert '"build/wheelhouse-$build_id" "build/cibw-cache-$build_id"' in gate
    assert "docs/atomic/windows-wheel-fingerprint-v2.json" in gate
    assert "WINDOWS_WHEEL_FINGERPRINT_SHA256" in gate
    assert "WINDOWS_WHEEL_IMAGE_VERSION" in gate
    assert "for build_id in b c d; do" in gate
    assert 'cmp "${first[0]}" "${candidate[0]}"' in gate
    assert "python -m abi3audit --strict" in gate
    assert "test_wheel_layout.py" in gate
    assert "Preserve all Windows reproducibility evidence" in gate
    assert "if: always()" in gate
    assert "diagnostic-pr-windows-repro-${{ github.run_attempt }}" in gate
    assert "build/wheelhouse-*/*.whl" in gate
    assert "name: release-pr-windows-wheel" in gate
    for forbidden in ("contents: write", "gh release", "releases/", "draft=false"):
        assert forbidden not in text


def test_exact_tag_controllers_are_isolated_on_the_windows_containment_runner() -> None:
    exact = job(workflow(), "exact-tag-external", "publication-gate")

    assert "runs-on: [self-hosted, Windows, X64, atomic-release-gates]" in exact
    for invocation in (
        "& $env:CONTROLLER_PYTHON -I scripts/atomic_release_main_trust.py",
        "& $env:CONTROLLER_PYTHON -I scripts/atomic_release_exact_tag_gates.py run",
        "& $env:CONTROLLER_PYTHON -I scripts/atomic_release_exact_tag_gates.py verify",
    ):
        assert invocation in exact
    assert exact.count("Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue") == 2
    assert exact.count("Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue") == 2

    controller = exact.split(
        "      - name: Create an isolated hash-closed Python 3.12 controller", 1
    )[1].split("      - id: setup_pipeline_python", 1)[0]
    pipeline = exact.split(
        "      - name: Create an isolated hash-closed Python 3.10 legacy pipeline", 1
    )[1].split(
        "      - name: Authenticate the same-run assembled artifact before download", 1
    )[0]
    for runtime, executable in (
        (controller, "$controllerPython"),
        (pipeline, "$pipelinePython"),
    ):
        scrub = "Where-Object { $_.Name -like 'PYTHON*' }"
        assert runtime.count(scrub) == 1
        assert runtime.index(scrub) < runtime.index("$bootstrap =")
        assert runtime.count("& $bootstrap -I -c") == 1
        assert runtime.count("& $bootstrap -I -m venv --copies $runtimeRoot") == 1
        assert runtime.count(f"& {executable} -I -m pip --isolated") == 4
    assert "& $controllerPython -I -P -c @'" in controller
    assert "& $pipelinePython -I -c @'" in pipeline

    release_pr = release_pr_workflow()
    assert "atomic_process_containment_linux.py" not in release_pr
    assert "tests/atomic_process_containment_posix.py" in release_pr
    assert "docker run --rm --network none --platform linux/amd64" in release_pr
    assert (
        "python3 -I /src/tests/atomic_process_containment_posix.py /src"
        in release_pr
    )
    assert "PR_SET_CHILD_SUBREAPER" not in recipe("atomic_process_containment.py")


def test_isolated_bootstrap_and_preflight_ignore_pythonpath_sitecustomize_and_shadows(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    marker = tmp_path / "attacker-ran.txt"
    payload = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('attacker ran', encoding='ascii')\n"
        "raise RuntimeError('attacker module executed')\n"
    )
    for name in ("sitecustomize.py", "venv.py", "json.py", "pip.py"):
        (shadow / name).write_text(payload, encoding="ascii")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(shadow)
    environment["PYTHONHOME"] = str(tmp_path / "invalid-python-home")
    environment["PYTHONSTARTUP"] = str(shadow / "sitecustomize.py")

    def isolated(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", *arguments],
            cwd=shadow,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )

    version = isolated("-c", "import sys; assert sys.version_info >= (3, 9)")
    assert version.returncode == 0, version.stderr
    runtime = tmp_path / "isolated-runtime"
    created = isolated("-m", "venv", "--without-pip", str(runtime))
    assert created.returncode == 0, created.stderr
    pip = isolated("-m", "pip", "--isolated", "--version")
    assert pip.returncode == 0, pip.stderr

    runtime_python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    preflight = subprocess.run(
        [
            str(runtime_python),
            "-I",
            "-c",
            "import json, pathlib; assert pathlib.Path(json.__file__).name != 'shadow'",
        ],
        cwd=shadow,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    assert preflight.returncode == 0, preflight.stderr
    assert not marker.exists()


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
    assert "scripts/build_atomic_native_release.sh" in linux
    assert "linux '${{ matrix.arch }}'" in linux
    assert "docker run --rm --platform linux/amd64" in linux
    assert 'for build_id in a b; do' in windows
    assert "build/native-windows-a/src/atomic-stockfish.exe" in windows
    assert "build/native-windows-b/src/atomic-stockfish.exe" in windows
    assert windows.count("scripts/build_atomic_native_release.sh") >= 2
    assert "windows '${{ matrix.arch }}'" in windows
    native_recipe = recipe("build_atomic_native_release.sh")
    assert 'COMP=gcc' in native_recipe
    assert 'COMP=mingw COMPCXX="$CXX"' in native_recipe
    assert "x86_64-w64-mingw32.static" in native_recipe
    assert 'test "$("$CXX" -dumpmachine)" = x86_64-w64-mingw32.static' in windows
    assert 'xz --threads=1 -9e --check=crc64' in native_recipe
    assert 'cmake -E tar' in native_recipe
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
        "&& github.ref_name == 'v1.0.3'"
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
    assert "steps.upload_draft.outputs.release_id" in publish
    assert 'test "$UPLOAD_RELEASE_ID" = "$RELEASE_ID"' in publish
    assert "softprops/action-gh-release" not in publish
    assert "steps.reserve_draft.outputs.upload_url" in publish
    assert 'test "$UPLOAD_URL" = "$expected_upload_url"' in publish
    assert "curl --fail-with-body --silent --show-error" in publish
    assert '"$UPLOAD_URL?name=$encoded"' in publish
    assert "--data-binary \"@$asset\"" in publish
    assert publish.count('"repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID"') >= 4
    assert '([.assets[].name] | index($name) == null)' in publish
    assert 'actual_assets=$(jq -c \'[.assets[].name] | sort\'' in publish
    assert 'test "$actual_assets" = "$expected_assets"' in publish
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
    assert "scripts/build_atomic_board_wasm_release.sh" in board
    assert '--volume "$GITHUB_WORKSPACE/build/board-wasm-a:/work"' in board
    assert 'toolchain="image=$EMSCRIPTEN_IMAGE;' in uci
    assert "scripts/build_atomic_uci_wasm_release.sh" in uci
    assert '--volume "$GITHUB_WORKSPACE/build/uci-wasm-a:/work"' in uci

    board_recipe = recipe("build_atomic_board_wasm_release.sh")
    assert "make -C src -f Makefile_js clean" in board_recipe
    assert "npm test" in board_recipe
    assert "npm pack --pack-destination" in board_recipe
    uci_recipe = recipe("build_atomic_uci_wasm_release.sh")
    assert "python3 tests/wasm-engine/build.py" in uci_recipe
    assert "tar --sort=name --format=gnu" in uci_recipe
    assert "atomic_verify_uci_wasm_archive.py" in uci_recipe


def test_board_wasm_executes_both_exports_from_the_exact_installed_tgz() -> None:
    board = job(workflow(), "board-wasm", "uci-wasm")

    assert '"build/board-wasm-a/build/release/$asset"' in board
    assert '"build/board-wasm-b/build/release/$asset"' in board
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

    uci_recipe = recipe("build_atomic_uci_wasm_release.sh")
    assert 'docs/atomic/node-uci-wasm-release.md "$stage_root/README.md"' in uci_recipe
    assert "tests/wasm-engine/README.md" not in uci_recipe
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


def test_uci_wasm_uses_clean_roots_and_never_packages_on_the_mutable_host() -> None:
    uci = job(workflow(), "uci-wasm", "assemble")
    build = uci.split(
        "      - name: Build and package twice with the versioned UCI WASM recipe\n",
        1,
    )[1].split(
        "      - name: Authenticate and exercise the exact packaged three-backend NNUE engine\n",
        1,
    )[0]
    authenticate = uci.split(
        "      - name: Authenticate and exercise the exact packaged three-backend NNUE engine\n",
        1,
    )[1]
    assert 'for build_id in a b; do' in build
    assert 'git archive "$COMMIT" | tar -x -C "$root"' in build
    assert '--user "$(id -u):$(id -g)"' in build
    assert "scripts/build_atomic_uci_wasm_release.sh" in build
    assert "-cJf" not in build
    assert "-cJf" not in authenticate
    assert "python3 tests/create_synthetic_zero_nnue.py" in authenticate
    assert "python3 tests/create_synthetic_atomic_v2_nnue.py" in authenticate
    assert "python3 tests/create_synthetic_atomic_v3_nnue.py" in authenticate
    assert "--v3-net build/wasm-fixtures/atomic-v3.nnue" in authenticate
    assert (
        "--v3-sha256 00E46223822D06D7927E884EEC10739BA19EF8DD82A6E262F627D361658080C2"
        in authenticate
    )
    assert 'build/wasm-archive-smoke' in authenticate
