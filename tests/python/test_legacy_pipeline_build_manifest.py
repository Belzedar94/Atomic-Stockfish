from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import legacy_pipeline_build_manifest as build_manifest


BMI2_COMPILER_OUTPUT = """
Compiled by                : g++ (GNUC) 15.2.0 on MinGW64
Compilation architecture   : x86-64-bmi2
Compilation settings       : 64bit BMI2 AVX2 SSE41 SSSE3 SSE2 POPCNT
Compiler __VERSION__ macro : 15.2.0
""".strip()

X86_64_COMPILER_OUTPUT = """
Compiled by                : g++ (GNUC) 15.2.0 on MinGW64
Compilation architecture   : x86-64
Compilation settings       : 64bit SSE2
Compiler __VERSION__ macro : 15.2.0
""".strip()


@pytest.mark.parametrize(
    ("recipe_name", "root", "artifact"),
    (
        (
            "strong-local-tools-windows-v2",
            Path("C:/variant-nnue-tools"),
            "src/atomic-data-tools.exe",
        ),
        (
            "synthetic-ci-tools-linux-v2",
            Path("/variant-nnue-tools"),
            "src/atomic-data-tools",
        ),
    ),
)
def test_tools_recipe_uses_the_pinned_root_wrapper_contract(
    recipe_name: str, root: Path, artifact: str
) -> None:
    recipe = build_manifest.RECIPES[recipe_name]
    rendered = tuple(" ".join(command) for command in recipe.commands(root))
    assert recipe.artifact_relative == artifact
    assert len(rendered) == 2
    if recipe.platform == "win32":
        assert recipe.commands(root)[0][-1].endswith(
            "&& make ARCH=x86-64 COMP=mingw verify-engine-pin"
        )
        assert recipe.commands(root)[1][-1].endswith(
            "&& make -j2 ARCH=x86-64 COMP=mingw data-tools"
        )
    else:
        assert recipe.commands(root) == (
            ("make", "ARCH=x86-64", "COMP=gcc", "verify-engine-pin"),
            ("make", "-j2", "ARCH=x86-64", "COMP=gcc", "data-tools"),
        )
    assert "make -C src" not in rendered[0]
    assert "make -C src" not in rendered[1]
    assert "all=no" not in "\n".join(rendered)
    assert "largeboards=no" not in "\n".join(rendered)
    assert "nnue=no" not in "\n".join(rendered)
    assert "data-generator" not in "\n".join(rendered)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def clean_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    tracked = root / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "/artifact.bin\n/build/\n", encoding="utf-8"
    )
    git(root, "add", "tracked.txt", ".gitignore")
    git(
        root,
        "-c",
        "user.name=Atomic CI",
        "-c",
        "user.email=atomic-ci@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return root, git(root, "rev-parse", "HEAD")


def fixture_recipe(
    root: Path,
    *,
    name: str = "fixture-v1",
    command: tuple[str, ...] | None = None,
    platform: str = sys.platform,
    artifact_relative: str = "artifact.bin",
    role: str = "trainer",
    expected_engine_target: str | None = None,
) -> build_manifest.BuildRecipe:
    artifact = root / artifact_relative
    clean_dir = root / "build" / "fixture"
    if command is None:
        command = (
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "artifact=Path(sys.argv[1]); stale=Path(sys.argv[2]); "
                "assert not artifact.exists(); assert not stale.exists(); "
                "artifact.write_bytes(b'new-build')"
            ),
            str(artifact),
            str(clean_dir / "stale.txt"),
        )
    return build_manifest.BuildRecipe(
        name,
        role,
        platform,
        artifact_relative,
        lambda checkout: (command,),
        "build/fixture",
        expected_engine_target,
    )


def fake_trainer_observation(
    artifact: Path, output: str = "fixture-toolchain"
) -> build_manifest.ToolchainObservation:
    fingerprint = build_manifest.fingerprint_file(
        artifact, label="fixture_toolchain"
    )
    return build_manifest.ToolchainObservation(
        output, {"compiler": fingerprint, "linker": fingerprint}
    )


