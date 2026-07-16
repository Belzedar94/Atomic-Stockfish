import json
from pathlib import Path
import subprocess
import sys

import pyffish


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = "1.0.0"


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
        "ref": "refs/tags/v1.0.0",
    }
    assert policy["windowsMakeComp"] == "mingw"
    assert policy["abi3AuditVersion"] == "0.0.26"
    assert policy["pythonManylinuxX86_64Image"] == (
        "quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1@sha256:"
        "853663dc8253b62be437bb52a5caecffd020792af4442f55d927d22e0ea795ae"
    )
    assert policy["releaseCiRequirementsSha256"] == (
        "33f274924a8f41ca9cf4ddc891c0d488dc30491c29d2b034de9088d9d032dd28"
    )
    assert policy["releaseWheelTestRequirementsSha256"] == (
        "b877081ac9f4a6aa56eff9c5ed6c7b832a9fc02ca2dca39f786401e1a03f842b"
    )
    assert policy["pythonWheelRuntimeSmoke"] == {
        "operatingSystems": ["ubuntu-24.04", "windows-2022"],
        "pythonVersions": ["3.9", "3.12", "3.14"],
    }
    patterns = [item["namePattern"] for item in inventory["assets"]]
    assert len(patterns) == 12
    assert len(patterns) == len(set(patterns))

    workflow = (ROOT / ".github" / "workflows" / "atomic-release.yml").read_text(
        encoding="utf-8"
    )
    assert "RELEASE_VERSION: " + EXPECTED in workflow
    assert "- v" + EXPECTED in workflow
    assert workflow.count("contents: write") == 1
    assemble = workflow.split("  assemble:\n", 1)[1].split("  publish:\n", 1)[0]
    publish = workflow.split("  publish:\n", 1)[1]
    assert "contents: write" not in assemble
    assert "contents: write" in publish
    assert (
        "if: github.event_name == 'push' && github.ref_type == 'tag' "
        "&& github.ref_name == 'v1.0.0'"
    ) in publish
    assert "overwrite_files: false" in publish
    source = workflow.split("  source:\n", 1)[1].split("  board-wasm:\n", 1)[0]
    assert source.count('git archive --format=tar --prefix=') == 3
    assert 'normalized_a="build/release/atomic_pyffish-${RELEASE_VERSION}.tar.gz"' in source
    assert 'cmp "$normalized_a" "$normalized_b"' in source

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
    assert pyffish.version() == (1, 0, 0)
    assert pyffish.info().startswith("Atomic-Stockfish 1.0.0 ")


def test_native_incremental_build_tracks_the_release_version_header() -> None:
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    rule = makefile.split("misc.o: misc.cpp", 1)[1].split("\n\n", 1)[0]
    assert "atomic_version.h" in rule.splitlines()[0]
