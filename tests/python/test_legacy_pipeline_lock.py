from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import legacy_pipeline_lock as pipeline_lock
import legacy_pipeline_build_manifest as build_manifest


def resolved_document() -> dict[str, object]:
    schema = pipeline_lock.load_training_data_schema()
    return {
        "schema_version": 4,
        "training_data_schema": {
            "path": pipeline_lock.EXPECTED_TRAINING_DATA_SCHEMA_PATH,
            "schema_id": schema.schema_id,
            "sha256": schema.sha256,
            "record_size": schema.record_size,
        },
        "repositories": {
            "tools": {
                "repository": "Belzedar94/variant-nnue-tools",
                "commit": "a" * 40,
                "resolved": True,
            },
            "trainer": {
                "repository": "Belzedar94/variant-nnue-pytorch",
                "commit": "b" * 40,
                "resolved": True,
            },
        },
        "profiles": {
            "strong-local": {
                "source_kind": "external",
                "records": 8,
                "seed": "20260711",
                "source_net_sha256": "e" * 64,
                "data_sha256": "a" * 64,
                "hashes_resolved": True,
                "build_recipes": pipeline_lock.EXPECTED_BUILD_RECIPES[
                    "strong-local"
                ],
            },
            "synthetic-ci": {
                "source_kind": "trainer-generated-zero",
                "records": 8,
                "seed": "20260711",
                "synthetic_model_seed": 20260711,
                "source_net_sha256": "c" * 64,
                "data_sha256": "b" * 64,
                "hashes_resolved": True,
                "build_recipes": pipeline_lock.EXPECTED_BUILD_RECIPES[
                    "synthetic-ci"
                ],
            },
        },
    }


def placeholder_document() -> dict[str, object]:
    document = resolved_document()
    for name in ("tools", "trainer"):
        document["repositories"][name].update(
            commit=pipeline_lock.ZERO_COMMIT,
            resolved=False,
            placeholder=f"REPLACE_WITH_{name.upper()}_COMMIT",
        )
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    return document


def write_document(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_checked_in_lock_is_structurally_valid() -> None:
    lock = pipeline_lock.load_pipeline_lock(
        pipeline_lock.DEFAULT_LOCK_FILE, allow_placeholders=True
    )
    assert set(lock.repositories) == {"tools", "trainer"}
    assert set(lock.profiles) == {"strong-local", "synthetic-ci"}
    assert lock.training_data_schema.schema_id == "legacy-atomic-v1"
    assert lock.training_data_schema.record_size == 72
    assert (
        lock.repositories["tools"].commit
        == "450049ee7a0ece32694b11f6c55deb7df1d42a84"
    )
    assert (
        lock.repositories["trainer"].commit
        == "44663e28c3e5464ff3be2cdaa26c8518b3951c5f"
    )
    assert (
        lock.profiles["strong-local"].data_sha256
        == "d95f5180c7d6319e8d838752b49c51f611c311aef728c30b42c2df02c2071639"
    )
    assert (
        lock.profiles["synthetic-ci"].data_sha256
        == "60308342207b66da3d07db5a7aee937837d0ca107a7a876150aadf911e0c1484"
    )
    assert lock.profiles["synthetic-ci"].hashes_resolved is True
    assert lock.profiles["strong-local"].build_recipes["tools"] == (
        "strong-local-tools-windows-v2"
    )
    assert lock.profiles["synthetic-ci"].build_recipes["tools"] == (
        "synthetic-ci-tools-linux-v2"
    )


def test_lock_rejects_duplicate_json_keys_at_any_depth(tmp_path: Path) -> None:
    encoded = json.dumps(resolved_document())
    repository = '"repository": "Belzedar94/variant-nnue-tools"'
    encoded = encoded.replace(
        repository, f"{repository}, {repository}", 1
    )
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(encoded, encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        pipeline_lock.load_pipeline_lock(lock_path)


@pytest.mark.parametrize("invalid_version", (True, 2, 3, 5))
def test_lock_requires_schema_version_four(
    tmp_path: Path, invalid_version: object
) -> None:
    document = resolved_document()
    document["schema_version"] = invalid_version
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match="schema_version must be exactly 4"):
        pipeline_lock.load_pipeline_lock(lock_path)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("path", "schemas/other.json", "path must be exactly"),
        ("schema_id", "atomic-bin-v2", "schema_id must be exactly"),
        ("sha256", "f" * 64, "SHA-256 mismatch"),
        ("record_size", 71, "record_size must be exactly"),
    ),
)
def test_lock_rejects_training_data_schema_drift(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    document = resolved_document()
    document["training_data_schema"][key] = value
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match=message):
        pipeline_lock.load_pipeline_lock(lock_path)


