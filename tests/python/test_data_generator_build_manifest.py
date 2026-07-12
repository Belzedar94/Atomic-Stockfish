from __future__ import annotations

from pathlib import Path
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import legacy_pipeline_build_manifest as build_manifest
import legacy_pipeline_e2e as pipeline
import run_hito5


@pytest.mark.parametrize(
    ("atomic_recipe", "generator_recipe", "platform", "target"),
    (
        (
            "strong-local-atomic-windows-v1",
            "strong-local-atomic-data-generator-windows-v1",
            "win32",
            build_manifest.X86_64_BMI2_RELEASE_TARGET,
        ),
        (
            "synthetic-ci-atomic-linux-v1",
            "synthetic-ci-atomic-data-generator-linux-v1",
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


def test_unknown_atomic_recipe_has_no_implicit_generator_recipe() -> None:
    with pytest.raises(AssertionError, match="has no data-generator recipe"):
        build_manifest.atomic_data_generator_recipe_for("untracked-atomic-build")


@pytest.mark.parametrize(
    ("recipe_name", "root"),
    (
        (
            "strong-local-atomic-data-generator-windows-v1",
            Path("C:/atomic-stockfish"),
        ),
        (
            "synthetic-ci-atomic-data-generator-linux-v1",
            Path("/atomic-stockfish"),
        ),
    ),
)
def test_generator_recipe_restores_the_normal_engine(
    recipe_name: str, root: Path
) -> None:
    commands = build_manifest.RECIPES[recipe_name].commands(root)
    generator_command = " ".join(commands[-2])
    normal_command = " ".join(commands[-1])
    assert "data-generator" in generator_command
    assert "build" in normal_command
    assert "data-generator" not in normal_command


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


@pytest.mark.parametrize("mode", ("release", "smoke"))
@pytest.mark.parametrize("missing_index", range(7))
def test_hito5_rejects_each_incomplete_pipeline_set(
    missing_index: int, mode: str
) -> None:
    complete = tuple(Path(f"input-{index}") for index in range(7))
    incomplete = list(complete)
    incomplete[missing_index] = None
    with pytest.raises(run_hito5.GateFailure, match="all four clean-build"):
        run_hito5.validate_pipeline_configuration(tuple(incomplete), mode)


def test_hito5_release_requires_all_seven_pipeline_inputs() -> None:
    complete = tuple(Path(f"input-{index}") for index in range(7))
    run_hito5.validate_pipeline_configuration(complete, "release")
    with pytest.raises(run_hito5.GateFailure, match="release mode requires"):
        run_hito5.validate_pipeline_configuration((None,) * 7, "release")


def test_hito5_smoke_may_omit_the_complete_pipeline_set() -> None:
    run_hito5.validate_pipeline_configuration((None,) * 7, "smoke")
    with pytest.raises(run_hito5.GateFailure, match="expected seven"):
        run_hito5.validate_pipeline_configuration((None,) * 6, "smoke")


def test_hito5_forwards_the_exact_cross_repository_command() -> None:
    paths = {
        name: Path(name)
        for name in (
            "tools",
            "trainer",
            "native",
            "generator",
            "tools.json",
            "trainer.json",
            "atomic.json",
            "generator.json",
            "source.nnue",
        )
    }
    command = run_hito5.build_pipeline_e2e_command(
        python="python-fixture",
        tools_engine=paths["tools"],
        trainer_root=paths["trainer"],
        native=paths["native"],
        atomic_data_generator=paths["generator"],
        tools_build_manifest=paths["tools.json"],
        trainer_build_manifest=paths["trainer.json"],
        atomic_build_manifest=paths["atomic.json"],
        atomic_data_generator_build_manifest=paths["generator.json"],
        source_net=paths["source.nnue"],
    )
    assert command == [
        "python-fixture",
        str(run_hito5.REPO_ROOT / "tests" / "legacy_pipeline_e2e.py"),
        "--profile",
        "strong-local",
        "--tools-engine",
        "tools",
        "--trainer-root",
        "trainer",
        "--engine",
        "native",
        "--atomic-data-generator",
        "generator",
        "--tools-build-manifest",
        "tools.json",
        "--trainer-build-manifest",
        "trainer.json",
        "--atomic-build-manifest",
        "atomic.json",
        "--atomic-data-generator-build-manifest",
        "generator.json",
        "--source-net",
        "source.nnue",
    ]
