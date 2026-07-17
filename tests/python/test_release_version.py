import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pyffish


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = "1.0.2"


def header_version() -> str:
    header = (ROOT / "src" / "atomic_version.h").read_text(encoding="utf-8")
    values = []
    for name in ("Major", "Minor", "Patch"):
        marker = f"AtomicVersion{name} = "
        line = next(line for line in header.splitlines() if marker in line)
        values.append(line.split(marker, 1)[1].split(";", 1)[0].strip())
    declared = next(
        line for line in header.splitlines() if "AtomicVersionString = " in line
    )
    string_value = declared.split('"', 2)[1]
    assert string_value == ".".join(values)
    return string_value


def test_release_version_is_consistent_across_packaging_surfaces() -> None:
    assert header_version() == EXPECTED

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Version 1.0.2 is the first stable, strength-qualified release" in readme
    assert "`v1.0.0` and `v1.0.1` tags are failed prepublication candidates" in readme

    checklist = (
        ROOT / "docs" / "atomic" / "release-1.0-checklist.md"
    ).read_text(encoding="utf-8")
    assert "permits exactly the tag\n   `v1.0.2`" in checklist
    assert "require that neither `v1.0.0` nor\n   `v1.0.1` is permitted" in checklist

    package = json.loads((ROOT / "tests" / "js" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == EXPECTED
    assert package["repository"]["url"].endswith("Belzedar94/Atomic-Stockfish.git")
    assert package["license"] == "GPL-3.0-or-later"

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert "license: GPL-3.0-or-later\n" in citation

    inventory = json.loads(
        (ROOT / "docs" / "atomic" / "release-1.0-inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert inventory["project"] == "Atomic-Stockfish"
    assert inventory["version"] == EXPECTED
    assert inventory["schemaVersion"] == 2
    policy = inventory["releasePolicy"]
    assert policy["tagObjectType"] == "annotated"
    assert policy["immutableReleasesRequired"] is True
    assert policy["immutableReleasesReadSecret"] == "ATOMIC_RELEASE_POLICY_TOKEN"
    assert policy["draftOnly"] is True
    assert policy["requiredWorkflow"] == {
        "event": "push",
        "name": "Atomic CI",
        "path": ".github/workflows/atomic.yml",
        "ref": "refs/tags/v1.0.2",
    }
    assert policy["mainTagTrust"] == {
        "defaultBranch": "main",
        "mergeCommitParents": 2,
        "mergeMethod": "merge",
        "onlineRequired": True,
        "releasePullRequest": 47,
        "requiredBaseCommitSha": "8fa6a46c92a7471743051f7c6d1ce9b093590043",
        "revalidatedBy": [
            "main-trust",
            "exact-tag-external",
            "publication-gate",
            "publish",
        ],
    }
    assert policy["prePublicationRecovery"] == {
        "failedTag": "v1.0.1",
        "failedTagObjectSha": "62084b84e1bbf9c432a8b898abe4e4f9b2f17983",
        "failedTagCommitSha": "8fa6a46c92a7471743051f7c6d1ce9b093590043",
        "failedWorkflowRunId": 29555199867,
        "failedWorkflowJobId": 87806684417,
        "githubReleaseCreated": False,
        "releaseAssetsCreated": False,
        "recoveryTag": "v1.0.2",
        "reason": "linux-wheel-recipe-included-unintended-musllinux",
    }
    assert policy["windowsMakeComp"] == "mingw"
    assert policy["abi3AuditVersion"] == "0.0.26"
    assert policy["emscriptenImage"] == (
        "emscripten/emsdk:4.0.10@sha256:"
        "90b757eb11fa9a0e3ce4d2d9f76d932a56018e4accc37b5a28b2783751e60eb7"
    )
    assert policy["sourceBuildRepetitions"] == 2
    assert policy["wasmBuildRepetitions"] == 2
    assert policy["pythonManylinuxX86_64Image"] == (
        "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
        "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
    )
    assert policy["releaseCiRequirementsSha256"] == (
        "f155cfda7577ee6a652e87ca54ebf8fecd49b2c8a158294c1fa1cd368b771b5a"
    )
    assert policy["releaseBuildRequirementsSha256"] == (
        "a2d6f8f099bbaf88509c38910f6d2aed1d0913ddf162813906ca7a667b260289"
    )
    assert policy["releaseWheelTestRequirementsSha256"] == (
        "b877081ac9f4a6aa56eff9c5ed6c7b832a9fc02ca2dca39f786401e1a03f842b"
    )
    assert policy["pythonWheelRuntimeSmoke"] == {
        "operatingSystems": ["ubuntu-24.04", "windows-2022"],
        "pythonVersions": ["3.9", "3.12", "3.14"],
    }
    assert policy["windowsWheelFingerprintSchemaVersion"] == 2
    assert policy["windowsWheelFingerprintDocument"] == (
        "docs/atomic/windows-wheel-fingerprint-v2.json"
    )
    assert policy["windowsWheelFingerprintSha256"] == (
        "9af3078f7f7d2635e5fe20c913c6948e53dbdc5f1ec81ba22f081d21e6a3f23d"
    )
    assert policy["windowsWheelImageOS"] == "win22"
    assert policy["windowsWheelImageVersion"] == "20260714.244.1"
    assert policy["windowsWheelPythonVersion"] == "3.9.13"

    fingerprint_path = ROOT / policy["windowsWheelFingerprintDocument"]
    assert fingerprint_path.is_file()
    assert not fingerprint_path.is_symlink()
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert (
        "docs/atomic/windows-wheel-fingerprint-v2.json text eol=lf" in attributes
    )
    fingerprint_bytes = fingerprint_path.read_bytes()
    assert hashlib.sha256(fingerprint_bytes).hexdigest() == policy[
        "windowsWheelFingerprintSha256"
    ]
    fingerprint = json.loads(fingerprint_bytes)
    canonical_bytes = (
        json.dumps(
            fingerprint,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    assert fingerprint_bytes == canonical_bytes
    assert set(fingerprint) == {
        "python",
        "runner",
        "schemaVersion",
        "tools",
        "visualStudio",
    }
    assert fingerprint["schemaVersion"] == policy[
        "windowsWheelFingerprintSchemaVersion"
    ]
    assert fingerprint["runner"]["imageOS"] == policy["windowsWheelImageOS"]
    assert fingerprint["runner"]["imageVersion"] == policy[
        "windowsWheelImageVersion"
    ]
    python_fingerprint = fingerprint["python"]
    assert python_fingerprint["version"] == policy["windowsWheelPythonVersion"]
    assert python_fingerprint["implementation"] == "CPython"
    assert python_fingerprint["pointerBits"] == 64
    assert python_fingerprint["compiler"] == "MSC v.1929 64 bit (AMD64)"
    assert python_fingerprint["sysconfig"] == {
        "EXT_SUFFIX": ".cp39-win_amd64.pyd",
        "SOABI": None,
        "platform": "win-amd64",
    }
    assert python_fingerprint["packages"] == {
        "pip": {"version": "26.0.1"},
        "setuptools": {"version": "80.9.0"},
        "wheel": {"version": "0.45.1"},
    }
    assert {
        name: record["role"]
        for name, record in python_fingerprint["artifacts"].items()
    } == {
        "baseExecutable": "baseExecutable",
        "runtimeLibrary": "runtimeLibrary",
        "venvExecutable": "venvExecutable",
    }
    assert all(
        set(record) == {"basename", "bytes", "role", "sha256"}
        and record["bytes"] > 0
        and len(record["sha256"]) == 64
        and record["sha256"] == record["sha256"].lower()
        for record in python_fingerprint["artifacts"].values()
    )
    patterns = [item["namePattern"] for item in inventory["assets"]]
    assert len(patterns) == 12
    assert len(patterns) == len(set(patterns))

    workflow = (ROOT / ".github" / "workflows" / "atomic-release.yml").read_text(
        encoding="utf-8"
    )
    assert "RELEASE_VERSION: " + EXPECTED in workflow
    assert "- v" + EXPECTED in workflow
    assert "assert pyffish.version() == (1, 0, 2)" in workflow
    assert "assert pyffish.version() == (1, 0, 1)" not in workflow
    assert workflow.count("contents: write") == 1
    assemble = workflow.split("  assemble:\n", 1)[1].split("  publish:\n", 1)[0]
    publish = workflow.split("  publish:\n", 1)[1]
    assert "contents: write" not in assemble
    assert "contents: write" in publish
    assert (
        "if: github.event_name == 'push' && github.ref_type == 'tag' "
        "&& github.ref_name == 'v1.0.2'"
    ) in publish
    assert "softprops/action-gh-release" not in publish
    assert 'test "$UPLOAD_URL" = "$expected_upload_url"' in publish
    assert "--data-binary \"@$asset\"" in publish
    source = workflow.split("  source:\n", 1)[1].split("  board-wasm:\n", 1)[0]
    assert '"$EMSCRIPTEN_IMAGE"' in source
    assert source.count("scripts/build_atomic_source_release.sh") >= 1
    assert '"${source_recipe_a[@]}"' in source
    assert '"${source_recipe_b[@]}"' in source
    assert '"${sdist_recipe_a[@]}"' in source
    assert '"${sdist_recipe_b[@]}"' in source
    assert '"build/source-a/build/release/$source_asset"' in source
    assert '"build/source-b/build/release/$source_asset"' in source
    assert '"build/sdist-a/build/release/$sdist_asset"' in source
    assert '"build/sdist-b/build/release/$sdist_asset"' in source

    quality_workflow = (ROOT / ".github" / "workflows" / "atomic.yml").read_text(
        encoding="utf-8"
    )
    assert "python-version: ['3.9', '3.12', '3.14']" in quality_workflow

    setup_version = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert setup_version == EXPECTED


def test_compiled_python_surface_reports_release_version() -> None:
    module_path = Path(pyffish.__file__).resolve()
    assert module_path.parent == ROOT
    assert pyffish.version() == (1, 0, 2)
    assert pyffish.info().startswith("Atomic-Stockfish 1.0.2 ")


def test_native_incremental_build_tracks_the_release_version_header() -> None:
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    rule = makefile.split("misc.o: misc.cpp", 1)[1].split("\n\n", 1)[0]
    assert "atomic_version.h" in rule.splitlines()[0]