@pytest.mark.parametrize(
    "case",
    (
        "root-extra",
        "resolved-repository-extra",
        "unresolved-repository-missing-placeholder",
        "external-profile-synthetic-seed",
        "generated-profile-missing-seed",
        "resolved-profile-placeholder",
        "unresolved-profile-missing-placeholder",
    ),
)
def test_lock_requires_exact_schema_dependent_keys(
    tmp_path: Path, case: str
) -> None:
    document = (
        placeholder_document()
        if case.startswith("unresolved-")
        else resolved_document()
    )
    if case == "root-extra":
        document["extra"] = True
    elif case == "resolved-repository-extra":
        document["repositories"]["tools"]["extra"] = True
    elif case == "unresolved-repository-missing-placeholder":
        document["repositories"]["tools"].pop("placeholder")
    elif case == "external-profile-synthetic-seed":
        document["profiles"]["strong-local"]["synthetic_model_seed"] = 1
    elif case == "generated-profile-missing-seed":
        document["profiles"]["synthetic-ci"].pop("synthetic_model_seed")
    elif case == "resolved-profile-placeholder":
        document["profiles"]["strong-local"]["placeholder"] = "REPLACE_WITH_HASHES"
    else:
        document["profiles"]["synthetic-ci"].pop("placeholder")
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match="keys must be exactly"):
        pipeline_lock.load_pipeline_lock(lock_path, allow_placeholders=True)


def test_release_load_rejects_an_unresolved_pin(tmp_path: Path) -> None:
    lock_path = write_document(tmp_path / "lock.json", placeholder_document())
    with pytest.raises(AssertionError, match="unresolved"):
        pipeline_lock.load_pipeline_lock(lock_path)


def test_resolved_lock_requires_full_hashes(tmp_path: Path) -> None:
    lock_path = write_document(tmp_path / "lock.json", resolved_document())
    lock = pipeline_lock.load_pipeline_lock(lock_path)
    assert lock.repositories["tools"].commit == "a" * 40
    assert lock.profiles["synthetic-ci"].source_net_sha256 == "c" * 64

    document = resolved_document()
    document["repositories"]["tools"]["commit"] = "abc123"
    write_document(lock_path, document)
    with pytest.raises(AssertionError, match="full 40-character SHA"):
        pipeline_lock.load_pipeline_lock(lock_path)


@pytest.mark.parametrize("resolved", (True, False))
def test_profile_rejects_mixed_zero_and_real_hashes(
    tmp_path: Path, resolved: bool
) -> None:
    document = resolved_document()
    synthetic = document["profiles"]["synthetic-ci"]
    synthetic.update(
        data_sha256=(pipeline_lock.ZERO_SHA256 if resolved else "b" * 64),
        hashes_resolved=resolved,
    )
    if not resolved:
        synthetic["placeholder"] = "REPLACE_WITH_SYNTHETIC_DATA_SHA256"
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match="all-zero|data_sha256"):
        pipeline_lock.load_pipeline_lock(lock_path, allow_placeholders=True)


def test_data_hash_bootstrap_keeps_source_network_hash_resolved(
    tmp_path: Path,
) -> None:
    document = placeholder_document()
    document["profiles"]["synthetic-ci"]["source_net_sha256"] = (
        pipeline_lock.ZERO_SHA256
    )
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match="must remain measured"):
        pipeline_lock.load_pipeline_lock(lock_path, allow_placeholders=True)


