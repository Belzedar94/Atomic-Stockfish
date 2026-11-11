from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import legacy_pipeline_build_manifest as build_manifest
import legacy_pipeline_e2e as pipeline
import data_generator


IDENTITY_ARGUMENTS = (
    "GIT_SHA=01234567",
    "GIT_SHA_FULL=0123456789abcdef0123456789abcdef01234567",
    "GIT_DATE=20260714",
)


def _continued_make_assignment(makefile: str, variable: str) -> str:
    lines = makefile.splitlines()
    prefix = f"{variable} ="
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue

        parts: list[str] = []
        current = line.split("=", 1)[1].strip()
        while True:
            continued = current.endswith("\\")
            parts.append(current[:-1].strip() if continued else current)
            if not continued:
                return " ".join(parts)
            index += 1
            assert index < len(lines), f"unterminated {variable} assignment"
            current = lines[index].strip()

    raise AssertionError(f"missing {variable} assignment")


def test_ci_pins_generator_identity_without_weakening_clean_tree_fallback() -> None:
    makefile = (TESTS_DIR.parent / "src" / "Makefile").read_text(encoding="utf-8")
    workflow = (TESTS_DIR.parent / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    assert "GIT_SHA_FULL    ?=" in makefile
    assert "git status --porcelain --untracked-files=normal" in makefile
    assert "GIT_SHA_FULL: ${{ github.sha }}" in workflow


def test_manifest_oracle_uses_the_same_authenticated_ci_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "89abcdef0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("GIT_SHA_FULL", commit)
    assert data_generator.current_repository_commit() == commit


@pytest.mark.parametrize(
    "commit",
    (
        "",
        "ABC",
        "A" * 40,
        "0" * 39,
        "g" * 40,
        " " + "0" * 40,
        "0" * 40 + " ",
        "0" * 40 + "\n",
    ),
)
def test_manifest_oracle_rejects_invalid_ci_pin(
    commit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_SHA_FULL", commit)
    with pytest.raises(AssertionError, match="40 lower-case hexadecimal"):
        data_generator.current_repository_commit()


def test_generator_search_uses_the_canonical_worker_baseline() -> None:
    history = (TESTS_DIR.parent / "src" / "history.h").read_text(encoding="utf-8")
    search = (TESTS_DIR.parent / "src" / "search.cpp").read_text(encoding="utf-8")
    generator = (
        TESTS_DIR.parent / "src" / "data" / "training_data_generator.cpp"
    ).read_text(encoding="utf-8")
    makefile = (TESTS_DIR.parent / "src" / "Makefile").read_text(encoding="utf-8")

    assert "void clear_for_search(usize threadIdx, usize numaTotal)" in history
    assert "correctionHistory.clear_range(-6, threadIdx, numaTotal);" in history
    assert "pawnHistory.clear_range(-1262, threadIdx, numaTotal);" in history
    assert "h.fill(-552);" in history
    assert "clear_for_new_game(sharedHistory, numaThreadIdx, numaTotal);" in search
    tt_rule = makefile.index("atomic_data_generator_tt.o: tt.cpp")
    next_rule = makefile.index("atomic_data_generator_training_data_generator.o:", tt_rule)
    assert "-DATOMIC_DATA_GENERATOR" in makefile[tt_rule:next_rule]
    # The LMR log scale stopped being a literal when the search constants became
    # tunable for the SPSA campaign. The reset formula is pinned through the
    # tunable and the converged spsa90 default is pinned next to it, so the pair
    # still nails the exact number the generator bakes in. TUNABLE_INT is the
    # storage class from tune.h: a constant here, a UCI spin option only under
    # `make tune=yes`, and the same 2736 either way.
    for local_reset in (
        "mainHistory.fill(-5);",
        "captureHistory.fill(-699);",
        "ttMoveHistory = 0;",
        "h.fill(5);",
        "reductions[i] = int(LmrLogScale / 128.0 * std::log(i));",
        "TUNABLE_INT LmrLogScale          = 2736;",
        "accumulator.rebind(network[numaAccessToken]);",
        "lowPlyHistory.fill(100);",
    ):
        assert local_reset in search
    assert "2834" not in search
    for baseline in ("clear_range(-6", "clear_range(-1262", "fill(-552"):
        assert baseline not in search
        assert baseline not in generator


def test_data_generator_object_manifest_is_target_isolated() -> None:
    makefile = (TESTS_DIR.parent / "src" / "Makefile").read_text(encoding="utf-8")
    objects = _continued_make_assignment(makefile, "ATOMIC_DATA_GENERATOR_OBJS")

    filtered = re.search(r"\$\(filter-out\s+([^,]+),\s*\$\(OBJS\)\)", objects)
    assert filtered is not None
    assert set(filtered.group(1).split()) == {"uci.o", "search.o", "tt.o"}

    for target_specific in (
        "atomic_data_generator_uci.o",
        "atomic_data_generator_search.o",
        "atomic_data_generator_tt.o",
    ):
        assert objects.split().count(target_specific) == 1


@pytest.mark.parametrize(
    ("atomic_recipe", "generator_recipe", "platform", "target"),
    (
        (
            "strong-local-atomic-windows-v2",
            "strong-local-atomic-data-generator-windows-v2",
            "win32",
            build_manifest.X86_64_BMI2_RELEASE_TARGET,
        ),
        (
            "synthetic-ci-atomic-linux-v2",
            "synthetic-ci-atomic-data-generator-linux-v2",
            "linux",
            build_manifest.X86_64_RELEASE_TARGET,
        ),
    ),
)
def test_atomic_data_generator_has_a_distinct_authenticated_recipe(
    atomic_recipe: str,
    generator_recipe: str,
    platform: str,
    target: str,
) -> None:
    assert build_manifest.atomic_data_generator_recipe_for(atomic_recipe) == (
        generator_recipe
    )
    atomic = build_manifest.RECIPES[atomic_recipe]
    generator = build_manifest.RECIPES[generator_recipe]
    assert generator.role == "atomic-data-generator"
    assert generator.platform == platform
    assert generator.expected_engine_target == target
    assert generator.artifact_relative != atomic.artifact_relative
    assert "data-generator" in generator.artifact_relative
    assert "pipeline" in generator.artifact_relative


def test_unknown_atomic_recipe_has_no_implicit_generator_recipe() -> None:
    with pytest.raises(AssertionError, match="has no data-generator recipe"):
        build_manifest.atomic_data_generator_recipe_for("untracked-atomic-build")


@pytest.mark.parametrize(
    ("recipe_name", "root"),
    (
        (
            "strong-local-atomic-data-generator-windows-v2",
            Path("C:/atomic-stockfish"),
        ),
        (
            "synthetic-ci-atomic-data-generator-linux-v2",
            Path("/atomic-stockfish"),
        ),
    ),
)
def test_generator_recipe_restores_the_normal_engine(
    recipe_name: str, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        build_manifest,
        "_git_make_identity_arguments",
        lambda _root: IDENTITY_ARGUMENTS,
    )
    recipe = build_manifest.RECIPES[recipe_name]
    commands = recipe.commands(root)
    generator_command = " ".join(commands[-3])
    copy_command = " ".join(commands[-2])
    normal_command = " ".join(commands[-1])
    assert "data-generator" in generator_command
    assert "data-generator" in copy_command
    assert recipe.artifact_relative in copy_command
    assert "build" in normal_command
    assert "data-generator" not in normal_command


@pytest.mark.parametrize(
    ("recipe_name", "root"),
    (
        ("strong-local-atomic-windows-v2", Path("C:/atomic-stockfish")),
        ("synthetic-ci-atomic-linux-v2", Path("/atomic-stockfish")),
    ),
)
def test_normal_recipe_preserves_an_authenticated_pipeline_copy(
    recipe_name: str, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        build_manifest,
        "_git_make_identity_arguments",
        lambda _root: IDENTITY_ARGUMENTS,
    )
    recipe = build_manifest.RECIPES[recipe_name]
    commands = recipe.commands(root)
    copy_command = " ".join(commands[-1])
    assert recipe.artifact_relative.endswith(
        ("atomic-stockfish-pipeline", "atomic-stockfish-pipeline.exe")
    )
    assert recipe.artifact_relative in copy_command


@pytest.mark.parametrize(
    "recipe_name",
    (
        "strong-local-atomic-windows-v2",
        "strong-local-atomic-data-generator-windows-v2",
        "synthetic-ci-atomic-linux-v2",
        "synthetic-ci-atomic-data-generator-linux-v2",
    ),
)
def test_atomic_build_commands_inject_authenticated_git_identity(
    recipe_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        build_manifest,
        "_git_make_identity_arguments",
        lambda _root: IDENTITY_ARGUMENTS,
    )
    commands = build_manifest.RECIPES[recipe_name].commands(
        Path("C:/atomic-stockfish")
    )
    build_commands = [
        command
        for command in commands
        if " clean" not in " ".join(command) and "cp " not in " ".join(command)
    ]
    assert build_commands
    for command in build_commands:
        rendered = " ".join(command)
        for argument in IDENTITY_ARGUMENTS:
            assert argument in rendered


def test_git_make_identity_matches_the_current_checkout() -> None:
    root = Path(__file__).resolve().parents[2]
    arguments = build_manifest._git_make_identity_arguments(root)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip().lower()
    assert arguments[0] == f"GIT_SHA={head[:8]}"
    assert arguments[1] == f"GIT_SHA_FULL={head}"
    assert arguments[2].startswith("GIT_DATE=")


@pytest.mark.parametrize(
    ("head", "date", "message"),
    (
        ("unknown", "20260714", "40-digit SHA-1"),
        ("0" * 40, "unknown", "YYYYMMDD"),
    ),
)
def test_git_make_identity_rejects_unverifiable_values(
    head: str,
    date: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter((head, date))
    monkeypatch.setattr(build_manifest, "_git_output", lambda *_args: next(values))
    with pytest.raises(AssertionError, match=message):
        build_manifest._git_make_identity_arguments(Path("C:/atomic-stockfish"))


def _e2e_arguments() -> list[str]:
    return [
        "--tools-engine",
        "tools",
        "--trainer-root",
        "trainer",
        "--engine",
        "atomic",
        "--tools-build-manifest",
        "tools.json",
        "--trainer-build-manifest",
        "trainer.json",
        "--atomic-build-manifest",
        "atomic.json",
        "--atomic-data-generator",
        "generator",
        "--atomic-data-generator-build-manifest",
        "generator.json",
    ]


@pytest.mark.parametrize(
    "missing_option",
    (
        "--atomic-data-generator",
        "--atomic-data-generator-build-manifest",
    ),
)
def test_e2e_requires_generator_artifact_and_manifest(
    missing_option: str,
) -> None:
    arguments = _e2e_arguments()
    index = arguments.index(missing_option)
    del arguments[index : index + 2]
    with pytest.raises(SystemExit):
        pipeline.parse_args(arguments)


def test_e2e_accepts_generator_artifact_with_its_manifest() -> None:
    args = pipeline.parse_args(_e2e_arguments())
    assert args.atomic_data_generator == Path("generator")
    assert args.atomic_data_generator_build_manifest == Path("generator.json")


def test_fixture_measurement_is_restricted_to_synthetic_profile() -> None:
    with pytest.raises(SystemExit):
        pipeline.parse_args(
            [*_e2e_arguments(), "--measure-synthetic-fixture"]
        )
    args = pipeline.parse_args(
        [
            *_e2e_arguments(),
            "--profile",
            "synthetic-ci",
            "--measure-synthetic-fixture",
        ]
    )
    assert args.measure_synthetic_fixture is True