def successful_manifest(tmp_path: Path, monkeypatch):
    root, head = clean_repo(tmp_path)
    artifact = root / "artifact.bin"
    artifact.write_bytes(b"stale")
    clean_dir = root / "build" / "fixture"
    clean_dir.mkdir(parents=True)
    (clean_dir / "stale.txt").write_text("stale", encoding="utf-8")
    recipe = fixture_recipe(root)
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    monkeypatch.setattr(
        build_manifest,
        "_trainer_toolchain_observation",
        lambda recipe, checkout: fake_trainer_observation(
            checkout / recipe.artifact_relative
        ),
    )
    output = tmp_path / "manifest.json"
    recorded = build_manifest.clean_build_manifest(
        recipe_name=recipe.name,
        repository_root=root,
        output=output,
    )
    return root, head, artifact, output, recorded


def successful_engine_manifest(
    tmp_path: Path, monkeypatch, *, role: str = "atomic"
):
    root, head = clean_repo(tmp_path)
    artifact = root / "artifact.bin"
    recipe = fixture_recipe(
        root,
        role=role,
        expected_engine_target=build_manifest.X86_64_BMI2_RELEASE_TARGET,
    )
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    monkeypatch.setattr(
        build_manifest,
        "_toolchain_output",
        lambda *args: BMI2_COMPILER_OUTPUT,
    )
    output = tmp_path / "manifest.json"
    recorded = build_manifest.clean_build_manifest(
        recipe_name=recipe.name,
        repository_root=root,
        output=output,
    )
    return root, head, artifact, output, recorded


def test_data_generator_role_uses_the_engine_manifest_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, head, artifact, output, recorded = successful_engine_manifest(
        tmp_path, monkeypatch, role="atomic-data-generator"
    )
    assert recorded.role == "atomic-data-generator"
    verified = build_manifest.verify_build_manifest(
        output,
        expected_recipe="fixture-v1",
        repository_root=root,
        artifact=artifact,
        expected_commit=head,
    )
    assert verified.artifact_sha256 == recorded.artifact_sha256