def test_profile_rejects_obsolete_tools_dataset_hash_key(
    tmp_path: Path,
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"]["atomic_data_sha256"] = "c" * 64
    lock_path = write_document(tmp_path / "lock.json", document)
    with pytest.raises(AssertionError, match="keys must be exactly"):
        pipeline_lock.load_pipeline_lock(lock_path)


def test_github_outputs_emit_only_resolved_repository_pins(tmp_path: Path) -> None:
    lock_path = write_document(tmp_path / "lock.json", resolved_document())
    lock = pipeline_lock.load_pipeline_lock(lock_path)
    output = tmp_path / "github-output.txt"
    pipeline_lock._write_github_outputs(lock, output)
    assert output.read_text(encoding="utf-8").splitlines() == [
        "training_data_schema_id=legacy-atomic-v1",
        f"training_data_schema_sha256={lock.training_data_schema.sha256}",
        "training_data_record_size=72",
        "tools_repository=Belzedar94/variant-nnue-tools",
        f"tools_commit={'a' * 40}",
        "trainer_repository=Belzedar94/variant-nnue-pytorch",
        f"trainer_commit={'b' * 40}",
        "synthetic_hashes_resolved=true",
    ]


def test_ci_checks_out_both_locked_sibling_repositories_recursively() -> None:
    workflow = (TESTS_DIR.parent / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )

    def checkout_step(name: str) -> str:
        marker = f"      - name: {name}\n"
        assert marker in workflow
        return workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]

    tools = checkout_step("Check out variant-nnue-tools at the locked commit")
    trainer = checkout_step("Check out variant-nnue-pytorch at the locked commit")
    assert "submodules: recursive" in tools
    assert "submodules: recursive" in trainer

    for name, directory in (
        (
            "Fetch the tools engine authentication ref",
            ".pipeline/tools/engine/Atomic-Stockfish",
        ),
        (
            "Fetch the trainer engine authentication ref",
            ".pipeline/trainer/external/Atomic-Stockfish",
        ),
    ):
        step = checkout_step(name)
        assert f"working-directory: {directory}" in step
        assert "git fetch --no-tags --unshallow origin" in step
        assert "git fetch --no-tags origin +main:refs/remotes/origin/main" in step


def test_github_outputs_can_bootstrap_only_unresolved_synthetic_hashes(
    tmp_path: Path,
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)
    output = tmp_path / "github-output.txt"

    assert pipeline_lock.main(
        [
            "--lock-file",
            str(lock_path),
            "github-outputs",
            "--output",
            str(output),
            "--allow-unresolved-hashes",
        ]
    ) == 0
    assert output.read_text(encoding="utf-8").splitlines()[-1] == (
        "synthetic_hashes_resolved=false"
    )


def test_github_outputs_rejects_unresolved_synthetic_hashes_without_bootstrap(
    tmp_path: Path,
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)

    with pytest.raises(AssertionError, match="hashes are unresolved"):
        pipeline_lock.main(
            [
                "--lock-file",
                str(lock_path),
                "github-outputs",
                "--output",
                str(tmp_path / "github-output.txt"),
            ]
        )


def test_github_outputs_bootstrap_still_rejects_unresolved_repository_pins(
    tmp_path: Path,
) -> None:
    document = placeholder_document()
    lock_path = write_document(tmp_path / "lock.json", document)

    with pytest.raises(
        AssertionError, match="requires resolved repository pins: tools, trainer"
    ):
        pipeline_lock.main(
            [
                "--lock-file",
                str(lock_path),
                "github-outputs",
                "--output",
                str(tmp_path / "github-output.txt"),
                "--allow-unresolved-hashes",
            ]
        )


