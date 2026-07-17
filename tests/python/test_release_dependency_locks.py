from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from scripts.atomic_release_manifest import expected_inventory_policy

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.9 lane
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")
REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)==([^\s;]+)(?:\s*;\s*(.*?))?\s+"
    r"((?:--hash=sha256:[0-9a-f]{64}(?:\s+|$))+)$"
)


@dataclass(frozen=True)
class LockedRequirement:
    version: str
    marker: str | None
    hashes: tuple[str, ...]


def load_lock(name: str) -> dict[str, LockedRequirement]:
    path = ROOT / "tests" / name
    logical: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical.append(pending)
        pending = ""
    assert not pending, f"unterminated continuation in {path}"

    result: dict[str, LockedRequirement] = {}
    for line in logical:
        assert "http://" not in line and "https://" not in line
        assert "--index" not in line and "--find-links" not in line
        match = REQUIREMENT.fullmatch(line)
        assert match is not None, f"noncanonical lock entry: {line}"
        package, version, marker, encoded_hashes = match.groups()
        key = package.lower().replace("_", "-")
        assert key not in result, f"duplicate lock entry: {key}"
        hashes = tuple(HASH.findall(encoded_hashes))
        assert hashes and len(hashes) == len(set(hashes))
        result[key] = LockedRequirement(version, marker, hashes)
    return result


def test_release_runner_lock_is_complete_and_hash_closed() -> None:
    locked = load_lock("release-ci-requirements.txt")
    assert set(locked) == {
        "abi3audit",
        "abi3info",
        "attrs",
        "bashlex",
        "bracex",
        "build",
        "cattrs",
        "certifi",
        "charset-normalizer",
        "cibuildwheel",
        "colorama",
        "dependency-groups",
        "filelock",
        "humanize",
        "idna",
            "iniconfig",
            "jsonschema",
            "jsonschema-specifications",
            "kaitaistruct",
        "librt",
        "markdown-it-py",
        "mdurl",
        "mypy",
        "mypy-extensions",
        "packaging",
        "pathspec",
        "patchelf",
        "pefile",
        "platformdirs",
        "pluggy",
        "psutil",
        "pyelftools",
        "pygments",
        "pyproject-hooks",
        "pytest",
        "requests",
            "requests-cache",
            "referencing",
            "rich",
            "rpds-py",
        "setuptools",
        "typing-extensions",
        "url-normalize",
        "urllib3",
        "wheel",
    }
    assert locked["pytest"].version == "8.3.5"
    assert locked["setuptools"].version == "80.9.0"
    assert locked["wheel"].version == "0.45.1"
    assert locked["cibuildwheel"].version == "3.4.1"
    assert locked["abi3audit"].version == "0.0.26"
    assert locked["mypy"].version == "1.19.1"
    assert locked["psutil"].version == "7.2.2"
    assert len(locked["psutil"].hashes) == 2
    assert len(locked["mypy"].hashes) == 2
    assert len(locked["librt"].hashes) == 2
    assert locked["colorama"].marker == 'sys_platform == "win32"'
    assert locked["patchelf"].marker == (
        'sys_platform == "linux" and platform_machine == "x86_64"'
    )
    assert len(locked["charset-normalizer"].hashes) == 2


def test_cibuildwheel_test_lock_is_complete_and_hash_closed() -> None:
    locked = load_lock("release-wheel-test-requirements.txt")
    assert set(locked) == {
        "librt",
        "mypy",
        "mypy-extensions",
        "pathspec",
        "tomli",
        "typing-extensions",
    }
    assert locked["mypy"].version == "1.19.1"
    assert locked["tomli"].version == "2.4.1"
    assert len(locked["mypy"].hashes) == 2
    assert set(locked["librt"].hashes) == {
        "3657346f867469e962549435aa05fd15330b1d6a92829f8e27988e194382d005",
        "b15e26cc0fe622d0c67e98bee6ef6bc8f792e20ee3006aa12627a00463d9399f",
    }
    assert all(requirement.marker is None for requirement in locked.values())


def test_pep517_build_lock_is_complete_and_hash_closed() -> None:
    locked = load_lock("release-build-requirements.txt")
    assert set(locked) == {"pip", "setuptools", "wheel"}
    assert locked["pip"].version == "26.0.1"
    assert locked["setuptools"].version == "80.9.0"
    assert locked["wheel"].version == "0.45.1"
    assert all(requirement.marker is None for requirement in locked.values())


def test_source_recipe_installs_build_dependencies_only_from_the_hash_lock() -> None:
    recipe = (ROOT / "scripts" / "build_atomic_source_release.sh").read_text(
        encoding="utf-8"
    )
    assert recipe.count("python3 -m pip install") == 1
    assert "--force-reinstall --no-deps --only-binary=:all: --require-hashes" in recipe
    assert "tests/release-build-requirements.txt" in recipe
    assert "setup.py sdist" in recipe
    assert "scripts/atomic_reproducible_sdist.py" in recipe


def test_pep517_build_requirements_are_exactly_pinned() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["build-system"] == {
        "requires": ["setuptools==80.9.0", "wheel==0.45.1"],
        "build-backend": "setuptools.build_meta",
    }
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "include scripts/atomic_windows_wheel_fingerprint.py" in manifest
    assert "include tests/release-build-requirements.txt" in manifest
    assert "include tests/release-wheel-test-requirements.txt" in manifest


def test_release_inventory_authenticates_both_dependency_locks() -> None:
    inventory = json.loads(
        (ROOT / "docs" / "atomic" / "release-1.0-inventory.json").read_text(
            encoding="utf-8"
        )
    )["releasePolicy"]
    expected = {
        "releaseBuildRequirementsSha256": "release-build-requirements.txt",
        "releaseCiRequirementsSha256": "release-ci-requirements.txt",
        "releaseWheelTestRequirementsSha256": (
            "release-wheel-test-requirements.txt"
        ),
    }
    for field, name in expected.items():
        actual = hashlib.sha256((ROOT / "tests" / name).read_bytes()).hexdigest()
        assert inventory[field] == actual


def test_checked_in_inventory_matches_the_assembler_policy() -> None:
    inventory = json.loads(
        (ROOT / "docs" / "atomic" / "release-1.0-inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert inventory["releasePolicy"] == expected_inventory_policy("1.0.2")