def trainer_cmake_fixture(
    root: Path, *, architecture: str = "x64", include_linker: bool = True
) -> tuple[Path, Path]:
    build = root / "build" / "fixture"
    metadata_dir = build / "CMakeFiles" / "3.31.8"
    metadata_dir.mkdir(parents=True)
    compiler = build / "fake-cl.exe"
    compiler.write_bytes(b"compiler")
    linker = build / "fake-link.exe"
    linker.write_bytes(b"linker")
    linker_line = (
        f'set(CMAKE_LINKER "{linker.as_posix()}")'
        if include_linker
        else ""
    )
    (metadata_dir / "CMakeCXXCompiler.cmake").write_text(
        "\n".join(
            (
                f'set(CMAKE_CXX_COMPILER "{compiler.as_posix()}")',
                'set(CMAKE_CXX_COMPILER_ID "MSVC")',
                'set(CMAKE_CXX_COMPILER_VERSION "19.44.35215.0")',
                'set(CMAKE_CXX_COMPILER_TARGET "")',
                f"set(CMAKE_CXX_COMPILER_ARCHITECTURE_ID {architecture})",
                'set(CMAKE_CXX_LIBRARY_ARCHITECTURE "")',
                linker_line,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (build / "CMakeCache.txt").write_text(
        "\n".join(
            (
                "CMAKE_GENERATOR:INTERNAL=Visual Studio 17 2022",
                f"CMAKE_GENERATOR_PLATFORM:INTERNAL={architecture}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return compiler, linker


def test_clean_build_deletes_stale_inputs_and_verifies_manifest(
    tmp_path, monkeypatch
):
    root, head, artifact, output, recorded = successful_manifest(
        tmp_path, monkeypatch
    )
    assert artifact.read_bytes() == b"new-build"
    assert recorded.repository_commit == head
    verified = build_manifest.verify_build_manifest(
        output,
        expected_recipe="fixture-v1",
        repository_root=root,
        artifact=artifact,
        expected_commit=head,
    )
    assert verified.artifact_sha256 == recorded.artifact_sha256
    document = json.loads(output.read_text(encoding="utf-8"))
    assert set(document["build"]["toolchain_artifacts"]) == {
        "compiler",
        "linker",
    }
    for fingerprint in document["build"]["toolchain_artifacts"].values():
        assert set(fingerprint) == {"path", "bytes", "sha256"}


def test_trainer_provenance_probes_cmake_compiler_identity_and_target(
    tmp_path, monkeypatch
):
    root, _ = clean_repo(tmp_path)
    compiler, linker = trainer_cmake_fixture(root)
    calls = []

    def probe(command, *, cwd, label, allow_nonzero=False):
        calls.append((tuple(command), cwd, label, allow_nonzero))
        if tuple(command) == ("cmake", "--version"):
            return "cmake version 3.31.8"
        assert tuple(command) == (str(compiler.resolve()), "/Bv")
        return (
            "Microsoft (R) C/C++ Optimizing Compiler "
            "Version 19.44.35215 for x64"
        )

    monkeypatch.setattr(build_manifest, "_run_toolchain_command", probe)
    recipe = fixture_recipe(root)
    provenance = build_manifest._toolchain_output(
        recipe, root / "artifact.bin", root
    )
    assert f"CMAKE_CXX_COMPILER={compiler.resolve()}" in provenance
    assert "CMAKE_CXX_COMPILER_ID=MSVC" in provenance
    assert "CMAKE_CXX_COMPILER_VERSION=19.44.35215.0" in provenance
    assert f"CMAKE_LINKER={linker.resolve()}" in provenance
    assert "CMAKE_CXX_COMPILER_BYTES=8" in provenance
    assert "CMAKE_CXX_COMPILER_SHA256=" in provenance
    assert "CMAKE_LINKER_BYTES=6" in provenance
    assert "CMAKE_LINKER_SHA256=" in provenance
    assert "CMAKE_CXX_COMPILER_ARCHITECTURE_ID=x64" in provenance
    assert "CMAKE_CXX_COMPILER_PROBE:" in provenance
    assert "Version 19.44.35215 for x64" in provenance
    assert "CMAKE_CXX_TARGET_PROBE:\nx64" in provenance
    assert len(calls) == 2


def test_trainer_provenance_rejects_missing_target_and_architecture(
    tmp_path, monkeypatch
):
    root, _ = clean_repo(tmp_path)
    trainer_cmake_fixture(root, architecture="")
    monkeypatch.setattr(
        build_manifest,
        "_run_toolchain_command",
        lambda *args, **kwargs: pytest.fail("compiler must not be probed"),
    )
    with pytest.raises(AssertionError, match="no compiler target or architecture"):
        build_manifest._toolchain_output(
            fixture_recipe(root), root / "artifact.bin", root
        )


def test_trainer_provenance_fails_closed_without_linker(tmp_path, monkeypatch):
    root, _ = clean_repo(tmp_path)
    trainer_cmake_fixture(root, include_linker=False)
    monkeypatch.setattr(
        build_manifest,
        "_run_toolchain_command",
        lambda *args, **kwargs: pytest.fail("compiler must not be probed"),
    )
    with pytest.raises(AssertionError, match="has no CMAKE_LINKER"):
        build_manifest._toolchain_output(
            fixture_recipe(root), root / "artifact.bin", root
        )


@pytest.mark.parametrize(
    "mutation, expected",
    (
        (lambda doc: doc.update(extra=True), "keys must be exactly"),
        (lambda doc: doc.update(role="tools"), "role/recipe mismatch"),
        (
            lambda doc: doc["repository"].update(commit="f" * 40),
            "commit/clean state mismatch",
        ),
        (
            lambda doc: doc["artifact"].update(sha256="0" * 64),
            "SHA-256 mismatch",
        ),
        (
            lambda doc: doc["build"].update(commands=[]),
            "commands do not match",
        ),
        (
            lambda doc: doc["build"].update(toolchain="tampered provenance"),
            "toolchain provenance mismatch",
        ),
    ),
)
def test_verify_rejects_tampered_manifest(
    tmp_path, monkeypatch, mutation, expected
):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    document = json.loads(output.read_text(encoding="utf-8"))
    mutation(document)
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match=expected):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_verify_rejects_duplicate_json_keys(tmp_path, monkeypatch):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    output.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


@pytest.mark.parametrize(
    "field,value,expected",
    (
        ("log_sha256", "a" * 64, "64 uppercase hexadecimal"),
        ("log_sha256", "A" * 63, "64 uppercase hexadecimal"),
        ("log_sha256", "G" * 64, "64 uppercase hexadecimal"),
        ("log_sha256", " " + "A" * 64, "64 uppercase hexadecimal"),
        ("recorded_utc", "not-a-timestamp", "parseable UTC timestamp"),
        ("recorded_utc", "2026-07-11T12:00:00", "timezone-aware UTC"),
        ("recorded_utc", "2026-07-11T12:00:00+01:00", "timezone-aware UTC"),
    ),
)
def test_verify_rejects_invalid_build_metadata(
    tmp_path, monkeypatch, field, value, expected
):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    document = json.loads(output.read_text(encoding="utf-8"))
    document["build"][field] = value
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match=expected):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


@pytest.mark.parametrize(
    "mutation,expected",
    (
        (
            lambda doc: doc["build"]["toolchain_artifacts"].pop("linker"),
            "keys must be exactly",
        ),
        (
            lambda doc: doc["build"]["toolchain_artifacts"]["compiler"].update(
                extra=True
            ),
            "keys must be exactly",
        ),
        (
            lambda doc: doc["build"]["toolchain_artifacts"]["compiler"].update(
                bytes=True
            ),
            "positive JSON integer",
        ),
        (
            lambda doc: doc["build"]["toolchain_artifacts"]["linker"].update(
                sha256="a" * 64
            ),
            "64 uppercase hexadecimal",
        ),
        (
            lambda doc: doc["build"]["toolchain_artifacts"]["compiler"].update(
                sha256="0" * 64
            ),
            "toolchain artifact provenance mismatch",
        ),
    ),
)
def test_verify_rejects_tampered_toolchain_artifacts(
    tmp_path, monkeypatch, mutation, expected
):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    document = json.loads(output.read_text(encoding="utf-8"))
    mutation(document)
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match=expected):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_verify_rejects_boolean_artifact_byte_count(tmp_path, monkeypatch):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    document = json.loads(output.read_text(encoding="utf-8"))
    document["artifact"]["bytes"] = True
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match="positive JSON integer"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_verify_requires_cli_artifact_to_match_recipe_path(tmp_path, monkeypatch):
    root, head, _, output, _ = successful_manifest(tmp_path, monkeypatch)
    alternate = root / "build" / "alternate.bin"
    alternate.parent.mkdir(parents=True, exist_ok=True)
    alternate.write_bytes(b"new-build")
    with pytest.raises(AssertionError, match="tracked recipe artifact path"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=alternate,
            expected_commit=head,
        )


def test_verify_rejects_wrong_platform(tmp_path, monkeypatch):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    wrong_platform = fixture_recipe(root, platform="never")
    monkeypatch.setitem(
        build_manifest.RECIPES, wrong_platform.name, wrong_platform
    )
    with pytest.raises(AssertionError, match="requires never"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_verify_rejects_manifest_inside_repository(tmp_path, monkeypatch):
    root, head, artifact, output, _ = successful_manifest(tmp_path, monkeypatch)
    inside = root / "build" / "manifest.json"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(output.read_bytes())
    with pytest.raises(AssertionError, match="outside its checkout"):
        build_manifest.verify_build_manifest(
            inside,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_clean_build_rejects_engine_that_ignores_recipe_target(
    tmp_path, monkeypatch
):
    root, _ = clean_repo(tmp_path)
    recipe = fixture_recipe(
        root,
        role="atomic",
        expected_engine_target=build_manifest.X86_64_BMI2_RELEASE_TARGET,
    )
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    monkeypatch.setattr(
        build_manifest,
        "_toolchain_output",
        lambda *args: X86_64_COMPILER_OUTPUT,
    )
    output = tmp_path / "wrong-target.json"
    with pytest.raises(AssertionError, match="artifact target mismatch"):
        build_manifest.clean_build_manifest(
            recipe_name=recipe.name,
            repository_root=root,
            output=output,
        )
    assert not output.exists()


def test_verify_reprobes_and_rejects_current_engine_target(
    tmp_path, monkeypatch
):
    root, head, artifact, output, _ = successful_engine_manifest(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        build_manifest,
        "_toolchain_output",
        lambda *args: X86_64_COMPILER_OUTPUT,
    )
    with pytest.raises(AssertionError, match="artifact target mismatch"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_verify_rejects_tampered_engine_compiler_output(
    tmp_path, monkeypatch
):
    root, head, artifact, output, _ = successful_engine_manifest(
        tmp_path, monkeypatch
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    document["build"]["toolchain"] = BMI2_COMPILER_OUTPUT.replace(
        "15.2.0", "14.2.0"
    )
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match="toolchain provenance mismatch"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


def test_engine_manifest_rejects_trainer_toolchain_artifacts(
    tmp_path, monkeypatch
):
    root, head, artifact, output, _ = successful_engine_manifest(
        tmp_path, monkeypatch
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    document["build"]["toolchain_artifacts"] = {}
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AssertionError, match="build keys must be exactly"):
        build_manifest.verify_build_manifest(
            output,
            expected_recipe="fixture-v1",
            repository_root=root,
            artifact=artifact,
            expected_commit=head,
        )


@pytest.mark.parametrize(
    "mutation,expected",
    (
        ("replace-artifact", "changed during toolchain probe"),
        ("dirty-checkout", "not clean"),
    ),
)
def test_clean_build_rejects_toolchain_probe_side_effects(
    tmp_path, monkeypatch, mutation, expected
):
    root, _ = clean_repo(tmp_path)
    recipe = fixture_recipe(root)
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)

    def mutating_probe(observed_recipe, checkout):
        artifact = checkout / observed_recipe.artifact_relative
        if mutation == "replace-artifact":
            artifact.write_bytes(b"self-replaced")
        else:
            (checkout / "tracked.txt").write_text("probe dirtied\n", encoding="utf-8")
        return fake_trainer_observation(artifact)

    monkeypatch.setattr(
        build_manifest, "_trainer_toolchain_observation", mutating_probe
    )
    output = tmp_path / f"{mutation}.json"
    with pytest.raises(AssertionError, match=expected):
        build_manifest.clean_build_manifest(
            recipe_name=recipe.name,
            repository_root=root,
            output=output,
        )
    assert not output.exists()


@pytest.mark.parametrize("mode", ("failure", "missing", "dirty"))
def test_clean_build_failure_never_writes_manifest(
    tmp_path, monkeypatch, mode
):
    root, _ = clean_repo(tmp_path)
    artifact = root / "artifact.bin"
    if mode == "failure":
        script = "raise SystemExit(3)"
    elif mode == "missing":
        script = "pass"
    else:
        script = (
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_bytes(b'built'); "
            "Path(sys.argv[2]).write_text('dirty')"
        )
    command = (
        sys.executable,
        "-c",
        script,
        str(artifact),
        str(root / "tracked.txt"),
    )
    recipe = fixture_recipe(root, command=command)
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    monkeypatch.setattr(
        build_manifest, "_toolchain_output", lambda *args: "fixture-toolchain"
    )
    output = tmp_path / "manifest.json"
    with pytest.raises(AssertionError):
        build_manifest.clean_build_manifest(
            recipe_name=recipe.name,
            repository_root=root,
            output=output,
        )
    assert not output.exists()


def test_clean_build_rejects_dirty_wrong_platform_and_outside_artifact(
    tmp_path, monkeypatch
):
    root, _ = clean_repo(tmp_path)
    (root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    recipe = fixture_recipe(root)
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    with pytest.raises(AssertionError, match="not clean"):
        build_manifest.clean_build_manifest(
            recipe_name=recipe.name,
            repository_root=root,
            output=tmp_path / "dirty.json",
        )

    git(root, "restore", "tracked.txt")
    wrong = fixture_recipe(root, name="wrong-platform", platform="never")
    monkeypatch.setitem(build_manifest.RECIPES, wrong.name, wrong)
    with pytest.raises(AssertionError, match="requires never"):
        build_manifest.clean_build_manifest(
            recipe_name=wrong.name,
            repository_root=root,
            output=tmp_path / "platform.json",
        )

    outside = fixture_recipe(
        root, name="outside", artifact_relative="../outside.bin"
    )
    monkeypatch.setitem(build_manifest.RECIPES, outside.name, outside)
    with pytest.raises(AssertionError, match="outside"):
        build_manifest.clean_build_manifest(
            recipe_name=outside.name,
            repository_root=root,
            output=tmp_path / "outside.json",
        )


def test_clean_build_refuses_existing_manifest_before_running(
    tmp_path, monkeypatch
):
    root, _ = clean_repo(tmp_path)
    marker = root / "should-not-run.txt"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        str(marker),
    )
    recipe = fixture_recipe(root, command=command)
    monkeypatch.setitem(build_manifest.RECIPES, recipe.name, recipe)
    output = tmp_path / "manifest.json"
    output.write_text("old manifest", encoding="utf-8")
    with pytest.raises(AssertionError, match="refusing to overwrite"):
        build_manifest.clean_build_manifest(
            recipe_name=recipe.name,
            repository_root=root,
            output=output,
        )
    assert not marker.exists()
