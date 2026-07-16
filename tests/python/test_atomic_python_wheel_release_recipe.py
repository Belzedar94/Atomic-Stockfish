import hashlib
import os
import runpy
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "scripts" / "build_atomic_python_wheel_release.sh"
WINDOWS_FINGERPRINT_DOCUMENT = (
    ROOT / "docs" / "atomic" / "windows-wheel-fingerprint-v2.json"
)
WHEEL_LAYOUT_TEST = ROOT / "tests" / "python" / "test_wheel_layout.py"
WINDOWS_FINGERPRINT_SHA256 = (
    "9af3078f7f7d2635e5fe20c913c6948e53dbdc5f1ec81ba22f081d21e6a3f23d"
)
MANYLINUX_IMAGE = (
    "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
    "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
)
BUILD_INSTALL = (
    "python -m pip install --disable-pip-version-check --force-reinstall "
    "--no-deps --only-binary=:all: --require-hashes "
    '-r "{project}/tests/release-build-requirements.txt"'
)
TEST_INSTALL = (
    "python -m pip install --disable-pip-version-check --force-reinstall "
    "--no-deps --only-binary=:all: --require-hashes "
    '-r "{project}/tests/release-wheel-test-requirements.txt"'
)


def recipe_text() -> str:
    return RECIPE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def working_bash() -> str:
    candidates = [os.environ.get("BASH"), shutil.which("bash"), "/bin/bash"]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
                r"C:\msys64\usr\bin\bash.exe",
            ]
        )
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not Path(candidate).is_file():
            continue
        seen.add(candidate)
        completed = subprocess.run(
            [candidate, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and "GNU bash" in completed.stdout:
            return candidate
    pytest.skip("a working GNU bash is required for recipe syntax tests")
    raise AssertionError("unreachable")


def run_recipe(*arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [working_bash(), str(RECIPE), *arguments],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_recipe_has_valid_bash_syntax() -> None:
    completed = subprocess.run(
        [working_bash(), "-n", str(RECIPE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_windows_extension_normalizes_absolute_source_paths() -> None:
    with (
        mock.patch("platform.python_compiler", return_value="MSC v.1944 64 bit"),
        mock.patch("setuptools.setup") as setup,
    ):
        runpy.run_path(str(ROOT / "setup.py"), run_name="__main__")

    extension = setup.call_args.kwargs["ext_modules"][0]
    assert extension.extra_compile_args[-1] == f"/d1trimfile:{ROOT.resolve()}\\"
    assert extension.extra_link_args == ["/Brepro"]


def test_wheel_layout_typechecks_the_isolated_wheel_environment() -> None:
    namespace = runpy.run_path(str(WHEEL_LAYOUT_TEST), run_name="wheel_layout_contract")
    typecheck_python = Path("isolated-typecheck-python")

    with mock.patch.object(namespace["subprocess"], "run") as run:
        namespace["typecheck_installed_wheel"](typecheck_python)

    run.assert_called_once_with(
        [
            namespace["sys"].executable,
            "-m",
            "mypy",
            "--python-executable",
            str(typecheck_python),
            "-m",
            "pyffish",
            "--no-incremental",
            "--no-error-summary",
        ],
        cwd=typecheck_python.parent.parent,
        check=True,
    )
    assert "system_site_packages=True" not in WHEEL_LAYOUT_TEST.read_text(
        encoding="utf-8"
    )


def test_recipe_usage_and_scalar_validation_fail_before_any_build() -> None:
    missing = run_recipe()
    assert missing.returncode == 2
    assert (
        "PLATFORM SDIST OUTPUT_DIR CACHE_DIR VERSION SOURCE_DATE_EPOCH "
        "FINGERPRINT_OUTPUT EXPECTED_FINGERPRINT_SHA256"
    ) in missing.stderr

    invalid_cases = [
        ("macos", "1.0.0", "1770000000"),
        ("linux", "01.0.0", "1770000000"),
        ("linux", "1.0", "1770000000"),
        ("linux", "1.0.0", "01770000000"),
        ("linux", "1.0.0", "not-an-epoch"),
    ]
    for platform, version, epoch in invalid_cases:
        completed = run_recipe(
            platform,
            "missing.tar.gz",
            "missing-output",
            "missing-cache",
            version,
            epoch,
            "none",
            "none",
        )
        assert completed.returncode == 2, (completed.stdout, completed.stderr)
        assert "usage:" in completed.stderr


def test_recipe_is_versioned_root_bound_and_fail_closed() -> None:
    text = recipe_text()
    assert "set -euo pipefail" in text
    assert "IFS=$'\\n\\t'" in text
    assert "readonly RECIPE_VERSION=1" in text
    assert '[ "$(pwd -P)" = "$repo_root" ]' in text
    assert "run this recipe from the repository root" in text
    assert 'sdist_absolute=$(canonical_existing_file "$sdist" "sdist")' in text
    assert '"atomic_pyffish-$version.tar.gz"' in text
    assert '[ ! -e "$requested" ] && [ ! -L "$requested" ]' in text
    assert 'output_absolute=$(canonical_new_directory "$output_dir"' in text
    assert 'cache_absolute=$(canonical_new_directory "$cache_dir"' in text
    assert "output and cache directories must differ" in text
    assert "output directory must not be inside the cache directory" in text
    assert "cache directory must not be inside the output directory" in text
    assert "rm -rf" not in text


def test_recipe_clears_overrides_and_sets_exact_cibuildwheel_contract() -> None:
    text = recipe_text()
    assert "compgen -A variable CIBW_" in text
    assert 'unset "$inherited_cibw"' in text
    assert "export CIBW_BUILD='cp39-*'" in text
    assert "export CIBW_ARCHS=x86_64" in text
    assert "export CIBW_ARCHS=AMD64" in text
    assert 'export CIBW_ENVIRONMENT="SOURCE_DATE_EPOCH=$epoch PYTHONHASHSEED=0"' in text
    assert "export CIBW_BUILD_FRONTEND='pip; args: --no-build-isolation'" in text
    assert "export CIBW_CACHE_PATH=$cache_for_cibuildwheel" in text
    assert "export SOURCE_DATE_EPOCH=$epoch" in text
    assert "export PYTHONHASHSEED=0" in text
    assert text.count("python -m cibuildwheel") == 1
    assert (
        'python -m cibuildwheel "$sdist_for_cibuildwheel" '
        '--output-dir "$output_for_cibuildwheel"'
    ) in text


def test_build_and_test_dependencies_use_only_the_exact_hash_locks() -> None:
    text = recipe_text()
    assert text.count(BUILD_INSTALL) == 1
    assert text.count(TEST_INSTALL) == 1
    assert "export CIBW_BEFORE_BUILD='{}'".format(BUILD_INSTALL) in text
    assert "export CIBW_BEFORE_TEST='{}'".format(TEST_INSTALL) in text
    for command in (BUILD_INSTALL, TEST_INSTALL):
        assert "--force-reinstall" in command
        assert "--no-deps" in command
        assert "--only-binary=:all:" in command
        assert "--require-hashes" in command
    assert "CIBW_TEST_REQUIRES" not in text


def test_installed_wheel_runs_existing_import_perft_and_mypy_contract() -> None:
    text = recipe_text()
    assert "import pyffish; assert pyffish.version() == (" in text
    assert "assert pyffish.variants() == ['atomic']" in text
    assert "assert pyffish.perft('atomic', pyffish.start_fen('atomic'), 1) == 20" in text
    assert "assert pyffish.info().startswith('Atomic-Stockfish $version ')" in text
    assert "python -m mypy -m pyffish --no-incremental --no-error-summary" in text


def test_linux_contract_has_no_host_fingerprint_and_uses_digest_pinned_image() -> None:
    text = recipe_text()
    assert text.count(MANYLINUX_IMAGE) == 1
    assert "export CIBW_MANYLINUX_X86_64_IMAGE=$MANYLINUX_X86_64_IMAGE" in text
    assert (
        '[ "$CIBW_MANYLINUX_X86_64_IMAGE" = "$MANYLINUX_X86_64_IMAGE" ]'
        in text
    )
    assert '[ "$fingerprint_output" = none ]' in text
    assert '[ "$expected_fingerprint_sha256" = none ]' in text
    assert "Linux FINGERPRINT_OUTPUT must be 'none'" in text
    assert "Linux EXPECTED_FINGERPRINT_SHA256 must be 'none'" in text


def test_windows_fingerprint_runs_in_actual_build_interpreter() -> None:
    text = recipe_text()
    assert (
        "readonly WINDOWS_WHEEL_FINGERPRINT_DOCUMENT="
        "'docs/atomic/windows-wheel-fingerprint-v2.json'"
    ) in text
    assert '"$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT" \\' in text
    assert "command -v python >/dev/null 2>&1" in text
    assert "Windows FINGERPRINT_OUTPUT must be absolute" in text
    assert "^[0-9A-Fa-f]{64}$" in text
    assert "frozen_fingerprint_sha256=$(python -I -c" in text
    assert "hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()" in text
    assert '"$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT")' in text
    assert '[ "$frozen_fingerprint_sha256" = "$normalized_expected" ]' in text
    assert (
        "Windows expected fingerprint SHA-256 does not match the frozen document"
        in text
    )
    assert text.index("frozen_fingerprint_sha256=$(python -I -c") < text.index(
        'mkdir -- "$output_absolute"'
    )
    assert 'command -v cygpath >/dev/null 2>&1' in text
    assert 'fingerprint_for_cibuildwheel=$(cygpath -w -- "$fingerprint_output")' in text
    fingerprint_invocation = (
        'python \\"{project}/scripts/atomic_windows_wheel_fingerprint.py\\" '
        '--output \\"$fingerprint_for_cibuildwheel\\"'
    )
    assert fingerprint_invocation in text
    assert "--expected-sha256 $normalized_expected" not in text
    assert 'export CIBW_BEFORE_BUILD="$CIBW_BEFORE_BUILD && $fingerprint_command"' in text
    assert text.index("release-build-requirements.txt") < text.index(
        "atomic_windows_wheel_fingerprint.py\\\" --output"
    )
    assert "Windows build did not create its toolchain fingerprint" in text
    assert "actual_fingerprint_sha256=$(python -I -c" in text
    assert '[ "$actual_fingerprint_sha256" = "$normalized_expected" ]' in text
    assert (
        'cmp -s -- "$fingerprint_for_host" '
        '"$WINDOWS_WHEEL_FINGERPRINT_DOCUMENT"' in text
    )
    assert "actual Windows wheel fingerprint does not match" in text
    assert text.index("actual_fingerprint_sha256=$(python -I -c") > text.index(
        "python -m cibuildwheel"
    )


def test_recipe_is_bound_to_the_exact_frozen_windows_fingerprint() -> None:
    payload = WINDOWS_FINGERPRINT_DOCUMENT.read_bytes()
    assert payload
    assert hashlib.sha256(payload).hexdigest() == WINDOWS_FINGERPRINT_SHA256

    text = recipe_text()
    assert text.count("normalized_expected=$(printf") == 1
    assert text.index("normalized_expected=$(printf") < text.index(
        "frozen_fingerprint_sha256=$(python -I -c"
    )
    assert text.index("frozen_fingerprint_sha256=$(python -I -c") < text.index(
        "fingerprint_command="
    )


def test_recipe_accepts_only_one_nonempty_regular_wheel() -> None:
    text = recipe_text()
    assert 'shopt -s nullglob' in text
    assert 'wheels=("$output_absolute"/*.whl)' in text
    assert '[ "${#wheels[@]}" -eq 1 ]' in text
    assert '[ -f "${wheels[0]}" ]' in text
    assert '[ ! -L "${wheels[0]}" ]' in text
    assert '[ -s "${wheels[0]}" ]' in text
    assert 'printf \'%s\\n\' "${wheels[0]}"' in text