def test_github_outputs_bootstrap_rejects_other_unresolved_profile_hashes(
    tmp_path: Path,
) -> None:
    document = resolved_document()
    document["profiles"]["strong-local"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_STRONG_LOCAL_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)

    with pytest.raises(
        AssertionError, match="only permits unresolved synthetic-ci hashes"
    ):
        pipeline_lock.main(
            [
                "--lock-file",
                str(lock_path),
                "github-outputs",
                "--output",
                str(tmp_path / "github-output.txt"),
                "--allow-unresolved-hashes",
            ]
        )


def _stub_clean_checkouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def clean_checkout(
        root: Path, label: str, expected_commit: str | None = None
    ) -> pipeline_lock.CheckoutState:
        del label
        return pipeline_lock.CheckoutState(
            root=root.resolve(), head=expected_commit or "c" * 40
        )

    monkeypatch.setattr(
        pipeline_lock, "enforce_clean_checkout", clean_checkout
    )


def test_verify_checkouts_can_bootstrap_only_unresolved_synthetic_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)
    lock = pipeline_lock.load_pipeline_lock(
        lock_path, allow_placeholders=True
    )
    _stub_clean_checkouts(monkeypatch)

    states = pipeline_lock.verify_release_checkouts(
        lock,
        tools_root=tmp_path / "tools",
        trainer_root=tmp_path / "trainer",
        atomic_root=tmp_path / "atomic",
        allow_unresolved_hashes=True,
    )

    assert set(states) == {"tools", "trainer", "atomic"}


def test_verify_checkouts_rejects_unresolved_synthetic_without_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)
    lock = pipeline_lock.load_pipeline_lock(
        lock_path, allow_placeholders=True
    )
    _stub_clean_checkouts(monkeypatch)

    with pytest.raises(AssertionError, match="hashes are unresolved"):
        pipeline_lock.verify_release_checkouts(
            lock,
            tools_root=tmp_path / "tools",
            trainer_root=tmp_path / "trainer",
            atomic_root=tmp_path / "atomic",
        )


def test_verify_checkouts_bootstrap_rejects_unresolved_repository_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = write_document(tmp_path / "lock.json", placeholder_document())
    lock = pipeline_lock.load_pipeline_lock(
        lock_path, allow_placeholders=True
    )
    _stub_clean_checkouts(monkeypatch)

    with pytest.raises(
        AssertionError,
        match="verify-checkouts requires resolved repository pins: tools, trainer",
    ):
        pipeline_lock.verify_release_checkouts(
            lock,
            tools_root=tmp_path / "tools",
            trainer_root=tmp_path / "trainer",
            atomic_root=tmp_path / "atomic",
            allow_unresolved_hashes=True,
        )


def test_verify_checkouts_bootstrap_rejects_unresolved_strong_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = resolved_document()
    document["profiles"]["strong-local"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_STRONG_LOCAL_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)
    lock = pipeline_lock.load_pipeline_lock(
        lock_path, allow_placeholders=True
    )
    _stub_clean_checkouts(monkeypatch)

    with pytest.raises(
        AssertionError,
        match="verify-checkouts only permits unresolved synthetic-ci hashes",
    ):
        pipeline_lock.verify_release_checkouts(
            lock,
            tools_root=tmp_path / "tools",
            trainer_root=tmp_path / "trainer",
            atomic_root=tmp_path / "atomic",
            allow_unresolved_hashes=True,
        )


