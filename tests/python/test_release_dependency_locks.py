from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

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
        "kaitaistruct",
        "markdown-it-py",
        "mdurl",
        "packaging",
        "patchelf",
        "pefile",
        "platformdirs",
        "pluggy",
        "pyelftools",
        "pygments",
        "pyproject-hooks",
        "pytest",
        "requests",
        "requests-cache",
        "rich",
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
    assert len(locked["librt"].hashes) == 2
    assert all(requirement.marker is None for requirement in locked.values())


def test_pep517_build_requirements_are_exactly_pinned() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["build-system"] == {
        "requires": ["setuptools==80.9.0", "wheel==0.45.1"],
        "build-backend": "setuptools.build_meta",
    }
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "include tests/release-wheel-test-requirements.txt" in manifest


def test_release_inventory_authenticates_both_dependency_locks() -> None:
    inventory = json.loads(
        (ROOT / "docs" / "atomic" / "release-1.0-inventory.json").read_text(
            encoding="utf-8"
        )
    )["releasePolicy"]
    expected = {
        "releaseCiRequirementsSha256": "release-ci-requirements.txt",
        "releaseWheelTestRequirementsSha256": (
            "release-wheel-test-requirements.txt"
        ),
    }
    for field, name in expected.items():
        actual = hashlib.sha256((ROOT / "tests" / name).read_bytes()).hexdigest()
        assert inventory[field] == actual
