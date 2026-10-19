import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional

import pyffish


ROOT = Path(__file__).resolve().parents[2]
# The release that the release workflows build (their RELEASE_VERSION). The
# source tree does not carry it: each build receives it as ATOMIC_VERSION.
EXPECTED = "1.0.3"
DEVELOPMENT_INFO = re.compile(r"Atomic-Stockfish dev-[0-9]{8}-(?:[0-9a-f]{8}|nogit) ")


def run_setup_version(atomic_version: Optional[str]) -> subprocess.CompletedProcess:
    environment = {
        name: value for name, value in os.environ.items() if name != "ATOMIC_VERSION"
    }
    if atomic_version is not None:
        environment["ATOMIC_VERSION"] = atomic_version
    return subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def git_identity() -> Optional[tuple[str, str]]:
    """Return the commit date and short SHA that name a development build."""
    commands = (
        ["rev-parse", "--show-cdup"],
        ["rev-parse", "HEAD"],
        ["show", "-s", "--date=format:%Y%m%d", "--format=%cd", "HEAD"],
    )
    outputs = []
    for command in commands:
        try:
            completed = subprocess.run(
                ["git", "-C", str(ROOT), *command],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        outputs.append(completed.stdout.strip())
    if outputs[0] != "":
        return None
    return outputs[2], outputs[1][:8]


def test_source_tree_carries_no_release_number() -> None:
    header = (ROOT / "src" / "atomic_version.h").read_text(encoding="utf-8")
    declarations = [
        line.strip() for line in header.splitlines() if "AtomicVersionString =" in line
    ]
    assert declarations == [
        "inline constexpr std::string_view AtomicVersionString = "
        "ATOMIC_VERSION_STRINGIFY(ATOMIC_VERSION);",
        'inline constexpr std::string_view AtomicVersionString = "dev";',
    ]
    assert re.search(r"[0-9]+\.[0-9]+\.[0-9]+", header) is None

    package = json.loads((ROOT / "tests" / "js" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "0.0.0-dev"
    assert EXPECTED not in (ROOT / "setup.py").read_text(encoding="utf-8")

    for recipe in (
        "build_atomic_board_wasm_release.sh",
        "build_atomic_native_release.sh",
        "build_atomic_source_release.sh",
        "build_atomic_uci_wasm_release.sh",
    ):
        text = (ROOT / "scripts" / recipe).read_text(encoding="utf-8")
        assert "AtomicVersionString" not in text
        assert 'ATOMIC_VERSION="$version"' in text


def test_setup_names_release_and_development_builds() -> None:
    release = run_setup_version(EXPECTED)
    assert release.returncode == 0, release.stderr
    assert release.stdout.strip() == EXPECTED

    development = run_setup_version(None)
    assert development.returncode == 0, development.stderr
    identity = git_identity()
    if identity is None:
        assert development.stdout.strip() == "0.0.0.dev0"
    else:
        assert development.stdout.strip() == "0.0.0.dev%s+g%s" % identity

    for malformed in ("1.0", "01.0.3", "1.0.3-rc1", "v1.0.3"):
        rejected = run_setup_version(malformed)
        assert rejected.returncode != 0
        assert "ATOMIC_VERSION must be MAJOR.MINOR.PATCH" in rejected.stderr


def test_release_version_is_consistent_across_packaging_surfaces() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Version 1.0.3 is the first stable, strength-qualified release" in readme
    assert (
        "`v1.0.0`, `v1.0.1` and `v1.0.2` tags are failed prepublication candidates"
        in readme
    )

    checklist = (
        ROOT / "docs" / "atomic" / "release-1.0-checklist.md"
    ).read_text(encoding="utf-8")
    assert "permits exactly the tag\n   `v1.0.3`" in checklist
    assert "require that none of `v1.0.0`,\n   `v1.0.1` or `v1.0.2` is permitted" in checklist

    notes = (ROOT / "docs" / "atomic" / "release-1.0-notes.md").read_text(
        encoding="utf-8"
    )
    assert notes.startswith("# Atomic-Stockfish 1.0.3\n")
    assert "clean Python binding\nbootstrap was missing" in notes
    assert "admitted musllinux" in notes
    assert "intermittent MSVC LTCG output" in notes

    package = json.loads((ROOT / "tests" / "js" / "package.json").read_text(encoding="utf-8"))
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
        "ref": "refs/tags/v1.0.3",
    }
    assert policy["mainTagTrust"] == {
        "defaultBranch": "main",
        "mergeCommitParents": 2,
        "mergeMethod": "merge",
        "onlineRequired": True,
        "releasePullRequest": 48,
        "requiredBaseCommitSha": "66b030907c35b3a4a91a35653162b7882fc6fd49",
        "revalidatedBy": [
            "main-trust",
            "exact-tag-external",
            "publication-gate",
            "publish",
        ],
    }
    assert policy["prePublicationRecovery"] == {
        "failedTag": "v1.0.2",
        "failedTagObjectSha": "6b58b018776163eb223c41612a397eea8a2e011f",
        "failedTagCommitSha": "66b030907c35b3a4a91a35653162b7882fc6fd49",
        "failedWorkflowRunId": 29557301333,
        "failedWorkflowJobId": 87813381533,
        "githubReleaseCreated": False,
        "releaseAssetsCreated": False,
        "recoveryTag": "v1.0.3",
        "reason": "windows-python-wheel-intermittent-nondeterminism",
    }
    assert policy["windowsMakeComp"] == "mingw"
    assert policy["abi3AuditVersion"] == "0.0.26"
    assert policy["emscriptenImage"] == (
        "emscripten/emsdk:4.0.10@sha256:"
        "90b757eb11fa9a0e3ce4d2d9f76d932a56018e4accc37b5a28b2783751e60eb7"
    )
    assert policy["sourceBuildRepetitions"] == 2
    assert policy["pythonWheelBuildRepetitions"] == 4
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
    validate = workflow.split("  validate:\n", 1)[1].split("  main-trust:\n", 1)[0]
    assert validate.index('export ATOMIC_VERSION="$RELEASE_VERSION"') < validate.index(
        'test "$(python -I setup.py --version)" = "$RELEASE_VERSION"'
    )
    smoke = workflow.split("  python-wheel-smoke:\n", 1)[1].split("  board-wasm:\n", 1)[0]
    assert 'release = os.environ["RELEASE_VERSION"]' in smoke
    assert (
        'assert pyffish.version() == tuple(int(part) for part in release.split("."))'
        in smoke
    )
    assert 'assert pyffish.info().startswith("Atomic-Stockfish " + release + " ")' in smoke
    uci = workflow.split("  uci-wasm:\n", 1)[1].split("  assemble:\n", 1)[0]
    assert 'grep -Fx "id name Atomic-Stockfish $RELEASE_VERSION"' in uci
    assert "assert pyffish.version() == (" not in workflow
    assert "id name Atomic-Stockfish 1." not in workflow
    assert workflow.count("contents: write") == 1
    assemble = workflow.split("  assemble:\n", 1)[1].split("  publish:\n", 1)[0]
    publish = workflow.split("  publish:\n", 1)[1]
    assert "contents: write" not in assemble
    assert "contents: write" in publish
    assert (
        "if: github.event_name == 'push' && github.ref_type == 'tag' "
        "&& github.ref_name == 'v1.0.3'"
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

    release_pr_workflow = (
        ROOT / ".github" / "workflows" / "atomic-release-pr.yml"
    ).read_text(encoding="utf-8")
    assert "RELEASE_VERSION: " + EXPECTED in release_pr_workflow
    assert (
        'test "$(ATOMIC_VERSION="$RELEASE_VERSION" python setup.py --version)" '
        '= "$RELEASE_VERSION"'
    ) in release_pr_workflow


def test_compiled_python_surface_reports_its_build_version() -> None:
    module_path = Path(pyffish.__file__).resolve()
    assert module_path.parent == ROOT

    # The in-place module must have been built in this same environment: as the
    # release named by ATOMIC_VERSION, or else as a development build.
    release = os.environ.get("ATOMIC_VERSION")
    if release:
        assert pyffish.version() == tuple(int(part) for part in release.split("."))
        assert pyffish.info().startswith("Atomic-Stockfish " + release + " ")
        return

    assert pyffish.version() == (0, 0, 0)
    identity = git_identity()
    if identity is None:
        assert DEVELOPMENT_INFO.match(pyffish.info()), pyffish.info()
    else:
        assert pyffish.info().startswith("Atomic-Stockfish dev-%s-%s " % identity)


def test_native_incremental_build_tracks_the_version() -> None:
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    rule = makefile.split("misc.o: misc.cpp", 1)[1].split("\n\n", 1)[0]
    assert "atomic_version.h" in rule.splitlines()[0]
    assert "$(BUILD_VERSION_FILE)" in rule.splitlines()[0]
    assert "$(if $(ATOMIC_RELEASE),-DATOMIC_VERSION=$(ATOMIC_RELEASE))" in rule
    assert "_ := $(call cache_file_contents,$(BUILD_VERSION_FILE),$(ATOMIC_RELEASE))" in makefile
    assert "ATOMIC_VERSION must be MAJOR.MINOR.PATCH" in makefile