def test_verify_checkouts_cli_loads_and_forwards_measurement_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = resolved_document()
    document["profiles"]["synthetic-ci"].update(
        data_sha256=pipeline_lock.ZERO_SHA256,
        hashes_resolved=False,
        placeholder="REPLACE_WITH_SYNTHETIC_DATA_SHA256",
    )
    lock_path = write_document(tmp_path / "lock.json", document)

    def verify(
        lock: pipeline_lock.PipelineLock, **arguments: object
    ) -> dict[str, pipeline_lock.CheckoutState]:
        assert lock.profiles["synthetic-ci"].hashes_resolved is False
        assert arguments.pop("allow_unresolved_hashes") is True
        assert set(arguments) == {
            "tools_root",
            "trainer_root",
            "atomic_root",
            "tools_engine",
            "engine",
            "atomic_commit",
        }
        return {
            name: pipeline_lock.CheckoutState(
                root=tmp_path / name, head=character * 40
            )
            for name, character in (
                ("tools", "a"),
                ("trainer", "b"),
                ("atomic", "c"),
            )
        }

    monkeypatch.setattr(pipeline_lock, "verify_release_checkouts", verify)
    assert pipeline_lock.main(
        [
            "--lock-file",
            str(lock_path),
            "verify-checkouts",
            "--tools-root",
            "tools",
            "--trainer-root",
            "trainer",
            "--atomic-root",
            "atomic",
            "--allow-unresolved-hashes",
        ]
    ) == 0
    assert "LEGACY PIPELINE CHECKOUTS VERIFIED" in capsys.readouterr().out


def run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def test_checkout_enforcement_rejects_wrong_head_and_dirty_tree(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    run_git(checkout, "init", "--quiet")
    tracked = checkout / "tracked.txt"
    tracked.write_text("fixture\n", encoding="utf-8")
    run_git(checkout, "add", "tracked.txt")
    run_git(
        checkout,
        "-c",
        "user.name=Atomic CI",
        "-c",
        "user.email=atomic-ci@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    head = run_git(checkout, "rev-parse", "HEAD")
    state = pipeline_lock.enforce_clean_checkout(checkout, "fixture", head)
    assert state.head == head

    with pytest.raises(AssertionError, match="HEAD mismatch"):
        pipeline_lock.enforce_clean_checkout(checkout, "fixture", "f" * 40)

    (checkout / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="not clean"):
        pipeline_lock.enforce_clean_checkout(checkout, "fixture", head)


def test_path_containment_rejects_cross_checkout_artifacts(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    artifact = tmp_path / "outside.exe"
    artifact.write_bytes(b"")
    with pytest.raises(AssertionError, match="outside its pinned checkout"):
        pipeline_lock.require_path_within(artifact, checkout, "artifact")


@pytest.mark.parametrize("engine_argument", ("tools_engine", "engine"))
def test_release_checkout_verification_rejects_missing_engine_files(
    tmp_path: Path, engine_argument: str
) -> None:
    lock_path = write_document(tmp_path / "lock.json", resolved_document())
    lock = pipeline_lock.load_pipeline_lock(lock_path)
    tools_root = tmp_path / "tools"
    trainer_root = tmp_path / "trainer"
    atomic_root = tmp_path / "atomic"
    for root in (tools_root, trainer_root, atomic_root):
        root.mkdir()
    arguments = {
        "tools_root": tools_root,
        "trainer_root": trainer_root,
        "atomic_root": atomic_root,
        engine_argument: (
            tools_root / "missing-engine"
            if engine_argument == "tools_engine"
            else atomic_root / "missing-engine"
        ),
    }
    with pytest.raises(AssertionError, match="is not an existing file"):
        pipeline_lock.verify_release_checkouts(lock, **arguments)


def test_locked_build_recipes_exist_with_expected_roles_and_platforms() -> None:
    expected_platform = {"strong-local": "win32", "synthetic-ci": "linux"}
    expected_target = {
        "strong-local": {
            "tools": build_manifest.X86_64_RELEASE_TARGET,
            "trainer": None,
            "atomic": build_manifest.X86_64_BMI2_RELEASE_TARGET,
        },
        "synthetic-ci": {
            "tools": build_manifest.X86_64_RELEASE_TARGET,
            "trainer": None,
            "atomic": build_manifest.X86_64_RELEASE_TARGET,
        },
    }
    for profile, recipes in pipeline_lock.EXPECTED_BUILD_RECIPES.items():
        for role, recipe_name in recipes.items():
            recipe = build_manifest.RECIPES[recipe_name]
            assert recipe.role == role
            assert recipe.platform == expected_platform[profile]
            assert recipe.expected_engine_target == expected_target[profile][role]
